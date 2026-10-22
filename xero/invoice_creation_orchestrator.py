"""Orchestrate the local preparation of a FieldQuo invoice for Xero."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal, DecimalException, localcontext
from typing import Any, Protocol

from xero.contact_resolver import (
    XeroContactResolutionError,
    resolve_xero_contact_id,
)
from xero.fieldquo_invoice_idempotency import (
    FieldQuoInvoiceIdempotencyStore,
    FieldQuoInvoiceOperation,
    OperationState,
)
from xero.fieldquo_invoice_adapter import adapt_fieldquo_invoice
from xero.invoice_creation_config import InvoiceCreationConfig
from xero.invoice_payload_builder import build_xero_invoice_payload
from fieldquo.pdf_invoice_workflow import (
    PDF_INVOICE_ORIGIN,
    PDFInvoiceWorkflowResult,
)


class InvoiceCreationGuardError(ValueError):
    """Raised when a PDF-origin invoice is not safe to send to Xero."""


class XeroInvoiceCreationClient(Protocol):
    """Injected client boundary for Xero invoice creation."""

    def create_invoice(
        self, payload: dict[str, object], idempotency_key: str | None = None
    ) -> Any:
        """Accept a prepared payload and return the client's result."""
        ...


class InvoiceCreationClientError(RuntimeError):
    """Raised when the invoice-creation client fails, without leaking details."""


class InvoiceCreationLedgerError(RuntimeError):
    """Raised when the ledger cannot persist an invoice-creation outcome."""


@dataclass(frozen=True)
class InvoiceCreationDeferredResult:
    """Result when creation is skipped or not confirmed by Xero."""

    status: str
    operation: FieldQuoInvoiceOperation
    response: Any = None


def create_fieldquo_invoice_in_xero(
    invoice: Mapping[str, Any],
    contacts: Iterable[Mapping[str, Any]] | None,
    config: InvoiceCreationConfig,
    client: XeroInvoiceCreationClient,
    *,
    source_identity: str,
    pdf_originated: bool,
    ledger: FieldQuoInvoiceIdempotencyStore | None = None,
    _source_origin: str | None = None,
) -> Any:
    """Prepare and idempotently delegate one caller-identified invoice.

    Callers must identify PDF-origin invoices so PDF-specific safety guards
    cannot be skipped by relying on the legacy single-line fallback.
    """
    if not isinstance(pdf_originated, bool):
        raise TypeError("pdf_originated must be a bool.")
    if _source_origin is not None and (
        not pdf_originated or _source_origin != PDF_INVOICE_ORIGIN
    ):
        raise InvoiceCreationGuardError("Origem da invoice PDF inválida.")
    draft = adapt_fieldquo_invoice(invoice, source_origin=_source_origin)
    if pdf_originated:
        _validate_pdf_invoice_guards(invoice, draft, config)

    contact = draft["Contact"]
    resolution = resolve_xero_contact_id(contact["Name"], contacts)
    if resolution.status != "matched":
        raise XeroContactResolutionError(
            f"Contato Xero não resolvido: {resolution.status}."
        )

    payload = build_xero_invoice_payload(draft, config, resolution)
    payload_fingerprint = hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            default=str,
        ).encode("utf-8")
    ).hexdigest()
    operation_store = ledger or FieldQuoInvoiceIdempotencyStore()
    operation = operation_store.reserve(
        config.tenant_id,
        source_identity,
        payload_fingerprint=payload_fingerprint,
    )

    if operation.state == OperationState.SUCCESS:
        return InvoiceCreationDeferredResult("already_created", operation)
    if not operation.has_claim:
        return InvoiceCreationDeferredResult("processing", operation)
    deferred = False
    client_failed = False
    outcome_unknown = False
    unknown_response: Any = None
    result: Any = None
    try:
        with operation_store.claim_execution(
            config.tenant_id,
            source_identity,
            operation.operation_id,
            operation.claim_token,
        ) as execution:
            if not execution.authorized:
                deferred = True
            else:
                try:
                    response = client.create_invoice(
                        payload, idempotency_key=operation.idempotency_key
                    )
                except Exception as exc:
                    client_failed = True
                    if _is_indeterminate_client_error(exc):
                        if not execution.mark_outcome_unknown():
                            raise InvoiceCreationLedgerError(
                                "Não foi possível persistir o resultado indeterminado."
                            ) from None
                else:
                    xero_invoice_id = _extract_xero_invoice_id(response)
                    if xero_invoice_id is None:
                        if not execution.mark_outcome_unknown():
                            raise InvoiceCreationLedgerError(
                                "Não foi possível persistir a resposta não confirmada."
                            )
                        outcome_unknown = True
                        unknown_response = response
                    elif not execution.mark_success(xero_invoice_id):
                        raise InvoiceCreationLedgerError(
                            "Não foi possível persistir a invoice criada no ledger."
                        )
                    else:
                        result = response
    except InvoiceCreationLedgerError:
        raise
    except Exception:
        raise InvoiceCreationLedgerError(
            "Não foi possível manter a autorização do claim no ledger."
        ) from None

    if deferred:
        current_operation = operation_store.get(config.tenant_id, source_identity)
        if current_operation is None:
            raise InvoiceCreationLedgerError(
                "A operação não foi encontrada após a validação do claim."
            )
        return InvoiceCreationDeferredResult("processing", current_operation)
    if client_failed:
        raise InvoiceCreationClientError(
            "Falha ao delegar a invoice para o cliente Xero."
        ) from None
    if outcome_unknown:
        updated_operation = operation_store.get(config.tenant_id, source_identity)
        if updated_operation is None:
            raise InvoiceCreationLedgerError(
                "A operação não foi encontrada após registrar a resposta."
            )
        return InvoiceCreationDeferredResult(
            "outcome_unknown", updated_operation, unknown_response
        )
    return result


def create_pdf_invoice_in_xero(
    workflow_result: PDFInvoiceWorkflowResult,
    contacts: Iterable[Mapping[str, Any]] | None,
    config: InvoiceCreationConfig,
    client: XeroInvoiceCreationClient,
    *,
    source_identity: str,
    ledger: FieldQuoInvoiceIdempotencyStore | None = None,
) -> Any:
    """Forward a workflow-produced PDF invoice through the PDF safety path."""
    if (
        not isinstance(workflow_result, PDFInvoiceWorkflowResult)
        or workflow_result.origin != PDF_INVOICE_ORIGIN
        or not isinstance(workflow_result.invoice, Mapping)
    ):
        raise InvoiceCreationGuardError(
            "Resultado do workflow PDF sem proveniência válida; criação bloqueada."
        )
    return create_fieldquo_invoice_in_xero(
        workflow_result.invoice,
        contacts,
        config,
        client,
        source_identity=source_identity,
        pdf_originated=True,
        ledger=ledger,
        _source_origin=workflow_result.origin,
    )


def _validate_pdf_invoice_guards(
    invoice: Mapping[str, Any],
    draft: Mapping[str, Any],
    config: InvoiceCreationConfig,
) -> None:
    source = draft["Source"]
    line_items = source.get("LineItems")
    if not isinstance(line_items, list) or not line_items:
        raise InvoiceCreationGuardError(
            "Invoice originada de PDF deve conter ao menos uma linha explícita."
        )

    for field_name in ("tax", "discount"):
        amount = invoice.get(field_name)
        if amount is not None and amount != 0:
            raise InvoiceCreationGuardError(
                f"{field_name.title()} da invoice PDF não pode ser descartado sem "
                "reconciliação ou configuração explícita."
            )

    for field_name in ("account_code", "tax_type"):
        value = getattr(config, field_name, None)
        if not isinstance(value, str) or not value.strip():
            raise InvoiceCreationGuardError(
                f"Invoice originada de PDF exige {field_name.replace('_', ' ').title()} "
                "configurado e não vazio."
            )

    line_totals: list[Decimal] = []
    for index, line_item in enumerate(line_items):
        if not isinstance(line_item, Mapping):
            raise InvoiceCreationGuardError(
                f"LineItems[{index}] da invoice PDF deve ser um objeto."
            )
        description = line_item.get("Description")
        if not isinstance(description, str) or not description.strip():
            raise InvoiceCreationGuardError(
                f"LineItems[{index}].Description da invoice PDF é obrigatório."
            )
        quantity = line_item.get("Quantity")
        unit_amount = line_item.get("UnitAmount")
        if not isinstance(quantity, Decimal) or not quantity.is_finite():
            raise InvoiceCreationGuardError(
                f"LineItems[{index}].Quantity da invoice PDF deve ser um Decimal finito."
            )
        if not isinstance(unit_amount, Decimal) or not unit_amount.is_finite():
            raise InvoiceCreationGuardError(
                f"LineItems[{index}].UnitAmount da invoice PDF deve ser um Decimal finito."
            )
        try:
            with localcontext() as context:
                context.prec = max(
                    1,
                    len(quantity.as_tuple().digits)
                    + len(unit_amount.as_tuple().digits),
                )
                calculated_line_total = quantity * unit_amount
        except DecimalException:
            raise InvoiceCreationGuardError(
                f"LineItems[{index}] da invoice PDF não pode ser calculado sem arredondamento."
            ) from None

        supplied_line_total = line_item.get("LineAmount")
        if supplied_line_total is not None:
            if (
                not isinstance(supplied_line_total, Decimal)
                or not supplied_line_total.is_finite()
            ):
                raise InvoiceCreationGuardError(
                    f"LineItems[{index}].LineAmount da invoice PDF deve ser um Decimal finito."
                )
            if supplied_line_total != calculated_line_total:
                raise InvoiceCreationGuardError(
                    f"LineItems[{index}].LineAmount da invoice PDF não corresponde "
                    "a Quantity × UnitAmount."
                )
        line_totals.append(calculated_line_total)

    subtotal = source.get("Subtotal")
    if not isinstance(subtotal, Decimal) or not subtotal.is_finite():
        raise InvoiceCreationGuardError(
            "Invoice originada de PDF exige Subtotal Decimal finito para reconciliar as linhas."
        )
    nonzero_totals = [amount for amount in line_totals if amount]
    if nonzero_totals:
        minimum_exponent = min(amount.as_tuple().exponent for amount in line_totals)
        maximum_adjusted = max(amount.adjusted() for amount in nonzero_totals)
        precision = (
            maximum_adjusted
            - minimum_exponent
            + 1
            + len(str(len(line_totals)))
        )
    else:
        precision = 1
    try:
        with localcontext() as context:
            context.prec = max(1, precision)
            calculated_subtotal = sum(line_totals, Decimal("0"))
    except DecimalException:
        raise InvoiceCreationGuardError(
            "Soma das linhas da invoice PDF não pode ser calculada sem arredondamento."
        ) from None
    if calculated_subtotal != subtotal:
        raise InvoiceCreationGuardError(
            "Subtotal da invoice PDF não corresponde à soma de Quantity × UnitAmount."
        )


def _extract_xero_invoice_id(response: Any) -> str | None:
    if not isinstance(response, Mapping):
        return None
    invoices = response.get("Invoices")
    if not isinstance(invoices, list) or not invoices:
        return None
    created_invoice = invoices[0]
    if not isinstance(created_invoice, Mapping):
        return None
    invoice_id = created_invoice.get("InvoiceID")
    return invoice_id if isinstance(invoice_id, str) and invoice_id.strip() else None


def _is_indeterminate_client_error(error: BaseException) -> bool:
    transport_error_names = {
        "ChunkedEncodingError",
        "ConnectionError",
        "ConnectTimeout",
        "ContentDecodingError",
        "JSONDecodeError",
        "ProxyError",
        "ReadTimeout",
        "RequestException",
        "SSLError",
        "Timeout",
        "TimeoutError",
    }
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if type(current).__name__ == "HTTPError":
            response = getattr(current, "response", None)
            status_code = getattr(response, "status_code", None)
            return status_code == 408 or (
                isinstance(status_code, int) and 500 <= status_code < 600
            )
        if (
            type(current).__name__ in transport_error_names
            or type(current).__module__.startswith("requests.")
        ):
            return True
        current = current.__cause__ or current.__context__
    return False
