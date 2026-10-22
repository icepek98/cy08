"""Translate Xero invoices into the internal transaction contract."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping


class XeroInvoiceAdapterError(ValueError):
    """Raised when an invoice cannot produce a valid internal transaction."""


XERO_SERIALIZED_DATE_RE = re.compile(
    r"^/Date\((-?\d+)([+-]\d{4})?\)/$"
)


def adapt_xero_invoice(invoice: Mapping[str, Any]) -> dict[str, object]:
    """Convert one Xero invoice to an internal transaction record."""

    if not isinstance(invoice, Mapping):
        raise XeroInvoiceAdapterError("Invoice Xero inválida: payload não é um mapeamento.")

    contact = invoice.get("Contact")
    if not isinstance(contact, Mapping):
        raise XeroInvoiceAdapterError("Invoice Xero sem contato.")

    client_id = _required_text(contact.get("ContactID"), "ContactID")
    client_name = _required_text(contact.get("Name"), "Contact.Name")
    amount = _parse_amount(invoice.get("Total"))
    transaction_date = _parse_date(invoice.get("Date"))
    description = _invoice_description(invoice)

    return {
        "client_id": client_id,
        "client_name": client_name,
        "amount": amount,
        "date": transaction_date,
        "description": description,
        "xero_invoice_id": invoice.get("InvoiceID"),
    }


def _required_text(value: Any, field_name: str) -> str:
    if value is None or not str(value).strip():
        raise XeroInvoiceAdapterError(f"Invoice Xero sem {field_name}.")
    return str(value).strip()


def _parse_amount(value: Any) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise XeroInvoiceAdapterError("Invoice Xero sem Total válido.")
    try:
        amount = Decimal(str(value).strip())
    except (InvalidOperation, AttributeError):
        raise XeroInvoiceAdapterError("Invoice Xero sem Total válido.") from None
    if not amount.is_finite() or amount <= 0:
        raise XeroInvoiceAdapterError("Invoice Xero sem Total válido.")
    return amount


def _parse_date(value: Any) -> str:
    if value is None or not str(value).strip():
        raise XeroInvoiceAdapterError("Invoice Xero sem Date.")

    raw_date = str(value).strip()
    serialized_match = XERO_SERIALIZED_DATE_RE.fullmatch(raw_date)
    if serialized_match:
        milliseconds = int(serialized_match.group(1))
        parsed = datetime.fromtimestamp(milliseconds / 1000, tz=timezone.utc)
        offset = serialized_match.group(2)
        if offset:
            sign = 1 if offset[0] == "+" else -1
            offset_minutes = int(offset[1:3]) * 60 + int(offset[3:5])
            parsed += sign * timedelta(minutes=offset_minutes)
        return parsed.date().isoformat()

    try:
        parsed = datetime.fromisoformat(raw_date.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            parsed = date.fromisoformat(raw_date[:10])
        except ValueError:
            raise XeroInvoiceAdapterError("Invoice Xero com Date inválida.") from None
    return parsed.isoformat()


def _invoice_description(invoice: Mapping[str, Any]) -> str:
    reference = invoice.get("Reference")
    if reference is not None and str(reference).strip():
        return str(reference).strip()

    line_items = invoice.get("LineItems")
    if isinstance(line_items, list):
        descriptions = [
            str(item.get("Description")).strip()
            for item in line_items
            if isinstance(item, Mapping)
            and item.get("Description") is not None
            and str(item.get("Description")).strip()
        ]
        if descriptions:
            return "; ".join(descriptions)

    invoice_number = invoice.get("InvoiceNumber")
    if isinstance(invoice_number, str) and invoice_number.strip():
        return invoice_number.strip()

    raise XeroInvoiceAdapterError("Invoice Xero sem descrição utilizável.")
