import unittest
from dataclasses import fields, replace
from decimal import Decimal
from unittest.mock import Mock, patch
from uuid import UUID

from fieldquo.pdf_invoice_parser import parse_fieldquo_invoice_text
from fieldquo.pdf_invoice_workflow import (
    PDFInvoiceWorkflow,
    PDFInvoiceWorkflowResult,
)
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
    PDFInvoicePreparationService,
    PDFInvoicePreparationTenantNotFoundError,
    PDFInvoiceWorkflowLineItemsError,
    PDFInvoiceWorkflowNotReadyError,
    PDFInvoiceWorkflowPreparationResult,
)
from xero_connection import XeroConnection


COMPLETE_TEXT = """\
Invoice number: INV-100
Client: Example Client
Issued: 2025-01-02
Currency: EUR
Total: 150.00
Description | Quantity | Unit Price | Line Total
Consulting | 2 | 75.00 | 150.00
"""


class TrackingInvoice(dict):
    def __init__(self, values, events):
        super().__init__(values)
        self.events = events

    def get(self, key, default=None):
        if key == "line_items":
            self.events.append("pdf-result")
        return super().get(key, default)


class PDFInvoiceWorkflowPreparationTests(unittest.TestCase):
    TENANT_ID = UUID("66d204ae-87bc-4ea5-8a37-a3d444ec7f34")
    OTHER_TENANT_ID = UUID("887308bf-0b1b-433e-8cbd-4686be14f948")

    def setUp(self):
        self.events = []
        self.license_validator = Mock()
        self.license_validator.validate_license.side_effect = (
            lambda _key: (self.events.append("license") or True, "valid")
        )
        self.tenant_resolver = Mock()
        self.tenant_resolver.resolve_tenant_id.side_effect = (
            lambda _key: (self.events.append("tenant") or self.TENANT_ID)
        )
        self.connection_resolver = Mock()
        self.connection_resolver.get_for_tenant.side_effect = (
            self.resolve_connection
        )
        self.fiscal_config_resolver = Mock()
        self.fiscal_config_resolver.get_for_tenant.side_effect = (
            self.resolve_fiscal_config
        )
        self.service = PDFInvoicePreparationService(
            license_validator=self.license_validator,
            tenant_resolver=self.tenant_resolver,
            connection_resolver=self.connection_resolver,
            fiscal_config_resolver=self.fiscal_config_resolver,
        )

    def resolve_connection(self, tenant_id, _license_key):
        self.events.append("connection")
        return XeroConnection(
            tenant_id=tenant_id,
            xero_tenant_id="xero-tenant-a",
            xero_tenant_name="Tenant A",
            xero_tenant_type="ORGANISATION",
        )

    def resolve_fiscal_config(self, tenant_id, _license_key):
        self.events.append("fiscal")
        return self.fiscal_config(tenant_id=tenant_id)

    @staticmethod
    def fiscal_config(**overrides):
        values = {
            "tenant_id": PDFInvoiceWorkflowPreparationTests.TENANT_ID,
            "account_code": "200",
            "tax_type": "OUTPUT",
            "tax_inclusive": False,
            "amount_basis": "subtotal",
            "discount_policy": "ignore",
        }
        values.update(overrides)
        return TenantFiscalConfig(**values)

    def complete_workflow_result(self):
        result = PDFInvoiceWorkflow().process_text(COMPLETE_TEXT)
        tracked_invoice = TrackingInvoice(
            result.parser_result.invoice,
            self.events,
        )
        parser_result = replace(
            result.parser_result,
            invoice=tracked_invoice,
        )
        return replace(result, parser_result=parser_result)

    def prepare(self, result=None):
        return self.service.prepare_workflow_result(
            license_key="license-a",
            workflow_result=result or self.complete_workflow_result(),
        )

    def test_valid_workflow_returns_context_after_ordered_resolution(self):
        result = self.prepare()

        self.assertIsInstance(result, PDFInvoiceWorkflowPreparationResult)
        self.assertIsInstance(result.workflow_result, PDFInvoiceWorkflowResult)
        context = result.preparation_context
        config = context.invoice_creation_config
        self.assertEqual(
            self.events,
            ["license", "tenant", "connection", "fiscal", "pdf-result"],
        )
        self.assertEqual(context.tenant_id, self.TENANT_ID)
        self.assertEqual(context.xero_tenant_id, "xero-tenant-a")
        self.assertIsInstance(config, InvoiceCreationConfig)
        self.assertEqual(config.tenant_id, str(self.TENANT_ID))
        self.assertEqual(config.description, "Consulting")
        self.assertEqual(config.quantity, Decimal("2"))
        self.assertEqual(config.account_code, "200")
        self.assertEqual(config.tax_type, "OUTPUT")
        self.assertIs(config.tax_inclusive, False)
        self.assertEqual(config.amount_basis, "subtotal")
        self.assertEqual(config.discount_policy, "ignore")
        self.assertEqual(
            {item.name for item in fields(context)},
            {"tenant_id", "xero_tenant_id", "invoice_creation_config"},
        )

    def test_invalid_license_does_not_resolve_tenant_or_inspect_pdf(self):
        self.license_validator.validate_license.side_effect = None
        self.license_validator.validate_license.return_value = (False, "invalid")

        with self.assertRaises(PDFInvoicePreparationLicenseError):
            self.prepare()

        self.assertEqual(self.events, [])
        self.tenant_resolver.resolve_tenant_id.assert_not_called()
        self.connection_resolver.get_for_tenant.assert_not_called()
        self.fiscal_config_resolver.get_for_tenant.assert_not_called()

    def test_missing_tenant_stops_before_connection_and_fiscal_config(self):
        self.tenant_resolver.resolve_tenant_id.side_effect = None
        self.tenant_resolver.resolve_tenant_id.return_value = None

        with self.assertRaises(PDFInvoicePreparationTenantNotFoundError):
            self.prepare()

        self.assertNotIn("connection", self.events)
        self.assertNotIn("fiscal", self.events)
        self.connection_resolver.get_for_tenant.assert_not_called()
        self.fiscal_config_resolver.get_for_tenant.assert_not_called()

    def test_missing_connection_stops_before_fiscal_config(self):
        self.connection_resolver.get_for_tenant.side_effect = None
        self.connection_resolver.get_for_tenant.return_value = None

        with self.assertRaises(PDFInvoicePreparationConnectionNotFoundError):
            self.prepare()

        self.fiscal_config_resolver.get_for_tenant.assert_not_called()

    def test_missing_fiscal_config_stops_before_pdf_result_inspection(self):
        self.fiscal_config_resolver.get_for_tenant.side_effect = None
        self.fiscal_config_resolver.get_for_tenant.return_value = None

        with self.assertRaises(PDFInvoicePreparationFiscalConfigNotFoundError):
            self.prepare()

        self.assertNotIn("pdf-result", self.events)

    def test_invalid_fiscal_config_prevents_preparation_context(self):
        self.fiscal_config_resolver.get_for_tenant.side_effect = None
        self.fiscal_config_resolver.get_for_tenant.return_value = (
            self.fiscal_config(amount_basis="unsupported")
        )

        with self.assertRaises(PDFInvoicePreparationConfigError):
            self.prepare()

    def test_incomplete_parser_result_is_rejected_after_resolvers(self):
        parsed = parse_fieldquo_invoice_text("Invoice number: INV-100")
        incomplete = PDFInvoiceWorkflowResult(
            invoice=parsed.invoice,
            source_text=parsed.source_text,
            fallback_used=False,
            missing_fields=parsed.missing_fields,
            parser_result=parsed,
        )

        with self.assertRaises(PDFInvoiceWorkflowNotReadyError):
            self.prepare(incomplete)

        self.assertEqual(
            self.events,
            ["license", "tenant", "connection", "fiscal"],
        )

    def test_interpreter_fallback_is_not_accepted_as_deterministic_pdf_data(self):
        result = self.complete_workflow_result()
        fallback_result = replace(result, fallback_used=True)

        with self.assertRaises(PDFInvoiceWorkflowNotReadyError):
            self.prepare(fallback_result)

    def test_multi_line_pdf_fails_closed_for_single_line_config_contract(self):
        text = COMPLETE_TEXT + "Support | 1 | 10.00 | 10.00\n"
        result = PDFInvoiceWorkflow().process_text(text)

        with self.assertRaises(PDFInvoiceWorkflowLineItemsError):
            self.prepare(result)

    def test_missing_parsed_description_is_rejected(self):
        result = self.malformed_complete_result(description=None)

        with self.assertRaises(PDFInvoiceWorkflowNotReadyError):
            self.prepare(result)

    def test_missing_parsed_quantity_is_rejected(self):
        result = self.malformed_complete_result(quantity=None)

        with self.assertRaises(PDFInvoiceWorkflowNotReadyError):
            self.prepare(result)

    def test_mismatched_workflow_and_parser_data_is_rejected(self):
        result = self.complete_workflow_result()
        changed_invoice = dict(result.invoice)
        changed_invoice["client"] = "Another client"
        mismatched = replace(result, invoice=changed_invoice)

        with self.assertRaises(PDFInvoiceWorkflowNotReadyError):
            self.prepare(mismatched)

    def malformed_complete_result(self, **line_overrides):
        result = self.complete_workflow_result()
        parser_invoice = dict(result.parser_result.invoice)
        line_item = dict(parser_invoice["line_items"][0])
        line_item.update(line_overrides)
        parser_invoice["line_items"] = [line_item]
        malformed_parser = replace(
            result.parser_result,
            invoice=parser_invoice,
            complete=True,
        )
        return replace(result, invoice=parser_invoice, parser_result=malformed_parser)

    def test_never_constructs_xero_client_or_calls_write_surfaces(self):
        with patch("xero.api.XeroApiClient") as client_constructor, patch(
            "xero.invoice_creation_orchestrator.create_pdf_invoice_in_xero"
        ) as pdf_orchestrator, patch.object(
            XeroApiClient, "create_invoice"
        ) as create_invoice, patch("xero.api.requests.post") as xero_post:
            result = self.prepare()

        self.assertIsInstance(result, PDFInvoiceWorkflowPreparationResult)
        client_constructor.assert_not_called()
        pdf_orchestrator.assert_not_called()
        create_invoice.assert_not_called()
        xero_post.assert_not_called()
        self.assertNotIn("access_token", vars(result.preparation_context))
        self.assertNotIn("refresh_token", vars(result.preparation_context))

    def test_tenant_configuration_cannot_be_resolved_for_another_tenant(self):
        self.fiscal_config_resolver.get_for_tenant.side_effect = None
        self.fiscal_config_resolver.get_for_tenant.return_value = self.fiscal_config(
            tenant_id=self.OTHER_TENANT_ID
        )

        with self.assertRaisesRegex(
            PDFInvoicePreparationInputError,
            "different internal tenant",
        ):
            self.prepare()

        self.assertNotIn("pdf-result", self.events)


if __name__ == "__main__":
    unittest.main()
