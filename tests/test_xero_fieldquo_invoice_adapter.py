import ast
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

from xero.fieldquo_invoice_adapter import (
    FieldQuoInvoiceAdapterError,
    adapt_fieldquo_invoice,
)


def fieldquo_invoice(**overrides):
    result = {
        "invoice_number": "FQ-INV-0042",
        "issued": date(2026, 9, 25),
        "date_taken_from": date(2026, 9, 1),
        "due": date(2026, 10, 25),
        "client": "Example Client",
        "status": "Issued",
        "version": "2",
        "currency": "EUR",
        "subtotal": Decimal("120.125"),
        "discount": Decimal("5.25"),
        "tax": Decimal("22.775"),
        "tax_applied": "true",
        "total": Decimal("137.650"),
        "paid_to_date": Decimal("10.00"),
        "received_in_range": Decimal("2.00"),
        "balance": Decimal("127.650"),
    }
    result.update(overrides)
    return result


class FieldQuoInvoiceAdapterTests(unittest.TestCase):
    def test_valid_invoice_produces_incomplete_xero_invoice_draft(self):
        result = adapt_fieldquo_invoice(fieldquo_invoice())

        self.assertEqual(result["InvoiceNumber"], "FQ-INV-0042")
        self.assertEqual(result["Date"], "2026-09-25")
        self.assertEqual(result["DueDate"], "2026-10-25")
        self.assertEqual(result["Contact"], {"Name": "Example Client"})
        self.assertEqual(result["CurrencyCode"], "EUR")
        self.assertFalse(result["CreationReadiness"]["ready"])

    def test_pdf_origin_is_preserved_in_adapter_source(self):
        result = adapt_fieldquo_invoice(
            fieldquo_invoice(),
            source_origin="fieldquo_pdf",
        )

        self.assertEqual(result["Source"]["Origin"], "fieldquo_pdf")

    def test_invoice_number_is_preserved(self):
        result = adapt_fieldquo_invoice(fieldquo_invoice(invoice_number="INV / 42"))

        self.assertEqual(result["InvoiceNumber"], "INV / 42")

    def test_dates_are_preserved_in_iso_format(self):
        result = adapt_fieldquo_invoice(
            fieldquo_invoice(issued=date(2025, 1, 2), due=date(2025, 2, 3))
        )

        self.assertEqual(result["Date"], "2025-01-02")
        self.assertEqual(result["DueDate"], "2025-02-03")

    def test_absent_due_date_remains_absent(self):
        result = adapt_fieldquo_invoice(fieldquo_invoice(due=None))

        self.assertIsNone(result["DueDate"])

    def test_currency_is_preserved(self):
        result = adapt_fieldquo_invoice(fieldquo_invoice(currency="USD"))

        self.assertEqual(result["CurrencyCode"], "USD")

    def test_source_amounts_keep_decimal_precision(self):
        result = adapt_fieldquo_invoice(fieldquo_invoice())
        source = result["Source"]

        self.assertEqual(source["Subtotal"], Decimal("120.125"))
        self.assertEqual(source["Discount"], Decimal("5.25"))
        self.assertEqual(source["Tax"], Decimal("22.775"))
        self.assertEqual(source["Total"], Decimal("137.650"))
        self.assertTrue(all(
            isinstance(source[field], Decimal)
            for field in ("Subtotal", "Discount", "Tax", "Total")
        ))

    def test_does_not_invent_account_code(self):
        result = adapt_fieldquo_invoice(fieldquo_invoice())

        self.assertNotIn("AccountCode", result)
        self.assertIn("AccountCode", " ".join(result["CreationReadiness"]["missing"]))

    def test_does_not_invent_tax_type(self):
        result = adapt_fieldquo_invoice(fieldquo_invoice())

        self.assertNotIn("TaxType", result)
        self.assertIn("TaxType", " ".join(result["CreationReadiness"]["missing"]))

    def test_missing_line_item_details_are_explicit(self):
        result = adapt_fieldquo_invoice(fieldquo_invoice())
        readiness = result["CreationReadiness"]

        self.assertFalse(readiness["line_items_available"])
        missing_details = " ".join(readiness["missing"])
        self.assertIn(
            "Provide LineItems[].Description, Quantity, and UnitAmount.",
            missing_details,
        )
        self.assertNotIn("LineItems", result)

    def test_invoice_without_line_items_does_not_create_lines(self):
        result = adapt_fieldquo_invoice(fieldquo_invoice())

        self.assertNotIn("LineItems", result["Source"])
        self.assertFalse(result["CreationReadiness"]["line_items_available"])

    def test_invoice_with_empty_line_items_preserves_empty_list(self):
        result = adapt_fieldquo_invoice(fieldquo_invoice(line_items=[]))

        self.assertEqual(result["Source"]["LineItems"], [])
        self.assertFalse(result["CreationReadiness"]["line_items_available"])

    def test_single_line_preserves_each_field_value(self):
        line_items = [
            {
                "description": "  Exact description  ",
                "quantity": Decimal("2.000"),
                "unit_price": Decimal("75.1250"),
                "line_total": Decimal("150.250000"),
            }
        ]
        original_line = line_items[0].copy()
        result = adapt_fieldquo_invoice(
            fieldquo_invoice(line_items=line_items)
        )

        self.assertEqual(
            result["Source"]["LineItems"],
            [
                {
                    "Description": "  Exact description  ",
                    "Quantity": Decimal("2.000"),
                    "UnitAmount": Decimal("75.1250"),
                    "LineAmount": Decimal("150.250000"),
                }
            ],
        )
        self.assertEqual(line_items, [original_line])

    def test_multiple_lines_preserve_order_and_each_field_value(self):
        line_items = [
            {
                "description": "First",
                "quantity": Decimal("1"),
                "unit_price": Decimal("10.00"),
                "line_total": Decimal("10.00"),
            },
            {
                "description": "Second",
                "quantity": Decimal("3.5"),
                "unit_price": Decimal("2.125"),
                "line_total": Decimal("7.4375"),
            },
        ]
        result = adapt_fieldquo_invoice(
            fieldquo_invoice(line_items=line_items)
        )

        self.assertEqual(
            result["Source"]["LineItems"],
            [
                {
                    "Description": "First",
                    "Quantity": Decimal("1"),
                    "UnitAmount": Decimal("10.00"),
                    "LineAmount": Decimal("10.00"),
                },
                {
                    "Description": "Second",
                    "Quantity": Decimal("3.5"),
                    "UnitAmount": Decimal("2.125"),
                    "LineAmount": Decimal("7.4375"),
                },
            ],
        )

    def test_missing_line_fields_remain_null_without_fallback_values(self):
        result = adapt_fieldquo_invoice(
            fieldquo_invoice(
                line_items=[
                    {
                        "description": "Materials",
                        "line_total": Decimal("12.50"),
                    }
                ]
            )
        )

        self.assertEqual(
            result["Source"]["LineItems"],
            [
                {
                    "Description": "Materials",
                    "Quantity": None,
                    "UnitAmount": None,
                    "LineAmount": Decimal("12.50"),
                }
            ],
        )

    def test_complete_line_items_satisfy_line_details_readiness(self):
        result = adapt_fieldquo_invoice(
            fieldquo_invoice(
                line_items=[
                    {
                        "description": "Site visit",
                        "quantity": Decimal("2"),
                        "unit_price": Decimal("75.00"),
                        "line_total": Decimal("150.00"),
                    }
                ]
            )
        )

        readiness = result["CreationReadiness"]
        self.assertTrue(readiness["line_items_available"])
        self.assertNotIn(
            "Provide LineItems[].Description, Quantity, and UnitAmount.",
            readiness["missing"],
        )

    def test_payment_and_version_fields_are_not_mapped(self):
        result = adapt_fieldquo_invoice(fieldquo_invoice())

        for field in (
            "paid_to_date",
            "received_in_range",
            "balance",
            "version",
        ):
            self.assertNotIn(field, result)

    def test_invalid_required_fields_and_values_are_rejected(self):
        invalid_cases = (
            ({"invoice_number": "  "}, "Invoice number"),
            ({"client": ""}, "Client"),
            ({"currency": "EURO"}, "Currency"),
            ({"currency": "eur"}, "Currency"),
            ({"issued": "2026-09-25"}, "Issued"),
            ({"due": "2026-10-25"}, "Due"),
            ({"total": Decimal("-0.01")}, "Total negativo"),
            ({"tax": Decimal("-0.01")}, "Tax negativo"),
            ({"total": "137.65"}, "Total inválido"),
            ({"subtotal": Decimal("NaN")}, "Subtotal inválido"),
            ({"total": None}, "Total válido"),
        )

        for overrides, message in invalid_cases:
            with self.subTest(overrides=overrides):
                with self.assertRaisesRegex(FieldQuoInvoiceAdapterError, message):
                    adapt_fieldquo_invoice(fieldquo_invoice(**overrides))

    def test_adapter_has_no_network_imports_or_network_calls(self):
        module_path = Path(__file__).parents[1] / "xero" / "fieldquo_invoice_adapter.py"
        tree = ast.parse(module_path.read_text(encoding="utf-8"))
        network_modules = {
            "aiohttp",
            "curl_cffi",
            "ftplib",
            "http",
            "httpx",
            "imaplib",
            "paramiko",
            "poplib",
            "requests",
            "socket",
            "ssl",
            "telnetlib",
            "urllib",
            "urllib3",
            "websocket",
            "websockets",
            "xmlrpc",
        }
        network_call_paths = {
            "urllib.request.urlopen",
            "urllib.request.urlretrieve",
            "webbrowser.open",
            "webbrowser.open_new",
            "webbrowser.open_new_tab",
            "socket.connect",
            "socket.create_connection",
            "socket.socket",
            "http.client.httpconnection",
            "http.client.httpsconnection",
        }
        http_methods = {"get", "post", "put", "patch", "delete", "head", "options", "request"}
        subprocess_methods = {"run", "popen", "call", "check_call", "check_output"}
        network_tool_names = {"curl", "wget"}
        imported_modules = set()
        imported_names = {}
        calls = []

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported_modules.add(alias.name)
                    imported_names[alias.asname or alias.name.split(".", 1)[0]] = (
                        alias.name if alias.asname else alias.name.split(".", 1)[0]
                    )
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_modules.add(node.module)
                for alias in node.names:
                    imported_names[alias.asname or alias.name] = f"{node.module}.{alias.name}"
            elif isinstance(node, ast.Call):
                function = node.func
                parts = []
                while isinstance(function, ast.Attribute):
                    parts.append(function.attr.lower())
                    function = function.value
                if isinstance(function, ast.Name):
                    root = imported_names.get(function.id, function.id).lower()
                    parts.append(root)
                    call_path = ".".join(reversed(parts))
                    calls.append((call_path, node))

        def is_network_module(module):
            return any(
                module == network_module or module.startswith(f"{network_module}.")
                for network_module in network_modules
            )

        def command_uses_network_tool(node):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                command_parts = node.value.strip().split()
                return bool(command_parts) and Path(command_parts[0]).name in network_tool_names
            if isinstance(node, (ast.List, ast.Tuple)) and node.elts:
                first = node.elts[0]
                return (
                    isinstance(first, ast.Constant)
                    and isinstance(first.value, str)
                    and Path(first.value).name in network_tool_names
                )
            return False

        self.assertFalse(any(is_network_module(module) for module in imported_modules))

        for call_path, call_node in calls:
            parts = call_path.split(".")
            root = parts[0]
            is_http_client_call = (
                root in {"requests", "httpx"}
                and parts[-1] in http_methods
            )
            is_known_network_call = any(
                call_path == path or call_path.endswith(f".{path}")
                for path in network_call_paths
            )
            is_subprocess_network_call = (
                root == "subprocess"
                and parts[-1] in subprocess_methods
                and any(command_uses_network_tool(arg) for arg in call_node.args)
            )
            is_shell_network_call = (
                call_path in {"os.system", "os.popen"}
                and any(command_uses_network_tool(arg) for arg in call_node.args)
            )
            self.assertFalse(
                is_http_client_call
                or is_known_network_call
                or is_subprocess_network_call
                or is_shell_network_call,
                f"Potential network call detected: {call_path}",
            )


if __name__ == "__main__":
    unittest.main()
