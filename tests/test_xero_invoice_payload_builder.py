import ast
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from xero.contact_resolver import XeroContactResolution
from xero.fieldquo_invoice_adapter import adapt_fieldquo_invoice
from xero.invoice_creation_config import InvoiceCreationConfig
from xero.invoice_payload_builder import (
    EmptyLineItemsError,
    InvalidInvoiceDraftError,
    MissingContactIDError,
    build_xero_invoice_payload,
)
from xero.line_item_builder import UnsupportedDiscountRepresentationError


def config(**overrides):
    values = {
        "tenant_id": "xero-tenant-1",
        "description": "FieldQuo invoice",
        "quantity": Decimal("2"),
        "account_code": "200",
        "tax_type": "OUTPUT",
        "tax_inclusive": False,
        "amount_basis": "subtotal",
        "discount_policy": "ignore",
    }
    values.update(overrides)
    return InvoiceCreationConfig(**values)


def normalized_invoice(**overrides):
    values = {
        "invoice_number": "FQ-INV-0042",
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
    values.update(overrides)
    return values


def draft(**overrides):
    result = adapt_fieldquo_invoice(normalized_invoice())
    result.update(overrides)
    return result


def matched_contact(contact_id="xero-contact-42"):
    return XeroContactResolution("matched", contact_id, 1)


class XeroInvoicePayloadBuilderTests(unittest.TestCase):
    def test_builds_valid_payload_with_exclusive_line_amounts(self):
        result = build_xero_invoice_payload(draft(), config(), matched_contact())

        self.assertEqual(result["Invoices"][0]["LineAmountTypes"], "Exclusive")
        self.assertEqual(result["Invoices"][0]["Type"], "ACCREC")

    def test_builds_valid_payload_with_inclusive_line_amounts(self):
        result = build_xero_invoice_payload(
            draft(), config(tax_inclusive=True), matched_contact()
        )

        self.assertEqual(result["Invoices"][0]["LineAmountTypes"], "Inclusive")
        self.assertNotIn("tax_inclusive", result["Invoices"][0])

    def test_contact_id_comes_from_contact_resolution(self):
        result = build_xero_invoice_payload(
            draft(), config(), matched_contact("resolved-contact")
        )

        self.assertEqual(
            result["Invoices"][0]["Contact"],
            {"ContactID": "resolved-contact"},
        )

    def test_preserves_invoice_number(self):
        result = build_xero_invoice_payload(
            draft(), config(), matched_contact()
        )

        self.assertEqual(
            result["Invoices"][0]["InvoiceNumber"], "FQ-INV-0042"
        )

    def test_preserves_date_due_date_and_currency(self):
        result = build_xero_invoice_payload(
            draft(), config(), matched_contact()
        )
        invoice = result["Invoices"][0]

        self.assertEqual(invoice["Date"], "2026-09-25")
        self.assertEqual(invoice["DueDate"], "2026-10-25")
        self.assertEqual(invoice["CurrencyCode"], "EUR")

    def test_preserves_account_code_tax_type_description_and_quantity(self):
        result = build_xero_invoice_payload(
            draft(),
            config(
                account_code="REV-01",
                tax_type="OUTPUT2",
                description="Configured line description",
                quantity=Decimal("2.50"),
            ),
            matched_contact(),
        )
        line_item = result["Invoices"][0]["LineItems"][0]

        self.assertEqual(line_item["AccountCode"], "REV-01")
        self.assertEqual(line_item["TaxType"], "OUTPUT2")
        self.assertEqual(line_item["Description"], "Configured line description")
        self.assertEqual(line_item["Quantity"], Decimal("2.50"))

    def test_unit_amount_uses_existing_line_item_builder(self):
        result = build_xero_invoice_payload(
            draft(),
            config(quantity=Decimal("3")),
            matched_contact(),
        )

        self.assertEqual(
            result["Invoices"][0]["LineItems"][0]["UnitAmount"],
            Decimal("40"),
        )
        self.assertIsInstance(
            result["Invoices"][0]["LineItems"][0]["UnitAmount"], Decimal
        )

    def test_builds_all_fieldquo_lines_and_preserves_line_totals(self):
        invoice_draft = adapt_fieldquo_invoice(
            normalized_invoice(
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
                ]
            )
        )

        result = build_xero_invoice_payload(
            invoice_draft,
            config(account_code="REV-01", tax_type="OUTPUT2"),
            matched_contact(),
        )

        self.assertEqual(
            result["Invoices"][0]["LineItems"],
            [
                {
                    "Description": "Site visit",
                    "Quantity": Decimal("2"),
                    "UnitAmount": Decimal("75.00"),
                    "LineAmount": Decimal("150.00"),
                    "AccountCode": "REV-01",
                    "TaxType": "OUTPUT2",
                },
                {
                    "Description": "Materials",
                    "Quantity": Decimal("3"),
                    "UnitAmount": Decimal("10.00"),
                    "LineAmount": Decimal("30.00"),
                    "AccountCode": "REV-01",
                    "TaxType": "OUTPUT2",
                },
            ],
        )

    def test_builds_single_fieldquo_line_and_preserves_invoice_fields(self):
        invoice_draft = adapt_fieldquo_invoice(
            normalized_invoice(
                line_items=[
                    {
                        "description": "  One-off service  ",
                        "quantity": Decimal("1.250"),
                        "unit_price": Decimal("19.8750"),
                        "line_total": Decimal("24.843750"),
                    }
                ]
            )
        )

        payload = build_xero_invoice_payload(
            invoice_draft, config(), matched_contact()
        )
        invoice = payload["Invoices"][0]

        self.assertEqual(invoice["Type"], "ACCREC")
        self.assertEqual(invoice["InvoiceNumber"], "FQ-INV-0042")
        self.assertEqual(invoice["Date"], "2026-09-25")
        self.assertEqual(invoice["DueDate"], "2026-10-25")
        self.assertEqual(invoice["CurrencyCode"], "EUR")
        self.assertEqual(invoice["Contact"], {"ContactID": "xero-contact-42"})
        self.assertEqual(invoice["LineAmountTypes"], "Exclusive")
        self.assertEqual(
            invoice["LineItems"],
            [
                {
                    "Description": "  One-off service  ",
                    "Quantity": Decimal("1.250"),
                    "UnitAmount": Decimal("19.8750"),
                    "LineAmount": Decimal("24.843750"),
                    "AccountCode": "200",
                    "TaxType": "OUTPUT",
                }
            ],
        )

    def test_missing_fieldquo_line_total_is_omitted_from_payload(self):
        invoice_draft = adapt_fieldquo_invoice(
            normalized_invoice(
                line_items=[
                    {
                        "description": "Service",
                        "quantity": Decimal("1"),
                        "unit_price": Decimal("15.00"),
                    }
                ]
            )
        )

        result = build_xero_invoice_payload(
            invoice_draft, config(), matched_contact()
        )

        line_item = result["Invoices"][0]["LineItems"][0]
        self.assertEqual(line_item["Description"], "Service")
        self.assertEqual(line_item["Quantity"], Decimal("1"))
        self.assertEqual(line_item["UnitAmount"], Decimal("15.00"))
        self.assertNotIn("LineAmount", line_item)

    def test_missing_fieldquo_line_quantity_or_price_is_not_filled_from_config(self):
        for missing_field in ("Quantity", "UnitAmount"):
            with self.subTest(missing_field=missing_field):
                invoice_draft = adapt_fieldquo_invoice(
                    normalized_invoice(
                        line_items=[
                            {
                                "description": "Site visit",
                                "quantity": None
                                if missing_field == "Quantity"
                                else Decimal("2"),
                                "unit_price": None
                                if missing_field == "UnitAmount"
                                else Decimal("75.00"),
                                "line_total": Decimal("150.00"),
                            }
                        ]
                    )
                )

                with self.assertRaisesRegex(
                    InvalidInvoiceDraftError, missing_field
                ):
                    build_xero_invoice_payload(
                        invoice_draft, config(), matched_contact()
                    )

    def test_ignore_discount_does_not_send_discount_fields(self):
        result = build_xero_invoice_payload(
            draft(), config(discount_policy="ignore"), matched_contact()
        )
        line_item = result["Invoices"][0]["LineItems"][0]

        self.assertNotIn("DiscountAmount", line_item)
        self.assertNotIn("DiscountRate", line_item)

    def test_line_discount_is_rejected_explicitly(self):
        with self.assertRaises(UnsupportedDiscountRepresentationError):
            build_xero_invoice_payload(
                draft(),
                config(discount_policy="line_discount"),
                matched_contact(),
            )

    def test_unmatched_or_missing_contact_id_is_rejected(self):
        for resolution in (
            XeroContactResolution("not_found", None, 0),
            XeroContactResolution("ambiguous", None, 2),
            XeroContactResolution("matched", None, 1),
            None,
        ):
            with self.subTest(resolution=resolution):
                with self.assertRaises(MissingContactIDError):
                    build_xero_invoice_payload(draft(), config(), resolution)

    def test_missing_required_invoice_draft_fields_are_rejected(self):
        for field_name in ("InvoiceNumber", "Date", "CurrencyCode", "Source"):
            with self.subTest(field=field_name):
                invalid_draft = draft()
                invalid_draft.pop(field_name)
                with self.assertRaises(InvalidInvoiceDraftError):
                    build_xero_invoice_payload(
                        invalid_draft, config(), matched_contact()
                    )

    def test_invalid_due_date_is_rejected(self):
        with self.assertRaisesRegex(InvalidInvoiceDraftError, "DueDate"):
            build_xero_invoice_payload(
                draft(DueDate="not-a-date"), config(), matched_contact()
            )

    def test_empty_line_item_result_is_rejected(self):
        with patch(
            "xero.invoice_payload_builder.build_xero_line_item",
            return_value={},
        ):
            with self.assertRaises(EmptyLineItemsError):
                build_xero_invoice_payload(draft(), config(), matched_contact())

    def test_missing_configured_amount_basis_is_rejected(self):
        invalid_draft = draft()
        invalid_draft["Source"].pop("Subtotal")

        with self.assertRaisesRegex(InvalidInvoiceDraftError, "Subtotal"):
            build_xero_invoice_payload(invalid_draft, config(), matched_contact())

    def test_missing_unselected_amount_basis_is_allowed(self):
        subtotal_draft = draft()
        subtotal_draft["Source"].pop("Total")
        total_draft = draft()
        total_draft["Source"].pop("Subtotal")

        subtotal_payload = build_xero_invoice_payload(
            subtotal_draft, config(amount_basis="subtotal"), matched_contact()
        )
        total_payload = build_xero_invoice_payload(
            total_draft, config(amount_basis="total"), matched_contact()
        )

        self.assertEqual(
            subtotal_payload["Invoices"][0]["LineItems"][0]["UnitAmount"],
            Decimal("60.00"),
        )
        self.assertEqual(
            total_payload["Invoices"][0]["LineItems"][0]["UnitAmount"],
            Decimal("68.90"),
        )

    def test_no_money_values_are_converted_to_float(self):
        result = build_xero_invoice_payload(
            draft(), config(), matched_contact()
        )
        unit_amount = result["Invoices"][0]["LineItems"][0]["UnitAmount"]

        self.assertIsInstance(unit_amount, Decimal)
        self.assertNotIsInstance(unit_amount, float)

    def test_builder_has_no_network_imports_or_calls(self):
        module_path = (
            Path(__file__).parents[1] / "xero" / "invoice_payload_builder.py"
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
