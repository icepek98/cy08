"""Minimal Supabase Auth REST client; access tokens are retained in memory only."""

from __future__ import annotations

import os
from dataclasses import dataclass
from uuid import UUID

import requests


class SupabaseAuthError(RuntimeError):
    """Raised when Supabase Auth configuration or authentication fails."""


@dataclass(frozen=True)
class AuthSession:
    user_id: UUID
    access_token: str


@dataclass(frozen=True)
class SignUpResult:
    user_id: UUID
    confirmation_required: bool


class SupabaseAuthClient:
    def __init__(
        self,
        supabase_url: str | None = None,
        anon_key: str | None = None,
        http_client: requests.Session | None = None,
    ) -> None:
        self.supabase_url = (
            os.getenv("SUPABASE_URL", "") if supabase_url is None else supabase_url
        ).rstrip("/")
        self.anon_key = (
            os.getenv("SUPABASE_ANON_KEY", "") if anon_key is None else anon_key
        )
        self.http_client = http_client or requests.Session()

    def sign_up(self, email: str, password: str) -> SignUpResult:
        payload = self._request(
            "/auth/v1/signup",
            {"email": email, "password": password},
        )
        user = payload.get("user", payload)
        user_id = self._user_id(user)
        return SignUpResult(
            user_id=user_id,
            confirmation_required=payload.get("access_token") is None,
        )

    def sign_in(self, email: str, password: str) -> AuthSession:
        payload = self._request(
            "/auth/v1/token?grant_type=password",
            {"email": email, "password": password},
        )
        access_token = payload.get("access_token")
        if not isinstance(access_token, str) or not access_token.strip():
            raise SupabaseAuthError("Supabase Auth returned no access token.")
        return AuthSession(
            user_id=self._user_id(payload.get("user")),
            access_token=access_token,
        )

    def _request(self, path: str, payload: dict[str, str]) -> dict[str, object]:
        if not self.supabase_url:
            raise SupabaseAuthError("SUPABASE_URL is not configured.")
        if not self.anon_key:
            raise SupabaseAuthError("SUPABASE_ANON_KEY is not configured.")

        try:
            response = self.http_client.post(
                f"{self.supabase_url}{path}",
                json=payload,
                headers={
                    "apikey": self.anon_key,
                    "Content-Type": "application/json",
                },
                timeout=15,
            )
            response.raise_for_status()
            data = response.json()
        except requests.RequestException as exc:
            raise SupabaseAuthError(
                f"Supabase Auth request failed ({type(exc).__name__})."
            ) from exc
        except ValueError as exc:
            raise SupabaseAuthError("Supabase Auth returned invalid JSON.") from exc

        if not isinstance(data, dict):
            raise SupabaseAuthError("Supabase Auth returned an invalid response.")
        return data

    @staticmethod
    def _user_id(user: object) -> UUID:
        if not isinstance(user, dict):
            raise SupabaseAuthError("Supabase Auth returned no user identity.")
        raw_user_id = user.get("id")
        if not isinstance(raw_user_id, str):
            raise SupabaseAuthError("Supabase Auth returned an invalid user identity.")
        try:
            return UUID(raw_user_id)
        except ValueError as exc:
            raise SupabaseAuthError(
                "Supabase Auth returned an invalid user identity."
            ) from exc
