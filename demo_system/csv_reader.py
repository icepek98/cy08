import csv
from pathlib import Path

from .input_contract import REQUIRED_COLUMNS, validate_input_path, validate_transaction_row


def read_csv_transactions(file_path: str | Path) -> list[dict[str, str]]:
    path = validate_input_path(file_path)
    if path.suffix.lower() != ".csv":
        raise ValueError("Processamento XLSX ainda não foi implementado.")

    with path.open("r", encoding="utf-8-sig", newline="") as csv_file:
        reader = csv.DictReader(csv_file)
        headers = reader.fieldnames or []
        missing = [column for column in REQUIRED_COLUMNS if column not in headers]
        if missing:
            raise ValueError(
                f"Cabeçalho inválido: colunas ausentes: {', '.join(missing)}."
            )

        records = []
        for line_number, row in enumerate(reader, start=2):
            try:
                validate_transaction_row(row)
            except ValueError as exc:
                raise ValueError(f"Erro na linha {line_number}: {exc}") from exc
            records.append({column: row[column].strip() for column in REQUIRED_COLUMNS})

    return records
