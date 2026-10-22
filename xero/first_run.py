"""Explicit, single-record orchestration for the first Xero execution."""

from __future__ import annotations

from automation import AutomationExecutor

from .api import XeroApiClient
from .idempotency import InvoiceIdempotencyStore
from .invoice_reader import read_xero_invoices


def run_first_xero_invoice(
    api_client: XeroApiClient | None,
    confirmed: bool,
    preselected_record: dict[str, object] | None = None,
    tenant_id: str | None = None,
) -> dict[str, object]:
    """Execute one confirmed Xero record, reading from Xero when not preselected."""

    if not isinstance(confirmed, bool):
        raise TypeError("confirmed must be a bool")

    if preselected_record is not None:
        records = [preselected_record]
        rejections: list[dict[str, object]] = []
    else:
        if api_client is None:
            raise ValueError("api_client is required when no preview record is provided")
        invoice_batch = read_xero_invoices(api_client)
        records = invoice_batch["records"]
        rejections = invoice_batch["rejections"]
    results: list[dict[str, object]] = []
    execution_batch_size = 0

    if confirmed and records:
        first_record = records[0]
        if not isinstance(tenant_id, str) or not tenant_id.strip():
            if api_client is not None:
                tenant_id = api_client.tenant_id
        if not isinstance(tenant_id, str) or not tenant_id.strip():
            raise ValueError("tenant_id must be a non-empty string")

        invoice_id = first_record.get("xero_invoice_id")
        if not isinstance(invoice_id, str) or not invoice_id.strip():
            rejections = [
                *rejections,
                {
                    "source": "xero",
                    "index": 1,
                    "status": "REJECTED",
                    "message": "Invoice Xero sem identificador utilizável.",
                },
            ]
        else:
            store = InvoiceIdempotencyStore()
            reservation = store.reserve_invoice(tenant_id, invoice_id)
            if reservation.reserved and reservation.attempt_token:
                executor = AutomationExecutor()
                try:
                    results = executor.run(records=[first_record])
                except Exception:
                    store.release_invoice(
                        tenant_id, invoice_id, reservation.attempt_token
                    )
                    raise

                matching_result = next(
                    (
                        result
                        for result in results
                        if result.get("row") == 2
                    ),
                    None,
                )
                if (
                    matching_result is not None
                    and matching_result.get("status") == "SUCCESS"
                ):
                    store.mark_success(
                        tenant_id, invoice_id, reservation.attempt_token
                    )
                else:
                    store.release_invoice(
                        tenant_id, invoice_id, reservation.attempt_token
                    )
                execution_batch_size = 1

    return {
        "records": records,
        "rejections": rejections,
        "results": results,
        "invoice_count": len(records) + len(rejections),
        "valid_record_count": len(records),
        "rejection_count": len(rejections),
        "execution_batch_size": execution_batch_size,
    }
