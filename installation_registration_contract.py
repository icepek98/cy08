"""Strict client payload contract for future authenticated installation onboarding."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID


class InstallationRegistrationContractError(ValueError):
    """Raised when an installation registration payload violates its contract."""


@dataclass(frozen=True)
class InstallationRegistrationRequest:
    """The client supplies only its installation ID; the server derives the tenant."""

    installation_id: UUID

    def __post_init__(self) -> None:
        if not isinstance(self.installation_id, UUID):
            raise InstallationRegistrationContractError(
                "installation_id must be a UUID."
            )

    def to_payload(self) -> dict[str, str]:
        return {"installation_id": str(self.installation_id)}

    @classmethod
    def from_payload(cls, payload: object) -> InstallationRegistrationRequest:
        if not isinstance(payload, dict) or set(payload) != {"installation_id"}:
            raise InstallationRegistrationContractError(
                "Registration payload must contain only installation_id."
            )

        raw_installation_id = payload["installation_id"]
        if not isinstance(raw_installation_id, str):
            raise InstallationRegistrationContractError(
                "installation_id must be a UUID string."
            )
        try:
            installation_id = UUID(raw_installation_id)
        except ValueError as exc:
            raise InstallationRegistrationContractError(
                "installation_id must be a UUID string."
            ) from exc

        return cls(installation_id=installation_id)
