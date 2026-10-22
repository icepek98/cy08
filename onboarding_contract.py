"""Strict response contract for the authenticated FieldQuo onboarding endpoint."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from license_contract import LicenseStatus


class OnboardingContractError(ValueError):
    """Raised when an onboarding response violates the server contract."""


_FIELDS = frozenset(
    {
        "tenant_id",
        "installation_id",
        "license_status",
        "trial_started_at",
        "trial_ends_at",
        "checked_at",
        "installation_registered",
        "access_allowed",
        "block_reason",
    }
)

_BLOCK_REASONS = frozenset(
    {
        "installation_revoked",
        "license_revoked",
        "trial_expired",
        "license_not_active",
        "license_missing",
    }
)


@dataclass(frozen=True)
class OnboardingResult:
    tenant_id: UUID
    installation_id: UUID
    license_status: LicenseStatus
    trial_started_at: datetime | None
    trial_ends_at: datetime | None
    checked_at: datetime
    installation_registered: bool
    access_allowed: bool
    block_reason: str | None


def parse_onboarding_result(payload: object) -> OnboardingResult:
    if not isinstance(payload, dict) or set(payload) != _FIELDS:
        raise OnboardingContractError(
            "Onboarding response has missing or unexpected fields."
        )

    tenant_id = _parse_uuid(payload["tenant_id"], "tenant_id")
    installation_id = _parse_uuid(payload["installation_id"], "installation_id")
    try:
        license_status = LicenseStatus(payload["license_status"])
    except (TypeError, ValueError) as exc:
        raise OnboardingContractError("License status is invalid.") from exc

    trial_started_at = _parse_timestamp(
        payload["trial_started_at"], "trial_started_at"
    )
    trial_ends_at = _parse_timestamp(payload["trial_ends_at"], "trial_ends_at")
    checked_at = _parse_timestamp(payload["checked_at"], "checked_at")
    if checked_at is None:
        raise OnboardingContractError("checked_at must be present.")
    if license_status is LicenseStatus.TRIALING and (
        trial_started_at is None
        or trial_ends_at is None
        or trial_ends_at <= trial_started_at
    ):
        raise OnboardingContractError("Trial dates are incomplete or invalid.")

    if payload["installation_registered"] is not True:
        raise OnboardingContractError("The installation was not registered.")
    access_allowed = payload["access_allowed"]
    if not isinstance(access_allowed, bool):
        raise OnboardingContractError("access_allowed must be a boolean.")

    block_reason = payload["block_reason"]
    if block_reason is not None and (
        not isinstance(block_reason, str) or block_reason not in _BLOCK_REASONS
    ):
        raise OnboardingContractError("The access block reason is invalid.")
    if access_allowed:
        if block_reason is not None or license_status not in {
            LicenseStatus.TRIALING,
            LicenseStatus.ACTIVE,
        }:
            raise OnboardingContractError("The access decision is inconsistent.")
        if (
            license_status is LicenseStatus.TRIALING
            and checked_at >= trial_ends_at
        ):
            raise OnboardingContractError("An expired trial cannot allow access.")
    elif block_reason is None:
        raise OnboardingContractError("A blocked result must include a reason.")

    return OnboardingResult(
        tenant_id=tenant_id,
        installation_id=installation_id,
        license_status=license_status,
        trial_started_at=trial_started_at,
        trial_ends_at=trial_ends_at,
        checked_at=checked_at,
        installation_registered=True,
        access_allowed=access_allowed,
        block_reason=block_reason,
    )


def _parse_uuid(value: object, field: str) -> UUID:
    if not isinstance(value, str):
        raise OnboardingContractError(f"{field} must be a UUID string.")
    try:
        return UUID(value)
    except ValueError as exc:
        raise OnboardingContractError(f"{field} must be a UUID string.") from exc


def _parse_timestamp(value: object, field: str) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise OnboardingContractError(f"{field} must be an ISO timestamp.")
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise OnboardingContractError(f"{field} must be an ISO timestamp.") from exc
    if timestamp.utcoffset() is None:
        raise OnboardingContractError(f"{field} must include a timezone.")
    return timestamp
