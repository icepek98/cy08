"""Explicit orchestration from Xero invoice records to the RPA executor."""

from __future__ import annotations

from automation import AutomationExecutor

from .api import XeroApiClient
from .idempotency import InvoiceIdempotencyStore
from .invoice_reader import read_xero_invoices


def run_xero_invoices(
    api_client: XeroApiClient,
    executor: AutomationExecutor | None = None,
    max_records: int | None = None,
) -> dict[str, list[dict[str, object]]]:
    """Read Xero invoices and optionally execute valid records.

    When max_records is set, only the first records up to that limit are sent
    to the executor. The returned rejections are not affected by the limit.
    """

    if max_records is not None and (
        isinstance(max_records, bool)
        or not isinstance(max_records, int)
        or max_records <= 0
    ):
        raise ValueError("max_records must be a positive integer")

    invoice_batch = read_xero_invoices(api_client)
    records = invoice_batch["records"]
    rejections = invoice_batch["rejections"]

    results: list[dict[str, object]] = []
    additional_rejections: list[dict[str, object]] = []
    if records:
        execution_records = records if max_records is None else records[:max_records]
        tenant_id = api_client.tenant_id
        if not isinstance(tenant_id, str) or not tenant_id.strip():
            raise ValueError("tenant_id must be a non-empty string")

        store = InvoiceIdempotencyStore()
        reserved: list[tuple[dict[str, object], str, str]] = []
        seen_invoice_ids: set[str] = set()
        for record_index, record in enumerate(execution_records, start=1):
            invoice_id = record.get("xero_invoice_id")
            if not isinstance(invoice_id, str) or not invoice_id.strip():
                additional_rejections.append(
                    {
                        "source": "xero",
                        "index": record_index,
                        "status": "REJECTED",
                        "message": "Invoice Xero sem identificador utilizável.",
                    }
                )
                continue
            if invoice_id in seen_invoice_ids:
                continue
            seen_invoice_ids.add(invoice_id)

            reservation = store.reserve_invoice(tenant_id, invoice_id)
            if reservation.reserved and reservation.attempt_token:
                reserved.append((record, invoice_id, reservation.attempt_token))

        if reserved:
            selected_executor = executor or AutomationExecutor()
            try:
                results = selected_executor.run(
                    records=[record for record, _, _ in reserved]
                )
            except Exception:
                for _, invoice_id, attempt_token in reserved:
                    store.release_invoice(tenant_id, invoice_id, attempt_token)
                raise

            results_by_row = {
                result.get("row"): result
                for result in results
                if isinstance(result.get("row"), int)
            }
            for index, (_, invoice_id, attempt_token) in enumerate(reserved, start=2):
                result = results_by_row.get(index)
                if result is not None and result.get("status") == "SUCCESS":
                    store.mark_success(tenant_id, invoice_id, attempt_token)
                else:
                    store.release_invoice(tenant_id, invoice_id, attempt_token)

    returned_rejections = [*rejections, *additional_rejections] if additional_rejections else rejections
    return {"results": results, "rejections": returned_rejections}
