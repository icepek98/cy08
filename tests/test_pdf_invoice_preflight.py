import json
import tempfile
import unittest
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

import requests

from fieldquo.pdf_invoice_workflow import PDFInvoiceWorkflow
from tenant_fiscal_config import TenantFiscalConfig
from xero.api import XeroApiClient
from xero.fieldquo_invoice_idempotency import FieldQuoInvoiceIdempotencyStore
from xero.invoice_creation_config import (
    InvoiceCreationConfig,
    InvoiceCreationConfigError,
)
from xero.oauth import XeroOAuthClient
from xero.pdf_invoice_preflight import (
    ContactResolutionStatus,
    LicenseEvidence,
    PDFInvoicePreflightResult,
    PreflightStatus,
    XeroAuthorizationEvidence,
    XeroAuthorizationStatus,
    invoice_payload_fingerprint,
    prepare_for_real_emission,
)
from xero.pdf_invoice_preparation import (
    PDFInvoicePreparationContext,
    PDFInvoiceWorkflowPreparationResult,
)
from xero.pdf_invoice_service import PDFInvoiceService


COMPLETE_TEXT = """\
Invoice number: INV-100
Client: Example Client
Issued: 2025-01-02
Currency: EUR
Subtotal: 150.00
Total: 150.00
Description | Quantity | Unit Price | Line Total
Consulting | 2 | 75.00 | 150.00
"""


class PDFInvoicePreflightTests(unittest.TestCase):
    TENANT_ID = UUID("66d204ae-87bc-4ea5-8a37-a3d444ec7f34")

    def setUp(self):
        self.now = datetime.now(timezone.utc)
        self.as_of = self.now.date()
        self.preparation = self.make_preparation()
        self.license = LicenseEvidence(
            tenant_id=self.TENANT_ID,
            matching_license_count=1,
            active=True,
            expires_on=self.as_of,
        )
        self.authorization = self.make_authorization()
        self.contacts = [{"ContactID": "contact-1", "Name": "Example Client"}]
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.store = FieldQuoInvoiceIdempotencyStore(
            Path(self.tempdir.name) / "ledger.sqlite3"
        )

    def make_preparation(self, *, workflow=None):
        invoice_workflow = workflow or PDFInvoiceWorkflow().process_text(
            COMPLETE_TEXT
        )
        fiscal = TenantFiscalConfig(
            tenant_id=self.TENANT_ID,
            account_code="200",
            tax_type="OUTPUT",
            tax_inclusive=False,
            amount_basis="subtotal",
            discount_policy="ignore",
        )
        config = InvoiceCreationConfig(
            tenant_id=str(self.TENANT_ID),
            description="Consulting",
            quantity=Decimal("2"),
            account_code=fiscal.account_code,
            tax_type=fiscal.tax_type,
            tax_inclusive=fiscal.tax_inclusive,
            amount_basis=fiscal.amount_basis,
            discount_policy=fiscal.discount_policy,
        )
        context = PDFInvoicePreparationContext(
            tenant_id=self.TENANT_ID,
            xero_tenant_id="xero-tenant-a",
            invoice_creation_config=config,
        )
        return PDFInvoiceWorkflowPreparationResult(
            workflow_result=invoice_workflow,
            preparation_context=context,
        )

    def make_authorization(self, **overrides):
        values = {
            "tenant_id": self.TENANT_ID,
            "xero_tenant_id": "xero-tenant-a",
            "connection_present": True,
            "access_token_present": True,
            "expires_at": self.now + timedelta(hours=1),
            "granted_scopes": frozenset({"accounting.invoices.read"}),
        }
        values.update(overrides)
        return XeroAuthorizationEvidence(**values)

    def prepare(self, **overrides):
        args = {
            "preparation": self.preparation,
            "license_evidence": self.license,
            "authorization": self.authorization,
            "contacts": self.contacts,
            "idempotency_store": None,
            "operation": None,
            "now": self.now,
            "as_of": self.as_of,
        }
        args.update(overrides)
        return prepare_for_real_emission(**args)

    def ready_result_with_claim(self):
        with patch.object(
            XeroAuthorizationEvidence,
            "status_at",
            return_value=XeroAuthorizationStatus.WRITE_AUTHORIZED,
        ):
            without_claim = self.prepare()
            payload = json.loads(without_claim.payload_json)
            identity = without_claim.source_identity
            operation = self.store.reserve(
                str(self.TENANT_ID),
                identity,
                invoice_payload_fingerprint(payload),
            )
            return self.prepare(
                idempotency_store=self.store,
                operation=operation,
            )

    def test_read_only_authorization_is_blocked(self):
        result = self.prepare()
        self.assertEqual(result.status, PreflightStatus.BLOCKED)
        self.assertEqual(
            result.authorization_status, XeroAuthorizationStatus.READ_ONLY
        )

    def test_unverifiable_write_scope_is_blocked(self):
        self.authorization = self.make_authorization(
            granted_scopes=frozenset({"accounting.invoices"})
        )
        result = self.prepare()
        self.assertEqual(result.status, PreflightStatus.BLOCKED)
        self.assertEqual(
            result.authorization_status,
            XeroAuthorizationStatus.WRITE_AUTHORIZATION_UNKNOWN,
        )

    def test_missing_or_expired_token_is_blocked(self):
        for evidence, expected in (
            (self.make_authorization(access_token_present=False),
             XeroAuthorizationStatus.TOKEN_MISSING),
            (self.make_authorization(expires_at=self.now),
             XeroAuthorizationStatus.TOKEN_EXPIRED),
        ):
            with self.subTest(expected=expected):
                result = self.prepare(authorization=evidence)
                self.assertEqual(result.status, PreflightStatus.BLOCKED)
                self.assertEqual(result.authorization_status, expected)

    def test_missing_scope_is_blocked(self):
        evidence = self.make_authorization(granted_scopes=frozenset())
        result = self.prepare(authorization=evidence)
        self.assertEqual(result.status, PreflightStatus.BLOCKED)
        self.assertEqual(
            result.authorization_status, XeroAuthorizationStatus.SCOPES_MISSING
        )

    def test_missing_source_identity_is_blocked(self):
        workflow = self.preparation.workflow_result
        parsed = replace(
            workflow.parser_result,
            evidence={**workflow.parser_result.evidence, "invoice_number": ()},
        )
        changed_workflow = replace(workflow, parser_result=parsed)
        result = self.prepare(
            preparation=self.make_preparation(workflow=changed_workflow)
        )
        self.assertEqual(result.status, PreflightStatus.BLOCKED)
        self.assertIn("BLOCKED_MISSING_SOURCE_IDENTITY", result.reasons)

    def test_contact_not_found_and_ambiguous_are_blocked(self):
        cases = (
            ([], ContactResolutionStatus.CONTACT_NOT_FOUND),
            (
                [
                    {"ContactID": "contact-1", "Name": "Example Client"},
                    {"ContactID": "contact-2", "Name": " Example  Client "},
                ],
                ContactResolutionStatus.CONTACT_AMBIGUOUS,
            ),
        )
        for contacts, expected in cases:
            with self.subTest(expected=expected):
                result = self.prepare(contacts=contacts)
                self.assertEqual(result.status, PreflightStatus.BLOCKED)
                self.assertEqual(result.contact.status, expected)

    def test_contact_source_unavailable_is_blocked(self):
        result = self.prepare(contacts=None)
        self.assertEqual(result.status, PreflightStatus.BLOCKED)
        self.assertEqual(
            result.contact.status,
            ContactResolutionStatus.CONTACT_SOURCE_UNAVAILABLE,
        )

    def test_unique_contact_passes_contact_resolution(self):
        result = self.ready_result_with_claim()
        self.assertEqual(result.contact.status, ContactResolutionStatus.CONTACT_RESOLVED)
        self.assertEqual(result.contact.contact_id, "contact-1")

    def test_missing_idempotency_claim_is_blocked(self):
        result = self.prepare()
        self.assertEqual(result.status, PreflightStatus.BLOCKED)
        self.assertIn("BLOCKED_IDEMPOTENCY_KEY_MISSING", result.reasons)

    def test_invalid_fiscal_configuration_is_rejected_at_contract_boundary(self):
        with self.assertRaises(InvoiceCreationConfigError):
            InvoiceCreationConfig(
                tenant_id=str(self.TENANT_ID),
                description="",
                quantity=Decimal("2"),
                account_code="200",
                tax_type="OUTPUT",
                tax_inclusive=False,
                amount_basis="subtotal",
                discount_policy="ignore",
            )

    def test_incomplete_workflow_and_invalid_line_counts_are_blocked(self):
        for line_items in ([], [
            {"description": "A", "quantity": 1, "unit_price": 1},
            {"description": "B", "quantity": 1, "unit_price": 1},
        ]):
            with self.subTest(line_count=len(line_items)):
                workflow = self.preparation.workflow_result
                invoice = {**workflow.invoice, "line_items": line_items}
                parsed = replace(workflow.parser_result, invoice=invoice)
                changed = replace(
                    workflow,
                    invoice=invoice,
                    parser_result=parsed,
                )
                result = self.prepare(
                    preparation=self.make_preparation(workflow=changed)
                )
                self.assertEqual(result.status, PreflightStatus.BLOCKED)
                self.assertIn("BLOCKED_LINE_ITEM_COUNT_INVALID", result.reasons)

    def test_ready_to_emit_does_not_create_invoice(self):
        with (
            patch.object(
                XeroApiClient,
                "__init__",
                side_effect=AssertionError("Xero client must not be created"),
            ) as client_init,
            patch.object(
                XeroApiClient,
                "create_invoice",
                side_effect=AssertionError("create_invoice must not be called"),
            ) as create_invoice,
            patch(
                "xero.invoice_creation_orchestrator.create_pdf_invoice_in_xero",
                side_effect=AssertionError(
                    "create_pdf_invoice_in_xero must not be called"
                ),
            ) as emit_pdf,
            patch.object(
                PDFInvoiceService,
                "create_pdf_invoice",
                side_effect=AssertionError(
                    "PDFInvoiceService must not create an invoice"
                ),
            ) as create_pdf_invoice,
            patch.object(
                requests,
                "post",
                side_effect=AssertionError("HTTP POST must not be called"),
            ) as http_post,
            patch.object(
                requests,
                "get",
                side_effect=AssertionError("HTTP GET must not be called"),
            ) as http_get,
            patch.object(
                XeroOAuthClient,
                "exchange_code",
                side_effect=AssertionError("OAuth exchange must not be called"),
            ) as oauth_exchange,
            patch.object(
                XeroOAuthClient,
                "refresh_access_token",
                side_effect=AssertionError("OAuth refresh must not be called"),
            ) as oauth_refresh,
        ):
            result = self.ready_result_with_claim()

        self.assertEqual(result.status, PreflightStatus.READY_TO_EMIT)
        self.assertIsInstance(result, PDFInvoicePreflightResult)
        self.assertIsNotNone(result.idempotency_key)
        self.assertIsNotNone(result.payload_json)
        client_init.assert_not_called()
        create_invoice.assert_not_called()
        emit_pdf.assert_not_called()
        create_pdf_invoice.assert_not_called()
        http_post.assert_not_called()
        http_get.assert_not_called()
        oauth_exchange.assert_not_called()
        oauth_refresh.assert_not_called()


if __name__ == "__main__":
    unittest.main()
