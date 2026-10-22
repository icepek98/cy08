"""Environment-based configuration for Xero OAuth."""

from dataclasses import dataclass, field
import os
from typing import Mapping


DEFAULT_REDIRECT_URI = "http://localhost:8765/xero/callback"


class XeroConfigurationError(ValueError):
    """Raised when required Xero configuration is missing."""


@dataclass(frozen=True)
class XeroConfig:
    """Runtime Xero OAuth configuration.

    The client secret is deliberately excluded from the representation so it
    cannot be exposed accidentally in logs or diagnostics.
    """

    client_id: str
    client_secret: str = field(repr=False)
    redirect_uri: str = DEFAULT_REDIRECT_URI

    def __repr__(self) -> str:
        return (
            f"XeroConfig(client_id={self.client_id!r}, "
            "client_secret=<redacted>, "
            f"redirect_uri={self.redirect_uri!r})"
        )

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "XeroConfig":
        values = os.environ if environ is None else environ
        client_id = values.get("XERO_CLIENT_ID", "").strip()
        client_secret = values.get("XERO_CLIENT_SECRET", "").strip()
        redirect_uri = values.get("XERO_REDIRECT_URI", DEFAULT_REDIRECT_URI).strip()

        missing = [
            name
            for name, value in (
                ("XERO_CLIENT_ID", client_id),
                ("XERO_CLIENT_SECRET", client_secret),
            )
            if not value
        ]
        if missing:
            raise XeroConfigurationError(
                "Missing required Xero configuration: " + ", ".join(missing)
            )
        if not redirect_uri:
            raise XeroConfigurationError("XERO_REDIRECT_URI must not be empty")
        return cls(client_id, client_secret, redirect_uri)
