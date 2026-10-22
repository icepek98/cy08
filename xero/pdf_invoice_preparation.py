"""Resolve a PDF invoice's tenant context without crossing the Xero write boundary."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Mapping, Protocol
from uuid import UUID

from fieldquo.pdf_invoice_parser import ParsedFieldQuoInvoice
from fieldquo.pdf_invoice_workflow import (
    PDF_INVOICE_ORIGIN,
    PDFInvoiceWorkflowResult,
)
from tenant_fiscal_config import (
    TenantFiscalConfig,
    TenantFiscalConfigResolver,
)
from tenant_identity import TenantResolver
from xero.invoice_creation_config import (
    InvoiceCreationConfig,
    InvoiceCreationConfigError,
    Quantity,
)
from xero_connection import XeroConnection, XeroConnectionResolver


class LicenseValidatorProtocol(Protocol):
    def validate_license(self, license_key: str) -> tuple[bool, str]: ...


class PDFInvoicePreparationError(RuntimeError):
    """Base class for fail-closed PDF invoice context preparation errors."""


class PDFInvoicePreparationInputError(PDFInvoicePreparationError):
    """Raised when preparation inputs or resolved values violate their contracts."""


class PDFInvoicePreparationLicenseError(PDFInvoicePreparationError):
    """Raised when license validation fails or returns an invalid result."""


class PDFInvoicePreparationTenantNotFoundError(PDFInvoicePreparationError):
    """Raised when the validated license has no internal tenant mapping."""


class PDFInvoicePreparationConnectionNotFoundError(PDFInvoicePreparationError):
    """Raised when the internal tenant has no Xero connection."""


class PDFInvoicePreparationFiscalConfigNotFoundError(PDFInvoicePreparationError):
    """Raised when the internal tenant has no persisted fiscal configuration."""


class PDFInvoicePreparationResolverError(PDFInvoicePreparationError):
    """Raised when a resolver fails while preparing the context."""


class PDFInvoicePreparationConfigError(PDFInvoicePreparationError):
    """Raised when the resolved values cannot form a valid creation config."""


class PDFInvoiceWorkflowNotReadyError(PDFInvoicePreparationError):
    """Raised when a PDF workflow result is not deterministically complete."""


class PDFInvoiceWorkflowLineItemsError(PDFInvoicePreparationError):
    """Raised when the config's single-line contract cannot represent the PDF."""


@dataclass(frozen=True)
class PDFInvoicePreparationContext:
    """Resolved context only; it contains no credentials or write-capable client."""

    tenant_id: UUID
    xero_tenant_id: str
    invoice_creation_config: InvoiceCreationConfig

    def __post_init__(self) -> None:
        if not isinstance(self.tenant_id, UUID):
            raise PDFInvoicePreparationInputError("tenant_id must be an internal UUID.")
        if (
            not isinstance(self.xero_tenant_id, str)
            or not self.xero_tenant_id.strip()
        ):
            raise PDFInvoicePreparationInputError("xero_tenant_id is invalid.")
        if not isinstance(self.invoice_creation_config, InvoiceCreationConfig):
            raise PDFInvoicePreparationInputError(
                "invoice_creation_config has an invalid type."
            )
        if self.invoice_creation_config.tenant_id != str(self.tenant_id):
            raise PDFInvoicePreparationInputError(
                "invoice_creation_config must use the internal tenant_id."
            )


@dataclass(frozen=True)
class PDFInvoiceWorkflowPreparationResult:
    """A complete PDF workflow result paired with its non-writing context."""

    workflow_result: PDFInvoiceWorkflowResult
    preparation_context: PDFInvoicePreparationContext

    def __post_init__(self) -> None:
        if not isinstance(self.workflow_result, PDFInvoiceWorkflowResult):
            raise PDFInvoicePreparationInputError(
                "workflow_result has an invalid type."
            )
        if not isinstance(
            self.preparation_context, PDFInvoicePreparationContext
        ):
            raise PDFInvoicePreparationInputError(
                "preparation_context has an invalid type."
            )


@dataclass(frozen=True)
class _ResolvedPreparationInputs:
    tenant_id: UUID
    xero_tenant_id: str
    fiscal_config: TenantFiscalConfig


class PDFInvoicePreparationService:
    """Validate a license and resolve tenant/configuration data, then stop."""

    def __init__(
        self,
        *,
        license_validator: LicenseValidatorProtocol,
        tenant_resolver: TenantResolver,
        connection_resolver: XeroConnectionResolver,
        fiscal_config_resolver: TenantFiscalConfigResolver,
    ) -> None:
        self._license_validator = license_validator
        self._tenant_resolver = tenant_resolver
        self._connection_resolver = connection_resolver
        self._fiscal_config_resolver = fiscal_config_resolver

    def prepare(
        self,
        *,
        license_key: str,
        description: str,
        quantity: Quantity,
    ) -> PDFInvoicePreparationContext:
        """Resolve the context without creating a client or invoking an orchestrator.

        ``description`` and ``quantity`` are required because the existing
        ``InvoiceCreationConfig`` contract requires them. This method does not
        infer or supply either value.
        """
        resolved = self._resolve_preparation_inputs(license_key)
        invoice_creation_config = self._create_invoice_creation_config(
            resolved,
            description=description,
            quantity=quantity,
        )
        return PDFInvoicePreparationContext(
            tenant_id=resolved.tenant_id,
            xero_tenant_id=resolved.xero_tenant_id,
            invoice_creation_config=invoice_creation_config,
        )

    def prepare_workflow_result(
        self,
        *,
        license_key: str,
        workflow_result: PDFInvoiceWorkflowResult,
    ) -> PDFInvoiceWorkflowPreparationResult:
        """Prepare a deterministic one-line workflow result without writing.

        The existing creation config represents one line item. Accordingly,
        multi-line PDFs and results supplied by the optional interpreter are
        rejected rather than collapsed or treated as deterministic parser data.
        """
        resolved = self._resolve_preparation_inputs(license_key)
        description, quantity = _single_deterministic_line(workflow_result)
        invoice_creation_config = self._create_invoice_creation_config(
            resolved,
            description=description,
            quantity=quantity,
        )
        context = PDFInvoicePreparationContext(
            tenant_id=resolved.tenant_id,
            xero_tenant_id=resolved.xero_tenant_id,
            invoice_creation_config=invoice_creation_config,
        )
        return PDFInvoiceWorkflowPreparationResult(
            workflow_result=workflow_result,
            preparation_context=context,
        )

    def _resolve_preparation_inputs(
        self, license_key: str
    ) -> _ResolvedPreparationInputs:
        if not isinstance(license_key, str) or not license_key.strip():
            raise PDFInvoicePreparationInputError("license_key must be non-empty.")

        try:
            validation = self._license_validator.validate_license(license_key)
        except Exception as exc:
            raise PDFInvoicePreparationLicenseError(
                "License validation failed."
            ) from exc
        if (
            not isinstance(validation, tuple)
            or len(validation) != 2
            or validation[0] is not True
        ):
            raise PDFInvoicePreparationLicenseError(
                "License validation did not succeed."
            )

        try:
            tenant_id = self._tenant_resolver.resolve_tenant_id(license_key)
        except Exception as exc:
            raise PDFInvoicePreparationResolverError(
                "Internal tenant resolution failed."
            ) from exc
        if tenant_id is None:
            raise PDFInvoicePreparationTenantNotFoundError(
                "The validated license has no internal tenant mapping."
            )
        if not isinstance(tenant_id, UUID):
            raise PDFInvoicePreparationInputError(
                "Tenant resolver returned an invalid internal tenant identifier."
            )

        try:
            connection = self._connection_resolver.get_for_tenant(
                tenant_id, license_key
            )
        except Exception as exc:
            raise PDFInvoicePreparationResolverError(
                "Xero connection resolution failed."
            ) from exc
        if connection is None:
            raise PDFInvoicePreparationConnectionNotFoundError(
                "No Xero connection is configured for the internal tenant."
            )
        if not isinstance(connection, XeroConnection):
            raise PDFInvoicePreparationInputError(
                "Connection resolver returned an invalid result."
            )
        if connection.tenant_id != tenant_id:
            raise PDFInvoicePreparationInputError(
                "Xero connection belongs to a different internal tenant."
            )
        if (
            not isinstance(connection.xero_tenant_id, str)
            or not connection.xero_tenant_id.strip()
        ):
            raise PDFInvoicePreparationInputError(
                "Xero connection contains an invalid xero_tenant_id."
            )

        try:
            fiscal_config = self._fiscal_config_resolver.get_for_tenant(
                tenant_id, license_key
            )
        except Exception as exc:
            raise PDFInvoicePreparationResolverError(
                "Fiscal configuration resolution failed."
            ) from exc
        if fiscal_config is None:
            raise PDFInvoicePreparationFiscalConfigNotFoundError(
                "No fiscal configuration is configured for the internal tenant."
            )
        if not isinstance(fiscal_config, TenantFiscalConfig):
            raise PDFInvoicePreparationInputError(
                "Fiscal configuration resolver returned an invalid result."
            )
        if fiscal_config.tenant_id != tenant_id:
            raise PDFInvoicePreparationInputError(
                "Fiscal configuration belongs to a different internal tenant."
            )

        return _ResolvedPreparationInputs(
            tenant_id=tenant_id,
            xero_tenant_id=connection.xero_tenant_id,
            fiscal_config=fiscal_config,
        )

    @staticmethod
    def _create_invoice_creation_config(
        resolved: _ResolvedPreparationInputs,
        *,
        description: str,
        quantity: Quantity,
    ) -> InvoiceCreationConfig:
        try:
            invoice_creation_config = InvoiceCreationConfig(
                tenant_id=str(resolved.tenant_id),
                description=description,
                quantity=quantity,
                account_code=resolved.fiscal_config.account_code,
                tax_type=resolved.fiscal_config.tax_type,
                tax_inclusive=resolved.fiscal_config.tax_inclusive,
                amount_basis=resolved.fiscal_config.amount_basis,
                discount_policy=resolved.fiscal_config.discount_policy,
            )
        except InvoiceCreationConfigError as exc:
            raise PDFInvoicePreparationConfigError(
                "Resolved values do not satisfy InvoiceCreationConfig."
            ) from exc
        return invoice_creation_config


def _single_deterministic_line(
    workflow_result: PDFInvoiceWorkflowResult,
) -> tuple[str, Quantity]:
    if (
        not isinstance(workflow_result, PDFInvoiceWorkflowResult)
        or workflow_result.origin != PDF_INVOICE_ORIGIN
        or workflow_result.fallback_used
        or not isinstance(workflow_result.parser_result, ParsedFieldQuoInvoice)
        or not workflow_result.parser_result.complete
        or not isinstance(workflow_result.invoice, Mapping)
        or workflow_result.invoice != workflow_result.parser_result.invoice
    ):
        raise PDFInvoiceWorkflowNotReadyError(
            "PDF workflow result must be complete and produced by the "
            "deterministic parser."
        )

    line_items: Any = workflow_result.parser_result.invoice.get("line_items")
    if not isinstance(line_items, list) or len(line_items) != 1:
        raise PDFInvoiceWorkflowLineItemsError(
            "PDF preparation requires exactly one parsed line item."
        )
    line_item = line_items[0]
    if not isinstance(line_item, Mapping):
        raise PDFInvoiceWorkflowNotReadyError(
            "Parsed PDF line item is not a valid mapping."
        )

    description = line_item.get("description")
    quantity = line_item.get("quantity")
    if (
        not isinstance(description, str)
        or not description.strip()
        or isinstance(quantity, bool)
        or not isinstance(quantity, (int, float, Decimal))
    ):
        raise PDFInvoiceWorkflowNotReadyError(
            "Parsed PDF line item lacks an explicit description or quantity."
        )
    return description, quantity
