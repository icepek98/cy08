from pathlib import Path

from .csv_reader import read_csv_transactions
from .input_contract import validate_input_path
from .xlsx_reader import read_xlsx_transactions


def read_transactions(file_path: str | Path) -> list[dict[str, str]]:
    path = validate_input_path(file_path)
    if path.suffix.lower() == ".csv":
        return read_csv_transactions(path)
    if path.suffix.lower() == ".xlsx":
        return read_xlsx_transactions(path)
    raise ValueError("Extensão de arquivo não suportada.")
