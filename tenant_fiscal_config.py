import os
from dataclasses import dataclass
from typing import Literal, get_args
from uuid import UUID

import requests

from xero.invoice_creation_config import AmountBasis, DiscountPolicy


class TenantFiscalConfigError(RuntimeError):
    """Base class for fiscal-configuration lookup failures."""


class TenantFiscalConfigSupabaseConfigError(TenantFiscalConfigError):
    """Raised when the public Supabase connection settings are missing."""


class TenantFiscalConfigTransportError(TenantFiscalConfigError):
    """Raised when the Supabase RPC cannot be reached successfully."""


class TenantFiscalConfigRpcError(TenantFiscalConfigError):
    """Raised when the Supabase RPC returns an unsuccessful HTTP status."""


class TenantFiscalConfigResponseError(TenantFiscalConfigError):
    """Raised when the Supabase RPC response has an unexpected shape."""


class TenantFiscalConfigInvalidError(TenantFiscalConfigError):
    """Raised when persisted fiscal configuration violates its contract."""


@dataclass(frozen=True)
class TenantFiscalConfig:
    tenant_id: UUID
    account_code: str
    tax_type: str
    tax_inclusive: bool
    amount_basis: AmountBasis
    discount_policy: DiscountPolicy


class TenantFiscalConfigResolver:
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
    ) -> TenantFiscalConfig | None:
        """Resolve fiscal config; None is not proof that the tenant is authorized.

        A blank license returns None without an RPC call. Otherwise, None means
        the RPC returned zero rows, which can indicate missing config or a
        license/tenant link that is invalid, inactive, or expired.
        """
        requested_tenant_id = self._parse_tenant_id(tenant_id)
        normalized_license_key = license_key.strip() if license_key else ""
        if not normalized_license_key:
            return None

        if not self.supabase_url:
            raise TenantFiscalConfigSupabaseConfigError(
                "SUPABASE_URL is not configured."
            )
        if not self.supabase_anon_key:
            raise TenantFiscalConfigSupabaseConfigError(
                "SUPABASE_ANON_KEY is not configured."
            )

        url = f"{self.supabase_url}/rest/v1/rpc/get_tenant_fiscal_config"
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
                    "p_license_key": normalized_license_key,
                },
                headers=headers,
                timeout=15,
            )
            response.raise_for_status()
        except requests.HTTPError as exc:
            status_code = exc.response.status_code if exc.response is not None else "unknown"
            raise TenantFiscalConfigRpcError(
                f"Supabase fiscal RPC returned HTTP {status_code}."
            ) from exc
        except requests.RequestException as exc:
            raise TenantFiscalConfigTransportError(
                f"Fiscal configuration request failed ({type(exc).__name__})."
            ) from exc

        try:
            data = response.json()
        except ValueError as exc:
            raise TenantFiscalConfigResponseError(
                "Supabase returned invalid fiscal configuration JSON."
            ) from exc

        if not isinstance(data, list):
            raise TenantFiscalConfigResponseError(
                "Supabase returned an invalid fiscal configuration response."
            )
        if not data:
            return None
        if len(data) != 1 or not isinstance(data[0], dict):
            raise TenantFiscalConfigResponseError(
                "Supabase returned an ambiguous fiscal configuration response."
            )

        return self._parse_config(data[0], requested_tenant_id)

    @staticmethod
    def _parse_tenant_id(tenant_id: UUID | str) -> UUID:
        if isinstance(tenant_id, UUID):
            return tenant_id
        if not isinstance(tenant_id, str):
            raise TenantFiscalConfigInvalidError("tenant_id must be a UUID.")
        try:
            return UUID(tenant_id)
        except ValueError as exc:
            raise TenantFiscalConfigInvalidError("tenant_id must be a UUID.") from exc

    @staticmethod
    def _parse_config(
        row: dict[str, object], requested_tenant_id: UUID
    ) -> TenantFiscalConfig:
        expected_fields = {
            "tenant_id",
            "account_code",
            "tax_type",
            "tax_inclusive",
            "amount_basis",
            "discount_policy",
        }
        if set(row) != expected_fields:
            raise TenantFiscalConfigResponseError(
                "Supabase returned an invalid fiscal configuration response."
            )

        raw_tenant_id = row["tenant_id"]
        if not isinstance(raw_tenant_id, str):
            raise TenantFiscalConfigResponseError(
                "Supabase returned an invalid tenant identifier."
            )
        try:
            response_tenant_id = UUID(raw_tenant_id)
        except ValueError as exc:
            raise TenantFiscalConfigResponseError(
                "Supabase returned an invalid tenant identifier."
            ) from exc
        if response_tenant_id != requested_tenant_id:
            raise TenantFiscalConfigResponseError(
                "Supabase returned configuration for a different tenant."
            )

        account_code = row["account_code"]
        if not isinstance(account_code, str) or not account_code.strip():
            raise TenantFiscalConfigInvalidError(
                "account_code must be a non-empty string."
            )

        tax_type = row["tax_type"]
        if not isinstance(tax_type, str) or not tax_type.strip():
            raise TenantFiscalConfigInvalidError(
                "tax_type must be a non-empty string."
            )

        tax_inclusive = row["tax_inclusive"]
        if not isinstance(tax_inclusive, bool):
            raise TenantFiscalConfigInvalidError("tax_inclusive must be a bool.")

        amount_basis = row["amount_basis"]
        if not isinstance(amount_basis, str) or amount_basis not in get_args(
            AmountBasis
        ):
            raise TenantFiscalConfigInvalidError(
                "amount_basis is not supported by InvoiceCreationConfig."
            )

        discount_policy = row["discount_policy"]
        if not isinstance(discount_policy, str) or discount_policy not in get_args(
            DiscountPolicy
        ):
            raise TenantFiscalConfigInvalidError(
                "discount_policy is not supported by InvoiceCreationConfig."
            )

        return TenantFiscalConfig(
            tenant_id=response_tenant_id,
            account_code=account_code,
            tax_type=tax_type,
            tax_inclusive=tax_inclusive,
            amount_basis=amount_basis,
            discount_policy=discount_policy,
        )
