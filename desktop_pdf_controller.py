"""Safe desktop boundary for selecting and previewing PDF invoices."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from auth import LicenseValidator
from fieldquo.pdf_invoice_workflow import (
    PDFInvoiceWorkflow,
    PDFInvoiceWorkflowError,
    PDFInvoiceWorkflowResult,
)


class LicenseValidationClient(Protocol):
    def validate_license(self, license_key: str) -> tuple[bool, str]: ...


class PDFWorkflowClient(Protocol):
    def process_pdf(self, pdf_path: str | Path) -> PDFInvoiceWorkflowResult: ...


class DesktopPDFError(RuntimeError):
    """Raised when desktop PDF preview cannot proceed safely."""


class DesktopPDFController:
    """Validate the license and process a PDF without invoking Xero."""

    def __init__(
        self,
        *,
        license_validator: LicenseValidationClient | None = None,
        workflow: PDFWorkflowClient | None = None,
    ) -> None:
        self._license_validator = (
            license_validator if license_validator is not None else LicenseValidator()
        )
        self._workflow = workflow if workflow is not None else PDFInvoiceWorkflow()

    def process_pdf(
        self, pdf_path: str | Path, license_key: str
    ) -> PDFInvoiceWorkflowResult:
        path = Path(pdf_path).expanduser()
        if path.suffix.lower() != ".pdf" or not path.is_file():
            raise DesktopPDFError("Selecione um arquivo PDF existente.")
        if not isinstance(license_key, str) or not license_key.strip():
            raise DesktopPDFError("Informe uma chave de licença.")

        is_valid, message = self._license_validator.validate_license(
            license_key.strip()
        )
        if not is_valid:
            raise DesktopPDFError(message or "A licença não foi validada.")

        try:
            return self._workflow.process_pdf(path)
        except PDFInvoiceWorkflowError as exc:
            raise DesktopPDFError(f"Não foi possível processar o PDF: {exc}") from exc


def classify_selected_file(file_path: str | Path) -> str | None:
    """Return the supported local input kind for a selected file."""
    suffix = Path(file_path).suffix.lower()
    if suffix in {".csv", ".xlsx", ".pdf"}:
        return suffix[1:]
    return None
