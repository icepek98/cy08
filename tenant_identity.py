import os
from uuid import UUID

import requests


class TenantResolutionError(RuntimeError):
    """Raised when tenant identity cannot be resolved safely."""


class TenantResolver:
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
        self.http_client = http_client or requests.Session()

    def resolve_tenant_id(self, license_key: str) -> UUID | None:
        normalized_key = license_key.strip() if license_key else ""
        if not normalized_key:
            return None

        if not self.supabase_url:
            raise TenantResolutionError("SUPABASE_URL is not configured.")
        if not self.supabase_anon_key:
            raise TenantResolutionError("SUPABASE_ANON_KEY is not configured.")

        url = f"{self.supabase_url}/rest/v1/rpc/resolve_tenant_for_license"
        headers = {
            "apikey": self.supabase_anon_key,
            "Authorization": f"Bearer {self.supabase_anon_key}",
            "Content-Type": "application/json",
        }

        try:
            response = self.http_client.post(
                url,
                json={"p_license_key": normalized_key},
                headers=headers,
                timeout=15,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            raise TenantResolutionError(
                f"Tenant resolution request failed ({type(exc).__name__})."
            ) from exc

        try:
            data = response.json()
        except ValueError as exc:
            raise TenantResolutionError(
                "Supabase returned an invalid tenant resolution response."
            ) from exc

        if not isinstance(data, list):
            raise TenantResolutionError(
                "Supabase returned an invalid tenant resolution response."
            )
        if not data:
            return None
        if len(data) != 1 or not isinstance(data[0], dict):
            raise TenantResolutionError(
                "Supabase returned an ambiguous tenant resolution response."
            )

        tenant_id = data[0].get("tenant_id")
        if not isinstance(tenant_id, str):
            raise TenantResolutionError(
                "Supabase returned an invalid tenant identifier."
            )

        try:
            return UUID(tenant_id)
        except ValueError as exc:
            raise TenantResolutionError(
                "Supabase returned an invalid tenant identifier."
            ) from exc
