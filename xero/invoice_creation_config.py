"""Validated, tenant-scoped settings for future Xero invoice creation."""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal


AmountBasis = Literal["subtotal", "total"]
DiscountPolicy = Literal["ignore", "line_discount"]
Quantity = int | float | Decimal


class InvoiceCreationConfigError(ValueError):
    """Raised when invoice-creation settings are incomplete or invalid."""


@dataclass(frozen=True)
class InvoiceCreationConfig:
    """Accounting settings for one connected Xero tenant.

    This model is intentionally in-memory only; it does not add persistence.
    """

    tenant_id: str
    description: str
    quantity: Quantity
    account_code: str
    tax_type: str
    tax_inclusive: bool
    amount_basis: AmountBasis
    discount_policy: DiscountPolicy

    def __post_init__(self) -> None:
        for field_name in ("tenant_id", "description", "account_code", "tax_type"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise InvoiceCreationConfigError(
                    f"{field_name} must be a non-empty string"
                )

        if isinstance(self.quantity, bool) or not isinstance(
            self.quantity, (int, float, Decimal)
        ):
            raise InvoiceCreationConfigError(
                "quantity must be a finite numeric value other than zero"
            )
        if isinstance(self.quantity, Decimal):
            quantity_is_finite = self.quantity.is_finite()
        elif isinstance(self.quantity, float):
            quantity_is_finite = math.isfinite(self.quantity)
        else:
            quantity_is_finite = True
        if not quantity_is_finite or self.quantity == 0:
            raise InvoiceCreationConfigError(
                "quantity must be a finite numeric value other than zero"
            )

        if not isinstance(self.tax_inclusive, bool):
            raise InvoiceCreationConfigError("tax_inclusive must be a bool")
        if self.amount_basis not in ("subtotal", "total") or not isinstance(
            self.amount_basis, str
        ):
            raise InvoiceCreationConfigError(
                "amount_basis must be 'subtotal' or 'total'"
            )
        if self.discount_policy not in ("ignore", "line_discount") or not isinstance(
            self.discount_policy, str
        ):
            raise InvoiceCreationConfigError(
                "discount_policy must be 'ignore' or 'line_discount'"
            )
