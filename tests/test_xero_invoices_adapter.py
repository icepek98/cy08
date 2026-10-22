import unittest
from decimal import Decimal

from xero.invoices_adapter import XeroInvoiceAdapterError, adapt_xero_invoice


def invoice(**overrides):
    result = {
        "InvoiceID": "invoice-1",
        "Contact": {"ContactID": "contact-1", "Name": "Cliente 1"},
        "Total": "125.50",
        "Date": "2026-09-25T00:00:00",
        "Reference": "",
        "LineItems": [{"Description": "Serviço mensal"}],
    }
    result.update(overrides)
    return result


class XeroInvoiceAdapterTests(unittest.TestCase):
    def test_valid_invoice(self):
        result = adapt_xero_invoice(invoice(Reference="REF-001"))

        self.assertEqual(
            result,
            {
                "client_id": "contact-1",
                "client_name": "Cliente 1",
                "amount": Decimal("125.50"),
                "date": "2026-09-25",
                "description": "REF-001",
                "xero_invoice_id": "invoice-1",
            },
        )

    def test_iso_date_is_normalized(self):
        result = adapt_xero_invoice(invoice(Date="2026-09-25T14:30:00Z"))
        self.assertEqual(result["date"], "2026-09-25")

    def test_xero_serialized_date_is_normalized(self):
        result = adapt_xero_invoice(invoice(Date="/Date(1790332800000+0000)/"))
        self.assertEqual(result["date"], "2026-09-25")

    def test_reference_is_preferred(self):
        result = adapt_xero_invoice(
            invoice(Reference="Referência", InvoiceNumber="INV-001")
        )
        self.assertEqual(result["description"], "Referência")

    def test_line_items_are_used_without_reference(self):
        result = adapt_xero_invoice(
            invoice(
                LineItems=[
                    {"Description": "Item A"},
                    {"Description": "Item B"},
                ]
            )
        )
        self.assertEqual(result["description"], "Item A; Item B")

    def test_invoice_number_is_used_without_reference_or_line_item_description(self):
        result = adapt_xero_invoice(
            invoice(Reference="", LineItems=[], InvoiceNumber="INV-001")
        )
        self.assertEqual(result["description"], "INV-001")

    def test_missing_contact_id(self):
        with self.assertRaises(XeroInvoiceAdapterError):
            adapt_xero_invoice(invoice(Contact={"Name": "Cliente 1"}))

    def test_missing_contact_name(self):
        with self.assertRaises(XeroInvoiceAdapterError):
            adapt_xero_invoice(invoice(Contact={"ContactID": "contact-1"}))

    def test_missing_total(self):
        with self.assertRaises(XeroInvoiceAdapterError):
            adapt_xero_invoice(invoice(Total=None))

    def test_missing_date(self):
        with self.assertRaises(XeroInvoiceAdapterError):
            adapt_xero_invoice(invoice(Date=None))

    def test_empty_date(self):
        with self.assertRaisesRegex(XeroInvoiceAdapterError, "sem Date"):
            adapt_xero_invoice(invoice(Date=""))

    def test_invalid_date(self):
        with self.assertRaisesRegex(XeroInvoiceAdapterError, "Date inválida"):
            adapt_xero_invoice(invoice(Date="not-a-date"))

    def test_unrecognized_date_format_is_rejected(self):
        with self.assertRaisesRegex(XeroInvoiceAdapterError, "Date inválida"):
            adapt_xero_invoice(invoice(Date="25/09/2026"))

    def test_missing_usable_description(self):
        with self.assertRaises(XeroInvoiceAdapterError):
            adapt_xero_invoice(invoice(LineItems=[], InvoiceNumber=None))

    def test_empty_invoice_number_is_not_accepted_as_description(self):
        for invoice_number in ("", "   ", None):
            with self.subTest(invoice_number=repr(invoice_number)):
                with self.assertRaises(XeroInvoiceAdapterError):
                    adapt_xero_invoice(
                        invoice(
                            Reference="",
                            LineItems=[],
                            InvoiceNumber=invoice_number,
                        )
                    )

    def test_multiple_line_items(self):
        result = adapt_xero_invoice(
            invoice(LineItems=[{"Description": "A"}, {}, {"Description": "B"}])
        )
        self.assertEqual(result["description"], "A; B")


if __name__ == "__main__":
    unittest.main()
