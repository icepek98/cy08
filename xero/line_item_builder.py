"""Pure construction of one Xero LineItem from FieldQuo invoice totals."""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from fractions import Fraction
from typing import Any

from .invoice_creation_config import InvoiceCreationConfig


class LineItemBuilderError(ValueError):
    """Raised when FieldQuo data cannot safely produce a Xero LineItem."""


class NonTerminatingUnitAmountError(LineItemBuilderError):
    """Raised when exact division would require rounding the UnitAmount."""


class UnsupportedDiscountRepresentationError(LineItemBuilderError):
    """Raised when a FieldQuo discount cannot be safely mapped to one line."""


def build_xero_line_item(
    invoice: Mapping[str, Any],
    config: InvoiceCreationConfig,
) -> dict[str, object]:
    """Build a LineItem without I/O, mutation, implicit accounting rules or rounding.

    ``invoice`` may be a normalized FieldQuo record or the draft returned by
    ``adapt_fieldquo_invoice``. Monetary bases must be finite ``Decimal`` values.
    """
    if not isinstance(invoice, Mapping):
        raise LineItemBuilderError("FieldQuo invoice must be a mapping")
    if not isinstance(config, InvoiceCreationConfig):
        raise LineItemBuilderError("config must be an InvoiceCreationConfig")
    if config.discount_policy == "line_discount":
        raise UnsupportedDiscountRepresentationError(
            "FieldQuo discount semantics are not established; "
            "line_discount cannot be represented safely."
        )

    basis_name = "subtotal" if config.amount_basis == "subtotal" else "total"
    amount = _invoice_amount(invoice, basis_name)
    if amount is None:
        raise LineItemBuilderError(
            f"FieldQuo {basis_name} is missing; cannot build UnitAmount."
        )
    if amount < 0:
        raise LineItemBuilderError(
            f"FieldQuo {basis_name} is negative; cannot build a LineItem."
        )

    quantity = _as_fraction(config.quantity)
    unit_amount = _exact_decimal(Fraction(amount) / quantity)

    return {
        "Description": config.description,
        "Quantity": config.quantity,
        "UnitAmount": unit_amount,
        "AccountCode": config.account_code,
        "TaxType": config.tax_type,
    }


def _invoice_amount(invoice: Mapping[str, Any], field_name: str) -> Decimal | None:
    source = invoice.get("Source")
    value: Any = None
    if isinstance(source, Mapping):
        value = source.get(field_name.title())
    else:
        value = invoice.get(field_name)

    if value is None:
        return None
    if not isinstance(value, Decimal) or not value.is_finite():
        raise LineItemBuilderError(
            f"FieldQuo {field_name} must be a finite Decimal value."
        )
    return value


def _as_fraction(value: int | float | Decimal) -> Fraction:
    if isinstance(value, Decimal):
        return Fraction(value)
    if isinstance(value, float):
        numerator, denominator = value.as_integer_ratio()
        return Fraction(numerator, denominator)
    return Fraction(value)


def _exact_decimal(value: Fraction) -> Decimal:
    if value.numerator == 0:
        return Decimal(0)

    denominator = value.denominator
    twos = 0
    while denominator % 2 == 0:
        denominator //= 2
        twos += 1
    fives = 0
    while denominator % 5 == 0:
        denominator //= 5
        fives += 1
    if denominator != 1:
        raise NonTerminatingUnitAmountError(
            "Exact UnitAmount has a non-terminating decimal expansion; "
            "construction would require rounding."
        )

    scale = max(twos, fives)
    scaled_numerator = value.numerator
    scaled_numerator *= 2 ** (scale - twos)
    scaled_numerator *= 5 ** (scale - fives)
    sign = 1 if scaled_numerator < 0 else 0
    digits = tuple(int(digit) for digit in str(abs(scaled_numerator)))
    return Decimal((sign, digits, -scale))
