"""Resolve a tenant's persisted Xero organization metadata through Supabase."""

from __future__ import annotations

import os
from dataclasses import dataclass
from uuid import UUID

import requests


class XeroConnectionError(RuntimeError):
    """Base class for Xero connection lookup failures."""


class XeroConnectionSupabaseConfigError(XeroConnectionError):
    """Raised when the public Supabase connection settings are missing."""


class XeroConnectionTransportError(XeroConnectionError):
    """Raised when the Supabase RPC cannot be reached successfully."""


class XeroConnectionRpcError(XeroConnectionError):
    """Raised when the Supabase RPC returns an unsuccessful HTTP status."""


class XeroConnectionAmbiguousLicenseError(XeroConnectionRpcError):
    """Raised when the license key matches multiple active license records."""


class XeroConnectionResponseError(XeroConnectionError):
    """Raised when the Supabase RPC response has an unexpected shape."""


class XeroConnectionInvalidError(XeroConnectionError):
    """Raised when a tenant, license, or persisted connection is invalid."""


@dataclass(frozen=True)
class XeroConnection:
    tenant_id: UUID
    xero_tenant_id: str
    xero_tenant_name: str | None
    xero_tenant_type: str | None


class XeroConnectionResolver:
    def __init__(
        self,
        supabase_url: str | None = None,
        supabase_anon_key: str | None = None,
        http_client: requests.Session | None = None,
    ):
        self.supabase_url = (
            os.getenv("SUPABASE_URL", "") if supabase_url is None else supabase_url
        ).rstrip("/")
        self.supabase_anon_key = (
            os.getenv("SUPABASE_ANON_KEY", "")
            if supabase_anon_key is None
            else supabase_anon_key
        )
        self.http_client = (
            http_client if http_client is not None else requests.Session()
        )

    def get_for_tenant(
        self, tenant_id: UUID | str, license_key: str
    ) -> XeroConnection | None:
        """Resolve connection metadata without treating zero rows as authorization.

        A blank license is rejected without an RPC request. For a nonblank key,
        None means the RPC returned zero rows: there may be no connection, or the
        license may be invalid, inactive, expired, or linked to another tenant.
        """
        requested_tenant_id = self._parse_tenant_id(tenant_id)
        if not isinstance(license_key, str) or not license_key.strip():
            raise XeroConnectionInvalidError("license_key must be a non-empty string.")

        if not self.supabase_url:
            raise XeroConnectionSupabaseConfigError(
                "SUPABASE_URL is not configured."
            )
        if not self.supabase_anon_key:
            raise XeroConnectionSupabaseConfigError(
                "SUPABASE_ANON_KEY is not configured."
            )

        url = f"{self.supabase_url}/rest/v1/rpc/get_xero_connection"
        headers = {
            "apikey": self.supabase_anon_key,
            "Authorization": f"Bearer {self.supabase_anon_key}",
            "Content-Type": "application/json",
        }

        try:
            response = self.http_client.post(
                url,
                json={
                    "p_tenant_id": str(requested_tenant_id),
                    "p_license_key": license_key.strip(),
                },
                headers=headers,
                timeout=15,
            )
            response.raise_for_status()
        except requests.HTTPError as exc:
            if self._is_ambiguous_license_error(exc):
                raise XeroConnectionAmbiguousLicenseError(
                    "Supabase rejected an ambiguous active license key."
                ) from exc
            status_code = (
                exc.response.status_code
                if exc.response is not None
                else "unknown"
            )
            raise XeroConnectionRpcError(
                f"Supabase Xero connection RPC returned HTTP {status_code}."
            ) from exc
        except requests.RequestException as exc:
            raise XeroConnectionTransportError(
                f"Xero connection request failed ({type(exc).__name__})."
            ) from exc

        try:
            data = response.json()
        except ValueError as exc:
            raise XeroConnectionResponseError(
                "Supabase returned invalid Xero connection JSON."
            ) from exc

        if not isinstance(data, list):
            raise XeroConnectionResponseError(
                "Supabase returned an invalid Xero connection response."
            )
        if not data:
            return None
        if len(data) != 1 or not isinstance(data[0], dict):
            raise XeroConnectionResponseError(
                "Supabase returned an ambiguous Xero connection response."
            )

        return self._parse_connection(data[0], requested_tenant_id)

    @staticmethod
    def _parse_tenant_id(tenant_id: UUID | str) -> UUID:
        if isinstance(tenant_id, UUID):
            return tenant_id
        if not isinstance(tenant_id, str):
            raise XeroConnectionInvalidError("tenant_id must be a UUID.")
        try:
            return UUID(tenant_id)
        except ValueError as exc:
            raise XeroConnectionInvalidError("tenant_id must be a UUID.") from exc

    @staticmethod
    def _is_ambiguous_license_error(exc: requests.HTTPError) -> bool:
        response = exc.response
        if response is None:
            return False
        try:
            payload = response.json()
        except (TypeError, ValueError):
            return False
        return (
            isinstance(payload, dict)
            and payload.get("code") == "P0001"
            and payload.get("message") == "Ambiguous active license key."
        )

    @staticmethod
    def _parse_connection(
        row: dict[str, object], requested_tenant_id: UUID
    ) -> XeroConnection:
        expected_fields = {
            "tenant_id",
            "xero_tenant_id",
            "xero_tenant_name",
            "xero_tenant_type",
        }
        if set(row) != expected_fields:
            raise XeroConnectionResponseError(
                "Supabase returned an invalid Xero connection response."
            )

        raw_tenant_id = row["tenant_id"]
        if not isinstance(raw_tenant_id, str):
            raise XeroConnectionResponseError(
                "Supabase returned an invalid tenant identifier."
            )
        try:
            response_tenant_id = UUID(raw_tenant_id)
        except ValueError as exc:
            raise XeroConnectionResponseError(
                "Supabase returned an invalid tenant identifier."
            ) from exc
        if response_tenant_id != requested_tenant_id:
            raise XeroConnectionResponseError(
                "Supabase returned a connection for a different tenant."
            )

        xero_tenant_id = row["xero_tenant_id"]
        if not isinstance(xero_tenant_id, str) or not xero_tenant_id.strip():
            raise XeroConnectionInvalidError(
                "xero_tenant_id must be a non-empty string."
            )

        xero_tenant_name = row["xero_tenant_name"]
        if xero_tenant_name is not None and not isinstance(xero_tenant_name, str):
            raise XeroConnectionInvalidError(
                "xero_tenant_name must be a string or None."
            )

        xero_tenant_type = row["xero_tenant_type"]
        if xero_tenant_type is not None and not isinstance(xero_tenant_type, str):
            raise XeroConnectionInvalidError(
                "xero_tenant_type must be a string or None."
            )

        return XeroConnection(
            tenant_id=response_tenant_id,
            xero_tenant_id=xero_tenant_id,
            xero_tenant_name=xero_tenant_name,
            xero_tenant_type=xero_tenant_type,
        )
