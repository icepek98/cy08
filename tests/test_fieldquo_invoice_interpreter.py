import json
import unittest
from datetime import date
from decimal import Decimal

from fieldquo.invoice_interpreter import (
    InvoiceInterpretationError,
    InvoiceInterpretationRequest,
    interpret_invoice,
    merge_invoice_interpretations,
    validate_invoice_interpretation,
)


class FakeInvoiceInterpreter:
    def __init__(self, response):
        self.response = response
        self.requests = []

    def interpret(self, request):
        self.requests.append(request)
        return self.response


class InvoiceInterpreterContractTests(unittest.TestCase):
    SOURCE_TEXT = """\
Invoice number: INV-100
Client: Example Client
Issued: 2025-01-02
Currency: EUR
Total: 175.00
Description | Quantity | Unit Price | Line Total
Consulting | 2 | 75.00 | 150.00
"""

    def valid_response(self):
        return {
            "schema_version": "1",
            "invoice": {
                "invoice_number": "INV-100",
                "client": "Example Client",
                "issued": "2025-01-02",
                "due": None,
                "currency": "EUR",
                "subtotal": None,
                "tax": None,
                "discount": None,
                "total": Decimal("175.00"),
                "line_items": [{
                    "description": "Consulting",
                    "quantity": Decimal("2"),
                    "unit_price": Decimal("75.00"),
                    "line_total": Decimal("150.00"),
                }],
            },
            "evidence": {
                "invoice_number": "INV-100",
                "client": "Example Client",
                "issued": "2025-01-02",
                "currency": "EUR",
                "total": "175.00",
                "line_items[0].description": "Consulting",
                "line_items[0].quantity": "2",
                "line_items[0].unit_price": "75.00",
                "line_items[0].line_total": "150.00",
            },
        }

    def test_valid_response_is_converted_to_safe_types(self):
        result = validate_invoice_interpretation(self.valid_response(), self.SOURCE_TEXT)

        self.assertEqual(result.invoice["issued"], date(2025, 1, 2))
        self.assertIsInstance(result.invoice["total"], Decimal)
        self.assertIsInstance(result.invoice["line_items"][0]["quantity"], Decimal)

    def test_invalid_json_is_rejected(self):
        with self.assertRaisesRegex(InvoiceInterpretationError, "JSON válido"):
            validate_invoice_interpretation("{not-json", self.SOURCE_TEXT)

    def test_json_with_non_object_root_is_rejected(self):
        with self.assertRaisesRegex(InvoiceInterpretationError, "deve ser um objeto"):
            validate_invoice_interpretation("null", self.SOURCE_TEXT)

    def test_invalid_schema_version_is_rejected(self):
        response = self.valid_response()
        response["schema_version"] = "2"

        with self.assertRaisesRegex(InvoiceInterpretationError, "schema_version"):
            validate_invoice_interpretation(response, self.SOURCE_TEXT)

    def test_unknown_invoice_field_is_rejected(self):
        response = self.valid_response()
        response["invoice"]["account_code"] = "200"

        with self.assertRaisesRegex(InvoiceInterpretationError, "desconhecidos"):
            validate_invoice_interpretation(response, self.SOURCE_TEXT)

    def test_unknown_root_field_is_rejected(self):
        response = self.valid_response()
        response["source_identity"] = "tenant/invoice"

        with self.assertRaises(InvoiceInterpretationError):
            validate_invoice_interpretation(response, self.SOURCE_TEXT)

    def test_invalid_type_is_rejected(self):
        response = self.valid_response()
        response["invoice"]["client"] = 27

        with self.assertRaisesRegex(InvoiceInterpretationError, "client"):
            validate_invoice_interpretation(response, self.SOURCE_TEXT)

    def test_ambiguous_or_invalid_date_is_rejected(self):
        response = self.valid_response()
        response["invoice"]["issued"] = "02/01/2025"
        response["evidence"]["issued"] = "2025-01-02"

        with self.assertRaisesRegex(InvoiceInterpretationError, "data inválida"):
            validate_invoice_interpretation(response, self.SOURCE_TEXT)

    def test_value_without_evidence_is_rejected(self):
        response = self.valid_response()
        del response["evidence"]["total"]

        with self.assertRaisesRegex(InvoiceInterpretationError, "evidence"):
            validate_invoice_interpretation(response, self.SOURCE_TEXT)

    def test_evidence_not_present_in_source_is_rejected(self):
        response = self.valid_response()
        response["evidence"]["client"] = "Different Client"

        with self.assertRaisesRegex(InvoiceInterpretationError, "literalmente"):
            validate_invoice_interpretation(response, self.SOURCE_TEXT)

    def test_evidence_must_support_the_returned_value(self):
        response = self.valid_response()
        response["invoice"]["total"] = Decimal("999.00")

        with self.assertRaisesRegex(InvoiceInterpretationError, "valor decimal"):
            validate_invoice_interpretation(response, self.SOURCE_TEXT)

    def test_deterministic_value_is_preserved_when_ai_returns_null(self):
        deterministic = self.valid_response()["invoice"]
        deterministic["issued"] = date(2025, 1, 2)
        deterministic["client"] = "Deterministic Client"
        ai = self.valid_response()
        ai["invoice"]["client"] = None
        del ai["evidence"]["client"]

        merged = merge_invoice_interpretations(
            deterministic, validate_invoice_interpretation(ai, self.SOURCE_TEXT)
        )

        self.assertEqual(merged.invoice["client"], "Deterministic Client")
        self.assertFalse(merged.has_conflicts)

    def test_equal_ai_value_does_not_create_conflict(self):
        deterministic = self.valid_response()["invoice"]
        deterministic["issued"] = date(2025, 1, 2)
        ai = validate_invoice_interpretation(self.valid_response(), self.SOURCE_TEXT)

        merged = merge_invoice_interpretations(deterministic, ai)

        self.assertFalse(merged.has_conflicts)
        self.assertEqual(merged.invoice["invoice_number"], "INV-100")

    def test_conflicting_ai_value_is_reported_without_overwrite(self):
        deterministic = self.valid_response()["invoice"]
        deterministic["invoice_number"] = "INV-100"
        deterministic["issued"] = date(2025, 1, 2)
        ai = self.valid_response()
        ai["invoice"]["invoice_number"] = "100"
        ai["evidence"]["invoice_number"] = "Invoice number: INV-100"
        conflicting = validate_invoice_interpretation(ai, self.SOURCE_TEXT)

        merged = merge_invoice_interpretations(deterministic, conflicting)

        self.assertEqual(merged.invoice["invoice_number"], "INV-100")
        self.assertIn("invoice_number", merged.conflicts)
        self.assertTrue(merged.has_conflicts)

    def test_ai_fills_a_deterministically_missing_field(self):
        deterministic = {key: None for key in self.valid_response()["invoice"]}
        deterministic["line_items"] = None
        ai = validate_invoice_interpretation(self.valid_response(), self.SOURCE_TEXT)

        merged = merge_invoice_interpretations(deterministic, ai)

        self.assertEqual(merged.invoice["invoice_number"], "INV-100")
        self.assertEqual(merged.invoice["issued"], date(2025, 1, 2))

    def test_ai_null_leaves_field_missing(self):
        response = self.valid_response()
        response["invoice"]["due"] = None
        validated = validate_invoice_interpretation(response, self.SOURCE_TEXT)

        merged = merge_invoice_interpretations(
            {key: None for key in validated.invoice}, validated
        )

        self.assertIsNone(merged.invoice["due"])
        self.assertIn("due", merged.missing_fields)

    def test_multiple_line_items_are_preserved(self):
        source = self.SOURCE_TEXT + "Travel | 1 | 25.00 | 25.00\n"
        response = self.valid_response()
        response["invoice"]["line_items"].append({
            "description": "Travel",
            "quantity": Decimal("1"),
            "unit_price": Decimal("25.00"),
            "line_total": Decimal("25.00"),
        })
        response["evidence"].update({
            "line_items[1].description": "Travel",
            "line_items[1].quantity": "1",
            "line_items[1].unit_price": "25.00",
            "line_items[1].line_total": "25.00",
        })

        result = validate_invoice_interpretation(response, source)

        self.assertEqual(len(result.invoice["line_items"]), 2)
        self.assertEqual(result.invoice["line_items"][1]["description"], "Travel")

    def test_line_item_without_description_remains_null(self):
        response = self.valid_response()
        response["invoice"]["line_items"][0]["description"] = None
        del response["evidence"]["line_items[0].description"]
        validated = validate_invoice_interpretation(response, self.SOURCE_TEXT)

        merged = merge_invoice_interpretations(
            {key: None for key in validated.invoice}, validated
        )

        self.assertIsNone(merged.invoice["line_items"][0]["description"])
        self.assertIn("line_items[0].description", merged.missing_fields)

    def test_line_item_without_quantity_remains_null(self):
        response = self.valid_response()
        response["invoice"]["line_items"][0]["quantity"] = None
        del response["evidence"]["line_items[0].quantity"]
        validated = validate_invoice_interpretation(response, self.SOURCE_TEXT)

        self.assertIsNone(validated.invoice["line_items"][0]["quantity"])

    def test_line_item_without_unit_price_remains_null(self):
        response = self.valid_response()
        response["invoice"]["line_items"][0]["unit_price"] = None
        del response["evidence"]["line_items[0].unit_price"]
        validated = validate_invoice_interpretation(response, self.SOURCE_TEXT)

        self.assertIsNone(validated.invoice["line_items"][0]["unit_price"])

    def test_float_monetary_value_is_rejected(self):
        response = self.valid_response()
        response["invoice"]["total"] = 175.0

        with self.assertRaisesRegex(InvoiceInterpretationError, "float"):
            validate_invoice_interpretation(response, self.SOURCE_TEXT)

    def test_json_monetary_number_is_parsed_as_decimal(self):
        payload = json.dumps(self.valid_response(), default=str)
        # JSON numbers are parsed with Decimal, never through binary float.
        payload = payload.replace('"175.00"', "175.00", 1)

        result = validate_invoice_interpretation(payload, self.SOURCE_TEXT)

        self.assertIsInstance(result.invoice["total"], Decimal)

    def test_fake_provider_is_injected_and_receives_fallback_context(self):
        fake = FakeInvoiceInterpreter(self.valid_response())
        deterministic = {"invoice_number": "INV-100"}

        result = interpret_invoice(
            fake,
            self.SOURCE_TEXT,
            deterministic,
            ["client", "issued"],
        )

        self.assertEqual(result.invoice["invoice_number"], "INV-100")
        self.assertEqual(len(fake.requests), 1)
        request = fake.requests[0]
        self.assertIsInstance(request, InvoiceInterpretationRequest)
        self.assertEqual(request.source_text, self.SOURCE_TEXT)
        self.assertIs(request.deterministic_invoice, deterministic)
        self.assertEqual(request.missing_fields, ("client", "issued"))


if __name__ == "__main__":
    unittest.main()
