from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from uuid import UUID


class LicenseStatus(str, Enum):
    TRIALING = "trialing"
    ACTIVE = "active"
    PAST_DUE = "past_due"
    CANCELED = "canceled"
    EXPIRED = "expired"
    BLOCKED = "blocked"
    REVOKED = "revoked"


LICENSE_VERIFICATION_UNAVAILABLE = "LICENSE_VERIFICATION_UNAVAILABLE"


class LicenseContractError(ValueError):
    """Raised when a privileged license RPC response violates its contract."""


@dataclass(frozen=True)
class LicenseSnapshot:
    tenant_id: UUID
    license_status: LicenseStatus
    trial_started_at: datetime | None
    trial_ends_at: datetime | None
    subscription_id: str | None
    subscription_started_at: datetime | None
    subscription_ends_at: datetime | None
    checked_at: datetime


_LICENSE_FIELDS = frozenset(
    {
        "tenant_id",
        "license_status",
        "trial_started_at",
        "trial_ends_at",
        "subscription_id",
        "subscription_started_at",
        "subscription_ends_at",
        "checked_at",
    }
)


def parse_license_snapshot(
    payload: object, requested_tenant_id: UUID | str
) -> LicenseSnapshot | None:
    """Parse exactly one row returned by the privileged tenant-license RPC."""
    tenant_id = _parse_tenant_id(requested_tenant_id)
    if not isinstance(payload, list):
        raise LicenseContractError("License RPC response must be a list.")
    if not payload:
        return None
    if len(payload) != 1 or not isinstance(payload[0], dict):
        raise LicenseContractError("License RPC response must contain at most one row.")

    row = payload[0]
    if set(row) != _LICENSE_FIELDS:
        raise LicenseContractError("License RPC response has unexpected fields.")

    returned_tenant_id = _parse_tenant_id(row["tenant_id"])
    if returned_tenant_id != tenant_id:
        raise LicenseContractError("License RPC returned a different tenant.")

    try:
        status = LicenseStatus(row["license_status"])
    except (TypeError, ValueError) as exc:
        raise LicenseContractError("License status is invalid.") from exc

    trial_started_at = _parse_timestamp(row["trial_started_at"], "trial_started_at")
    trial_ends_at = _parse_timestamp(row["trial_ends_at"], "trial_ends_at")
    subscription_started_at = _parse_timestamp(
        row["subscription_started_at"], "subscription_started_at"
    )
    subscription_ends_at = _parse_timestamp(
        row["subscription_ends_at"], "subscription_ends_at"
    )
    checked_at = _parse_timestamp(row["checked_at"], "checked_at")
    if checked_at is None:
        raise LicenseContractError("checked_at must be present.")

    subscription_id = row["subscription_id"]
    if subscription_id is not None and (
        not isinstance(subscription_id, str) or not subscription_id.strip()
    ):
        raise LicenseContractError("subscription_id must be a non-empty string.")

    if status is LicenseStatus.TRIALING and (
        trial_started_at is None
        or trial_ends_at is None
        or trial_ends_at <= trial_started_at
    ):
        raise LicenseContractError("Trial dates are incomplete or invalid.")

    return LicenseSnapshot(
        tenant_id=returned_tenant_id,
        license_status=status,
        trial_started_at=trial_started_at,
        trial_ends_at=trial_ends_at,
        subscription_id=subscription_id,
        subscription_started_at=subscription_started_at,
        subscription_ends_at=subscription_ends_at,
        checked_at=checked_at,
    )


def _parse_tenant_id(value: object) -> UUID:
    if isinstance(value, UUID):
        return value
    if not isinstance(value, str):
        raise LicenseContractError("tenant_id must be a UUID.")
    try:
        return UUID(value)
    except ValueError as exc:
        raise LicenseContractError("tenant_id must be a UUID.") from exc


def _parse_timestamp(value: object, field: str) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise LicenseContractError(f"{field} must be an ISO timestamp.")
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise LicenseContractError(f"{field} must be an ISO timestamp.") from exc
    if timestamp.utcoffset() is None:
        raise LicenseContractError(f"{field} must include a timezone.")
    return timestamp
