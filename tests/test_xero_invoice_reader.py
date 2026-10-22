import unittest
from decimal import Decimal
from unittest.mock import Mock

from xero.invoice_reader import read_xero_invoices


def valid_invoice(contact_id: str, name: str, number: str) -> dict[str, object]:
    return {
        "Contact": {"ContactID": contact_id, "Name": name},
        "Total": "100.00",
        "Date": "2026-09-25",
        "Reference": "",
        "InvoiceNumber": number,
        "LineItems": [],
    }


class XeroInvoiceReaderTests(unittest.TestCase):
    def test_all_valid_invoices_are_returned_as_records(self):
        client = Mock()
        client.get_invoices.return_value = [
            valid_invoice("id-1", "Cliente 1", "INV-001"),
            valid_invoice("id-2", "Cliente 2", "INV-002"),
        ]

        result = read_xero_invoices(client)

        self.assertEqual(len(result["records"]), 2)
        self.assertEqual(result["rejections"], [])
        self.assertEqual(result["records"][0]["client_id"], "id-1")
        self.assertEqual(result["records"][1]["client_id"], "id-2")

    def test_invalid_invoice_is_added_to_rejections(self):
        client = Mock()
        client.get_invoices.return_value = [
            valid_invoice("id-1", "Cliente 1", "INV-001"),
            valid_invoice("id-2", "Cliente 2", ""),
        ]

        result = read_xero_invoices(client)

        self.assertEqual(len(result["records"]), 1)
        self.assertEqual(len(result["rejections"]), 1)
        self.assertEqual(result["rejections"][0]["index"], 1)

    def test_invalid_invoice_does_not_stop_remaining_invoices(self):
        client = Mock()
        client.get_invoices.return_value = [
            valid_invoice("id-1", "Cliente 1", "INV-001"),
            {"Contact": {"ContactID": "id-2", "Name": "Cliente 2"}},
            valid_invoice("id-3", "Cliente 3", "INV-003"),
        ]

        result = read_xero_invoices(client)

        self.assertEqual([record["client_id"] for record in result["records"]], ["id-1", "id-3"])
        self.assertEqual(len(result["rejections"]), 1)

    def test_valid_invoice_order_is_preserved(self):
        client = Mock()
        client.get_invoices.return_value = [
            valid_invoice("id-3", "Cliente 3", "INV-003"),
            valid_invoice("id-1", "Cliente 1", "INV-001"),
        ]

        result = read_xero_invoices(client)

        self.assertEqual(
            [record["client_id"] for record in result["records"]],
            ["id-3", "id-1"],
        )

    def test_get_invoices_is_called_once(self):
        client = Mock()
        client.get_invoices.return_value = []

        read_xero_invoices(client)

        client.get_invoices.assert_called_once_with()

    def test_two_valid_and_one_rejected_fixture(self):
        client = Mock()
        client.get_invoices.return_value = [
            valid_invoice("id-1", "Cliente 1", "INV-001"),
            {"Contact": {"ContactID": "id-2", "Name": "Cliente 2"}, "Total": "10.00"},
            valid_invoice("id-3", "Cliente 3", "INV-003"),
        ]

        result = read_xero_invoices(client)

        self.assertEqual(len(result["records"]), 2)
        self.assertEqual(len(result["rejections"]), 1)

    def test_rejection_contains_only_safe_structured_fields(self):
        client = Mock()
        client.get_invoices.return_value = [
            {
                "Contact": {"ContactID": "secret-id", "Name": "Cliente"},
                "Total": "100.00",
                "Date": "2026-09-25",
                "Reference": "",
                "InvoiceNumber": "",
                "LineItems": [],
                "AccessToken": "should-not-return",
            }
        ]

        result = read_xero_invoices(client)
        rejection = result["rejections"][0]

        self.assertEqual(
            set(rejection),
            {"source", "index", "status", "message"},
        )
        self.assertNotIn("should-not-return", str(result))
        self.assertNotIn("secret-id", str(rejection))

    def test_raw_payload_is_not_returned(self):
        client = Mock()
        payload = valid_invoice("id-1", "Cliente 1", "INV-001")
        client.get_invoices.return_value = [payload]

        result = read_xero_invoices(client)

        self.assertNotIn("Contact", result)
        self.assertNotIn("InvoiceNumber", result)
        self.assertNotIn(payload, result["records"])
        self.assertEqual(result["records"][0]["amount"], Decimal("100.00"))


if __name__ == "__main__":
    unittest.main()
