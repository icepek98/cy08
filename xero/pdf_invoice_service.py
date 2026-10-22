"""Tenant-scoped application service for PDF invoice creation orchestration."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, Protocol
from uuid import UUID

from fieldquo.pdf_invoice_workflow import PDFInvoiceWorkflowResult
from tenant_fiscal_config import (
    TenantFiscalConfig,
    TenantFiscalConfigResolver,
)
from tenant_identity import TenantResolver
from xero.invoice_creation_config import InvoiceCreationConfig, Quantity
from xero.invoice_creation_orchestrator import XeroInvoiceCreationClient
from xero_connection import XeroConnection, XeroConnectionResolver


class PDFInvoiceServiceError(RuntimeError):
    """Base class for failures while resolving the PDF invoice context."""


class PDFInvoiceServiceInputError(PDFInvoiceServiceError):
    """Raised when a required service input is invalid."""


class PDFInvoiceTenantNotFoundError(PDFInvoiceServiceError):
    """Raised when a license has no internal tenant mapping."""


class PDFInvoiceConnectionNotFoundError(PDFInvoiceServiceError):
    """Raised when the tenant has no persisted Xero connection."""


class PDFFiscalConfigNotFoundError(PDFInvoiceServiceError):
    """Raised when the tenant has no persisted fiscal configuration."""


class PDFInvoiceTenantMismatchError(PDFInvoiceServiceError):
    """Raised when resolved tenant-scoped data does not match the license tenant."""


class InvoiceClientFactory(Protocol):
    def __call__(self, xero_tenant_id: str) -> XeroInvoiceCreationClient: ...


class PDFInvoiceOrchestrator(Protocol):
    def __call__(
        self,
        workflow_result: PDFInvoiceWorkflowResult,
        contacts: Iterable[Mapping[str, Any]] | None,
        config: InvoiceCreationConfig,
        client: XeroInvoiceCreationClient,
        *,
        source_identity: str,
    ) -> Any: ...


class PDFInvoiceService:
    """Resolve a license's tenant context and pass a PDF invoice to the orchestrator.

    The injected client factory is responsible for obtaining a valid access token.
    This service neither performs OAuth nor stores credentials.
    """

    def __init__(
        self,
        *,
        tenant_resolver: TenantResolver,
        connection_resolver: XeroConnectionResolver,
        fiscal_config_resolver: TenantFiscalConfigResolver,
        client_factory: InvoiceClientFactory,
        orchestrator: PDFInvoiceOrchestrator,
    ) -> None:
        self._tenant_resolver = tenant_resolver
        self._connection_resolver = connection_resolver
        self._fiscal_config_resolver = fiscal_config_resolver
        self._client_factory = client_factory
        self._orchestrator = orchestrator

    def create_pdf_invoice(
        self,
        *,
        license_key: str,
        workflow_result: PDFInvoiceWorkflowResult,
        contacts: Iterable[Mapping[str, Any]] | None,
        source_identity: str,
        description: str,
        quantity: Quantity,
    ) -> Any:
        """Resolve persisted tenant settings and invoke the injected orchestrator."""
        if not isinstance(license_key, str) or not license_key.strip():
            raise PDFInvoiceServiceInputError("license_key must be non-empty.")
        if not isinstance(source_identity, str) or not source_identity.strip():
            raise PDFInvoiceServiceInputError("source_identity must be non-empty.")
        if not isinstance(workflow_result, PDFInvoiceWorkflowResult):
            raise PDFInvoiceServiceInputError(
                "workflow_result must be a PDFInvoiceWorkflowResult."
            )

        tenant_id = self._tenant_resolver.resolve_tenant_id(license_key)
        if tenant_id is None:
            raise PDFInvoiceTenantNotFoundError(
                "The license has no resolvable internal tenant."
            )
        if not isinstance(tenant_id, UUID):
            raise PDFInvoiceServiceInputError(
                "Tenant resolver returned an invalid internal tenant identifier."
            )

        connection = self._connection_resolver.get_for_tenant(
            tenant_id, license_key
        )
        if connection is None:
            raise PDFInvoiceConnectionNotFoundError(
                "No Xero connection is configured for the license tenant."
            )
        self._ensure_tenant_matches(connection, tenant_id, "Xero connection")
        if (
            not isinstance(connection.xero_tenant_id, str)
            or not connection.xero_tenant_id
            or connection.xero_tenant_id != connection.xero_tenant_id.strip()
        ):
            raise PDFInvoiceServiceInputError(
                "Xero connection contains an invalid xero_tenant_id."
            )

        fiscal_config = self._fiscal_config_resolver.get_for_tenant(
            tenant_id, license_key
        )
        if fiscal_config is None:
            raise PDFFiscalConfigNotFoundError(
                "No fiscal configuration is configured for the license tenant."
            )
        self._ensure_tenant_matches(fiscal_config, tenant_id, "Fiscal configuration")

        config = InvoiceCreationConfig(
            tenant_id=str(tenant_id),
            description=description,
            quantity=quantity,
            account_code=fiscal_config.account_code,
            tax_type=fiscal_config.tax_type,
            tax_inclusive=fiscal_config.tax_inclusive,
            amount_basis=fiscal_config.amount_basis,
            discount_policy=fiscal_config.discount_policy,
        )

        client = self._client_factory(connection.xero_tenant_id)
        return self._orchestrator(
            workflow_result,
            contacts,
            config,
            client,
            source_identity=source_identity,
        )

    @staticmethod
    def _ensure_tenant_matches(
        scoped_data: XeroConnection | TenantFiscalConfig,
        requested_tenant_id: UUID,
        context_name: str,
    ) -> None:
        if not isinstance(scoped_data, (XeroConnection, TenantFiscalConfig)):
            raise PDFInvoiceServiceInputError(
                f"{context_name} resolver returned an invalid result."
            )
        if scoped_data.tenant_id != requested_tenant_id:
            raise PDFInvoiceTenantMismatchError(
                f"{context_name} belongs to a different internal tenant."
            )
