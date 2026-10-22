"""Deterministic resolution of FieldQuo client names to Xero contact IDs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Literal, Mapping, Any

from xero.contacts_adapter import XeroContact, adapt_xero_contacts


class XeroContactResolutionError(ValueError):
    """Raised when the FieldQuo client name cannot be used for resolution."""


@dataclass(frozen=True)
class XeroContactResolution:
    """Outcome of resolving a FieldQuo client name against Xero contacts."""

    status: Literal["matched", "not_found", "ambiguous"]
    contact_id: str | None
    match_count: int


def resolve_xero_contact_id(
    fieldquo_client_name: str,
    xero_contacts: Iterable[Mapping[str, Any]] | None,
) -> XeroContactResolution:
    """Resolve a client name only when exactly one valid Xero contact matches."""
    if not isinstance(fieldquo_client_name, str) or not _normalize_name(
        fieldquo_client_name
    ):
        raise XeroContactResolutionError(
            "Nome do cliente FieldQuo vazio ou inválido para resolução."
        )

    normalized_name = _normalize_name(fieldquo_client_name)
    contacts: list[XeroContact] = adapt_xero_contacts(xero_contacts)
    matches = [
        contact
        for contact in contacts
        if _normalize_name(contact.name or "") == normalized_name
    ]

    if not matches:
        return XeroContactResolution("not_found", None, 0)
    if len(matches) > 1:
        return XeroContactResolution("ambiguous", None, len(matches))
    return XeroContactResolution("matched", matches[0].contact_id, 1)


def _normalize_name(name: str) -> str:
    return " ".join(name.split()).casefold()
