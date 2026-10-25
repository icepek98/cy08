import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

from fieldquo.pdf_invoice_parser import parse_fieldquo_invoice_text
from fieldquo.pdf_text_extractor import (
    PDFTextExtractionError,
    extract_pdf_text,
)


class FieldQuoPDFInvoiceParserTests(unittest.TestCase):
    COMPLETE_INVOICE = """\
Invoice number: INV-100
Client: Example Client
Issued: 2025-01-02
Due: 2025-02-01
Currency: EUR
Subtotal: 150.00
Tax: 30.00
Discount: 5.00
Total: 175.00
Description | Quantity | Unit Price | Line Total
Consulting | 2 | 75.00 | 150.00
"""

    def test_parses_complete_invoice_and_one_line(self):
        result = parse_fieldquo_invoice_text(self.COMPLETE_INVOICE)

        self.assertTrue(result.complete)
        self.assertEqual(result.invoice["invoice_number"], "INV-100")
        self.assertEqual(result.invoice["client"], "Example Client")
        self.assertEqual(result.invoice["issued"], date(2025, 1, 2))
        self.assertEqual(result.invoice["due"], date(2025, 2, 1))
        self.assertEqual(result.invoice["currency"], "EUR")
        self.assertEqual(result.invoice["subtotal"], Decimal("150.00"))
        self.assertEqual(result.invoice["tax"], Decimal("30.00"))
        self.assertEqual(result.invoice["discount"], Decimal("5.00"))
        self.assertEqual(result.invoice["total"], Decimal("175.00"))
        self.assertEqual(
            result.invoice["line_items"],
            [{
                "description": "Consulting",
                "quantity": Decimal("2"),
                "unit_price": Decimal("75.00"),
                "line_total": Decimal("150.00"),
            }],
        )
        self.assertIn("source_text", result.__dataclass_fields__)
        self.assertIn("Consulting | 2 | 75.00 | 150.00", result.evidence["line_items"])

    def test_parses_written_issue_and_payment_due_dates(self):
        text = self.COMPLETE_INVOICE.replace(
            "Issued: 2025-01-02", "Issued: 2 January 2025"
        ).replace(
            "Due: 2025-02-01", "Payment Due: 1 February 2025"
        )

        result = parse_fieldquo_invoice_text(text)

        self.assertEqual(result.invoice["issued"], date(2025, 1, 2))
        self.assertEqual(result.invoice["due"], date(2025, 2, 1))

    def test_parses_currency_from_total_label(self):
        text = self.COMPLETE_INVOICE.replace(
            "Currency: EUR\n", ""
        ).replace(
            "Total: 175.00", "Total (EUR): 175.00"
        )

        result = parse_fieldquo_invoice_text(text)

        self.assertEqual(result.invoice["currency"], "EUR")
        self.assertEqual(result.invoice["total"], Decimal("175.00"))

    def test_parses_aligned_table_without_separators(self):
        text = """\
Invoice number: INV-101
Client: Example Client
Issued: 2025-01-02
Due: 2025-02-01
Currency: EUR
Total: 175.00
Description Qty Unit Price Amount
Consulting 2 75.00 150.00
Travel 1 25.00 25.00
"""

        result = parse_fieldquo_invoice_text(text)

        self.assertEqual(
            result.invoice["line_items"],
            [
                {
                    "description": "Consulting",
                    "quantity": Decimal("2"),
                    "unit_price": Decimal("75.00"),
                    "line_total": Decimal("150.00"),
                },
                {
                    "description": "Travel",
                    "quantity": Decimal("1"),
                    "unit_price": Decimal("25.00"),
                    "line_total": Decimal("25.00"),
                },
            ],
        )

    def test_parses_supported_money_formats_in_aligned_table(self):
        text = """\
Invoice number: INV-102
Client: Example Client
Issued: 2025-01-02
Currency: EUR
Total: 4,919.12
Description Qty Unit Price Amount
Consulting 2 1,234.56 1,234.56
Travel 1 1.234,56 1.234,56
Admin 1 150.00 150,00
Support 1 150,00 150.00
"""

        result = parse_fieldquo_invoice_text(text)

        self.assertEqual(
            result.invoice["line_items"],
            [
                {
                    "description": "Consulting",
                    "quantity": Decimal("2"),
                    "unit_price": Decimal("1234.56"),
                    "line_total": Decimal("1234.56"),
                },
                {
                    "description": "Travel",
                    "quantity": Decimal("1"),
                    "unit_price": Decimal("1234.56"),
                    "line_total": Decimal("1234.56"),
                },
                {
                    "description": "Admin",
                    "quantity": Decimal("1"),
                    "unit_price": Decimal("150.00"),
                    "line_total": Decimal("150.00"),
                },
                {
                    "description": "Support",
                    "quantity": Decimal("1"),
                    "unit_price": Decimal("150.00"),
                    "line_total": Decimal("150.00"),
                },
            ],
        )

    def test_parses_multiple_line_items_in_source_order(self):
        text = self.COMPLETE_INVOICE.replace(
            "Consulting | 2 | 75.00 | 150.00",
            "Consulting | 2 | 75.00 | 150.00\nTravel | 1 | 25.00 | 25.00",
        )

        result = parse_fieldquo_invoice_text(text)

        self.assertEqual(
            [item["description"] for item in result.invoice["line_items"]],
            ["Consulting", "Travel"],
        )
        self.assertEqual(result.invoice["line_items"][1]["line_total"], Decimal("25.00"))

    def test_missing_invoice_number_is_not_inferred(self):
        text = self.COMPLETE_INVOICE.replace("Invoice number: INV-100\n", "")

        result = parse_fieldquo_invoice_text(text)

        self.assertIsNone(result.invoice["invoice_number"])
        self.assertIn("invoice_number", result.missing_fields)
        self.assertFalse(result.complete)

    def test_missing_or_ambiguous_date_remains_none(self):
        missing = parse_fieldquo_invoice_text(
            self.COMPLETE_INVOICE.replace("Issued: 2025-01-02\n", "")
        )
        ambiguous = parse_fieldquo_invoice_text(
            self.COMPLETE_INVOICE.replace("Issued: 2025-01-02", "Issued: 02/01/2025")
        )

        self.assertIsNone(missing.invoice["issued"])
        self.assertIn("issued", missing.missing_fields)
        self.assertIsNone(ambiguous.invoice["issued"])
        self.assertTrue(any("ambígua" in issue for issue in ambiguous.ambiguities))

    def test_missing_quantity_is_reported_and_not_inferred(self):
        result = parse_fieldquo_invoice_text(
            self.COMPLETE_INVOICE.replace(
                "Consulting | 2 | 75.00 | 150.00",
                "Consulting |  | 75.00 | 150.00",
            )
        )

        self.assertIsNone(result.invoice["line_items"][0]["quantity"])
        self.assertIn("line_items[0].quantity", result.missing_fields)
        self.assertFalse(result.complete)

    def test_missing_unit_price_is_reported_and_not_inferred(self):
        result = parse_fieldquo_invoice_text(
            self.COMPLETE_INVOICE.replace(
                "Consulting | 2 | 75.00 | 150.00",
                "Consulting | 2 |  | 150.00",
            )
        )

        self.assertIsNone(result.invoice["line_items"][0]["unit_price"])
        self.assertIn("line_items[0].unit_price", result.missing_fields)
        self.assertFalse(result.complete)

    def test_missing_line_total_is_reported_and_not_calculated(self):
        result = parse_fieldquo_invoice_text(
            self.COMPLETE_INVOICE.replace(
                "Consulting | 2 | 75.00 | 150.00",
                "Consulting | 2 | 75.00 | ",
            )
        )

        self.assertIsNone(result.invoice["line_items"][0]["line_total"])
        self.assertIn("line_items[0].line_total", result.missing_fields)
        self.assertFalse(result.complete)

    def test_missing_invoice_total_is_not_inferred_from_lines(self):
        result = parse_fieldquo_invoice_text(
            self.COMPLETE_INVOICE.replace("Total: 175.00\n", "")
        )

        self.assertIsNone(result.invoice["total"])
        self.assertIn("total", result.missing_fields)
        self.assertFalse(result.complete)

    def test_parses_explicit_money_with_comma_decimal_separator(self):
        text = self.COMPLETE_INVOICE.replace("Subtotal: 150.00", "Subtotal: 150,00")
        result = parse_fieldquo_invoice_text(text)

        self.assertEqual(result.invoice["subtotal"], Decimal("150.00"))

    def test_missing_values_are_reported_without_zero_defaults(self):
        text = self.COMPLETE_INVOICE.replace("Tax: 30.00\n", "").replace(
            "Discount: 5.00\n", ""
        )
        result = parse_fieldquo_invoice_text(text)

        self.assertIsNone(result.invoice["tax"])
        self.assertIsNone(result.invoice["discount"])
        self.assertIn("tax", result.missing_fields)
        self.assertIn("discount", result.missing_fields)

    def test_non_invoice_text_is_rejected_clearly(self):
        result = parse_fieldquo_invoice_text("A general note with no invoice data.")

        self.assertFalse(result.complete)
        self.assertTrue(result.rejections)
        self.assertIsNone(result.invoice["total"])
        self.assertIsNone(result.invoice["line_items"])

    def test_unrecognized_line_structure_is_reported(self):
        text = self.COMPLETE_INVOICE.replace(
            "Description | Quantity | Unit Price | Line Total",
            "Item | Qty | Rate",
        ).replace(
            "Consulting | 2 | 75.00 | 150.00",
            "Consulting | 2 | 75.00",
        )
        result = parse_fieldquo_invoice_text(text)

        self.assertIsNone(result.invoice["line_items"])
        self.assertTrue(any("estrutura de linhas" in issue for issue in result.ambiguities))
        self.assertFalse(result.complete)

    def test_unknown_fields_and_currency_are_not_inferred(self):
        text = self.COMPLETE_INVOICE.replace("Currency: EUR\n", "").replace(
            "Subtotal: 150.00", "Subtotal: €150.00"
        )
        result = parse_fieldquo_invoice_text(text)

        self.assertIsNone(result.invoice["currency"])
        self.assertIn("currency", result.missing_fields)
        self.assertEqual(
            set(result.invoice["line_items"][0]),
            {"description", "quantity", "unit_price", "line_total"},
        )

    def test_ambiguous_money_is_not_parsed(self):
        result = parse_fieldquo_invoice_text(
            self.COMPLETE_INVOICE.replace("Subtotal: 150.00", "Subtotal: 1,234")
        )

        self.assertIsNone(result.invoice["subtotal"])
        self.assertTrue(any("ambígu" in issue for issue in result.ambiguities))

    def test_conflicting_duplicate_field_is_marked_ambiguous(self):
        result = parse_fieldquo_invoice_text(
            self.COMPLETE_INVOICE.replace(
                "Client: Example Client",
                "Client: Example Client\nClient: Other Client",
            )
        )

        self.assertTrue(any("client: valores conflitantes" in issue for issue in result.ambiguities))
        self.assertFalse(result.complete)


class PDFTextExtractorInterfaceTests(unittest.TestCase):
    def test_extracts_text_through_injected_backend(self):
        class StubExtractor:
            def extract_text(self, pdf_path):
                return f"read {Path(pdf_path).name}"

        with tempfile.TemporaryDirectory() as directory:
            pdf_path = Path(directory) / "invoice.pdf"
            pdf_path.touch()

            self.assertEqual(
                extract_pdf_text(pdf_path, StubExtractor()),
                "read invoice.pdf",
            )

    def test_empty_extraction_raises_clear_error(self):
        class EmptyExtractor:
            def extract_text(self, pdf_path):
                return " \n "

        with tempfile.TemporaryDirectory() as directory:
            pdf_path = Path(directory) / "invoice.pdf"
            pdf_path.touch()

            with self.assertRaises(PDFTextExtractionError):
                extract_pdf_text(pdf_path, EmptyExtractor())

    def test_missing_pdf_raises_file_not_found(self):
        class StubExtractor:
            def extract_text(self, pdf_path):
                return "invoice"

        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(FileNotFoundError):
                extract_pdf_text(Path(directory) / "missing.pdf", StubExtractor())


if __name__ == "__main__":
    unittest.main()
