"""Non-writing boundary for representing a prepared PDF invoice.

This module is intentionally not wired to the desktop preview. The current
desktop PDF path is preview-only and does not inject the tenant-scoped
preparation dependencies required here.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol
from uuid import UUID

from fieldquo.pdf_invoice_workflow import (
    PDFInvoiceWorkflow,
    PDFInvoiceWorkflowResult,
)
from xero.invoice_creation_config import InvoiceCreationConfig, Quantity
from xero.pdf_invoice_preparation import (
    PDFInvoicePreparationService,
    PDFInvoiceWorkflowPreparationResult,
)


class PDFWorkflowClient(Protocol):
    def process_pdf(self, pdf_path: str | Path) -> PDFInvoiceWorkflowResult: ...


class PDFWorkflowPreparationClient(Protocol):
    def prepare_workflow_result(
        self,
        *,
        license_key: str,
        workflow_result: PDFInvoiceWorkflowResult,
    ) -> PDFInvoiceWorkflowPreparationResult: ...


@dataclass(frozen=True)
class PreparedPDFInvoice:
    """A prepared operation, not evidence that an invoice was created."""

    preparation_result: PDFInvoiceWorkflowPreparationResult

    def __post_init__(self) -> None:
        if not isinstance(
            self.preparation_result, PDFInvoiceWorkflowPreparationResult
        ):
            raise TypeError(
                "preparation_result must be a "
                "PDFInvoiceWorkflowPreparationResult."
            )


@dataclass(frozen=True)
class PDFInvoiceDryRunResult:
    """Deterministic, credential-free representation of a proposed operation."""

    status: Literal["dry_run"]
    tenant_id: UUID
    xero_tenant_id: str
    description: str
    quantity: Quantity
    account_code: str
    tax_type: str
    tax_inclusive: bool
    amount_basis: str
    discount_policy: str
    source_origin: str


class PDFInvoiceDryRunEmitter:
    """Convert prepared data into a dry-run record without performing I/O."""

    def emit(
        self, prepared_invoice: PreparedPDFInvoice
    ) -> PDFInvoiceDryRunResult:
        if not isinstance(prepared_invoice, PreparedPDFInvoice):
            raise TypeError("prepared_invoice must be a PreparedPDFInvoice.")

        prepared = prepared_invoice.preparation_result
        context = prepared.preparation_context
        config: InvoiceCreationConfig = context.invoice_creation_config
        return PDFInvoiceDryRunResult(
            status="dry_run",
            tenant_id=context.tenant_id,
            xero_tenant_id=context.xero_tenant_id,
            description=config.description,
            quantity=config.quantity,
            account_code=config.account_code,
            tax_type=config.tax_type,
            tax_inclusive=config.tax_inclusive,
            amount_basis=config.amount_basis,
            discount_policy=config.discount_policy,
            source_origin=prepared.workflow_result.origin,
        )


class PDFInvoiceDryRunService:
    """Run local PDF parsing, tenant preparation, and dry-run representation."""

    def __init__(
        self,
        *,
        workflow: PDFWorkflowClient | None = None,
        preparation_service: PDFWorkflowPreparationClient,
        emitter: PDFInvoiceDryRunEmitter | None = None,
    ) -> None:
        self._workflow = workflow if workflow is not None else PDFInvoiceWorkflow()
        self._preparation_service = preparation_service
        self._emitter = emitter if emitter is not None else PDFInvoiceDryRunEmitter()

    def run_pdf(
        self, *, pdf_path: str | Path, license_key: str
    ) -> PDFInvoiceDryRunResult:
        workflow_result = self._workflow.process_pdf(pdf_path)
        preparation_result = self._preparation_service.prepare_workflow_result(
            license_key=license_key,
            workflow_result=workflow_result,
        )
        prepared_invoice = PreparedPDFInvoice(preparation_result)
        return self._emitter.emit(prepared_invoice)


__all__ = [
    "PDFInvoiceDryRunEmitter",
    "PDFInvoiceDryRunResult",
    "PDFInvoiceDryRunService",
    "PreparedPDFInvoice",
]
