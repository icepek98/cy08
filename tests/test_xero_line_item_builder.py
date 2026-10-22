import copy
import unittest
from decimal import Decimal

from xero.invoice_creation_config import InvoiceCreationConfig
from xero.line_item_builder import (
    LineItemBuilderError,
    NonTerminatingUnitAmountError,
    UnsupportedDiscountRepresentationError,
    build_xero_line_item,
)


def config(**overrides):
    values = {
        "tenant_id": "tenant-1",
        "description": "FieldQuo services",
        "quantity": Decimal("1"),
        "account_code": "200",
        "tax_type": "OUTPUT",
        "tax_inclusive": False,
        "amount_basis": "subtotal",
        "discount_policy": "ignore",
    }
    values.update(overrides)
    return InvoiceCreationConfig(**values)


def fieldquo_invoice(**overrides):
    result = {
        "subtotal": Decimal("100.00"),
        "discount": Decimal("5.00"),
        "tax": Decimal("19.00"),
        "total": Decimal("114.00"),
    }
    result.update(overrides)
    return result


class XeroLineItemBuilderTests(unittest.TestCase):
    def test_subtotal_with_quantity_one(self):
        result = build_xero_line_item(fieldquo_invoice(), config())

        self.assertEqual(result["UnitAmount"], Decimal("100.00"))
        self.assertEqual(result["Quantity"] * result["UnitAmount"], Decimal("100.00"))

    def test_subtotal_with_configured_quantity(self):
        result = build_xero_line_item(
            fieldquo_invoice(), config(quantity=Decimal("4"))
        )

        self.assertEqual(result["UnitAmount"], Decimal("25"))
        self.assertEqual(result["Quantity"] * result["UnitAmount"], Decimal("100.00"))

    def test_total_with_quantity_one(self):
        result = build_xero_line_item(
            fieldquo_invoice(), config(amount_basis="total")
        )

        self.assertEqual(result["UnitAmount"], Decimal("114.00"))
        self.assertEqual(result["Quantity"] * result["UnitAmount"], Decimal("114.00"))

    def test_total_with_configured_quantity(self):
        result = build_xero_line_item(
            fieldquo_invoice(),
            config(amount_basis="total", quantity=Decimal("3")),
        )

        self.assertEqual(result["UnitAmount"], Decimal("38"))
        self.assertEqual(result["Quantity"] * result["UnitAmount"], Decimal("114.00"))

    def test_decimal_unit_amount_is_preserved(self):
        amount = Decimal("100.12345678901234567890")

        result = build_xero_line_item(
            fieldquo_invoice(subtotal=amount),
            config(quantity=Decimal("2")),
        )

        self.assertEqual(result["UnitAmount"], Decimal("50.06172839450617283945"))
        self.assertIsInstance(result["UnitAmount"], Decimal)

    def test_account_code_is_preserved(self):
        result = build_xero_line_item(
            fieldquo_invoice(), config(account_code="REV-01")
        )

        self.assertEqual(result["AccountCode"], "REV-01")

    def test_tax_type_is_preserved(self):
        result = build_xero_line_item(fieldquo_invoice(), config(tax_type="TAX-EXEMPT"))

        self.assertEqual(result["TaxType"], "TAX-EXEMPT")

    def test_description_is_preserved(self):
        result = build_xero_line_item(
            fieldquo_invoice(), config(description="Configured description")
        )

        self.assertEqual(result["Description"], "Configured description")

    def test_quantity_is_preserved(self):
        quantity = Decimal("2.50")

        result = build_xero_line_item(fieldquo_invoice(), config(quantity=quantity))

        self.assertIs(result["Quantity"], quantity)

    def test_missing_basis_is_rejected(self):
        with self.assertRaisesRegex(LineItemBuilderError, "subtotal is missing"):
            build_xero_line_item(fieldquo_invoice(subtotal=None), config())

    def test_negative_basis_is_rejected(self):
        with self.assertRaisesRegex(LineItemBuilderError, "subtotal is negative"):
            build_xero_line_item(fieldquo_invoice(subtotal=Decimal("-0.01")), config())

    def test_ignore_policy_does_not_add_discount(self):
        result = build_xero_line_item(
            fieldquo_invoice(), config(discount_policy="ignore")
        )

        self.assertEqual(
            set(result),
            {"Description", "Quantity", "UnitAmount", "AccountCode", "TaxType"},
        )
        self.assertNotIn("Discount", result)
        self.assertNotIn("DiscountAmount", result)
        self.assertNotIn("DiscountRate", result)

    def test_line_discount_is_rejected_without_inventing_representation(self):
        with self.assertRaises(UnsupportedDiscountRepresentationError):
            build_xero_line_item(
                fieldquo_invoice(), config(discount_policy="line_discount")
            )

    def test_input_data_is_not_modified(self):
        original = fieldquo_invoice()
        snapshot = copy.deepcopy(original)

        build_xero_line_item(original, config())

        self.assertEqual(original, snapshot)

    def test_non_terminating_unit_amount_is_rejected_without_rounding(self):
        with self.assertRaises(NonTerminatingUnitAmountError):
            build_xero_line_item(
                fieldquo_invoice(subtotal=Decimal("1")),
                config(quantity=Decimal("3")),
            )

    def test_draft_source_amounts_are_supported(self):
        draft = {"Source": {"Subtotal": Decimal("24.00")}}

        result = build_xero_line_item(draft, config(quantity=Decimal("2")))

        self.assertEqual(result["UnitAmount"], Decimal("12"))

    def test_non_decimal_basis_is_rejected(self):
        with self.assertRaisesRegex(LineItemBuilderError, "finite Decimal"):
            build_xero_line_item(fieldquo_invoice(subtotal="100"), config())


if __name__ == "__main__":
    unittest.main()
