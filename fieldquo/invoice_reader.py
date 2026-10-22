import csv
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any


FIELDQUO_INVOICE_COLUMNS = (
    "Invoice number",
    "Issued",
    "Date taken from",
    "Due",
    "Client",
    "Status",
    "Version",
    "Currency",
    "Subtotal",
    "Discount",
    "Tax",
    "Tax applied",
    "Total",
    "Paid to date",
    "Received in range",
    "Balance",
)

_FIELD_NAMES = {
    "Invoice number": "invoice_number",
    "Issued": "issued",
    "Date taken from": "date_taken_from",
    "Due": "due",
    "Client": "client",
    "Status": "status",
    "Version": "version",
    "Currency": "currency",
    "Subtotal": "subtotal",
    "Discount": "discount",
    "Tax": "tax",
    "Tax applied": "tax_applied",
    "Total": "total",
    "Paid to date": "paid_to_date",
    "Received in range": "received_in_range",
    "Balance": "balance",
}
_DATE_COLUMNS = {"Issued", "Date taken from", "Due"}
_MONEY_COLUMNS = {
    "Subtotal",
    "Discount",
    "Tax",
    "Total",
    "Paid to date",
    "Received in range",
    "Balance",
}
_DATE_FORMATS = ("%d/%m/%Y", "%Y/%m/%d", "%d-%m-%Y")


def _parse_date(value: str, column: str) -> date | None:
    value = value.strip()
    if not value:
        return None

    try:
        return date.fromisoformat(value)
    except ValueError:
        pass

    try:
        return datetime.fromisoformat(value).date()
    except ValueError:
        pass

    for date_format in _DATE_FORMATS:
        try:
            return datetime.strptime(value, date_format).date()
        except ValueError:
            continue

    raise ValueError(
        f"'{column}' deve ser uma data ISO (YYYY-MM-DD) ou uma data válida no formato DD/MM/YYYY."
    )


def _parse_money(value: str | None, column: str) -> Decimal | None:
    if value is None or not value.strip():
        return None

    try:
        amount = Decimal(value.strip())
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(f"'{column}' deve conter um valor monetário válido.") from None

    if not amount.is_finite():
        raise ValueError(f"'{column}' deve conter um valor monetário finito.")
    return amount


def _normalize_record(row: dict[str | None, str | None]) -> dict[str, Any]:
    if None in row:
        raise ValueError("A linha contém mais valores do que o cabeçalho.")

    record: dict[str, Any] = {}
    for source_column in FIELDQUO_INVOICE_COLUMNS:
        value = row.get(source_column)
        field_name = _FIELD_NAMES[source_column]
        if source_column in _DATE_COLUMNS:
            record[field_name] = _parse_date(value or "", source_column)
        elif source_column in _MONEY_COLUMNS:
            record[field_name] = _parse_money(value, source_column)
        else:
            record[field_name] = (value or "").strip()
    return record


def read_fieldquo_invoices(file_path: str | Path) -> dict[str, list[dict[str, Any]]]:
    """Read a FieldQuo invoices CSV into normalized records and row rejections.

    Dates are returned as ``datetime.date`` objects, monetary values as
    ``Decimal`` objects, and empty optional date/money cells as ``None``.
    Rejection indexes are physical CSV line numbers, including the header line.
    """
    path = Path(file_path).expanduser()
    if not path.is_file():
        raise FileNotFoundError(f"Arquivo não encontrado: {path}")

    records: list[dict[str, Any]] = []
    rejections: list[dict[str, Any]] = []

    with path.open("r", encoding="utf-8-sig", newline="") as csv_file:
        reader = csv.DictReader(csv_file)
        if reader.fieldnames is None:
            return {"records": records, "rejections": rejections}

        headers = [header.strip() for header in reader.fieldnames]
        if len(headers) != len(set(headers)):
            raise ValueError("Cabeçalho inválido: há nomes de colunas duplicados.")

        missing = [column for column in FIELDQUO_INVOICE_COLUMNS if column not in headers]
        unexpected = [column for column in headers if column not in _FIELD_NAMES]
        if missing or unexpected:
            details = []
            if missing:
                details.append(f"colunas ausentes: {', '.join(missing)}")
            if unexpected:
                details.append(f"colunas não reconhecidas: {', '.join(unexpected)}")
            raise ValueError(f"Cabeçalho inválido: {'; '.join(details)}.")

        reader.fieldnames = headers
        for line_number, row in enumerate(reader, start=2):
            try:
                records.append(_normalize_record(row))
            except ValueError as exc:
                rejections.append({
                    "source": "fieldquo",
                    "index": line_number,
                    "status": "REJECTED",
                    "message": str(exc),
                })

    return {"records": records, "rejections": rejections}
