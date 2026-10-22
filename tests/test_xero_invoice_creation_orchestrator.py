import ast
import copy
import json
import sqlite3
import tempfile
import threading
import unittest
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock, patch

import requests

import xero.invoice_creation_orchestrator as invoice_orchestrator
from xero.contact_resolver import XeroContactResolutionError
from xero.api import XeroApiClient, XeroApiError
from xero.fieldquo_invoice_idempotency import (
    FieldQuoInvoiceIdempotencyStore,
    OperationState,
)
from xero.fieldquo_invoice_adapter import FieldQuoInvoiceAdapterError
from xero.invoice_creation_config import (
    InvoiceCreationConfig,
    InvoiceCreationConfigError,
)
from xero.invoice_creation_orchestrator import (
    InvoiceCreationClientError,
    InvoiceCreationDeferredResult,
    InvoiceCreationGuardError,
    create_fieldquo_invoice_in_xero,
    create_pdf_invoice_in_xero,
)
from fieldquo.pdf_invoice_workflow import PDFInvoiceWorkflow


class XeroInvoiceCreationOrchestratorTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.ledger = FieldQuoInvoiceIdempotencyStore(
            Path(self.temp_dir.name) / "ledger.sqlite3",
            lease_seconds=300,
        )
        self.source_identity = "local-source-identity-1"
        self.invoice = {
            "invoice_number": "FQ-100",
            "issued": date(2026, 9, 25),
            "due": date(2026, 10, 25),
            "client": "Example Client",
            "currency": "EUR",
            "subtotal": Decimal("120.00"),
            "discount": Decimal("5.00"),
            "tax": Decimal("22.80"),
            "tax_applied": "true",
            "total": Decimal("137.80"),
        }
        self.contacts = [
            {
                "ContactID": "resolved-contact-id",
                "Name": "Example Client",
            }
        ]
        self.config = InvoiceCreationConfig(
            tenant_id="tenant-1",
            description="Configured description",
            quantity=Decimal("2"),
            account_code="200",
            tax_type="OUTPUT",
            tax_inclusive=False,
            amount_basis="subtotal",
            discount_policy="ignore",
        )
        self.client = Mock()
        self.client.create_invoice.return_value = {
            "Invoices": [{"InvoiceID": "xero-invoice-1"}]
        }

    def create_invoice(
        self,
        invoice=None,
        contacts=None,
        config=None,
        client=None,
        source_identity=None,
        pdf_originated=False,
    ):
        return create_fieldquo_invoice_in_xero(
            self.invoice if invoice is None else invoice,
            self.contacts if contacts is None else contacts,
            self.config if config is None else config,
            self.client if client is None else client,
            source_identity=(
                self.source_identity if source_identity is None else source_identity
            ),
            pdf_originated=pdf_originated,
            ledger=self.ledger,
        )

    def test_payload_and_resolved_contact_id_reach_client(self):
        result = self.create_invoice()

        self.assertEqual(
            result, {"Invoices": [{"InvoiceID": "xero-invoice-1"}]}
        )
        self.client.create_invoice.assert_called_once()
        payload = self.client.create_invoice.call_args.args[0]
        invoice = payload["Invoices"][0]
        self.assertEqual(invoice["Contact"], {"ContactID": "resolved-contact-id"})
        self.assertEqual(invoice["InvoiceNumber"], "FQ-100")
        self.assertEqual(invoice["LineItems"][0]["AccountCode"], "200")
        self.assertEqual(invoice["LineItems"][0]["TaxType"], "OUTPUT")
        self.assertEqual(
            invoice["LineItems"][0]["Description"], "Configured description"
        )
        self.assertEqual(invoice["LineItems"][0]["UnitAmount"], Decimal("60.00"))

    def test_pdf_workflow_result_uses_pdf_originated_orchestration_path(self):
        pdf_text = """\
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
        workflow_result = PDFInvoiceWorkflow().process_text(pdf_text)

        with patch(
            "xero.invoice_creation_orchestrator.adapt_fieldquo_invoice",
            wraps=invoice_orchestrator.adapt_fieldquo_invoice,
        ) as adapt_invoice, patch(
            "xero.invoice_creation_orchestrator._validate_pdf_invoice_guards",
            wraps=invoice_orchestrator._validate_pdf_invoice_guards,
        ) as pdf_guards:
            create_pdf_invoice_in_xero(
                workflow_result,
                self.contacts,
                self.config,
                self.client,
                source_identity="pdf-workflow-origin",
                ledger=self.ledger,
            )

        self.assertEqual(
            adapt_invoice.call_args.kwargs["source_origin"],
            "fieldquo_pdf",
        )
        pdf_guards.assert_called_once()
        self.client.create_invoice.assert_called_once()

    def test_pdf_entrypoint_blocks_missing_provenance_before_client(self):
        with self.assertRaisesRegex(InvoiceCreationGuardError, "proveniência"):
            create_pdf_invoice_in_xero(
                self.invoice,
                self.contacts,
                self.config,
                self.client,
                source_identity="missing-pdf-provenance",
                ledger=self.ledger,
            )

        self.client.create_invoice.assert_not_called()

    def test_legacy_entrypoint_remains_non_pdf(self):
        with patch(
            "xero.invoice_creation_orchestrator.adapt_fieldquo_invoice",
            wraps=invoice_orchestrator.adapt_fieldquo_invoice,
        ) as adapt_invoice:
            self.create_invoice()

        self.assertIsNone(adapt_invoice.call_args.kwargs["source_origin"])
        self.client.create_invoice.assert_called_once()

    def test_multiple_fieldquo_lines_reach_injected_client(self):
        invoice = dict(self.invoice)
        invoice["line_items"] = [
            {
                "description": "Site visit",
                "quantity": Decimal("2"),
                "unit_price": Decimal("75.00"),
                "line_total": Decimal("150.00"),
            },
            {
                "description": "Materials",
                "quantity": Decimal("3"),
                "unit_price": Decimal("10.00"),
                "line_total": Decimal("30.00"),
            },
        ]

        self.create_invoice(invoice=invoice)

        payload = self.client.create_invoice.call_args.args[0]
        self.assertEqual(
            payload["Invoices"][0]["LineItems"],
            [
                {
                    "Description": "Site visit",
                    "Quantity": Decimal("2"),
                    "UnitAmount": Decimal("75.00"),
                    "LineAmount": Decimal("150.00"),
                    "AccountCode": "200",
                    "TaxType": "OUTPUT",
                },
                {
                    "Description": "Materials",
                    "Quantity": Decimal("3"),
                    "UnitAmount": Decimal("10.00"),
                    "LineAmount": Decimal("30.00"),
                    "AccountCode": "200",
                    "TaxType": "OUTPUT",
                },
            ],
        )

    def test_pdf_originated_invoice_with_explicit_lines_is_allowed_when_tax_and_discount_are_zero(self):
        invoice = dict(self.invoice)
        invoice.update(
            line_items=[
                {
                    "description": "Service",
                    "quantity": Decimal("1"),
                    "unit_price": Decimal("120.00"),
                    "line_total": Decimal("120.00"),
                }
            ],
            tax=Decimal("0"),
            discount=Decimal("0"),
        )

        self.create_invoice(invoice=invoice, pdf_originated=True)

        self.client.create_invoice.assert_called_once()

    def test_pdf_originated_invoice_without_tax_is_allowed_without_inventing_tax(self):
        invoice = dict(self.invoice)
        invoice.pop("tax")
        invoice.update(
            subtotal=Decimal("120.00"),
            discount=Decimal("0"),
            line_items=[
                {
                    "description": "Service",
                    "quantity": Decimal("1"),
                    "unit_price": Decimal("120.00"),
                }
            ],
        )

        self.create_invoice(invoice=invoice, pdf_originated=True)

        self.client.create_invoice.assert_called_once()
        payload_invoice = self.client.create_invoice.call_args.args[0]["Invoices"][0]
        self.assertEqual(payload_invoice["LineItems"][0]["TaxType"], "OUTPUT")
        self.assertNotIn("TaxAmount", payload_invoice)
        self.assertNotIn("Tax", payload_invoice)

    def test_pdf_originated_invoice_without_discount_is_allowed_without_discount_fields(self):
        invoice = dict(self.invoice)
        invoice.pop("discount")
        invoice.update(
            subtotal=Decimal("120.00"),
            tax=Decimal("0"),
            line_items=[
                {
                    "description": "Service",
                    "quantity": Decimal("1"),
                    "unit_price": Decimal("120.00"),
                }
            ],
        )

        self.create_invoice(invoice=invoice, pdf_originated=True)

        self.client.create_invoice.assert_called_once()
        payload_invoice = self.client.create_invoice.call_args.args[0]["Invoices"][0]
        self.assertNotIn("DiscountRate", payload_invoice)
        self.assertNotIn("DiscountAmount", payload_invoice)
        self.assertNotIn("Discount", payload_invoice)

    def test_pdf_originated_invoice_without_line_amount_does_not_invent_it(self):
        invoice = dict(self.invoice)
        invoice.update(
            subtotal=Decimal("120.00"),
            tax=Decimal("0"),
            discount=Decimal("0"),
            line_items=[
                {
                    "description": "Service",
                    "quantity": Decimal("1"),
                    "unit_price": Decimal("120.00"),
                }
            ],
        )

        self.create_invoice(invoice=invoice, pdf_originated=True)

        self.client.create_invoice.assert_called_once()
        payload_line = self.client.create_invoice.call_args.args[0]["Invoices"][0][
            "LineItems"
        ][0]
        self.assertNotIn("LineAmount", payload_line)

    def test_pdf_originated_invoice_with_nonfinite_subtotal_is_blocked_before_effects(self):
        for index, subtotal in enumerate((Decimal("NaN"), Decimal("Infinity"))):
            with self.subTest(subtotal=subtotal):
                invoice = dict(self.invoice)
                invoice.update(
                    subtotal=subtotal,
                    tax=Decimal("0"),
                    discount=Decimal("0"),
                    line_items=[
                        {
                            "description": "Service",
                            "quantity": Decimal("1"),
                            "unit_price": Decimal("120.00"),
                        }
                    ],
                )
                with patch(
                    "xero.invoice_creation_orchestrator.resolve_xero_contact_id",
                    wraps=invoice_orchestrator.resolve_xero_contact_id,
                ) as resolve_contact, patch.object(
                    self.ledger, "reserve", wraps=self.ledger.reserve
                ) as reserve:
                    with self.assertRaises(FieldQuoInvoiceAdapterError):
                        self.create_invoice(
                            invoice=invoice,
                            source_identity=f"pdf-nonfinite-subtotal-{index}",
                            pdf_originated=True,
                        )

                resolve_contact.assert_not_called()
                reserve.assert_not_called()

        self.client.create_invoice.assert_not_called()

    def test_pdf_originated_tax_guard_runs_before_contact_ledger_and_client(self):
        invoice = dict(self.invoice)
        invoice.update(
            tax=Decimal("20"),
            discount=Decimal("0"),
            line_items=[
                {
                    "description": "Service",
                    "quantity": Decimal("1"),
                    "unit_price": Decimal("120.00"),
                }
            ],
        )
        with patch(
            "xero.invoice_creation_orchestrator.resolve_xero_contact_id",
            wraps=invoice_orchestrator.resolve_xero_contact_id,
        ) as resolve_contact, patch.object(
            self.ledger, "reserve", wraps=self.ledger.reserve
        ) as reserve:
            with self.assertRaisesRegex(InvoiceCreationGuardError, "Tax"):
                self.create_invoice(
                    invoice=invoice,
                    source_identity="pdf-tax-blocked-before-effects",
                    pdf_originated=True,
                )

        resolve_contact.assert_not_called()
        reserve.assert_not_called()
        self.client.create_invoice.assert_not_called()

    def test_pdf_originated_invoice_with_two_reconciled_lines_preserves_both_lines(self):
        invoice = dict(self.invoice)
        invoice.update(
            subtotal=Decimal("180.00"),
            tax=Decimal("0"),
            discount=Decimal("0"),
            line_items=[
                {
                    "description": "Site visit",
                    "quantity": Decimal("2"),
                    "unit_price": Decimal("75.00"),
                    "line_total": Decimal("150.00"),
                },
                {
                    "description": "Materials",
                    "quantity": Decimal("3"),
                    "unit_price": Decimal("10.00"),
                    "line_total": Decimal("30.00"),
                },
            ],
        )

        self.create_invoice(invoice=invoice, pdf_originated=True)

        payload_lines = self.client.create_invoice.call_args.args[0]["Invoices"][0][
            "LineItems"
        ]
        self.assertEqual(len(payload_lines), 2)
        self.assertEqual(
            sum(
                line["Quantity"] * line["UnitAmount"] for line in payload_lines
            ),
            Decimal("180.00"),
        )
        self.assertEqual(
            [line["Description"] for line in payload_lines],
            ["Site visit", "Materials"],
        )

    def test_pdf_originated_invoice_with_subtotal_mismatch_is_blocked_before_client(self):
        invoice = dict(self.invoice)
        invoice.update(
            subtotal=Decimal("121.00"),
            tax=Decimal("0"),
            discount=Decimal("0"),
            line_items=[
                {
                    "description": "Service",
                    "quantity": Decimal("1"),
                    "unit_price": Decimal("120.00"),
                    "line_total": Decimal("120.00"),
                }
            ],
        )

        with self.assertRaisesRegex(InvoiceCreationGuardError, "Subtotal"):
            self.create_invoice(
                invoice=invoice,
                source_identity="pdf-subtotal-mismatch",
                pdf_originated=True,
            )

        self.client.create_invoice.assert_not_called()

    def test_pdf_originated_invoice_requires_subtotal_for_safe_reconciliation(self):
        invoice = dict(self.invoice)
        invoice.pop("subtotal")
        invoice.update(
            tax=Decimal("0"),
            discount=Decimal("0"),
            line_items=[
                {
                    "description": "Service",
                    "quantity": Decimal("1"),
                    "unit_price": Decimal("120.00"),
                    "line_total": Decimal("120.00"),
                }
            ],
        )

        with self.assertRaisesRegex(InvoiceCreationGuardError, "Subtotal"):
            self.create_invoice(
                invoice=invoice,
                source_identity="pdf-missing-subtotal",
                pdf_originated=True,
            )

        self.client.create_invoice.assert_not_called()

    def test_pdf_originated_invoice_rejects_line_items_without_required_fields(self):
        valid_line = {
            "description": "Service",
            "quantity": Decimal("1"),
            "unit_price": Decimal("120.00"),
            "line_total": Decimal("120.00"),
        }
        invalid_lines = (
            {"quantity": Decimal("1"), "unit_price": Decimal("120.00")},
            {"description": "Service", "unit_price": Decimal("120.00")},
            {"description": "Service", "quantity": Decimal("1")},
            {**valid_line, "quantity": 1},
            {**valid_line, "unit_price": Decimal("NaN")},
            {**valid_line, "line_total": Decimal("119.00")},
        )
        for index, line_item in enumerate(invalid_lines):
            with self.subTest(line_item=line_item):
                invoice = dict(self.invoice)
                invoice.update(
                    tax=Decimal("0"),
                    discount=Decimal("0"),
                    line_items=[line_item],
                )
                with self.assertRaises(InvoiceCreationGuardError):
                    self.create_invoice(
                        invoice=invoice,
                        source_identity=f"pdf-invalid-line-{index}",
                        pdf_originated=True,
                    )

        self.client.create_invoice.assert_not_called()

    def test_pdf_originated_invoice_requires_nonempty_account_code_and_tax_type(self):
        for field_name in ("account_code", "tax_type"):
            values = {
                "tenant_id": "tenant-1",
                "description": "Configured description",
                "quantity": Decimal("2"),
                "account_code": "200",
                "tax_type": "OUTPUT",
                "tax_inclusive": False,
                "amount_basis": "subtotal",
                "discount_policy": "ignore",
            }
            values[field_name] = " "
            with self.subTest(field=field_name):
                with self.assertRaises(InvoiceCreationConfigError):
                    InvoiceCreationConfig(**values)

        self.client.create_invoice.assert_not_called()

    def test_pdf_originated_invoice_without_explicit_lines_is_blocked_before_client(self):
        invalid_line_items = (
            (None, {"line_items": None}),
            ([], {"line_items": []}),
            ("missing", {}),
        )
        for source_identity, (label, changes) in enumerate(
            invalid_line_items, start=1
        ):
            with self.subTest(line_items=label):
                invoice = dict(self.invoice)
                invoice.pop("line_items", None)
                invoice.update(changes)

                with self.assertRaisesRegex(
                    InvoiceCreationGuardError, "linha explícita"
                ):
                    self.create_invoice(
                        invoice=invoice,
                        source_identity=f"pdf-no-lines-{source_identity}",
                        pdf_originated=True,
                    )

        self.client.create_invoice.assert_not_called()

    def test_pdf_originated_invoice_with_nonzero_tax_or_discount_is_blocked(self):
        for field_name, value in (
            ("tax", Decimal("22.80")),
            ("discount", Decimal("5.00")),
        ):
            with self.subTest(field=field_name):
                invoice = dict(self.invoice)
                invoice.update(
                    line_items=[
                        {
                            "description": "Service",
                            "quantity": Decimal("1"),
                            "unit_price": Decimal("120.00"),
                            "line_total": Decimal("120.00"),
                        }
                    ],
                    tax=Decimal("0"),
                    discount=Decimal("0"),
                )
                invoice[field_name] = value

                with self.assertRaisesRegex(
                    InvoiceCreationGuardError, field_name.title()
                ):
                    self.create_invoice(
                        invoice=invoice,
                        source_identity=f"pdf-nonzero-{field_name}",
                        pdf_originated=True,
                    )

        self.client.create_invoice.assert_not_called()

    def test_builder_payload_is_forwarded_directly_and_result_is_returned(self):
        payload = {"Invoices": [{"Contact": {"ContactID": "resolved-contact-id"}}]}
        response = {"Invoices": [{"InvoiceID": "xero-invoice-2"}]}
        self.client.create_invoice.return_value = response
        with patch(
            "xero.invoice_creation_orchestrator.build_xero_invoice_payload",
            return_value=payload,
        ) as build_payload:
            result = self.create_invoice()

        build_payload.assert_called_once()
        self.assertIs(self.client.create_invoice.call_args.args[0], payload)
        self.assertIs(result, response)
        self.assertEqual(
            self.client.create_invoice.call_args.kwargs["idempotency_key"],
            self.ledger.get("tenant-1", self.source_identity).idempotency_key,
        )

    def test_unresolved_contact_prevents_client_call(self):
        with patch(
            "xero.invoice_creation_orchestrator.build_xero_invoice_payload"
        ) as build_payload:
            with self.assertRaises(XeroContactResolutionError):
                self.create_invoice(contacts=[])

        build_payload.assert_not_called()
        self.client.create_invoice.assert_not_called()

    def test_ambiguous_contact_prevents_builder_and_client_call(self):
        ambiguous_contacts = self.contacts + [
            {
                "ContactID": "another-contact-id",
                "Name": "Example Client",
            }
        ]
        with patch(
            "xero.invoice_creation_orchestrator.build_xero_invoice_payload"
        ) as build_payload:
            with self.assertRaisesRegex(
                XeroContactResolutionError, "ambiguous"
            ):
                self.create_invoice(contacts=ambiguous_contacts)

        build_payload.assert_not_called()
        self.client.create_invoice.assert_not_called()

    def test_client_error_is_raised_without_exposing_sensitive_details(self):
        self.client.create_invoice.side_effect = RuntimeError(
            "request failed with bearer token secret-token"
        )

        with self.assertRaises(InvoiceCreationClientError) as raised:
            self.create_invoice()

        self.assertEqual(
            str(raised.exception),
            "Falha ao delegar a invoice para o cliente Xero.",
        )
        self.assertNotIn("secret-token", str(raised.exception))
        self.assertEqual(
            self.ledger.get("tenant-1", self.source_identity).state,
            OperationState.PROCESSING,
        )

    def test_original_invoice_and_contacts_are_not_mutated(self):
        original_invoice = copy.deepcopy(self.invoice)
        original_contacts = copy.deepcopy(self.contacts)

        self.create_invoice()

        self.assertEqual(self.invoice, original_invoice)
        self.assertEqual(self.contacts, original_contacts)

    def test_orchestration_does_not_make_network_requests(self):
        with patch("requests.post") as post:
            self.create_invoice()

        post.assert_not_called()

    def test_success_persists_invoice_id_and_second_call_skips_post(self):
        first_result = self.create_invoice()
        self.assertEqual(first_result["Invoices"][0]["InvoiceID"], "xero-invoice-1")
        stored = self.ledger.get("tenant-1", self.source_identity)
        self.assertEqual(stored.state, OperationState.SUCCESS)
        self.assertEqual(stored.xero_invoice_id, "xero-invoice-1")
        operation_id = stored.operation_id
        idempotency_key = stored.idempotency_key
        self.client.create_invoice.reset_mock()

        result = self.create_invoice()

        self.assertIsInstance(result, InvoiceCreationDeferredResult)
        self.assertEqual(result.status, "already_created")
        self.assertEqual(result.operation.operation_id, operation_id)
        self.assertEqual(result.operation.idempotency_key, idempotency_key)
        self.assertEqual(result.operation.xero_invoice_id, "xero-invoice-1")
        self.client.create_invoice.assert_not_called()

    def test_integrated_success_posts_once_and_reuses_terminal_operation(self):
        invoice = dict(self.invoice)
        invoice["subtotal"] = Decimal("246.913578024691357802")
        invoice["total"] = Decimal("269.713578024691357802")
        client = XeroApiClient("test-access-token", "tenant-1")
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "Invoices": [{"InvoiceID": "xero-invoice-integrated"}]
        }

        with patch("xero.api.requests.post", return_value=response) as post:
            first_result = self.create_invoice(invoice=invoice, client=client)
            stored = self.ledger.get("tenant-1", self.source_identity)
            self.assertEqual(first_result["Invoices"][0]["InvoiceID"], stored.xero_invoice_id)
            self.assertEqual(stored.state, OperationState.SUCCESS)

            body = post.call_args.kwargs["data"]
            idempotency_key = stored.idempotency_key
            sent_payload = json.loads(
                body, parse_float=Decimal, parse_int=Decimal
            )
            self.assertEqual(
                sent_payload,
                {
                    "Invoices": [
                        {
                            "Type": "ACCREC",
                            "Contact": {"ContactID": "resolved-contact-id"},
                            "InvoiceNumber": "FQ-100",
                            "Date": "2026-09-25",
                            "DueDate": "2026-10-25",
                            "CurrencyCode": "EUR",
                            "LineAmountTypes": "Exclusive",
                            "LineItems": [
                                {
                                    "Description": "Configured description",
                                    "Quantity": Decimal("2"),
                                    "UnitAmount": Decimal("123.456789012345678901"),
                                    "AccountCode": "200",
                                    "TaxType": "OUTPUT",
                                }
                            ],
                        }
                    ]
                },
            )
            self.assertIn(
                '"UnitAmount":123.456789012345678901',
                body,
            )
            self.assertNotIn(idempotency_key, body)
            headers = post.call_args.kwargs["headers"]
            self.assertEqual(headers["Content-Type"], "application/json")
            self.assertEqual(headers["Xero-tenant-id"], "tenant-1")
            self.assertEqual(headers["Idempotency-Key"], idempotency_key)

            second_result = self.create_invoice(invoice=invoice, client=client)

        self.assertIsInstance(second_result, InvoiceCreationDeferredResult)
        self.assertEqual(second_result.status, "already_created")
        self.assertEqual(second_result.operation.operation_id, stored.operation_id)
        self.assertEqual(second_result.operation.idempotency_key, idempotency_key)
        self.assertEqual(second_result.operation.xero_invoice_id, stored.xero_invoice_id)
        self.assertEqual(post.call_count, 1)
        self.assertEqual(
            headers["Authorization"],
            "Bearer test-access-token",
        )

    def test_active_claim_from_another_caller_skips_xero(self):
        existing = self.ledger.reserve("tenant-1", self.source_identity)

        result = self.create_invoice()

        self.assertIsInstance(result, InvoiceCreationDeferredResult)
        self.assertEqual(result.status, "processing")
        self.assertEqual(result.operation.operation_id, existing.operation_id)
        self.assertFalse(result.operation.has_claim)
        self.client.create_invoice.assert_not_called()

    def test_outcome_unknown_reuses_existing_idempotency_key(self):
        initial = self.ledger.reserve("tenant-1", self.source_identity)
        self.ledger.mark_outcome_unknown(
            "tenant-1",
            self.source_identity,
            initial.operation_id,
            initial.claim_token,
        )
        database_path = Path(self.temp_dir.name) / "ledger.sqlite3"
        with sqlite3.connect(database_path) as connection:
            connection.execute(
                """
                UPDATE fieldquo_xero_invoice_operations
                SET claim_expires_at = ?
                WHERE tenant_id = ? AND source_identity = ?
                """,
                (
                    "2000-01-01T00:00:00+00:00",
                    "tenant-1",
                    self.source_identity,
                ),
            )
        self.client.create_invoice.return_value = {
            "Invoices": [{"InvoiceID": "recovered-invoice-id"}]
        }

        self.create_invoice()

        self.assertEqual(
            self.client.create_invoice.call_args.kwargs["idempotency_key"],
            initial.idempotency_key,
        )
        stored = self.ledger.get("tenant-1", self.source_identity)
        self.assertEqual(stored.operation_id, initial.operation_id)
        self.assertEqual(stored.idempotency_key, initial.idempotency_key)
        self.assertEqual(stored.state, OperationState.SUCCESS)

    def test_timeout_marks_outcome_unknown_without_retry(self):
        self.client.create_invoice.side_effect = TimeoutError("network timeout")

        with self.assertRaises(InvoiceCreationClientError):
            self.create_invoice()

        self.client.create_invoice.assert_called_once()
        stored = self.ledger.get("tenant-1", self.source_identity)
        self.assertEqual(stored.state, OperationState.OUTCOME_UNKNOWN)
        self.assertEqual(
            self.client.create_invoice.call_args.kwargs["idempotency_key"],
            stored.idempotency_key,
        )

    def test_http_408_and_5xx_mark_outcome_unknown(self):
        class HttpResponse:
            def __init__(self, status_code):
                self.status_code = status_code

        def xero_error_with_status(status_code):
            try:
                raise requests.HTTPError(
                    "HTTP failure", response=HttpResponse(status_code)
                )
            except requests.HTTPError as http_error:
                raise XeroApiError("Xero API returned an HTTP error") from http_error

        for status_code in (408, 500, 503):
            with self.subTest(status_code=status_code):
                self.ledger = FieldQuoInvoiceIdempotencyStore(
                    Path(self.temp_dir.name) / f"ledger-{status_code}.sqlite3"
                )
                self.client.create_invoice.reset_mock()
                self.client.create_invoice.side_effect = (
                    lambda *args, **kwargs: xero_error_with_status(status_code)
                )

                with self.assertRaises(InvoiceCreationClientError):
                    self.create_invoice()

                self.client.create_invoice.assert_called_once()
                stored = self.ledger.get("tenant-1", self.source_identity)
                self.assertEqual(stored.state, OperationState.OUTCOME_UNKNOWN)
                self.assertEqual(
                    stored.idempotency_key,
                    self.client.create_invoice.call_args.kwargs["idempotency_key"],
                )

    def test_deterministic_http_error_remains_definitive(self):
        class HttpResponse:
            status_code = 400

        def xero_error():
            try:
                raise requests.HTTPError("HTTP failure", response=HttpResponse())
            except requests.HTTPError as http_error:
                raise XeroApiError("Xero API returned HTTP 400") from http_error

        self.client.create_invoice.side_effect = xero_error

        with self.assertRaises(InvoiceCreationClientError):
            self.create_invoice()

        self.assertEqual(
            self.ledger.get("tenant-1", self.source_identity).state,
            OperationState.PROCESSING,
        )
        self.client.create_invoice.assert_called_once()

    def test_invalid_success_json_marks_outcome_unknown(self):
        self.client.create_invoice.side_effect = json.JSONDecodeError(
            "Invalid JSON", "{", 0
        )

        with self.assertRaises(InvoiceCreationClientError):
            self.create_invoice()

        self.client.create_invoice.assert_called_once()
        stored = self.ledger.get("tenant-1", self.source_identity)
        self.assertEqual(stored.state, OperationState.OUTCOME_UNKNOWN)
        self.assertTrue(stored.idempotency_key)

    def test_response_without_invoice_id_remains_unconfirmed(self):
        response = {"Invoices": [{"Status": "PENDING"}]}
        self.client.create_invoice.return_value = response

        result = self.create_invoice()

        self.assertIsInstance(result, InvoiceCreationDeferredResult)
        self.assertEqual(result.status, "outcome_unknown")
        self.assertIs(result.response, response)
        self.assertEqual(result.operation.state, OperationState.OUTCOME_UNKNOWN)
        self.assertIsNone(result.operation.xero_invoice_id)

    def test_expired_claim_is_rejected_before_post(self):
        database_path = Path(self.temp_dir.name) / "ledger.sqlite3"
        reserve = self.ledger.reserve

        def reserve_then_expire(*args, **kwargs):
            operation = reserve(*args, **kwargs)
            with sqlite3.connect(database_path) as connection:
                connection.execute(
                    """
                    UPDATE fieldquo_xero_invoice_operations
                    SET claim_expires_at = ?
                    WHERE tenant_id = ? AND source_identity = ?
                    """,
                    (
                        "2000-01-01T00:00:00+00:00",
                        "tenant-1",
                        self.source_identity,
                    ),
                )
            return operation

        with patch.object(self.ledger, "reserve", side_effect=reserve_then_expire):
            result = self.create_invoice()

        self.assertIsInstance(result, InvoiceCreationDeferredResult)
        self.assertEqual(result.status, "processing")
        self.assertEqual(result.operation.operation_id, self.ledger.get(
            "tenant-1", self.source_identity
        ).operation_id)
        self.client.create_invoice.assert_not_called()

    def test_http_error_preserves_existing_wrapped_error_behavior(self):
        class HTTPError(Exception):
            pass

        self.client.create_invoice.side_effect = HTTPError("rejected")

        with self.assertRaises(InvoiceCreationClientError):
            self.create_invoice()

        self.assertEqual(
            self.ledger.get("tenant-1", self.source_identity).state,
            OperationState.PROCESSING,
        )

    def test_payload_and_source_identity_are_forwarded_without_modification(self):
        original_invoice = copy.deepcopy(self.invoice)
        original_contacts = copy.deepcopy(self.contacts)
        payload = {"Invoices": [{"InvoiceNumber": "FQ-100"}]}
        response = {"Invoices": [{"InvoiceID": "xero-invoice-3"}]}
        self.client.create_invoice.return_value = response
        with patch(
            "xero.invoice_creation_orchestrator.build_xero_invoice_payload",
            return_value=payload,
        ):
            self.create_invoice()

        self.assertIs(self.client.create_invoice.call_args.args[0], payload)
        self.assertNotIn(
            "Idempotency-Key", self.client.create_invoice.call_args.args[0]
        )
        stored = self.ledger.get("tenant-1", self.source_identity)
        self.assertEqual(stored.source_identity, self.source_identity)
        self.assertEqual(stored.tenant_id, "tenant-1")
        self.assertIsNotNone(stored.payload_fingerprint)
        self.assertEqual(self.invoice, original_invoice)
        self.assertEqual(self.contacts, original_contacts)

    def test_expired_lease_cannot_be_reclaimed_during_in_flight_post(self):
        self.ledger = FieldQuoInvoiceIdempotencyStore(
            Path(self.temp_dir.name) / "ledger.sqlite3", lease_seconds=1
        )
        post_started = threading.Event()
        allow_post_to_finish = threading.Event()
        second_reserve_attempted = threading.Event()
        second_finished = threading.Event()
        results = []
        errors = []
        current_time = [datetime(2026, 9, 25, tzinfo=timezone.utc)]

        class TestClock:
            @staticmethod
            def now(tz=None):
                return current_time[0]

        def create_invoice_during_post(*args, **kwargs):
            post_started.set()
            if not allow_post_to_finish.wait(timeout=5):
                raise TimeoutError("test did not release the in-flight POST")
            return {"Invoices": [{"InvoiceID": "xero-invoice-locked"}]}

        self.client.create_invoice.side_effect = create_invoice_during_post

        def run_orchestrator():
            try:
                results.append(self.create_invoice())
            except Exception as error:
                errors.append(error)

        original_connect = self.ledger._connect

        def connect_with_short_busy_timeout():
            connection = original_connect()
            if threading.current_thread().name == "second-reserver":
                connection.execute("PRAGMA busy_timeout = 100")
                connection.set_trace_callback(
                    lambda statement: second_reserve_attempted.set()
                    if statement.strip().upper() == "BEGIN IMMEDIATE"
                    else None
                )
            return connection

        with (
            patch("xero.fieldquo_invoice_idempotency.datetime", TestClock),
            patch.object(self.ledger, "_connect", side_effect=connect_with_short_busy_timeout),
        ):
            first = threading.Thread(target=run_orchestrator, name="first-creator")
            first.start()
            self.assertTrue(post_started.wait(timeout=5))
            current_time[0] += timedelta(seconds=2)

            second_errors = []

            def run_second_orchestrator():
                try:
                    self.create_invoice()
                except Exception as error:
                    second_errors.append(error)
                finally:
                    second_finished.set()

            second = threading.Thread(
                target=run_second_orchestrator, name="second-reserver"
            )
            second.start()
            self.assertTrue(second_reserve_attempted.wait(timeout=5))
            self.assertTrue(second_finished.wait(timeout=5))
            self.assertEqual(len(second_errors), 1)
            self.assertIsInstance(second_errors[0], sqlite3.OperationalError)
            self.client.create_invoice.assert_called_once()

            allow_post_to_finish.set()
            first.join(timeout=5)
            second.join(timeout=5)

            self.assertFalse(first.is_alive())
            self.assertFalse(second.is_alive())
            self.assertFalse(errors)

            retry_result = self.create_invoice()

        self.assertIsInstance(retry_result, InvoiceCreationDeferredResult)
        self.assertEqual(retry_result.status, "already_created")
        self.client.create_invoice.assert_called_once()
        self.assertEqual(len(results), 1)
        stored = self.ledger.get("tenant-1", self.source_identity)
        self.assertEqual(stored.state, OperationState.SUCCESS)
        self.assertEqual(stored.xero_invoice_id, "xero-invoice-locked")
        self.assertEqual(
            self.client.create_invoice.call_args.kwargs["idempotency_key"],
            stored.idempotency_key,
        )

    def test_distinct_invoice_waits_for_sqlite_writer_lock_before_post(self):
        self.ledger = FieldQuoInvoiceIdempotencyStore(
            Path(self.temp_dir.name) / "ledger.sqlite3", lease_seconds=300
        )
        post_started = threading.Event()
        allow_first_post_to_finish = threading.Event()
        second_finished = threading.Event()
        second_reserve_attempted = threading.Event()
        second_errors = []
        first_results = []
        client = Mock()

        def create_invoice_during_post(*args, **kwargs):
            if threading.current_thread().name == "invoice-a":
                post_started.set()
                if not allow_first_post_to_finish.wait(timeout=5):
                    raise TimeoutError("test did not release invoice A")
                return {"Invoices": [{"InvoiceID": "xero-invoice-a"}]}
            return {"Invoices": [{"InvoiceID": "xero-invoice-b"}]}

        client.create_invoice.side_effect = create_invoice_during_post
        original_connect = self.ledger._connect

        def connect_with_short_busy_timeout():
            connection = original_connect()
            connection.execute("PRAGMA busy_timeout = 100")
            if threading.current_thread().name == "invoice-b-first-attempt":
                connection.set_trace_callback(
                    lambda statement: second_reserve_attempted.set()
                    if statement.strip().upper() == "BEGIN IMMEDIATE"
                    else None
                )
            return connection

        def run_invoice_a():
            try:
                first_results.append(
                    self.create_invoice(client=client, source_identity="invoice-a")
                )
            except Exception as error:
                first_results.append(error)

        def run_invoice_b():
            try:
                self.create_invoice(client=client, source_identity="invoice-b")
            except Exception as error:
                second_errors.append(error)
            finally:
                second_finished.set()

        with patch.object(
            self.ledger, "_connect", side_effect=connect_with_short_busy_timeout
        ):
            first = threading.Thread(target=run_invoice_a, name="invoice-a")
            first.start()
            self.assertTrue(post_started.wait(timeout=5))

            second = threading.Thread(
                target=run_invoice_b, name="invoice-b-first-attempt"
            )
            second.start()
            self.assertTrue(second_reserve_attempted.wait(timeout=5))
            self.assertTrue(second_finished.wait(timeout=5))
            self.assertEqual(len(second_errors), 1)
            self.assertIsInstance(second_errors[0], sqlite3.OperationalError)
            self.assertEqual(client.create_invoice.call_count, 1)
            self.assertIsNone(self.ledger.get("tenant-1", "invoice-b"))

            allow_first_post_to_finish.set()
            first.join(timeout=5)
            second.join(timeout=5)

            self.assertFalse(first.is_alive())
            self.assertFalse(second.is_alive())

            second_result = self.create_invoice(
                client=client, source_identity="invoice-b"
            )

        self.assertEqual(
            first_results, [{"Invoices": [{"InvoiceID": "xero-invoice-a"}]}]
        )
        self.assertEqual(
            second_result, {"Invoices": [{"InvoiceID": "xero-invoice-b"}]}
        )
        self.assertEqual(client.create_invoice.call_count, 2)
        self.assertEqual(
            self.ledger.get("tenant-1", "invoice-a").state, OperationState.SUCCESS
        )
        self.assertEqual(
            self.ledger.get("tenant-1", "invoice-b").state, OperationState.SUCCESS
        )

    def test_sqlite_busy_error_does_not_authorize_or_post(self):
        blocker = sqlite3.connect(self.ledger._database_path, timeout=0)
        blocker.execute("BEGIN IMMEDIATE")
        self.addCleanup(blocker.close)
        original_connect = self.ledger._connect

        def connect_without_waiting():
            connection = original_connect()
            connection.execute("PRAGMA busy_timeout = 1")
            return connection

        with patch.object(self.ledger, "_connect", side_effect=connect_without_waiting):
            with self.assertRaises(sqlite3.OperationalError):
                self.create_invoice()

        self.client.create_invoice.assert_not_called()
        self.assertIsNone(self.ledger.get("tenant-1", self.source_identity))
        blocker.rollback()

    def _assert_real_xero_client_failure_preserves_operation(
        self, response, expected_cause_type
    ):
        client = XeroApiClient("test-access-token", "tenant-1")
        captured_errors = []
        original_create_invoice = client.create_invoice

        def capture_api_error(*args, **kwargs):
            try:
                return original_create_invoice(*args, **kwargs)
            except XeroApiError as error:
                captured_errors.append(error)
                raise

        client.create_invoice = capture_api_error

        with patch("xero.api.requests.post", return_value=response) as post:
            with self.assertRaises(InvoiceCreationClientError):
                self.create_invoice(client=client)

            stored = self.ledger.get("tenant-1", self.source_identity)
            self.assertEqual(stored.state, OperationState.OUTCOME_UNKNOWN)
            self.assertEqual(len(captured_errors), 1)
            self.assertIsInstance(captured_errors[0].__cause__, expected_cause_type)
            post.assert_called_once()
            self.assertEqual(
                post.call_args.kwargs["headers"]["Idempotency-Key"],
                stored.idempotency_key,
            )
            self.assertNotIn(
                "Idempotency-Key",
                post.call_args.kwargs["data"],
            )

            deferred = self.create_invoice(client=client)

        self.assertIsInstance(deferred, InvoiceCreationDeferredResult)
        self.assertEqual(deferred.status, "processing")
        self.assertEqual(deferred.operation.operation_id, stored.operation_id)
        self.assertEqual(deferred.operation.idempotency_key, stored.idempotency_key)
        self.assertEqual(
            self.ledger.get("tenant-1", self.source_identity).operation_id,
            stored.operation_id,
        )
        self.assertEqual(post.call_count, 1)
        with sqlite3.connect(self.ledger._database_path) as connection:
            operation_count = connection.execute(
                "SELECT COUNT(*) FROM fieldquo_xero_invoice_operations"
            ).fetchone()[0]
        self.assertEqual(operation_count, 1)

    def test_real_xero_client_http_408_is_indeterminate_without_retry(self):
        response = requests.Response()
        response.status_code = 408
        self._assert_real_xero_client_failure_preserves_operation(
            response, requests.HTTPError
        )

    def test_real_xero_client_http_500_is_indeterminate_without_retry(self):
        response = requests.Response()
        response.status_code = 500
        self._assert_real_xero_client_failure_preserves_operation(
            response, requests.HTTPError
        )

    def test_real_xero_client_invalid_json_is_indeterminate_without_retry(self):
        response = Mock()
        response.status_code = 200
        response.raise_for_status.return_value = None
        response.json.side_effect = json.JSONDecodeError("Invalid JSON", "{", 0)
        self._assert_real_xero_client_failure_preserves_operation(
            response, json.JSONDecodeError
        )

    def test_module_has_no_network_client_dependencies(self):
        module_path = (
            Path(__file__).parents[1]
            / "xero"
            / "invoice_creation_orchestrator.py"
        )
        tree = ast.parse(module_path.read_text(encoding="utf-8"))
        network_modules = {
            "aiohttp",
            "http",
            "httpx",
            "requests",
            "socket",
            "ssl",
            "urllib",
            "urllib3",
            "webbrowser",
        }

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported = {alias.name.split(".", 1)[0] for alias in node.names}
                self.assertTrue(imported.isdisjoint(network_modules))
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_root = node.module.split(".", 1)[0]
                self.assertNotIn(imported_root, network_modules)


if __name__ == "__main__":
    unittest.main()
