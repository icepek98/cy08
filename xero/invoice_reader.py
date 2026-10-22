"""Batch reader for adapting Xero invoices to internal transactions."""

from __future__ import annotations

from .api import XeroApiClient
from .invoices_adapter import XeroInvoiceAdapterError, adapt_xero_invoice


def read_xero_invoices(api_client: XeroApiClient) -> dict[str, list[dict[str, object]]]:
    """Read and adapt invoices, preserving valid records and reporting rejections."""

    invoices = api_client.get_invoices()
    records: list[dict[str, object]] = []
    rejections: list[dict[str, object]] = []

    for index, invoice in enumerate(invoices):
        try:
            records.append(adapt_xero_invoice(invoice))
        except XeroInvoiceAdapterError as exc:
            rejections.append(
                {
                    "source": "xero",
                    "index": index,
                    "status": "REJECTED",
                    "message": str(exc),
                }
            )

    return {"records": records, "rejections": rejections}
