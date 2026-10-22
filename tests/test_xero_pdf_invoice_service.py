import unittest
from decimal import Decimal
from unittest.mock import Mock
from uuid import UUID

from fieldquo.pdf_invoice_workflow import PDFInvoiceWorkflow
from tenant_fiscal_config import TenantFiscalConfig
from xero.invoice_creation_config import (
    InvoiceCreationConfig,
    InvoiceCreationConfigError,
)
from xero.pdf_invoice_service import (
    PDFFiscalConfigNotFoundError,
    PDFInvoiceConnectionNotFoundError,
    PDFInvoiceService,
    PDFInvoiceServiceInputError,
    PDFInvoiceTenantMismatchError,
    PDFInvoiceTenantNotFoundError,
)
from xero_connection import XeroConnection


PDF_TEXT = """\
Invoice number: PDF-100
Client: Example Client
Issued: 2026-09-25
Due: 2026-10-25
Currency: EUR
Subtotal: 120.00
Tax: 0.00
Discount: 0.00
Total: 120.00
Description | Quantity | Unit Price | Line Total
Service | 1 | 120.00 | 120.00
"""


class PDFInvoiceServiceTests(unittest.TestCase):
    TENANT_ID = UUID("66d204ae-87bc-4ea5-8a37-a3d444ec7f34")
    OTHER_TENANT_ID = UUID("887308bf-0b1b-433e-8cbd-4686be14f948")
    XERO_TENANT_ID = "xero-org-for-tenant-a"

    def setUp(self):
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
        self.client = Mock()
        self.client_factory = Mock(return_value=self.client)
        self.orchestrator = Mock(return_value="orchestrator-result")
        self.service = self.create_service()
        self.workflow_result = PDFInvoiceWorkflow().process_text(PDF_TEXT)
        self.contacts = [{"ContactID": "contact-a", "Name": "Example Client"}]

    def create_service(self):
        return PDFInvoiceService(
            tenant_resolver=self.tenant_resolver,
            connection_resolver=self.connection_resolver,
            fiscal_config_resolver=self.fiscal_config_resolver,
            client_factory=self.client_factory,
            orchestrator=self.orchestrator,
        )

    def fiscal_config(self, **overrides):
        values = {
            "tenant_id": self.TENANT_ID,
            "account_code": "200",
            "tax_type": "OUTPUT",
            "tax_inclusive": False,
            "amount_basis": "subtotal",
            "discount_policy": "ignore",
        }
        values.update(overrides)
        return TenantFiscalConfig(**values)

    def create_pdf_invoice(self, **overrides):
        values = {
            "license_key": "license-a",
            "workflow_result": self.workflow_result,
            "contacts": self.contacts,
            "source_identity": "local-pdf-identity-a",
            "description": "Service invoice",
            "quantity": Decimal("1"),
        }
        values.update(overrides)
        return self.service.create_pdf_invoice(**values)

    def test_resolves_license_context_and_forwards_workflow_to_orchestrator(self):
        self.assertEqual(self.create_pdf_invoice(), "orchestrator-result")

        self.tenant_resolver.resolve_tenant_id.assert_called_once_with("license-a")
        self.connection_resolver.get_for_tenant.assert_called_once_with(
            self.TENANT_ID, "license-a"
        )
        self.fiscal_config_resolver.get_for_tenant.assert_called_once_with(
            self.TENANT_ID, "license-a"
        )
        self.client_factory.assert_called_once_with(self.XERO_TENANT_ID)
        self.orchestrator.assert_called_once()
        args, kwargs = self.orchestrator.call_args
        self.assertIs(args[0], self.workflow_result)
        self.assertEqual(args[1], self.contacts)
        self.assertEqual(kwargs["source_identity"], "local-pdf-identity-a")
        self.assertIs(args[3], self.client)

    def test_config_uses_internal_tenant_and_all_persisted_fiscal_fields(self):
        self.create_pdf_invoice()
        config = self.orchestrator.call_args.args[2]

        self.assertIsInstance(config, InvoiceCreationConfig)
        self.assertEqual(config.tenant_id, str(self.TENANT_ID))
        self.assertNotEqual(config.tenant_id, self.XERO_TENANT_ID)
        self.assertEqual(config.description, "Service invoice")
        self.assertEqual(config.quantity, Decimal("1"))
        self.assertEqual(config.account_code, "200")
        self.assertEqual(config.tax_type, "OUTPUT")
        self.assertIs(config.tax_inclusive, False)
        self.assertEqual(config.amount_basis, "subtotal")
        self.assertEqual(config.discount_policy, "ignore")

    def test_pdf_fields_cannot_override_persisted_fiscal_configuration(self):
        self.workflow_result.invoice.update(
            {
                "account_code": "PDF-ACCOUNT",
                "tax_type": "PDF-TAX",
                "tax_inclusive": True,
                "amount_basis": "total",
                "discount_policy": "line_discount",
            }
        )

        self.create_pdf_invoice()

        config = self.orchestrator.call_args.args[2]
        self.assertEqual(config.account_code, "200")
        self.assertEqual(config.tax_type, "OUTPUT")
        self.assertIs(config.tax_inclusive, False)
        self.assertEqual(config.amount_basis, "subtotal")
        self.assertEqual(config.discount_policy, "ignore")

    def test_missing_license_tenant_blocks_before_other_resolution(self):
        self.tenant_resolver.resolve_tenant_id.return_value = None

        with self.assertRaises(PDFInvoiceTenantNotFoundError):
            self.create_pdf_invoice()

        self.connection_resolver.get_for_tenant.assert_not_called()
        self.fiscal_config_resolver.get_for_tenant.assert_not_called()
        self.client_factory.assert_not_called()
        self.orchestrator.assert_not_called()

    def test_missing_connection_blocks_before_client_and_orchestrator(self):
        self.connection_resolver.get_for_tenant.return_value = None

        with self.assertRaises(PDFInvoiceConnectionNotFoundError):
            self.create_pdf_invoice()

        self.fiscal_config_resolver.get_for_tenant.assert_not_called()
        self.client_factory.assert_not_called()
        self.orchestrator.assert_not_called()

    def test_missing_fiscal_config_blocks_before_client_and_orchestrator(self):
        self.fiscal_config_resolver.get_for_tenant.return_value = None

        with self.assertRaises(PDFFiscalConfigNotFoundError):
            self.create_pdf_invoice()

        self.client_factory.assert_not_called()
        self.orchestrator.assert_not_called()

    def test_connection_for_another_internal_tenant_is_rejected(self):
        self.connection_resolver.get_for_tenant.return_value = XeroConnection(
            tenant_id=self.OTHER_TENANT_ID,
            xero_tenant_id="xero-org-for-tenant-b",
            xero_tenant_name="Tenant B",
            xero_tenant_type="ORGANISATION",
        )

        with self.assertRaises(PDFInvoiceTenantMismatchError):
            self.create_pdf_invoice()

        self.fiscal_config_resolver.get_for_tenant.assert_not_called()
        self.client_factory.assert_not_called()
        self.orchestrator.assert_not_called()

    def test_fiscal_config_for_another_internal_tenant_is_rejected(self):
        self.fiscal_config_resolver.get_for_tenant.return_value = self.fiscal_config(
            tenant_id=self.OTHER_TENANT_ID
        )

        with self.assertRaises(PDFInvoiceTenantMismatchError):
            self.create_pdf_invoice()

        self.client_factory.assert_not_called()
        self.orchestrator.assert_not_called()

    def test_invalid_persisted_fiscal_config_blocks_before_client_creation(self):
        self.fiscal_config_resolver.get_for_tenant.return_value = self.fiscal_config(
            amount_basis="unsupported"
        )

        with self.assertRaises(InvoiceCreationConfigError):
            self.create_pdf_invoice()

        self.client_factory.assert_not_called()
        self.orchestrator.assert_not_called()

    def test_required_non_fiscal_config_inputs_have_no_defaults(self):
        with self.assertRaises(TypeError):
            self.service.create_pdf_invoice(
                license_key="license-a",
                workflow_result=self.workflow_result,
                contacts=self.contacts,
                source_identity="local-pdf-identity-a",
            )

        self.tenant_resolver.resolve_tenant_id.assert_not_called()

    def test_invalid_invoice_config_inputs_block_before_client_creation(self):
        with self.assertRaises(InvoiceCreationConfigError):
            self.create_pdf_invoice(description="", quantity=Decimal("1"))

        self.client_factory.assert_not_called()
        self.orchestrator.assert_not_called()

    def test_infrastructure_resolution_errors_propagate_without_becoming_missing(self):
        resolver_error = RuntimeError("Supabase transport unavailable")
        self.fiscal_config_resolver.get_for_tenant.side_effect = resolver_error

        with self.assertRaisesRegex(RuntimeError, "transport unavailable"):
            self.create_pdf_invoice()

        self.client_factory.assert_not_called()
        self.orchestrator.assert_not_called()

    def test_client_factory_receives_xero_id_without_replacing_internal_tenant(self):
        self.create_pdf_invoice()

        self.assertEqual(
            self.connection_resolver.get_for_tenant.call_args.args[0],
            self.TENANT_ID,
        )
        self.client_factory.assert_called_once_with(self.XERO_TENANT_ID)
        config = self.orchestrator.call_args.args[2]
        self.assertEqual(config.tenant_id, str(self.TENANT_ID))
        self.client.create_invoice.assert_not_called()


if __name__ == "__main__":
    unittest.main()
