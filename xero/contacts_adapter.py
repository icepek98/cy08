"""Adapter layer for translating Xero contact payloads to a neutral, non-transaction shape.

This module intentionally does not try to fit Xero contacts into the project's
internal CSV/XLSX transaction contract (client_id, client_name, amount, date,
description). The internal transaction contract is executable by the RPA layer and
requires monetary and date values that a contact payload does not have.

Instead, we preserve only the fields that are actually present and relevant for a
Xero contact record: the source identifier and the contact name.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping


class XeroContactAdapterError(ValueError):
    """Raised when a Xero contact payload is incomplete or invalid for mapping."""


@dataclass(frozen=True)
class XeroContact:
    """Neutral representation of a Xero contact.

    This is not an executable internal transaction. It is an intermediate record
    that keeps the Xero ContactID and the contact name without inventing values
    such as amount, date or description.
    """

    source_system: str = "xero"
    contact_id: str | None = None
    name: str | None = None
    raw_contact: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_xero_contact(cls, contact: Mapping[str, Any]) -> "XeroContact":
        if not isinstance(contact, Mapping):
            raise XeroContactAdapterError("Contato Xero inválido: payload não é um mapeamento.")

        raw = dict(contact)
        contact_id = raw.get("ContactID")
        name = raw.get("Name")

        if contact_id is None or str(contact_id).strip() == "":
            raise XeroContactAdapterError("Contato Xero sem ContactID.")
        if name is None or str(name).strip() == "":
            raise XeroContactAdapterError("Contato Xero sem nome.")

        return cls(
            source_system="xero",
            contact_id=str(contact_id).strip(),
            name=str(name).strip(),
            raw_contact=raw,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_system": self.source_system,
            "contact_id": self.contact_id,
            "name": self.name,
            "raw_contact": self.raw_contact,
        }


def adapt_xero_contacts(contacts: Iterable[Mapping[str, Any]] | None) -> list[XeroContact]:
    """Map Xero contact payloads into a safe intermediate contact representation.

    The adapter preserves ContactID and Name only and intentionally discards any
    fields that would require inventing values not present in the source payload.
    """

    if contacts is None:
        return []

    adapted: list[XeroContact] = []
    for contact in contacts:
        adapted.append(XeroContact.from_xero_contact(contact))
    return adapted
