import unittest
from decimal import Decimal
from unittest.mock import Mock, patch
from uuid import UUID

from tenant_fiscal_config import TenantFiscalConfig
from xero.api import XeroApiClient
from xero.invoice_creation_config import InvoiceCreationConfig
from xero.invoice_creation_orchestrator import create_pdf_invoice_in_xero
from xero.pdf_invoice_preparation import (
    PDFInvoicePreparationConfigError,
    PDFInvoicePreparationConnectionNotFoundError,
    PDFInvoicePreparationFiscalConfigNotFoundError,
    PDFInvoicePreparationInputError,
    PDFInvoicePreparationLicenseError,
    PDFInvoicePreparationResolverError,
    PDFInvoicePreparationService,
    PDFInvoicePreparationTenantNotFoundError,
)
from xero_connection import XeroConnection


class PDFInvoicePreparationTests(unittest.TestCase):
    TENANT_ID = UUID("66d204ae-87bc-4ea5-8a37-a3d444ec7f34")
    OTHER_TENANT_ID = UUID("887308bf-0b1b-433e-8cbd-4686be14f948")
    XERO_TENANT_ID = "xero-org-tenant-a"

    def setUp(self):
        self.calls = []
        self.license_validator = Mock()
        self.license_validator.validate_license.return_value = (
            True,
            "Licença validada.",
        )
        self.tenant_resolver = Mock()
        self.tenant_resolver.resolve_tenant_id.return_value = self.TENANT_ID
        self.connection_resolver = Mock()
        self.connection_resolver.get_for_tenant.return_value = XeroConnection(
            tenant_id=self.TENANT_ID,
            xero_tenant_id=self.XERO_TENANT_ID,
            xero_tenant_name="Tenant A",
            xero_tenant_type="ORGANISATION",
        )
        self.fiscal_config_resolver = Mock()
        self.fiscal_config_resolver.get_for_tenant.return_value = (
            self.fiscal_config()
        )
        self.service = PDFInvoicePreparationService(
            license_validator=self.license_validator,
            tenant_resolver=self.tenant_resolver,
            connection_resolver=self.connection_resolver,
            fiscal_config_resolver=self.fiscal_config_resolver,
        )

    def fiscal_config(self, **overrides):
        values = {
            "tenant_id": self.TENANT_ID,
            "account_code": " 200 ",
            "tax_type": "OUTPUT",
            "tax_inclusive": False,
            "amount_basis": "subtotal",
            "discount_policy": "ignore",
        }
        values.update(overrides)
        return TenantFiscalConfig(**values)

    def prepare(self, **overrides):
        values = {
            "license_key": "license-a",
            "description": "Explicit invoice description",
            "quantity": Decimal("2.50"),
        }
        values.update(overrides)
        return self.service.prepare(**values)

    def test_prepares_context_in_required_order_and_preserves_config_values(self):
        def resolve_tenant(license_key):
            self.calls.append(("tenant", license_key))
            return self.TENANT_ID

        def resolve_connection(tenant_id, license_key):
            self.calls.append(("connection", tenant_id, license_key))
            return XeroConnection(
                tenant_id=self.TENANT_ID,
                xero_tenant_id=self.XERO_TENANT_ID,
                xero_tenant_name="Tenant A",
                xero_tenant_type="ORGANISATION",
            )

        def resolve_fiscal(tenant_id, license_key):
            self.calls.append(("fiscal", tenant_id, license_key))
            return self.fiscal_config()

        self.tenant_resolver.resolve_tenant_id.side_effect = resolve_tenant
        self.connection_resolver.get_for_tenant.side_effect = resolve_connection
        self.fiscal_config_resolver.get_for_tenant.side_effect = resolve_fiscal

        context = self.prepare()

        self.assertEqual(
            [call[0] for call in self.calls],
            ["tenant", "connection", "fiscal"],
        )
        self.assertEqual(context.tenant_id, self.TENANT_ID)
        self.assertEqual(context.xero_tenant_id, self.XERO_TENANT_ID)
        self.assertNotEqual(str(context.tenant_id), context.xero_tenant_id)
        config = context.invoice_creation_config
        self.assertIsInstance(config, InvoiceCreationConfig)
        self.assertEqual(config.tenant_id, str(self.TENANT_ID))
        self.assertEqual(config.description, "Explicit invoice description")
        self.assertEqual(config.quantity, Decimal("2.50"))
        self.assertEqual(config.account_code, " 200 ")
        self.assertEqual(config.tax_type, "OUTPUT")
        self.assertIs(config.tax_inclusive, False)
        self.assertEqual(config.amount_basis, "subtotal")
        self.assertEqual(config.discount_policy, "ignore")

    def test_invalid_license_blocks_all_tenant_resolution(self):
        self.license_validator.validate_license.return_value = (
            False,
            "invalid",
        )

        with self.assertRaises(PDFInvoicePreparationLicenseError):
            self.prepare()

        self.tenant_resolver.resolve_tenant_id.assert_not_called()
        self.connection_resolver.get_for_tenant.assert_not_called()
        self.fiscal_config_resolver.get_for_tenant.assert_not_called()

    def test_license_without_tenant_fails_closed_before_later_resolvers(self):
        self.tenant_resolver.resolve_tenant_id.return_value = None

        with self.assertRaises(PDFInvoicePreparationTenantNotFoundError):
            self.prepare()

        self.connection_resolver.get_for_tenant.assert_not_called()
        self.fiscal_config_resolver.get_for_tenant.assert_not_called()

    def test_tenant_resolution_error_blocks_later_resolvers(self):
        self.tenant_resolver.resolve_tenant_id.side_effect = RuntimeError("offline")

        with self.assertRaises(PDFInvoicePreparationResolverError):
            self.prepare()

        self.connection_resolver.get_for_tenant.assert_not_called()
        self.fiscal_config_resolver.get_for_tenant.assert_not_called()

    def test_missing_connection_blocks_fiscal_resolution(self):
        self.connection_resolver.get_for_tenant.return_value = None

        with self.assertRaises(PDFInvoicePreparationConnectionNotFoundError):
            self.prepare()

        self.fiscal_config_resolver.get_for_tenant.assert_not_called()

    def test_connection_resolution_error_blocks_fiscal_resolution(self):
        self.connection_resolver.get_for_tenant.side_effect = RuntimeError("offline")

        with self.assertRaises(PDFInvoicePreparationResolverError):
            self.prepare()

        self.fiscal_config_resolver.get_for_tenant.assert_not_called()

    def test_connection_for_other_tenant_fails_closed(self):
        self.connection_resolver.get_for_tenant.return_value = XeroConnection(
            tenant_id=self.OTHER_TENANT_ID,
            xero_tenant_id="xero-org-tenant-b",
            xero_tenant_name="Tenant B",
            xero_tenant_type="ORGANISATION",
        )

        with self.assertRaises(PDFInvoicePreparationInputError):
            self.prepare()

        self.fiscal_config_resolver.get_for_tenant.assert_not_called()

    def test_missing_fiscal_configuration_fails_closed(self):
        self.fiscal_config_resolver.get_for_tenant.return_value = None

        with self.assertRaises(PDFInvoicePreparationFiscalConfigNotFoundError):
            self.prepare()

    def test_fiscal_resolution_error_fails_closed(self):
        self.fiscal_config_resolver.get_for_tenant.side_effect = RuntimeError(
            "offline"
        )

        with self.assertRaises(PDFInvoicePreparationResolverError):
            self.prepare()

    def test_fiscal_configuration_for_other_tenant_is_rejected(self):
        self.fiscal_config_resolver.get_for_tenant.return_value = self.fiscal_config(
            tenant_id=self.OTHER_TENANT_ID
        )

        with self.assertRaises(PDFInvoicePreparationInputError):
            self.prepare()

    def test_invalid_persisted_fiscal_value_blocks_context_creation(self):
        self.fiscal_config_resolver.get_for_tenant.return_value = self.fiscal_config(
            amount_basis="unsupported"
        )

        with self.assertRaises(PDFInvoicePreparationConfigError):
            self.prepare()

    def test_missing_required_config_inputs_are_not_defaulted(self):
        with self.assertRaises(TypeError):
            self.service.prepare(license_key="license-a", quantity=Decimal("1"))
        with self.assertRaises(TypeError):
            self.service.prepare(
                license_key="license-a",
                description="description",
            )

    def test_context_rejects_xero_tenant_id_as_internal_tenant_id(self):
        context = self.prepare()

        with self.assertRaises(PDFInvoicePreparationInputError):
            type(context)(
                tenant_id=self.TENANT_ID,
                xero_tenant_id=self.XERO_TENANT_ID,
                invoice_creation_config=InvoiceCreationConfig(
                    tenant_id=self.XERO_TENANT_ID,
                    description="description",
                    quantity=Decimal("1"),
                    account_code="200",
                    tax_type="OUTPUT",
                    tax_inclusive=False,
                    amount_basis="subtotal",
                    discount_policy="ignore",
                ),
            )

    def test_preparation_never_creates_client_calls_orchestrator_or_posts(self):
        with patch("xero.api.XeroApiClient") as client_constructor, patch(
            "xero.invoice_creation_orchestrator.create_pdf_invoice_in_xero"
        ) as pdf_orchestrator, patch.object(
            XeroApiClient, "create_invoice"
        ) as create_invoice, patch("xero.api.requests.post") as xero_post:
            context = self.prepare()

        self.assertEqual(context.tenant_id, self.TENANT_ID)
        client_constructor.assert_not_called()
        pdf_orchestrator.assert_not_called()
        create_invoice.assert_not_called()
        xero_post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
