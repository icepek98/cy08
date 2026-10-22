import tempfile
import unittest
from dataclasses import FrozenInstanceError
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock, patch
from uuid import UUID

from fieldquo.pdf_invoice_workflow import PDFInvoiceWorkflow
from tenant_fiscal_config import TenantFiscalConfig
from xero.api import XeroApiClient
from xero.invoice_creation_orchestrator import create_pdf_invoice_in_xero
from xero.pdf_invoice_dry_run import (
    PDFInvoiceDryRunEmitter,
    PDFInvoiceDryRunResult,
    PDFInvoiceDryRunService,
    PreparedPDFInvoice,
)
from xero.pdf_invoice_preparation import (
    PDFInvoicePreparationLicenseError,
    PDFInvoicePreparationService,
)
from xero.pdf_invoice_service import PDFInvoiceService
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


class PDFInvoiceDryRunTests(unittest.TestCase):
    TENANT_ID = UUID("66d204ae-87bc-4ea5-8a37-a3d444ec7f34")

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
        self.preparation_service = PDFInvoicePreparationService(
            license_validator=self.license_validator,
            tenant_resolver=self.tenant_resolver,
            connection_resolver=self.connection_resolver,
            fiscal_config_resolver=self.fiscal_config_resolver,
        )
        self.extractor = Mock()
        self.extractor.extract_text.side_effect = (
            lambda _path: (self.events.append("extract") or COMPLETE_TEXT)
        )
        self.workflow = PDFInvoiceWorkflow(extractor=self.extractor)
        self.emitter = PDFInvoiceDryRunEmitter()
        self.service = PDFInvoiceDryRunService(
            workflow=self.workflow,
            preparation_service=self.preparation_service,
            emitter=self.emitter,
        )

    def resolve_connection(self, tenant_id, _license_key):
        self.events.append("connection")
        return XeroConnection(
            tenant_id=tenant_id,
            xero_tenant_id="xero-org-tenant-a",
            xero_tenant_name="Tenant A",
            xero_tenant_type="ORGANISATION",
        )

    def resolve_fiscal_config(self, tenant_id, _license_key):
        self.events.append("fiscal")
        return TenantFiscalConfig(
            tenant_id=tenant_id,
            account_code="200",
            tax_type="OUTPUT",
            tax_inclusive=False,
            amount_basis="subtotal",
            discount_policy="ignore",
        )

    def test_pdf_to_preparation_returns_deterministic_dry_run_without_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            pdf_path = Path(directory) / "invoice.pdf"
            pdf_path.write_bytes(b"%PDF-test")
            with (
                patch("xero.api.requests.post") as http_post,
                patch.object(XeroApiClient, "__init__", return_value=None) as client_init,
                patch.object(XeroApiClient, "create_invoice") as create_invoice,
                patch.object(
                    PDFInvoiceService, "create_pdf_invoice"
                ) as create_pdf_invoice,
                patch(
                    "xero.invoice_creation_orchestrator.create_pdf_invoice_in_xero",
                    wraps=create_pdf_invoice_in_xero,
                ) as orchestrator,
            ):
                result = self.service.run_pdf(
                    pdf_path=pdf_path,
                    license_key="license-a",
                )
                second = self.service.run_pdf(
                    pdf_path=pdf_path,
                    license_key="license-a",
                )

        self.assertIsInstance(result, PDFInvoiceDryRunResult)
        self.assertEqual(result.status, "dry_run")
        self.assertEqual(result.tenant_id, self.TENANT_ID)
        self.assertEqual(result.xero_tenant_id, "xero-org-tenant-a")
        self.assertNotEqual(str(result.tenant_id), result.xero_tenant_id)
        self.assertEqual(result.description, "Consulting")
        self.assertEqual(result.quantity, Decimal("2"))
        self.assertEqual(result.account_code, "200")
        self.assertEqual(result.tax_type, "OUTPUT")
        self.assertIs(result.tax_inclusive, False)
        self.assertEqual(result.amount_basis, "subtotal")
        self.assertEqual(result.discount_policy, "ignore")
        self.assertEqual(result.source_origin, "fieldquo_pdf")
        self.assertEqual(
            self.events,
            [
                "extract",
                "license",
                "tenant",
                "connection",
                "fiscal",
                "extract",
                "license",
                "tenant",
                "connection",
                "fiscal",
            ],
        )
        self.assertEqual(second, result)
        http_post.assert_not_called()
        client_init.assert_not_called()
        create_invoice.assert_not_called()
        create_pdf_invoice.assert_not_called()
        orchestrator.assert_not_called()

    def test_emitter_only_accepts_prepared_invoice_and_does_not_mutate_it(self):
        workflow_result = self.workflow.process_text(COMPLETE_TEXT)
        preparation_result = self.preparation_service.prepare_workflow_result(
            license_key="license-a",
            workflow_result=workflow_result,
        )
        prepared = PreparedPDFInvoice(preparation_result)
        context = prepared.preparation_result.preparation_context
        before = (
            context.tenant_id,
            context.xero_tenant_id,
            context.invoice_creation_config,
        )

        dry_run = self.emitter.emit(prepared)

        self.assertEqual(
            (
                context.tenant_id,
                context.xero_tenant_id,
                context.invoice_creation_config,
            ),
            before,
        )
        with self.assertRaises(FrozenInstanceError):
            dry_run.status = "created"
        with self.assertRaises(TypeError):
            self.emitter.emit(preparation_result)

    def test_invalid_license_stops_before_resolvers_and_emitter(self):
        self.license_validator.validate_license.side_effect = None
        self.license_validator.validate_license.return_value = (False, "invalid")
        emitter = Mock(spec=PDFInvoiceDryRunEmitter)
        service = PDFInvoiceDryRunService(
            workflow=self.workflow,
            preparation_service=self.preparation_service,
            emitter=emitter,
        )
        with tempfile.TemporaryDirectory() as directory:
            pdf_path = Path(directory) / "invoice.pdf"
            pdf_path.write_bytes(b"%PDF-test")

            with self.assertRaises(PDFInvoicePreparationLicenseError):
                service.run_pdf(pdf_path=pdf_path, license_key="invalid")

        self.tenant_resolver.resolve_tenant_id.assert_not_called()
        self.connection_resolver.get_for_tenant.assert_not_called()
        self.fiscal_config_resolver.get_for_tenant.assert_not_called()
        emitter.emit.assert_not_called()

    def test_incomplete_workflow_does_not_prepare_or_emit(self):
        workflow = Mock()
        workflow.process_pdf.side_effect = RuntimeError("incomplete PDF")
        preparation = Mock()
        emitter = Mock(spec=PDFInvoiceDryRunEmitter)
        service = PDFInvoiceDryRunService(
            workflow=workflow,
            preparation_service=preparation,
            emitter=emitter,
        )

        with self.assertRaisesRegex(RuntimeError, "incomplete PDF"):
            service.run_pdf(pdf_path="invoice.pdf", license_key="license-a")

        preparation.prepare_workflow_result.assert_not_called()
        emitter.emit.assert_not_called()
