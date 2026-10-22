"""Offline readiness checks for a future PDF-origin Xero invoice emission."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import Any, Iterable, Mapping
from uuid import UUID

from xero.contact_resolver import (
    XeroContactResolution,
    XeroContactResolutionError,
    resolve_xero_contact_id,
)
from xero.fieldquo_invoice_adapter import adapt_fieldquo_invoice
from xero.fieldquo_invoice_idempotency import (
    FieldQuoInvoiceIdempotencyStore,
    FieldQuoInvoiceOperation,
    OperationState,
)
from xero.invoice_creation_config import InvoiceCreationConfig
from xero.invoice_creation_orchestrator import (
    InvoiceCreationGuardError,
    _validate_pdf_invoice_guards,
)
from xero.invoice_payload_builder import (
    InvoicePayloadBuilderError,
    build_xero_invoice_payload,
)
from xero.pdf_invoice_preparation import PDFInvoiceWorkflowPreparationResult


class PreflightStatus(str, Enum):
    READY_TO_EMIT = "READY_TO_EMIT"
    BLOCKED = "BLOCKED"


class XeroAuthorizationStatus(str, Enum):
    UNAUTHORIZED = "UNAUTHORIZED"
    READ_ONLY = "READ_ONLY"
    WRITE_AUTHORIZED = "WRITE_AUTHORIZED"
    WRITE_AUTHORIZATION_UNKNOWN = "WRITE_AUTHORIZATION_UNKNOWN"
    TOKEN_EXPIRED = "TOKEN_EXPIRED"
    TOKEN_MISSING = "TOKEN_MISSING"
    TOKEN_EXPIRY_UNKNOWN = "TOKEN_EXPIRY_UNKNOWN"
    SCOPES_MISSING = "SCOPES_MISSING"
    CONNECTION_MISSING = "CONNECTION_MISSING"
    TENANT_MISMATCH = "TENANT_MISMATCH"


class ContactResolutionStatus(str, Enum):
    CONTACT_RESOLVED = "CONTACT_RESOLVED"
    CONTACT_NOT_FOUND = "CONTACT_NOT_FOUND"
    CONTACT_AMBIGUOUS = "CONTACT_AMBIGUOUS"
    CONTACT_SOURCE_UNAVAILABLE = "CONTACT_SOURCE_UNAVAILABLE"


@dataclass(frozen=True)
class LicenseEvidence:
    """Non-secret, validated license facts supplied by a trusted local resolver."""

    tenant_id: UUID | None
    matching_license_count: int
    active: bool
    expires_on: date | None


@dataclass(frozen=True)
class XeroAuthorizationEvidence:
    """Offline snapshot; the current OAuth contract cannot prove write access."""

    tenant_id: UUID | None
    xero_tenant_id: str | None
    connection_present: bool
    access_token_present: bool
    expires_at: datetime | None
    granted_scopes: frozenset[str] | None

    def status_at(
        self, *, tenant_id: UUID, xero_tenant_id: str, now: datetime
    ) -> XeroAuthorizationStatus:
        if not self.connection_present:
            return XeroAuthorizationStatus.CONNECTION_MISSING
        if (
            self.tenant_id != tenant_id
            or self.xero_tenant_id != xero_tenant_id
        ):
            return XeroAuthorizationStatus.TENANT_MISMATCH
        if not self.access_token_present:
            return XeroAuthorizationStatus.TOKEN_MISSING
        if not isinstance(self.expires_at, datetime):
            return XeroAuthorizationStatus.TOKEN_EXPIRY_UNKNOWN
        if self.expires_at.tzinfo is None or now.tzinfo is None:
            return XeroAuthorizationStatus.TOKEN_EXPIRY_UNKNOWN
        if self.expires_at <= now:
            return XeroAuthorizationStatus.TOKEN_EXPIRED
        if not isinstance(self.granted_scopes, frozenset) or not self.granted_scopes:
            return XeroAuthorizationStatus.SCOPES_MISSING
        if any(
            not isinstance(scope, str) or not scope.strip()
            for scope in self.granted_scopes
        ):
            return XeroAuthorizationStatus.SCOPES_MISSING
        if all(
            scope.rsplit(".", 1)[-1].casefold() == "read"
            for scope in self.granted_scopes
        ):
            return XeroAuthorizationStatus.READ_ONLY
        return XeroAuthorizationStatus.WRITE_AUTHORIZATION_UNKNOWN


@dataclass(frozen=True)
class ContactResolutionEvidence:
    status: ContactResolutionStatus
    contact_id: str | None
    match_count: int


@dataclass(frozen=True)
class PDFInvoicePreflightResult:
    """Immutable readiness report; READY_TO_EMIT is not an emission result."""

    status: PreflightStatus
    reasons: tuple[str, ...]
    authorization_status: XeroAuthorizationStatus
    contact: ContactResolutionEvidence
    source_identity: str | None
    idempotency_key: str | None
    payload_json: str | None


def invoice_payload_fingerprint(payload: Mapping[str, Any]) -> str:
    """Return the deterministic fingerprint used to bind a ledger claim to payload."""
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def prepare_for_real_emission(
    preparation: PDFInvoiceWorkflowPreparationResult,
    *,
    license_evidence: LicenseEvidence,
    authorization: XeroAuthorizationEvidence,
    contacts: Iterable[Mapping[str, Any]] | None,
    idempotency_store: FieldQuoInvoiceIdempotencyStore | None,
    operation: FieldQuoInvoiceOperation | None,
    now: datetime,
    as_of: date,
) -> PDFInvoicePreflightResult:
    """Validate emission prerequisites without making network calls or ledger writes."""
    reasons: list[str] = []
    contact_evidence = ContactResolutionEvidence(
        ContactResolutionStatus.CONTACT_SOURCE_UNAVAILABLE, None, 0
    )
    source_identity: str | None = None
    idempotency_key: str | None = None
    payload_json: str | None = None
    authorization_status = XeroAuthorizationStatus.UNAUTHORIZED

    if not isinstance(preparation, PDFInvoiceWorkflowPreparationResult):
        reasons.append("BLOCKED_WORKFLOW_INCOMPLETE")
        context = None
        workflow = None
    else:
        context = preparation.preparation_context
        workflow = preparation.workflow_result

    if not isinstance(license_evidence, LicenseEvidence):
        reasons.append("BLOCKED_LICENSE_EVIDENCE_MISSING")
    elif license_evidence.matching_license_count != 1:
        reasons.append("BLOCKED_LICENSE_AMBIGUOUS")
    elif license_evidence.tenant_id is None:
        reasons.append("BLOCKED_LICENSE_TENANT_MISSING")
    elif not license_evidence.active:
        reasons.append("BLOCKED_LICENSE_INACTIVE")
    elif license_evidence.expires_on is None:
        reasons.append("BLOCKED_LICENSE_EXPIRATION_UNKNOWN")
    elif license_evidence.expires_on < as_of:
        reasons.append("BLOCKED_LICENSE_EXPIRED")

    config: InvoiceCreationConfig | None = None
    if context is not None:
        tenant_id = context.tenant_id
        xero_tenant_id = context.xero_tenant_id
        config = context.invoice_creation_config
        if not isinstance(tenant_id, UUID):
            reasons.append("BLOCKED_TENANT_UNRESOLVED")
        if (
            not isinstance(xero_tenant_id, str)
            or not xero_tenant_id.strip()
        ):
            reasons.append("BLOCKED_XERO_TENANT_MISSING")
        if not isinstance(config, InvoiceCreationConfig):
            reasons.append("BLOCKED_INVOICE_CONFIG_INVALID")
        elif config.tenant_id != str(tenant_id):
            reasons.append("BLOCKED_TENANT_MISMATCH")
        if (
            isinstance(license_evidence, LicenseEvidence)
            and license_evidence.tenant_id is not None
            and license_evidence.tenant_id != tenant_id
        ):
            reasons.append("BLOCKED_LICENSE_TENANT_MISMATCH")
    else:
        tenant_id = None
        xero_tenant_id = None

    if (
        isinstance(authorization, XeroAuthorizationEvidence)
        and isinstance(tenant_id, UUID)
        and isinstance(xero_tenant_id, str)
    ):
        authorization_status = authorization.status_at(
            tenant_id=tenant_id,
            xero_tenant_id=xero_tenant_id,
            now=now,
        )
    else:
        authorization_status = XeroAuthorizationStatus.CONNECTION_MISSING
    if authorization_status is not XeroAuthorizationStatus.WRITE_AUTHORIZED:
        reasons.append(f"BLOCKED_XERO_AUTHORIZATION_{authorization_status.value}")

    draft: Mapping[str, Any] | None = None
    payload: dict[str, object] | None = None
    if workflow is None:
        reasons.append("BLOCKED_WORKFLOW_INCOMPLETE")
    elif not _workflow_is_complete(workflow):
        invoice = getattr(workflow, "invoice", None)
        line_items = invoice.get("line_items") if isinstance(invoice, Mapping) else None
        if not isinstance(line_items, list) or len(line_items) != 1:
            reasons.append("BLOCKED_LINE_ITEM_COUNT_INVALID")
        reasons.append("BLOCKED_WORKFLOW_INCOMPLETE")
    elif config is not None:
        invoice = workflow.invoice
        source_identity = _source_identity(workflow)
        if source_identity is None:
            reasons.append("BLOCKED_MISSING_SOURCE_IDENTITY")
        try:
            draft = adapt_fieldquo_invoice(invoice, source_origin=workflow.origin)
            _validate_pdf_invoice_guards(invoice, draft, config)
            resolution = _resolve_contact(invoice, contacts)
            contact_evidence = resolution
            if resolution.status is not ContactResolutionStatus.CONTACT_RESOLVED:
                reasons.append(f"BLOCKED_{resolution.status.value}")
            else:
                resolved = XeroContactResolution(
                    "matched", resolution.contact_id, resolution.match_count
                )
                payload = build_xero_invoice_payload(draft, config, resolved)
                payload_json = json.dumps(
                    payload,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                    default=str,
                )
        except (
            InvoiceCreationGuardError,
            InvoicePayloadBuilderError,
            ValueError,
            TypeError,
        ):
            reasons.append("BLOCKED_INVOICE_PAYLOAD_INVALID")

    if payload is not None and config is not None and source_identity is not None:
        fingerprint = invoice_payload_fingerprint(payload)
        if (
            idempotency_store is None
            or not isinstance(operation, FieldQuoInvoiceOperation)
        ):
            reasons.append("BLOCKED_IDEMPOTENCY_KEY_MISSING")
        elif (
            operation.tenant_id != config.tenant_id
            or operation.source_identity != source_identity
            or operation.state not in (
                OperationState.PROCESSING,
                OperationState.OUTCOME_UNKNOWN,
            )
            or operation.payload_fingerprint != fingerprint
            or not _valid_idempotency_key(operation.idempotency_key)
            or not operation.has_claim
            or not operation.claim_expires_at
            or not _claim_is_current(
                idempotency_store, operation, now=now
            )
        ):
            reasons.append("BLOCKED_IDEMPOTENCY_CLAIM_INVALID")
        else:
            idempotency_key = operation.idempotency_key
    elif source_identity is None:
        reasons.append("BLOCKED_IDEMPOTENCY_SOURCE_IDENTITY_MISSING")

    unique_reasons = tuple(dict.fromkeys(reasons))
    return PDFInvoicePreflightResult(
        status=(
            PreflightStatus.READY_TO_EMIT
            if not unique_reasons
            else PreflightStatus.BLOCKED
        ),
        reasons=unique_reasons,
        authorization_status=authorization_status,
        contact=contact_evidence,
        source_identity=source_identity,
        idempotency_key=idempotency_key,
        payload_json=payload_json,
    )


def _workflow_is_complete(workflow: Any) -> bool:
    parser_result = getattr(workflow, "parser_result", None)
    invoice = getattr(workflow, "invoice", None)
    return bool(
        getattr(workflow, "origin", None) == "fieldquo_pdf"
        and getattr(workflow, "fallback_used", True) is False
        and getattr(parser_result, "complete", False) is True
        and not getattr(parser_result, "ambiguities", ())
        and isinstance(invoice, Mapping)
        and isinstance(invoice.get("line_items"), list)
        and len(invoice["line_items"]) == 1
        and invoice == getattr(parser_result, "invoice", None)
    )


def _source_identity(workflow: Any) -> str | None:
    parser_result = getattr(workflow, "parser_result", None)
    invoice = getattr(workflow, "invoice", None)
    if not isinstance(invoice, Mapping):
        return None
    number = invoice.get("invoice_number")
    evidence = getattr(parser_result, "evidence", {})
    values = evidence.get("invoice_number") if isinstance(evidence, Mapping) else None
    if (
        not isinstance(number, str)
        or not number.strip()
        or not isinstance(values, tuple)
        or len(values) != 1
        or not isinstance(values[0], str)
        or values[0].strip() != number.strip()
    ):
        return None
    return f"fieldquo_pdf:invoice_number:{number.strip()}"


def _resolve_contact(
    invoice: Mapping[str, Any],
    contacts: Iterable[Mapping[str, Any]] | None,
) -> ContactResolutionEvidence:
    if contacts is None:
        return ContactResolutionEvidence(
            ContactResolutionStatus.CONTACT_SOURCE_UNAVAILABLE, None, 0
        )
    try:
        result = resolve_xero_contact_id(invoice.get("client"), contacts)
    except (XeroContactResolutionError, TypeError, ValueError):
        return ContactResolutionEvidence(
            ContactResolutionStatus.CONTACT_NOT_FOUND, None, 0
        )
    if result.status == "ambiguous":
        return ContactResolutionEvidence(
            ContactResolutionStatus.CONTACT_AMBIGUOUS, None, result.match_count
        )
    if (
        result.status != "matched"
        or not isinstance(result.contact_id, str)
        or not result.contact_id.strip()
    ):
        return ContactResolutionEvidence(
            ContactResolutionStatus.CONTACT_NOT_FOUND, None, result.match_count
        )
    return ContactResolutionEvidence(
        ContactResolutionStatus.CONTACT_RESOLVED,
        result.contact_id.strip(),
        result.match_count,
    )


def _valid_idempotency_key(value: str) -> bool:
    return bool(
        isinstance(value, str)
        and value
        and len(value) <= 128
        and re.fullmatch(r"[A-Za-z0-9_-]+", value)
    )


def _claim_is_current(
    store: FieldQuoInvoiceIdempotencyStore,
    operation: FieldQuoInvoiceOperation,
    *,
    now: datetime,
) -> bool:
    if (
        not isinstance(now, datetime)
        or now.tzinfo is None
        or not isinstance(operation.claim_token, str)
    ):
        return False
    try:
        expires_at = datetime.fromisoformat(operation.claim_expires_at or "")
    except (TypeError, ValueError):
        return False
    if expires_at.tzinfo is None or expires_at <= now:
        return False
    return store.validate_claim(
        operation.tenant_id,
        operation.source_identity,
        operation.operation_id,
        operation.claim_token,
    )


__all__ = [
    "ContactResolutionEvidence",
    "ContactResolutionStatus",
    "LicenseEvidence",
    "PDFInvoicePreflightResult",
    "PreflightStatus",
    "XeroAuthorizationEvidence",
    "XeroAuthorizationStatus",
    "invoice_payload_fingerprint",
    "prepare_for_real_emission",
]
