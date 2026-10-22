"""Minimal, read-only Xero Accounting API client."""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

import requests

API_BASE_URL = "https://api.xero.com/api.xro/2.0"
ORGANISATION_ENDPOINT = f"{API_BASE_URL}/Organisation"
CONTACTS_ENDPOINT = f"{API_BASE_URL}/Contacts"
INVOICES_ENDPOINT = f"{API_BASE_URL}/Invoices"


def _serialize_json_value(value: object) -> str:
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("Decimal values in JSON must be finite")
        return str(value)
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, (int, float, str)):
        return json.dumps(value, ensure_ascii=False, allow_nan=False)
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(_serialize_json_value(item) for item in value) + "]"
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise TypeError("JSON object keys must be strings")
        return "{" + ",".join(
            json.dumps(key, ensure_ascii=False)
            + ":"
            + _serialize_json_value(item)
            for key, item in value.items()
        ) + "}"
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


class XeroApiError(RuntimeError):
    """Raised when the Xero Accounting API cannot be queried."""


class XeroApiClient:
    """Simple in-memory client for the read-only Xero organisation endpoint."""

    def __init__(self, access_token: str, tenant_id: str):
        if not access_token or not access_token.strip():
            raise XeroApiError("Access token must not be empty")
        if not tenant_id or not tenant_id.strip():
            raise XeroApiError("Tenant ID must not be empty")
        self.access_token = access_token.strip()
        self.tenant_id = tenant_id.strip()

    def get_organisation(self) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Xero-tenant-id": self.tenant_id,
            "Accept": "application/json",
        }
        try:
            response = requests.get(ORGANISATION_ENDPOINT, headers=headers, timeout=15)
            response.raise_for_status()
        except requests.exceptions.Timeout as exc:
            raise XeroApiError("Xero API timed out") from exc
        except requests.exceptions.HTTPError as exc:
            status_code = getattr(exc.response, "status_code", None) if exc.response is not None else None
            if status_code == 401:
                raise XeroApiError("Xero API retornou HTTP 401") from exc
            if status_code == 403:
                raise XeroApiError("Xero API retornou HTTP 403") from exc
            if status_code == 429:
                raise XeroApiError("Xero API retornou HTTP 429") from exc
            if isinstance(status_code, int) and 500 <= status_code < 600:
                raise XeroApiError(f"Xero API retornou HTTP {status_code}") from exc
            raise XeroApiError("Xero API retornou um erro HTTP") from exc
        except requests.exceptions.RequestException as exc:
            raise XeroApiError("Erro de conexão com a Xero Accounting API") from exc

        try:
            payload = response.json()
        except ValueError as exc:
            raise XeroApiError("Resposta JSON inválida da Xero Accounting API") from exc

        if not isinstance(payload, dict):
            raise XeroApiError("Resposta da Xero Accounting API não contém Organisations")

        organisations = payload.get("Organisations")
        if not isinstance(organisations, list) or not organisations:
            raise XeroApiError("Resposta da Xero Accounting API não contém Organisations")

        first = organisations[0]
        if not isinstance(first, dict):
            raise XeroApiError("Resposta da Xero Accounting API não contém Organisations")
        return first

    def get_contacts(self) -> list[dict[str, Any]]:
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Xero-tenant-id": self.tenant_id,
            "Accept": "application/json",
        }
        try:
            response = requests.get(CONTACTS_ENDPOINT, headers=headers, timeout=15)
            response.raise_for_status()
        except requests.exceptions.Timeout as exc:
            raise XeroApiError("Xero API timed out") from exc
        except requests.exceptions.HTTPError as exc:
            status_code = getattr(exc.response, "status_code", None) if exc.response is not None else None
            if status_code == 401:
                raise XeroApiError("Xero API retornou HTTP 401") from exc
            if status_code == 403:
                raise XeroApiError("Xero API retornou HTTP 403") from exc
            if status_code == 429:
                raise XeroApiError("Xero API retornou HTTP 429") from exc
            if isinstance(status_code, int) and 500 <= status_code < 600:
                raise XeroApiError(f"Xero API retornou HTTP {status_code}") from exc
            raise XeroApiError("Xero API retornou um erro HTTP") from exc
        except requests.exceptions.RequestException as exc:
            raise XeroApiError("Erro de conexão com a Xero Accounting API") from exc

        try:
            payload = response.json()
        except ValueError as exc:
            raise XeroApiError("Resposta JSON inválida da Xero Accounting API") from exc

        if not isinstance(payload, dict):
            raise XeroApiError("Resposta da Xero Accounting API não contém Contacts")

        contacts = payload.get("Contacts")
        if not isinstance(contacts, list):
            raise XeroApiError("Resposta da Xero Accounting API não contém Contacts")
        return contacts

    def get_invoices(self) -> list[dict[str, Any]]:
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Xero-tenant-id": self.tenant_id,
            "Accept": "application/json",
        }
        try:
            response = requests.get(INVOICES_ENDPOINT, headers=headers, timeout=15)
            response.raise_for_status()
        except requests.exceptions.Timeout as exc:
            raise XeroApiError("Xero API timed out") from exc
        except requests.exceptions.HTTPError as exc:
            status_code = getattr(exc.response, "status_code", None) if exc.response is not None else None
            if status_code == 401:
                raise XeroApiError("Xero API retornou HTTP 401") from exc
            if status_code == 403:
                raise XeroApiError("Xero API retornou HTTP 403") from exc
            if status_code == 429:
                raise XeroApiError("Xero API retornou HTTP 429") from exc
            if isinstance(status_code, int) and 500 <= status_code < 600:
                raise XeroApiError(f"Xero API retornou HTTP {status_code}") from exc
            raise XeroApiError("Xero API retornou um erro HTTP") from exc
        except requests.exceptions.RequestException as exc:
            raise XeroApiError("Erro de conexão com a Xero Accounting API") from exc

        try:
            payload = response.json()
        except ValueError as exc:
            raise XeroApiError("Resposta JSON inválida da Xero Accounting API") from exc

        if not isinstance(payload, dict):
            raise XeroApiError("Resposta da Xero Accounting API não contém Invoices")

        invoices = payload.get("Invoices")
        if not isinstance(invoices, list):
            raise XeroApiError("Resposta da Xero Accounting API não contém Invoices")
        return invoices

    def create_invoice(
        self,
        payload: dict[str, object],
        idempotency_key: str | None = None,
    ) -> Any:
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Xero-tenant-id": self.tenant_id,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if idempotency_key is not None:
            headers["Idempotency-Key"] = idempotency_key

        try:
            response = requests.post(
                INVOICES_ENDPOINT,
                headers=headers,
                data=_serialize_json_value(payload),
                timeout=15,
            )
            response.raise_for_status()
        except requests.exceptions.Timeout as exc:
            raise XeroApiError("Xero API timed out") from exc
        except requests.exceptions.HTTPError as exc:
            status_code = getattr(exc.response, "status_code", None) if exc.response is not None else None
            if status_code == 401:
                raise XeroApiError("Xero API retornou HTTP 401") from exc
            if status_code == 403:
                raise XeroApiError("Xero API retornou HTTP 403") from exc
            if status_code == 429:
                raise XeroApiError("Xero API retornou HTTP 429") from exc
            if isinstance(status_code, int) and 500 <= status_code < 600:
                raise XeroApiError(f"Xero API retornou HTTP {status_code}") from exc
            raise XeroApiError("Xero API retornou um erro HTTP") from exc
        except requests.exceptions.RequestException as exc:
            raise XeroApiError("Erro de conexão com a Xero Accounting API") from exc

        try:
            return response.json()
        except ValueError as exc:
            raise XeroApiError("Resposta JSON inválida da Xero Accounting API") from exc
