"""Build an incomplete, non-sendable Xero Invoice draft from FieldQuo data."""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Mapping

from fieldquo.pdf_invoice_workflow import PDF_INVOICE_ORIGIN


class FieldQuoInvoiceAdapterError(ValueError):
    """Raised when FieldQuo invoice data cannot form a valid Xero draft."""


_CURRENCY_CODES = frozenset(
    """
    AED AFN ALL AMD ANG AOA ARS AUD AWG AZN BAM BBD BDT BGN BHD BIF BMD BND
    BOB BOV BRL BSD BTN BWP BYN BZD CAD CDF CHE CHF CHW CLF CLP CNY COP COU
    CRC CUC CUP CVE CZK DJF DKK DOP DZD EGP ERN ETB EUR FJD FKP GBP GEL GHS
    GIP GMD GNF GTQ GYD HKD HNL HTG HUF IDR ILS INR IQD IRR ISK JMD JOD JPY
    KES KGS KHR KMF KPW KRW KWD KYD KZT LAK LBP LKR LRD LSL LYD MAD MDL MGA
    MKD MMK MNT MOP MRU MUR MVR MWK MXN MXV MYR MZN NAD NGN NIO NOK NPR NZD
    OMR PAB PEN PGK PHP PKR PLN PYG QAR RON RSD RUB RWF SAR SBD SCR SDG SEK
    SGD SHP SLE SLL SOS SRD SSP STN SVC SYP SZL THB TJS TMT TND TOP TRY TTD
    TWD TZS UAH UGX USD USN UYI UYU UYW UZS VED VES VND VUV WST XAF XAG XAU
    XBA XBB XBC XBD XCD XCG XDR XOF XPD XPF XPT XSU XTS XUA XXX YER ZAR ZMW
    ZWG
    """.split()
)
_CURRENCY_CODE_PATTERN = re.compile(r"^[A-Z]{3}$")
_SOURCE_AMOUNT_FIELDS = ("subtotal", "discount", "tax", "total")
_LINE_ITEM_REQUIREMENT = (
    "Provide LineItems[].Description, Quantity, and UnitAmount."
)
_CREATION_REQUIREMENTS = (
    "Resolve ContactID for Contact.Name.",
    _LINE_ITEM_REQUIREMENT,
    "Configure LineItems[].AccountCode; FieldQuo does not provide it.",
    "Configure LineItems[].TaxType; FieldQuo does not provide it.",
)


def adapt_fieldquo_invoice(
    invoice: Mapping[str, Any],
    *,
    source_origin: str | None = None,
) -> dict[str, Any]:
    """Map a normalized FieldQuo invoice into an explicitly incomplete draft.

    The returned object is an internal planning model, not a request body that
    can be submitted to Xero. In particular, it has no ContactID.
    Source amounts and line values remain unchanged and are never rounded.
    """
    if not isinstance(invoice, Mapping):
        raise FieldQuoInvoiceAdapterError("Invoice FieldQuo inválida: esperado um mapeamento.")
    if source_origin is not None and source_origin != PDF_INVOICE_ORIGIN:
        raise FieldQuoInvoiceAdapterError("Origem da invoice FieldQuo inválida.")

    invoice_number = _required_text(invoice.get("invoice_number"), "Invoice number")
    client = _required_text(invoice.get("client"), "Client")
    currency = _validate_currency(invoice.get("currency"))
    issued = _required_date(invoice.get("issued"), "Issued")
    due = _optional_date(invoice.get("due"), "Due")

    source_amounts = {
        field_name: _source_amount(invoice.get(field_name), field_name)
        for field_name in _SOURCE_AMOUNT_FIELDS
    }
    line_items = (
        _normalize_line_items(invoice.get("line_items"))
        if "line_items" in invoice
        else None
    )
    if source_amounts["total"] is None:
        raise FieldQuoInvoiceAdapterError("Invoice FieldQuo sem Total válido.")
    if source_amounts["tax"] is not None and source_amounts["tax"] < 0:
        raise FieldQuoInvoiceAdapterError("Invoice FieldQuo com Tax negativo.")
    if source_amounts["total"] < 0:
        raise FieldQuoInvoiceAdapterError("Invoice FieldQuo com Total negativo.")

    line_item_details_available = _has_line_item_details(line_items)
    missing = tuple(
        requirement
        for requirement in _CREATION_REQUIREMENTS
        if (
            requirement != _LINE_ITEM_REQUIREMENT
            or not line_item_details_available
        )
    )
    source: dict[str, Any] = {
        "Status": _optional_text(invoice.get("status")),
        "Subtotal": source_amounts["subtotal"],
        "Discount": source_amounts["discount"],
        "Tax": source_amounts["tax"],
        "TaxApplied": _optional_text(invoice.get("tax_applied")),
        "Total": source_amounts["total"],
    }
    if source_origin is not None:
        source["Origin"] = source_origin
    if "line_items" in invoice:
        source["LineItems"] = line_items

    return {
        "InvoiceNumber": invoice_number,
        "Date": issued.isoformat(),
        "DueDate": due.isoformat() if due is not None else None,
        "Contact": {"Name": client},
        "CurrencyCode": currency,
        "Source": source,
        "CreationReadiness": {
            "ready": False,
            "missing": missing,
            "line_items_available": bool(line_items),
        },
    }


def _required_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise FieldQuoInvoiceAdapterError(
            f"Invoice FieldQuo sem {field_name} utilizável."
        )
    return value.strip()


def _optional_text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _validate_currency(value: Any) -> str:
    if (
        not isinstance(value, str)
        or not _CURRENCY_CODE_PATTERN.fullmatch(value)
        or value not in _CURRENCY_CODES
    ):
        raise FieldQuoInvoiceAdapterError(
            "Invoice FieldQuo com Currency inválida: esperado código ISO 4217 válido."
        )
    return value


def _required_date(value: Any, field_name: str) -> date:
    if isinstance(value, datetime) or not isinstance(value, date):
        raise FieldQuoInvoiceAdapterError(
            f"Invoice FieldQuo sem {field_name} normalizada."
        )
    return value


def _optional_date(value: Any, field_name: str) -> date | None:
    if value is None:
        return None
    return _required_date(value, field_name)


def _source_amount(value: Any, field_name: str) -> Decimal | None:
    if value is None:
        return None
    if not isinstance(value, Decimal) or not value.is_finite():
        raise FieldQuoInvoiceAdapterError(
            f"Invoice FieldQuo com {field_name.title()} inválido: esperado Decimal finito."
        )
    return value


def _normalize_line_items(value: Any) -> list[dict[str, Any]] | None:
    if value is None:
        return None
    if not isinstance(value, list):
        raise FieldQuoInvoiceAdapterError(
            "Invoice FieldQuo com Line items inválidos: esperado uma lista."
        )

    field_mapping = {
        "description": "Description",
        "quantity": "Quantity",
        "unit_price": "UnitAmount",
        "line_total": "LineAmount",
    }
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise FieldQuoInvoiceAdapterError(
                f"Invoice FieldQuo com Line item {index + 1} inválido: "
                "esperado um mapeamento."
            )
        normalized.append(
            {
                target: item.get(source)
                for source, target in field_mapping.items()
            }
        )
    return normalized


def _has_line_item_details(line_items: list[dict[str, Any]] | None) -> bool:
    return bool(line_items) and all(
        isinstance(item["Description"], str)
        and bool(item["Description"].strip())
        and item["Quantity"] is not None
        and item["UnitAmount"] is not None
        for item in line_items
    )
