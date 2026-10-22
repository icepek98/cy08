"""Local workflow for deterministic parsing and optional Ollama fallback."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

from fieldquo.invoice_interpreter import (
    InvoiceInterpretationError,
    InvoiceInterpreter,
    interpret_invoice,
    merge_invoice_interpretations,
)
from fieldquo.pdf_invoice_parser import (
    ParsedFieldQuoInvoice,
    parse_fieldquo_invoice_text,
)
from fieldquo.pdf_text_extractor import (
    PDFTextExtractor,
    extract_pdf_text,
)


_INVOICE_FIELDS = (
    "invoice_number",
    "client",
    "issued",
    "due",
    "currency",
    "subtotal",
    "tax",
    "discount",
    "total",
    "line_items",
)
_REQUIRED_FIELDS = ("invoice_number", "client", "issued", "currency", "total")
_REQUIRED_LINE_FIELDS = ("description", "quantity", "unit_price")
PDF_INVOICE_ORIGIN = "fieldquo_pdf"


class PDFInvoiceWorkflowError(RuntimeError):
    """Raised when a PDF invoice cannot be normalized safely."""


@dataclass(frozen=True)
class PDFInvoiceWorkflowResult:
    """Normalized invoice and diagnostics produced by the local workflow."""

    invoice: dict[str, Any]
    source_text: str
    fallback_used: bool
    missing_fields: tuple[str, ...]
    parser_result: ParsedFieldQuoInvoice | None
    origin: str = field(default=PDF_INVOICE_ORIGIN, init=False)


class PDFInvoiceWorkflow:
    """Coordinate local PDF text extraction, parsing, and optional interpretation."""

    def __init__(
        self,
        *,
        parser: Callable[[str], ParsedFieldQuoInvoice] = parse_fieldquo_invoice_text,
        interpreter: InvoiceInterpreter | None = None,
        fallback_enabled: bool = False,
        extractor: PDFTextExtractor | None = None,
    ) -> None:
        self.parser = parser
        self.interpreter = interpreter
        self.fallback_enabled = fallback_enabled
        self.extractor = extractor

    def process_text(self, source_text: str) -> PDFInvoiceWorkflowResult:
        """Normalize invoice text, using the interpreter only when enabled."""
        if not isinstance(source_text, str):
            raise TypeError("source_text must be a string")
        if not source_text.strip():
            raise PDFInvoiceWorkflowError(
                "O texto extraído do PDF está vazio; nenhuma inferência foi realizada."
            )

        parser_error: ValueError | TypeError | None = None
        parsed: ParsedFieldQuoInvoice | None = None
        try:
            parsed = self.parser(source_text)
        except (ValueError, TypeError) as exc:
            parser_error = exc

        if parsed is not None and parsed.complete:
            return PDFInvoiceWorkflowResult(
                invoice=_copy_invoice(parsed.invoice),
                source_text=source_text,
                fallback_used=False,
                missing_fields=parsed.missing_fields,
                parser_result=parsed,
            )

        if not self.fallback_enabled:
            reason = (
                f"Parser determinístico falhou: {parser_error}"
                if parser_error is not None
                else "Parser determinístico não encontrou uma invoice utilizável."
            )
            raise PDFInvoiceWorkflowError(
                f"{reason} Fallback Ollama está desabilitado."
            ) from parser_error

        if self.interpreter is None:
            raise PDFInvoiceWorkflowError(
                "Fallback Ollama está habilitado, mas nenhum interpretador foi injetado."
            ) from parser_error

        deterministic_invoice = (
            parsed.invoice if parsed is not None else _empty_invoice()
        )
        missing_fields = (
            parsed.missing_fields
            if parsed is not None
            else tuple(_INVOICE_FIELDS)
        )

        try:
            validated = interpret_invoice(
                self.interpreter,
                source_text,
                deterministic_invoice,
                missing_fields,
            )
        except InvoiceInterpretationError as exc:
            raise PDFInvoiceWorkflowError(
                f"Fallback Ollama ou sua validação falhou: {exc}"
            ) from exc

        merged = merge_invoice_interpretations(deterministic_invoice, validated)
        if merged.has_conflicts:
            raise PDFInvoiceWorkflowError(
                "Fallback Ollama conflita com valores determinísticos: "
                + ", ".join(merged.conflicts)
                + "."
            )
        if not _is_usable_invoice(merged.invoice):
            raise PDFInvoiceWorkflowError(
                "A invoice continua incompleta após o fallback; "
                "campos mínimos ausentes: "
                + ", ".join(merged.missing_fields)
                + "."
            )

        return PDFInvoiceWorkflowResult(
            invoice=_copy_invoice(merged.invoice),
            source_text=source_text,
            fallback_used=True,
            missing_fields=merged.missing_fields,
            parser_result=parsed,
        )

    def process_pdf(
        self,
        pdf_path: str | Path,
        *,
        extractor: PDFTextExtractor | None = None,
    ) -> PDFInvoiceWorkflowResult:
        """Extract local PDF text through an injected backend and process it."""
        selected_extractor = extractor if extractor is not None else self.extractor
        if selected_extractor is None:
            raise PDFInvoiceWorkflowError(
                "Nenhum extrator local de texto PDF foi injetado."
            )
        try:
            source_text = extract_pdf_text(pdf_path, selected_extractor)
        except (OSError, TypeError, RuntimeError) as exc:
            raise PDFInvoiceWorkflowError(
                f"Não foi possível extrair texto do PDF: {exc}"
            ) from exc
        return self.process_text(source_text)


def _empty_invoice() -> dict[str, Any]:
    return {
        field: None
        for field in _INVOICE_FIELDS
    }


def _copy_invoice(invoice: Mapping[str, Any]) -> dict[str, Any]:
    copied = dict(invoice)
    line_items = copied.get("line_items")
    if isinstance(line_items, list):
        copied["line_items"] = [
            dict(item) if isinstance(item, Mapping) else item
            for item in line_items
        ]
    return copied


def _is_usable_invoice(invoice: Mapping[str, Any]) -> bool:
    if any(invoice.get(field) is None for field in _REQUIRED_FIELDS):
        return False
    line_items = invoice.get("line_items")
    if not isinstance(line_items, list) or not line_items:
        return False
    return all(
        isinstance(item, Mapping)
        and all(
            item.get(field) is not None
            and (field != "description" or bool(str(item[field]).strip()))
            for field in _REQUIRED_LINE_FIELDS
        )
        for item in line_items
    )
