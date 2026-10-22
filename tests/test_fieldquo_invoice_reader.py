import ast
import csv
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

from demo_system.csv_reader import read_csv_transactions
from fieldquo.invoice_reader import (
    FIELDQUO_INVOICE_COLUMNS,
    read_fieldquo_invoices,
)


class FieldQuoInvoiceReaderTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.csv_path = Path(self.temp_dir.name) / "invoices.csv"

    def write_csv(self, rows, columns=FIELDQUO_INVOICE_COLUMNS):
        with self.csv_path.open("w", encoding="utf-8-sig", newline="") as csv_file:
            writer = csv.DictWriter(csv_file, fieldnames=columns)
            writer.writeheader()
            writer.writerows(rows)
        return self.csv_path

    @staticmethod
    def invoice_row(**overrides):
        row = {
            "Invoice number": "INV-100",
            "Issued": "2025-01-02",
            "Date taken from": "02/01/2025",
            "Due": "2025-02-01",
            "Client": "Example Client",
            "Status": "Issued",
            "Version": "1",
            "Currency": "EUR",
            "Subtotal": "100.25",
            "Discount": "5.00",
            "Tax": "19.05",
            "Tax applied": "true",
            "Total": "114.30",
            "Paid to date": "20.00",
            "Received in range": "10.00",
            "Balance": "94.30",
        }
        row.update(overrides)
        return row

    def test_reads_one_valid_invoice_and_normalizes_values(self):
        self.write_csv([self.invoice_row()])

        result = read_fieldquo_invoices(self.csv_path)

        self.assertEqual(result["rejections"], [])
        self.assertEqual(len(result["records"]), 1)
        record = result["records"][0]
        self.assertEqual(record["invoice_number"], "INV-100")
        self.assertEqual(record["issued"], date(2025, 1, 2))
        self.assertEqual(record["date_taken_from"], date(2025, 1, 2))
        self.assertEqual(record["due"], date(2025, 2, 1))
        self.assertEqual(record["subtotal"], Decimal("100.25"))
        self.assertIsInstance(record["total"], Decimal)

    def test_reads_multiple_invoices_and_preserves_order(self):
        self.write_csv([
            self.invoice_row(**{"Invoice number": "INV-1"}),
            self.invoice_row(**{"Invoice number": "INV-2"}),
            self.invoice_row(**{"Invoice number": "INV-3"}),
        ])

        result = read_fieldquo_invoices(self.csv_path)

        self.assertEqual(
            [record["invoice_number"] for record in result["records"]],
            ["INV-1", "INV-2", "INV-3"],
        )

    def test_record_preserves_all_documented_fields(self):
        self.write_csv([self.invoice_row()])

        result = read_fieldquo_invoices(self.csv_path)

        self.assertEqual(
            set(result["records"][0]),
            {
                "invoice_number",
                "issued",
                "date_taken_from",
                "due",
                "client",
                "status",
                "version",
                "currency",
                "subtotal",
                "discount",
                "tax",
                "tax_applied",
                "total",
                "paid_to_date",
                "received_in_range",
                "balance",
            },
        )
        self.assertEqual(result["records"][0]["tax_applied"], "true")

    def test_invalid_header_is_rejected(self):
        columns = ("Wrong header",)
        self.write_csv([{"Wrong header": "invalid"}], columns=columns)

        with self.assertRaisesRegex(ValueError, "Cabeçalho inválido"):
            read_fieldquo_invoices(self.csv_path)

    def test_missing_required_column_is_reported(self):
        columns = tuple(
            column for column in FIELDQUO_INVOICE_COLUMNS if column != "Invoice number"
        )
        row = {
            column: value
            for column, value in self.invoice_row().items()
            if column in columns
        }
        self.write_csv([row], columns=columns)

        with self.assertRaisesRegex(ValueError, "Invoice number"):
            read_fieldquo_invoices(self.csv_path)

    def test_invalid_money_rejects_only_its_row_and_continues(self):
        self.write_csv([
            self.invoice_row(**{"Invoice number": "INV-1", "Total": "not-money"}),
            self.invoice_row(**{"Invoice number": "INV-2"}),
        ])

        result = read_fieldquo_invoices(self.csv_path)

        self.assertEqual(
            [record["invoice_number"] for record in result["records"]],
            ["INV-2"],
        )
        self.assertEqual(len(result["rejections"]), 1)
        self.assertEqual(result["rejections"][0]["source"], "fieldquo")
        self.assertEqual(result["rejections"][0]["index"], 2)
        self.assertEqual(result["rejections"][0]["status"], "REJECTED")
        self.assertIn("Total", result["rejections"][0]["message"])

    def test_non_finite_money_is_rejected(self):
        self.write_csv([self.invoice_row(**{"Subtotal": "NaN"})])

        result = read_fieldquo_invoices(self.csv_path)

        self.assertEqual(result["records"], [])
        self.assertIn("finito", result["rejections"][0]["message"])

    def test_invalid_date_rejects_only_its_row(self):
        self.write_csv([
            self.invoice_row(**{"Invoice number": "INV-1", "Due": "not-a-date"}),
            self.invoice_row(**{"Invoice number": "INV-2"}),
        ])

        result = read_fieldquo_invoices(self.csv_path)

        self.assertEqual(
            [record["invoice_number"] for record in result["records"]],
            ["INV-2"],
        )
        self.assertEqual(result["rejections"][0]["index"], 2)
        self.assertIn("Due", result["rejections"][0]["message"])

    def test_empty_csv_returns_empty_result(self):
        self.csv_path.write_text("", encoding="utf-8")

        self.assertEqual(
            read_fieldquo_invoices(self.csv_path),
            {"records": [], "rejections": []},
        )

    def test_no_network_or_xero_dependencies_are_imported(self):
        module_path = Path(__file__).parents[1] / "fieldquo" / "invoice_reader.py"
        tree = ast.parse(module_path.read_text(encoding="utf-8"))
        imported_roots = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_roots.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_roots.add(node.module.split(".", 1)[0])

        self.assertTrue(imported_roots <= {"csv", "datetime", "decimal", "pathlib", "typing"})
        self.assertFalse({"xero", "playwright", "requests", "urllib"} & imported_roots)

    def test_generic_csv_reader_behavior_remains_unchanged(self):
        generic_path = Path(self.temp_dir.name) / "generic.csv"
        with generic_path.open("w", encoding="utf-8", newline="") as csv_file:
            writer = csv.DictWriter(
                csv_file,
                fieldnames=("client_id", "client_name", "amount", "date", "description"),
            )
            writer.writeheader()
            writer.writerow({
                "client_id": "C-1",
                "client_name": "Client",
                "amount": "12.50",
                "date": "2025-01-02",
                "description": "Existing generic CSV",
            })

        self.assertEqual(
            read_csv_transactions(generic_path),
            [{
                "client_id": "C-1",
                "client_name": "Client",
                "amount": "12.50",
                "date": "2025-01-02",
                "description": "Existing generic CSV",
            }],
        )


if __name__ == "__main__":
    unittest.main()
