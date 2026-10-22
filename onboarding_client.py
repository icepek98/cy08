"""Desktop client for the authenticated Supabase onboarding Edge Function."""

from __future__ import annotations

import os

import requests

from installation_registration_contract import InstallationRegistrationRequest
from onboarding_contract import OnboardingContractError, OnboardingResult, parse_onboarding_result
from supabase_auth import AuthSession


class OnboardingClientError(RuntimeError):
    """Raised when onboarding cannot be completed safely."""


class OnboardingClient:
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

    def register_installation(
        self,
        session: AuthSession,
        request: InstallationRegistrationRequest,
    ) -> OnboardingResult:
        if not self.supabase_url:
            raise OnboardingClientError("SUPABASE_URL is not configured.")
        if not self.anon_key:
            raise OnboardingClientError("SUPABASE_ANON_KEY is not configured.")
        if not isinstance(session, AuthSession) or not session.access_token:
            raise OnboardingClientError("An authenticated session is required.")
        if not isinstance(request, InstallationRegistrationRequest):
            raise OnboardingClientError("A valid installation registration is required.")

        try:
            response = self.http_client.post(
                f"{self.supabase_url}/functions/v1/fieldquo-onboarding",
                json=request.to_payload(),
                headers={
                    "apikey": self.anon_key,
                    "Authorization": f"Bearer {session.access_token}",
                    "Content-Type": "application/json",
                },
                timeout=20,
            )
            response.raise_for_status()
            payload = response.json()
        except requests.RequestException as exc:
            raise OnboardingClientError(
                f"Onboarding request failed ({type(exc).__name__})."
            ) from exc
        except ValueError as exc:
            raise OnboardingClientError("Onboarding returned invalid JSON.") from exc

        try:
            result = parse_onboarding_result(payload)
        except OnboardingContractError as exc:
            raise OnboardingClientError(
                "Onboarding returned an invalid response contract."
            ) from exc

        if result.installation_id != request.installation_id:
            raise OnboardingClientError(
                "Onboarding returned a different installation identifier."
            )
        return result
