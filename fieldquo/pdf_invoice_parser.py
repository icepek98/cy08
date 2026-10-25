"""Conservative deterministic parsing of FieldQuo invoice text."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any


_FIELD_LABELS = {
    "invoice_number": r"invoice\s*(?:number|no\.?|#)",
    "client": r"(?:client|customer|bill\s+to)",
    "issued": r"(?:issued|issue\s+date|invoice\s+date)",
    "due": r"(?:payment\s+due|due(?:\s+date)?)",
    "currency": r"currency",
    "subtotal": r"subtotal",
    "tax": r"(?:tax|vat)",
    "discount": r"discount",
    "total": r"total",
}
_REQUIRED_FIELDS = ("invoice_number", "client", "issued", "currency", "total")
_LINE_FIELDS = ("description", "quantity", "unit_price", "line_total")
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
_DATE_FORMATS = ("%d/%m/%Y", "%d-%m-%Y")
_MONEY_TOKEN = re.compile(r"^[+-]?(?:\d[\d., ]*\d|\d)(?:\s*[A-Z]{3})?$")
_KNOWN_CURRENCY_PREFIX = re.compile(r"^(?:EUR|GBP|USD|CAD|AUD|NZD|CHF|JPY)\s*", re.I)
_CURRENCY_SYMBOL_PREFIX = re.compile(r"^[€£$¥]\s*")
_LABEL_PATTERNS = {
    name: re.compile(
        rf"^\s*(?:{label})\s*(?::\s*|[ \t]+)(.*?)\s*$",
        re.IGNORECASE,
    )
    for name, label in _FIELD_LABELS.items()
}
_DATE_TOKEN = re.compile(r"^\d{4}-\d{2}-\d{2}$|^\d{1,2}[/-]\d{1,2}[/-]\d{4}$")


@dataclass(frozen=True)
class ParsedFieldQuoInvoice:
    """Normalized invoice mapping and deterministic parsing diagnostics.

    ``complete`` means the parser found the minimum invoice-creation fields:
    invoice number, client, issue date, currency, total, and at least one line
    with description, quantity, and unit price. Optional source fields and line
    totals are still listed in ``missing_fields`` when they were not identified.
    """

    invoice: dict[str, Any]
    complete: bool
    missing_fields: tuple[str, ...]
    ambiguities: tuple[str, ...]
    rejections: tuple[str, ...]
    evidence: dict[str, tuple[str, ...]]
    source_text: str


def parse_fieldquo_invoice_text(text: str) -> ParsedFieldQuoInvoice:
    """Extract only explicitly labeled fields and recognizable delimited lines."""
    if not isinstance(text, str):
        raise TypeError("invoice text must be a string")

    invoice: dict[str, Any] = {
        "invoice_number": None,
        "client": None,
        "issued": None,
        "due": None,
        "currency": None,
        "subtotal": None,
        "tax": None,
        "discount": None,
        "total": None,
        "line_items": None,
    }
    evidence_lists: dict[str, list[str]] = {}
    ambiguities: list[str] = []
    rejections: list[str] = []
    conflicting_fields: set[str] = set()
    lines = text.splitlines()

    for line in lines:
        total_match = re.match(
            r"^\s*total\s*\(([A-Z]{3})\)\s*[: ]\s*"
            r"([+-]?(?:\d[\d., ]*\d|\d))\s*$",
            line,
            re.IGNORECASE,
        )
        if total_match:
            _collect_field(
                invoice, evidence_lists, ambiguities,
                conflicting_fields, "currency", total_match.group(1).upper()
            )
            _collect_field(
                invoice, evidence_lists, ambiguities,
                conflicting_fields, "total", total_match.group(2).strip()
            )
            continue

        for field_name, pattern in _LABEL_PATTERNS.items():
            match = pattern.match(line)
            if match and match.group(1).strip():
                _collect_field(
                    invoice,
                    evidence_lists,
                    ambiguities,
                    conflicting_fields,
                    field_name,
                    match.group(1).strip(),
                )

    for field_name in ("issued", "due"):
        raw = invoice[field_name]
        if raw is not None:
            parsed, error = _parse_date(raw)
            invoice[field_name] = parsed
            if error:
                ambiguities.append(f"{field_name}: {error}")

    for field_name in ("subtotal", "tax", "discount", "total"):
        raw = invoice[field_name]
        if raw is not None:
            parsed, error = _parse_money(raw)
            invoice[field_name] = parsed
            if error:
                ambiguities.append(f"{field_name}: {error}")

    currency = invoice["currency"]
    if currency is not None:
        normalized_currency = currency.upper()
        if (
            not _CURRENCY_CODE_PATTERN.fullmatch(normalized_currency)
            or normalized_currency not in _CURRENCY_CODES
        ):
            ambiguities.append("currency: código ISO 4217 não reconhecido.")
            invoice["currency"] = None
        else:
            invoice["currency"] = normalized_currency

    line_items, line_evidence, line_issues, table_found = _parse_line_table(
        lines
    )
    if table_found:
        invoice["line_items"] = line_items
        evidence_lists["line_items"] = line_evidence
        ambiguities.extend(line_issues)
    elif _looks_like_unrecognized_line_structure(lines):
        ambiguities.append(
            "line_items: estrutura de linhas detectada, mas cabeçalho/tabela "
            "não reconhecido com segurança."
        )

    missing_fields = _missing_fields(invoice)
    if not text.strip() or not any(
        invoice[field] is not None for field in _FIELD_LABELS
    ):
        rejections.append(
            "Texto vazio ou sem campos de invoice reconhecidos explicitamente."
        )

    line_items_complete = bool(invoice["line_items"]) and all(
        isinstance(item["description"], str)
        and bool(item["description"].strip())
        and item["quantity"] is not None
        and item["unit_price"] is not None
        for item in invoice["line_items"] or []
    )
    complete = (
        not rejections
        and not ambiguities
        and all(invoice[field] is not None for field in _REQUIRED_FIELDS)
        and line_items_complete
    )
    return ParsedFieldQuoInvoice(
        invoice=invoice,
        complete=complete,
        missing_fields=tuple(missing_fields),
        ambiguities=tuple(dict.fromkeys(ambiguities)),
        rejections=tuple(rejections),
        evidence={
            field: tuple(values) for field, values in evidence_lists.items()
        },
        source_text=text,
    )


def _collect_field(
    invoice: dict[str, Any],
    evidence: dict[str, list[str]],
    ambiguities: list[str],
    conflicting_fields: set[str],
    field_name: str,
    raw_value: str,
) -> None:
    evidence.setdefault(field_name, []).append(raw_value)
    if field_name in conflicting_fields:
        return
    current = invoice[field_name]
    if current is None:
        invoice[field_name] = raw_value
        return
    if current != raw_value:
        invoice[field_name] = None
        conflicting_fields.add(field_name)
        ambiguities.append(f"{field_name}: valores conflitantes no documento.")


def _parse_date(value: str) -> tuple[date | None, str | None]:
    token = value.strip()

    # Datas em inglês por extenso, como 15 January 2026.
    candidate = token.replace(",", "")
    for fmt in ("%d %B %Y", "%d %b %Y", "%B %d %Y", "%b %d %Y"):
        try:
            return datetime.strptime(candidate, fmt).date(), None
        except ValueError:
            pass

    if not _DATE_TOKEN.fullmatch(token):
        return None, "formato de data não reconhecido."

    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", token):
            return date.fromisoformat(token), None

        first, second, _ = re.split(r"[/-]", token)
        if int(first) <= 12 and int(second) <= 12:
            return None, "data numérica ambígua; formato não determinado."

        for date_format in _DATE_FORMATS:
            try:
                return datetime.strptime(token, date_format).date(), None
            except ValueError:
                continue
    except ValueError:
        return None, "data inválida."

    return None, "data inválida."

def _parse_money(value: str) -> tuple[Decimal | None, str | None]:
    token = value.strip()
    token = _KNOWN_CURRENCY_PREFIX.sub("", token)
    token = _CURRENCY_SYMBOL_PREFIX.sub("", token)
    if not _MONEY_TOKEN.fullmatch(token):
        return None, "valor monetário não reconhecido."
    token = re.sub(r"\s*[A-Z]{3}$", "", token, flags=re.IGNORECASE).strip()
    token = token.replace(" ", "")
    if "," in token and "." in token:
        decimal_separator = "," if token.rfind(",") > token.rfind(".") else "."
        grouping_separator = "." if decimal_separator == "," else ","
        integer_part, fraction = token.rsplit(decimal_separator, 1)
        if len(fraction) not in (1, 2) or not _valid_grouping(
            integer_part, grouping_separator
        ):
            return None, "separadores monetários ambíguos ou inválidos."
        token = integer_part.replace(grouping_separator, "") + "." + fraction
    elif "," in token or "." in token:
        separator = "," if "," in token else "."
        if token.count(separator) > 1:
            groups = token.split(separator)
            if not all(group.isdigit() for group in groups):
                return None, "separadores monetários inválidos."
            if not all(len(group) == 3 for group in groups[1:]):
                return None, "separadores monetários ambíguos."
            token = "".join(groups)
        else:
            integer_part, fraction = token.split(separator)
            if not integer_part.lstrip("+-").isdigit() or not fraction.isdigit():
                return None, "separadores monetários inválidos."
            if len(fraction) == 3:
                return None, "separador único com três dígitos finais é ambíguo."
            token = integer_part + "." + fraction
    try:
        amount = Decimal(token)
    except InvalidOperation:
        return None, "valor monetário não reconhecido."
    if not amount.is_finite():
        return None, "valor monetário não finito."
    return amount, None


def _valid_grouping(integer_part: str, separator: str) -> bool:
    signless = integer_part.lstrip("+-")
    groups = signless.split(separator)
    return (
        bool(groups)
        and groups[0].isdigit()
        and 1 <= len(groups[0]) <= 3
        and all(len(group) == 3 and group.isdigit() for group in groups[1:])
    )


def _parse_line_table(
    lines: list[str],
) -> tuple[list[dict[str, Any]], list[str], list[str], bool]:
    for header_index, line in enumerate(lines):
        separator = "\t" if "\t" in line else "|" if "|" in line else None
        if separator is None:
            continue
        headers = [_normalize_header(cell) for cell in line.split(separator)]
        indexes = {
            "description": _find_header(headers, {"description"}),
            "quantity": _find_header(headers, {"quantity", "qty"}),
            "unit_price": _find_header(
                headers, {"unitprice", "rate", "unitamount"}
            ),
            "line_total": _find_header(
                headers, {"linetotal", "amount", "total"}
            ),
        }
        required_columns = ("description", "quantity", "unit_price", "line_total")
        if any(indexes[field] is None for field in required_columns):
            continue

        items: list[dict[str, Any]] = []
        evidence: list[str] = [line]
        issues: list[str] = []
        for row_number, row in enumerate(
            lines[header_index + 1 :], start=header_index + 2
        ):
            if not row.strip():
                if items:
                    break
                continue
            if separator not in row:
                if items:
                    break
                continue
            cells = [cell.strip() for cell in row.split(separator)]
            if len(cells) != len(headers):
                issues.append(
                    f"line_items: linha {row_number} tem número de colunas diferente do cabeçalho."
                )
                evidence.append(row)
                continue
            description_index = indexes["description"]
            quantity_index = indexes["quantity"]
            unit_price_index = indexes["unit_price"]
            line_total_index = indexes["line_total"]
            assert (
                description_index is not None
                and quantity_index is not None
                and unit_price_index is not None
                and line_total_index is not None
            )
            description = cells[description_index]
            quantity_raw = cells[quantity_index]
            unit_price_raw = cells[unit_price_index]
            line_total_raw = cells[line_total_index]
            if _is_summary_row(cells):
                break
            quantity, quantity_error = _parse_money(quantity_raw)
            unit_price, unit_error = _parse_money(unit_price_raw)
            line_total, total_error = _parse_money(line_total_raw)
            item = {
                "description": description or None,
                "quantity": quantity,
                "unit_price": unit_price,
                "line_total": line_total,
            }
            items.append(item)
            evidence.append(row)
            for field_name, error in (
                ("quantity", quantity_error),
                ("unit_price", unit_error),
                ("line_total", total_error),
            ):
                if error:
                    issues.append(
                        f"line_items[{len(items) - 1}].{field_name}: "
                        f"{error} (linha {row_number})."
                    )
            if not description:
                issues.append(
                    f"line_items[{len(items) - 1}].description ausente "
                    f"(linha {row_number})."
                )
        if not items:
            issues.append("line_items: tabela reconhecida, mas sem linhas legíveis.")
        return items, evidence, issues, True
    aligned = _parse_aligned_line_table(lines)
    if aligned is not None:
        return aligned
    return [], [], [], False


def _parse_aligned_line_table(
    lines: list[str],
) -> tuple[list[dict[str, Any]], list[str], list[str], bool] | None:
    header_index = None

    for index, line in enumerate(lines):
        if (
            re.search(r"\bdescription\b", line, re.I)
            and re.search(r"\bqty\b", line, re.I)
            and re.search(r"\bunit\s*price\b", line, re.I)
            and re.search(r"\bamount\b", line, re.I)
        ):
            header_index = index
            break

    if header_index is None:
        return None

    items: list[dict[str, Any]] = []
    evidence = [lines[header_index]]
    issues: list[str] = []

    # Descrição seguida de quantidade, preço unitário e total da linha.
    row_pattern = re.compile(
        r"^\s*(.*?)\s+"
        r"(\d+(?:[.,]\d+)?)\s+"
        r"([+-]?\d[\d.,]*)\s+"
        r"([+-]?\d[\d.,]*)\s*$"
    )

    for row_number, row in enumerate(
        lines[header_index + 1:], start=header_index + 2
    ):
        if not row.strip():
            if items:
                break
            continue

        if re.match(r"^\s*total\b", row, re.I):
            break

        match = row_pattern.fullmatch(row)
        if not match:
            if items:
                # Não atravessa texto posterior à tabela.
                break
            continue

        description, qty_raw, price_raw, amount_raw = match.groups()
        quantity, quantity_error = _parse_money(qty_raw)
        unit_price, price_error = _parse_money(price_raw)
        line_total, amount_error = _parse_money(amount_raw)

        item = {
            "description": description.strip() or None,
            "quantity": quantity,
            "unit_price": unit_price,
            "line_total": line_total,
        }
        items.append(item)
        evidence.append(row)

        for field_name, error in (
            ("quantity", quantity_error),
            ("unit_price", price_error),
            ("line_total", amount_error),
        ):
            if error:
                issues.append(
                    f"line_items[{len(items) - 1}].{field_name}: "
                    f"{error} (linha {row_number})."
                )

        if not item["description"]:
            issues.append(
                f"line_items[{len(items) - 1}].description ausente "
                f"(linha {row_number})."
            )

    if not items:
        return (
            [],
            evidence,
            ["line_items: cabeçalho reconhecido, mas sem linhas legíveis."],
            True,
        )

    return items, evidence, issues, True


def _normalize_header(value: str) -> str:
    return re.sub(r"[^a-z]", "", value.casefold())


def _find_header(headers: list[str], aliases: set[str]) -> int | None:
    matches = [index for index, value in enumerate(headers) if value in aliases]
    return matches[0] if len(matches) == 1 else None


def _is_summary_row(cells: list[str]) -> bool:
    first = re.sub(r"[^a-z]", "", cells[0].casefold()) if cells else ""
    return first in {"subtotal", "tax", "vat", "discount", "total", "balance"}


def _looks_like_unrecognized_line_structure(lines: list[str]) -> bool:
    return any(
        "|" in line
        or "\t" in line
        or re.search(
            r"\b(?:quantity|qty|unit\s+price|line\s+total)\b", line, re.I
        )
        for line in lines
    )


def _missing_fields(invoice: dict[str, Any]) -> list[str]:
    missing = [name for name in _FIELD_LABELS if invoice[name] is None]
    line_items = invoice["line_items"]
    if not line_items:
        missing.append("line_items")
    else:
        for index, item in enumerate(line_items):
            missing.extend(
                f"line_items[{index}].{field}"
                for field in _LINE_FIELDS
                if item[field] is None
            )
    return missing
