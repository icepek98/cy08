"""Build a Xero invoice-creation payload without performing network I/O."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import Any, Literal

from xero.contact_resolver import XeroContactResolution
from xero.invoice_creation_config import InvoiceCreationConfig
from xero.line_item_builder import (
    UnsupportedDiscountRepresentationError,
    build_xero_line_item,
)


LineAmountTypes = Literal["Exclusive", "Inclusive"]


class InvoicePayloadBuilderError(ValueError):
    """Raised when an invoice draft cannot form a valid Xero payload."""


class InvalidInvoiceDraftError(InvoicePayloadBuilderError):
    """Raised when required draft fields are missing or malformed."""


class MissingContactIDError(InvoicePayloadBuilderError):
    """Raised when contact resolution did not produce a usable Xero ID."""


class EmptyLineItemsError(InvoicePayloadBuilderError):
    """Raised when no valid LineItem can be included in the payload."""


def build_xero_invoice_payload(
    draft: Mapping[str, Any],
    config: InvoiceCreationConfig,
    contact_resolution: XeroContactResolution,
) -> dict[str, object]:
    """Build one ACCREC invoice payload from an adapter draft and resolved contact.

    The draft is expected to come from ``adapt_fieldquo_invoice`` and therefore
    contains the normalized FieldQuo values under its ``Source`` key.
    """
    if not isinstance(draft, Mapping):
        raise InvalidInvoiceDraftError("Invoice draft must be a mapping.")
    if not isinstance(config, InvoiceCreationConfig):
        raise InvoicePayloadBuilderError(
            "config must be an InvoiceCreationConfig."
        )

    contact_id = _resolved_contact_id(contact_resolution)
    _validate_draft(draft, config)

    line_items = _build_line_items(draft, config)

    invoice: dict[str, object] = {
        "Type": "ACCREC",
        "Contact": {"ContactID": contact_id},
        "InvoiceNumber": draft["InvoiceNumber"],
        "Date": draft["Date"],
        "CurrencyCode": draft["CurrencyCode"],
        "LineAmountTypes": (
            "Inclusive" if config.tax_inclusive else "Exclusive"
        ),
        "LineItems": line_items,
    }

    due_date = draft.get("DueDate")
    if due_date is not None:
        invoice["DueDate"] = due_date

    return {"Invoices": [invoice]}


def _build_line_items(
    draft: Mapping[str, Any],
    config: InvoiceCreationConfig,
) -> list[dict[str, Any]]:
    source = draft["Source"]
    source_line_items = source.get("LineItems")
    if source_line_items is None:
        line_item = build_xero_line_item(draft, config)
        if not isinstance(line_item, Mapping) or not line_item:
            raise EmptyLineItemsError("Invoice payload must contain a LineItem.")
        return [dict(line_item)]
    if not isinstance(source_line_items, list):
        raise InvalidInvoiceDraftError(
            "Invoice draft Source LineItems must be a list or None."
        )
    if not source_line_items:
        raise EmptyLineItemsError("Invoice payload must contain a LineItem.")
    if config.discount_policy == "line_discount":
        raise UnsupportedDiscountRepresentationError(
            "FieldQuo invoice discount cannot be represented safely as a "
            "line-level discount."
        )

    line_items: list[dict[str, Any]] = []
    for index, source_line_item in enumerate(source_line_items):
        if not isinstance(source_line_item, Mapping):
            raise InvalidInvoiceDraftError(
                f"Invoice draft LineItems[{index}] must be a mapping."
            )
        description = source_line_item.get("Description")
        quantity = source_line_item.get("Quantity")
        unit_amount = source_line_item.get("UnitAmount")
        if not isinstance(description, str) or not description.strip():
            raise InvalidInvoiceDraftError(
                f"Invoice draft LineItems[{index}].Description is required."
            )
        if quantity is None:
            raise InvalidInvoiceDraftError(
                f"Invoice draft LineItems[{index}].Quantity is required."
            )
        if unit_amount is None:
            raise InvalidInvoiceDraftError(
                f"Invoice draft LineItems[{index}].UnitAmount is required."
            )

        line_item: dict[str, Any] = {
            "Description": description,
            "Quantity": quantity,
            "UnitAmount": unit_amount,
            "AccountCode": config.account_code,
            "TaxType": config.tax_type,
        }
        line_amount = source_line_item.get("LineAmount")
        if line_amount is not None:
            line_item["LineAmount"] = line_amount
        line_items.append(line_item)

    return line_items


def _resolved_contact_id(
    resolution: XeroContactResolution,
) -> str:
    if not isinstance(resolution, XeroContactResolution):
        raise MissingContactIDError(
            "A XeroContactResolution is required to build an invoice."
        )
    if resolution.status != "matched":
        raise MissingContactIDError(
            f"Contact resolution status is '{resolution.status}', not 'matched'."
        )

    contact_id = resolution.contact_id
    if not isinstance(contact_id, str) or not contact_id.strip():
        raise MissingContactIDError(
            "Contact resolution did not provide a usable ContactID."
        )
    return contact_id.strip()


def _validate_draft(
    draft: Mapping[str, Any],
    config: InvoiceCreationConfig,
) -> None:
    for field_name in ("InvoiceNumber", "Date", "CurrencyCode"):
        value = draft.get(field_name)
        if not isinstance(value, str) or not value.strip():
            raise InvalidInvoiceDraftError(
                f"Invoice draft is missing a usable {field_name}."
            )

    try:
        date.fromisoformat(draft["Date"])
    except ValueError:
        raise InvalidInvoiceDraftError(
            "Invoice draft Date must be a normalized ISO date."
        ) from None

    due_date = draft.get("DueDate")
    if due_date is not None:
        if not isinstance(due_date, str) or not due_date.strip():
            raise InvalidInvoiceDraftError(
                "Invoice draft DueDate must be a normalized ISO date or None."
            )
        try:
            date.fromisoformat(due_date)
        except ValueError:
            raise InvalidInvoiceDraftError(
                "Invoice draft DueDate must be a normalized ISO date."
            ) from None

    source = draft.get("Source")
    if not isinstance(source, Mapping):
        raise InvalidInvoiceDraftError(
            "Invoice draft is missing FieldQuo Source amounts."
        )

    amount_field = "Subtotal" if config.amount_basis == "subtotal" else "Total"
    if amount_field not in source:
        raise InvalidInvoiceDraftError(
            f"Invoice draft is missing the configured monetary basis: {amount_field}."
        )
