import threading
from pathlib import Path
from typing import Callable, Mapping, Protocol

from playwright.sync_api import sync_playwright

from demo_system.input_contract import validate_input_path, validate_transaction_row
from demo_system.transaction_reader import read_transactions
from report import write_results_csv
from rpa.browser import chromium_browser
from rpa.flows.accounting_demo import AccountingDemoFlow

ProgressCallback = Callable[[int, int, dict[str, object]], None]


class AutomationRunner(Protocol):
    def run(
        self,
        records: list[Mapping[str, object]] | None = None,
        progress_callback: ProgressCallback | None = None,
        cancel_event: threading.Event | None = None,
    ) -> list[dict[str, object]]:
        ...


class DemoAutomationExecutor:
    SUPPORTED_SUFFIXES = {".csv", ".xlsx"}

    def __init__(self, file_path: str | None = None):
        self.file_path = file_path

    def validate_input_file(self) -> Path | None:
        if not self.file_path:
            return None
        return validate_input_path(self.file_path)

    def _load_transactions(self) -> list[dict[str, str]]:
        path = self.validate_input_file()
        if path is None:
            raise ValueError("Selecione um arquivo CSV.")
        records = read_transactions(path)
        if not records:
            raise ValueError("O arquivo não contém registros.")
        return records

    def run(
        self,
        records: list[Mapping[str, object]] | None = None,
        progress_callback: ProgressCallback | None = None,
        cancel_event: threading.Event | None = None,
    ) -> list[dict[str, object]]:
        if records is None:
            records = self._load_transactions()
        if not records:
            raise ValueError("Nenhum registro para processar.")

        cancel_event = cancel_event or threading.Event()
        results = []

        def report(current: int, result: dict[str, object]) -> None:
            results.append(result)
            if progress_callback:
                progress_callback(current, len(records), result)

        if cancel_event.is_set():
            for current, transaction in enumerate(records, start=1):
                report(
                    current,
                    self._cancelled_result(current, transaction),
                )
            write_results_csv(results)
            return results

        preparation_error = None
        processing_started = False
        try:
            with sync_playwright() as playwright:
                with chromium_browser(playwright) as browser:
                    try:
                        page = browser.new_page()
                        flow = AccountingDemoFlow()
                        flow.login(page)
                    except Exception as exc:
                        preparation_error = exc

                    if preparation_error is None:
                        processing_started = True
                        for row_number, transaction in enumerate(records, start=2):
                            current = row_number - 1
                            if cancel_event.is_set():
                                for remaining_number, remaining in enumerate(
                                    records[current - 1 :], start=current
                                ):
                                    report(
                                        remaining_number,
                                        self._cancelled_result(
                                            remaining_number, remaining
                                        ),
                                    )
                                break

                            result = {
                                "row": row_number,
                                "client_id": str(transaction.get("client_id", "")),
                                "client_name": str(transaction.get("client_name", "")),
                                "status": "ERROR",
                                "message": "",
                            }
                            try:
                                validate_transaction_row(transaction)
                                flow.create_transaction(page, transaction)
                                result["status"] = "SUCCESS"
                                result["message"] = "Transaction successfully created"
                            except Exception as exc:
                                result["message"] = str(exc)
                            report(current, result)

                            if cancel_event.is_set():
                                for remaining_number, remaining in enumerate(
                                    records[current:], start=current + 1
                                ):
                                    report(
                                        remaining_number,
                                        self._cancelled_result(
                                            remaining_number, remaining
                                        ),
                                    )
                                break
        except Exception as exc:
            if processing_started:
                raise
            preparation_error = exc

        if preparation_error is not None:
            message = f"Falha na preparação do RPA: {preparation_error}"
            for current, transaction in enumerate(records, start=1):
                if cancel_event.is_set():
                    report(current, self._cancelled_result(current, transaction))
                else:
                    report(
                        current,
                        {
                            "row": current + 1,
                            "client_id": str(transaction.get("client_id", "")),
                            "client_name": str(transaction.get("client_name", "")),
                            "status": "ERROR",
                            "message": message,
                        },
                    )

        write_results_csv(results)
        return results

    @staticmethod
    def _cancelled_result(
        row_number: int, transaction: Mapping[str, object]
    ) -> dict[str, object]:
        return {
            "row": row_number + 1,
            "client_id": str(transaction.get("client_id", "")),
            "client_name": str(transaction.get("client_name", "")),
            "status": "CANCELLED",
            "message": "Cancelamento solicitado antes do processamento.",
        }


AutomationExecutor = DemoAutomationExecutor
