from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Mapping


REQUIRED_COLUMNS = (
    "client_id",
    "client_name",
    "amount",
    "date",
    "description",
)
SUPPORTED_SUFFIXES = {".csv", ".xlsx"}


def validate_input_path(file_path: str | Path) -> Path:
    path = Path(file_path).expanduser()
    if not path.is_file():
        raise FileNotFoundError(f"Arquivo não encontrado: {path}")
    if path.suffix.lower() not in SUPPORTED_SUFFIXES:
        raise ValueError("Formato não suportado. Use .csv ou .xlsx.")
    return path


def validate_transaction_row(row: Mapping[str, object]) -> None:
    missing = [column for column in REQUIRED_COLUMNS if not str(row.get(column, "")).strip()]
    if missing:
        raise ValueError(f"Colunas obrigatórias sem valor: {', '.join(missing)}")

    try:
        amount = Decimal(str(row["amount"]))
    except (InvalidOperation, KeyError, TypeError):
        raise ValueError("amount deve ser um número.") from None
    if amount <= 0:
        raise ValueError("amount deve ser maior que zero.")

    try:
        date.fromisoformat(str(row["date"]))
    except (TypeError, ValueError, KeyError):
        raise ValueError("date deve usar o formato YYYY-MM-DD.") from None
