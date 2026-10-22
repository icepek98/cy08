"""Local, non-emitting PDF invoice preview."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from fieldquo.pdf_invoice_parser import (
    ParsedFieldQuoInvoice,
    parse_fieldquo_invoice_text,
)
from fieldquo.pdf_invoice_workflow import PDF_INVOICE_ORIGIN
from fieldquo.pdf_text_extractor import (
    PDFTextExtractor,
    PopplerPDFTextExtractor,
    extract_pdf_text,
)


_EMISSION_BLOCKERS = (
    "Validação de licença e resolução do tenant interno.",
    "Autorização OAuth Xero com escopo de escrita.",
    "Resolução do ContactID no Xero; o nome extraído não é um ContactID.",
    "Configuração fiscal do tenant (account_code e tax_type).",
    "Mapeamento explícito dos argumentos description e quantity exigidos pelo serviço.",
)


class PDFInvoicePreviewError(RuntimeError):
    """Raised when a local PDF preview cannot be produced safely."""


@dataclass(frozen=True)
class PDFInvoicePreview:
    """Only normalized values and diagnostics; never contains source PDF text."""

    invoice: dict[str, Any]
    complete: bool
    missing_fields: tuple[str, ...]
    ambiguities: tuple[str, ...]
    rejections: tuple[str, ...]
    source_identity: str
    emission_blockers: tuple[str, ...]


class PDFInvoicePreviewProcessor:
    """Extract and parse one PDF locally without invoking emission services."""

    def __init__(
        self,
        *,
        extractor: PDFTextExtractor | None = None,
        parser: Callable[[str], ParsedFieldQuoInvoice] = parse_fieldquo_invoice_text,
    ) -> None:
        self._extractor = (
            extractor if extractor is not None else PopplerPDFTextExtractor()
        )
        self._parser = parser

    def process_pdf(self, pdf_path: str | Path) -> PDFInvoicePreview:
        path = Path(pdf_path).expanduser()
        if path.suffix.lower() != ".pdf":
            raise PDFInvoicePreviewError(
                "O arquivo selecionado não tem extensão .pdf."
            )
        if not path.is_file():
            raise PDFInvoicePreviewError("O arquivo PDF selecionado não existe.")

        try:
            text = extract_pdf_text(path, self._extractor)
        except (OSError, TypeError, RuntimeError) as exc:
            raise PDFInvoicePreviewError(str(exc)) from exc

        try:
            parsed = self._parser(text)
        except (TypeError, ValueError) as exc:
            raise PDFInvoicePreviewError(
                "Não foi possível interpretar os campos identificados no PDF."
            ) from exc

        return PDFInvoicePreview(
            invoice=_copy_invoice(parsed.invoice),
            complete=parsed.complete,
            missing_fields=parsed.missing_fields,
            ambiguities=parsed.ambiguities,
            rejections=parsed.rejections,
            source_identity=PDF_INVOICE_ORIGIN,
            emission_blockers=_EMISSION_BLOCKERS,
        )


def _copy_invoice(invoice: dict[str, Any]) -> dict[str, Any]:
    return deepcopy(invoice)


def format_pdf_invoice_preview(preview: PDFInvoicePreview) -> tuple[str, ...]:
    """Format recognized values and pending requirements for the desktop log."""
    invoice = preview.invoice
    lines = [
        "Pré-visualização — nenhuma invoice foi enviada ao Xero.",
        (
            "Pronto para próxima etapa: os campos mínimos do PDF foram "
            "identificados."
            if preview.complete
            else "Pré-visualização parcial: há campos do PDF pendentes."
        ),
    ]
    for label, field in (
        ("Invoice", "invoice_number"),
        ("Cliente", "client"),
        ("Data de emissão", "issued"),
        ("Data de vencimento", "due"),
        ("Moeda", "currency"),
        ("Subtotal", "subtotal"),
        ("Imposto", "tax"),
        ("Desconto", "discount"),
        ("Total", "total"),
    ):
        value = invoice.get(field)
        if value is not None:
            lines.append(f"{label}: {value}")

    line_items = invoice.get("line_items")
    if isinstance(line_items, list):
        for index, item in enumerate(line_items, start=1):
            if isinstance(item, dict):
                found = [
                    f"{label}={item[field]}"
                    for label, field in (
                        ("descrição", "description"),
                        ("quantidade", "quantity"),
                        ("preço unitário", "unit_price"),
                        ("total da linha", "line_total"),
                    )
                    if item.get(field) is not None
                ]
                if found:
                    lines.append(f"Linha {index}: " + "; ".join(found))

    if preview.missing_fields:
        lines.append(
            "Campos não identificados no PDF: "
            + ", ".join(preview.missing_fields)
        )
    if preview.ambiguities:
        lines.append("Valores ambíguos: " + "; ".join(preview.ambiguities))
    if preview.rejections:
        lines.append("Observações do parser: " + "; ".join(preview.rejections))

    lines.append("Pendências antes de qualquer emissão:")
    lines.extend(f"- {blocker}" for blocker in preview.emission_blockers)
    return tuple(lines)
