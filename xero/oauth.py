"""Small, dependency-light Xero OAuth 2.0 client."""

from dataclasses import dataclass
import secrets
from typing import Any
from urllib.parse import urlencode

import requests

from .config import XeroConfig


AUTHORIZATION_ENDPOINT = "https://login.xero.com/identity/connect/authorize"
TOKEN_ENDPOINT = "https://identity.xero.com/connect/token"
CONNECTIONS_ENDPOINT = "https://api.xero.com/connections"
SCOPES = (
    "accounting.settings.read",
    "accounting.contacts.read",
    "accounting.invoices.read",
)


class OAuthError(RuntimeError):
    """Raised when an OAuth operation returns an unusable result."""


@dataclass(frozen=True)
class AuthorizationRequest:
    url: str
    state: str


@dataclass(frozen=True)
class TokenResponse:
    access_token: str
    refresh_token: str | None
    token_type: str
    expires_in: int | None


def generate_state() -> str:
    """Generate a cryptographically secure CSRF state value."""

    return secrets.token_urlsafe(32)


def validate_state(expected: str, received: str) -> None:
    """Validate the callback state, raising on mismatch."""

    if not expected or not received or not secrets.compare_digest(expected, received):
        raise OAuthError("OAuth state validation failed")


class XeroOAuthClient:
    """In-memory OAuth operations; no tokens are persisted."""

    def __init__(self, config: XeroConfig):
        self.config = config

    def authorization_url(self, state: str) -> str:
        if not state:
            raise OAuthError("OAuth state must not be empty")
        query = urlencode(
            {
                "response_type": "code",
                "client_id": self.config.client_id,
                "redirect_uri": self.config.redirect_uri,
                "scope": " ".join(SCOPES),
                "state": state,
            }
        )
        return f"{AUTHORIZATION_ENDPOINT}?{query}"

    def create_authorization_request(self) -> AuthorizationRequest:
        state = generate_state()
        return AuthorizationRequest(self.authorization_url(state), state)

    def exchange_code(self, code: str) -> TokenResponse:
        if not code:
            raise OAuthError("Authorization code must not be empty")
        return self._token_request(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self.config.redirect_uri,
            }
        )

    def refresh_access_token(self, refresh_token: str) -> TokenResponse:
        if not refresh_token:
            raise OAuthError("Refresh token must not be empty")
        return self._token_request(
            {"grant_type": "refresh_token", "refresh_token": refresh_token}
        )

    def get_connections(self, access_token: str) -> list[dict[str, Any]]:
        if not access_token:
            raise OAuthError("Access token must not be empty")
        try:
            response = requests.get(
                CONNECTIONS_ENDPOINT,
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=15,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            raise OAuthError(f"Xero connections request failed ({exc.__class__.__name__})") from exc
        payload = _json_object(response, "connections")
        if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
            raise OAuthError("Xero connections response has an invalid format")
        return payload

    def _token_request(self, data: dict[str, str]) -> TokenResponse:
        try:
            response = requests.post(
                TOKEN_ENDPOINT,
                data=data,
                auth=(self.config.client_id, self.config.client_secret),
                headers={"Accept": "application/json"},
                timeout=15,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            raise OAuthError(f"Xero token request failed ({exc.__class__.__name__})") from exc
        payload = _json_object(response, "token")
        if not isinstance(payload, dict):
            raise OAuthError("Xero token response has an invalid format")
        access_token = payload.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise OAuthError("Xero token response is missing access_token")
        refresh_token = payload.get("refresh_token")
        if refresh_token is not None and not isinstance(refresh_token, str):
            raise OAuthError("Xero token response has an invalid refresh_token")
        token_type = payload.get("token_type", "Bearer")
        if not isinstance(token_type, str) or not token_type:
            raise OAuthError("Xero token response has an invalid token_type")
        expires_in = payload.get("expires_in")
        if expires_in is not None and not isinstance(expires_in, int):
            raise OAuthError("Xero token response has an invalid expires_in")
        return TokenResponse(access_token, refresh_token, token_type, expires_in)


def _json_object(response: Any, operation: str) -> Any:
    try:
        payload = response.json()
    except (ValueError, TypeError) as exc:
        raise OAuthError(f"Xero {operation} response is not valid JSON") from exc
    if not isinstance(payload, (dict, list)):
        raise OAuthError(f"Xero {operation} response has an invalid format")
    return payload
