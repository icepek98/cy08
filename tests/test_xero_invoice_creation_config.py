import unittest
from decimal import Decimal

from xero.invoice_creation_config import (
    InvoiceCreationConfig,
    InvoiceCreationConfigError,
)


def valid_config(**overrides):
    values = {
        "tenant_id": "xero-tenant-1",
        "description": "FieldQuo invoice",
        "quantity": Decimal("2.50"),
        "account_code": "200",
        "tax_type": "OUTPUT",
        "tax_inclusive": False,
        "amount_basis": "subtotal",
        "discount_policy": "ignore",
    }
    values.update(overrides)
    return InvoiceCreationConfig(**values)


class InvoiceCreationConfigTests(unittest.TestCase):
    def test_valid_configuration(self):
        config = valid_config()

        self.assertEqual(config.tenant_id, "xero-tenant-1")
        self.assertEqual(config.description, "FieldQuo invoice")
        self.assertEqual(config.quantity, Decimal("2.50"))
        self.assertIs(config.tax_inclusive, False)

    def test_empty_description_is_rejected(self):
        with self.assertRaisesRegex(InvoiceCreationConfigError, "description"):
            valid_config(description="  ")

    def test_zero_quantity_is_rejected(self):
        with self.assertRaisesRegex(InvoiceCreationConfigError, "quantity"):
            valid_config(quantity=0)

    def test_empty_account_code_is_rejected(self):
        with self.assertRaisesRegex(InvoiceCreationConfigError, "account_code"):
            valid_config(account_code="")

    def test_empty_tax_type_is_rejected(self):
        with self.assertRaisesRegex(InvoiceCreationConfigError, "tax_type"):
            valid_config(tax_type=" ")

    def test_invalid_amount_basis_is_rejected(self):
        with self.assertRaisesRegex(InvoiceCreationConfigError, "amount_basis"):
            valid_config(amount_basis="discounted_subtotal")

    def test_invalid_discount_policy_is_rejected(self):
        with self.assertRaisesRegex(InvoiceCreationConfigError, "discount_policy"):
            valid_config(discount_policy="apply_automatically")

    def test_invalid_types_are_rejected(self):
        invalid_cases = (
            ({"tenant_id": None}, "tenant_id"),
            ({"description": 42}, "description"),
            ({"quantity": True}, "quantity"),
            ({"quantity": "1"}, "quantity"),
            ({"account_code": 200}, "account_code"),
            ({"tax_type": None}, "tax_type"),
            ({"tax_inclusive": 1}, "tax_inclusive"),
            ({"amount_basis": None}, "amount_basis"),
            ({"discount_policy": None}, "discount_policy"),
        )
        for overrides, message in invalid_cases:
            with self.subTest(overrides=overrides):
                with self.assertRaisesRegex(InvoiceCreationConfigError, message):
                    valid_config(**overrides)

    def test_numeric_values_are_preserved_without_rounding_or_float_conversion(self):
        quantity = Decimal("3.141592653589793238462643383279")

        config = valid_config(quantity=quantity)

        self.assertIs(config.quantity, quantity)
        self.assertEqual(str(config.quantity), "3.141592653589793238462643383279")
        self.assertIsInstance(config.quantity, Decimal)

    def test_subtotal_amount_basis_is_valid(self):
        self.assertEqual(valid_config(amount_basis="subtotal").amount_basis, "subtotal")

    def test_total_amount_basis_is_valid(self):
        self.assertEqual(valid_config(amount_basis="total").amount_basis, "total")

    def test_supported_discount_policies_are_preserved(self):
        for policy in ("ignore", "line_discount"):
            with self.subTest(policy=policy):
                self.assertEqual(
                    valid_config(discount_policy=policy).discount_policy, policy
                )
