"""Backend policy for authorizing tenant-scoped connection operations.

Concrete authentication, tenant-association, and license adapters must be
provided by a trusted backend. This module performs no network requests.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID


class TenantAuthorizationError(RuntimeError):
    """Base class for fail-closed tenant authorization failures."""


class InvalidAuthorizationRequestError(TenantAuthorizationError):
    """Raised when the request attempts to supply authorization context."""


class AuthenticationRequiredError(TenantAuthorizationError):
    """Raised when no valid authenticated user can be established."""


class AuthenticationBackendError(TenantAuthorizationError):
    """Raised when the trusted authentication adapter is unavailable."""


class TenantAssociationNotFoundError(TenantAuthorizationError):
    """Raised when the authenticated user has no tenant association."""


class TenantAssociationAmbiguousError(TenantAuthorizationError):
    """Raised when the authenticated user resolves to multiple tenants."""


class TenantAssociationBackendError(TenantAuthorizationError):
    """Raised when the trusted tenant-association adapter is unavailable."""


class TenantLicenseDeniedError(TenantAuthorizationError):
    """Raised when the resolved tenant is not eligible for the operation."""


class TenantLicenseBackendError(TenantAuthorizationError):
    """Raised when the trusted license adapter is unavailable or malformed."""


@dataclass(frozen=True)
class AuthenticatedUser:
    """Identity returned by a trusted session-verification adapter."""

    user_id: UUID


@dataclass(frozen=True)
class AuthorizedTenantContext:
    """Server-derived authorization context; contains no credentials."""

    user_id: UUID
    tenant_id: UUID


class SessionAuthenticator(Protocol):
    def authenticate(self, access_token: str) -> AuthenticatedUser | None: ...


class TenantAssociationResolver(Protocol):
    def resolve_tenants_for_user(self, user_id: UUID) -> Sequence[UUID]: ...


class TenantLicenseAuthorizer(Protocol):
    def validate_tenant_license(self, tenant_id: UUID) -> tuple[bool, str]: ...


class TenantConnectionAuthorizer:
    """Authenticate a request, derive its tenant, then validate its license.

    The request body is intentionally empty for this authorization boundary.
    Tenant IDs and other authorization claims must come from trusted backend
    adapters, never from client-supplied fields.
    """

    def __init__(
        self,
        *,
        authenticator: SessionAuthenticator,
        tenant_resolver: TenantAssociationResolver,
        license_authorizer: TenantLicenseAuthorizer,
    ) -> None:
        self._authenticator = authenticator
        self._tenant_resolver = tenant_resolver
        self._license_authorizer = license_authorizer

    def authorize(
        self,
        authorization_header: str | None,
        request_body: Mapping[str, object] | None = None,
    ) -> AuthorizedTenantContext:
        self._validate_request_body(request_body)
        access_token = self._extract_bearer_token(authorization_header)

        try:
            identity = self._authenticator.authenticate(access_token)
        except Exception:
            raise AuthenticationBackendError(
                "Authentication backend unavailable."
            ) from None
        if identity is None:
            raise AuthenticationRequiredError("A valid authenticated session is required.")
        if not isinstance(identity, AuthenticatedUser) or not isinstance(
            identity.user_id, UUID
        ):
            raise AuthenticationBackendError(
                "Authentication adapter returned an invalid identity."
            )

        try:
            tenant_ids = self._tenant_resolver.resolve_tenants_for_user(
                identity.user_id
            )
        except Exception:
            raise TenantAssociationBackendError(
                "Tenant association backend unavailable."
            ) from None
        if not isinstance(tenant_ids, Sequence) or isinstance(
            tenant_ids, (str, bytes)
        ):
            raise TenantAssociationBackendError(
                "Tenant adapter returned an invalid association result."
            )
        if not tenant_ids:
            raise TenantAssociationNotFoundError(
                "The authenticated user has no authorized tenant."
            )
        if len(tenant_ids) != 1:
            raise TenantAssociationAmbiguousError(
                "The authenticated user has an ambiguous tenant association."
            )

        tenant_id = tenant_ids[0]
        if not isinstance(tenant_id, UUID):
            raise TenantAssociationBackendError(
                "Tenant adapter returned an invalid tenant identifier."
            )

        try:
            license_result = self._license_authorizer.validate_tenant_license(
                tenant_id
            )
        except Exception:
            raise TenantLicenseBackendError(
                "Tenant license backend unavailable."
            ) from None
        if (
            not isinstance(license_result, tuple)
            or len(license_result) != 2
            or type(license_result[0]) is not bool
            or not isinstance(license_result[1], str)
        ):
            raise TenantLicenseBackendError(
                "License adapter returned an invalid result."
            )
        if license_result[0] is not True:
            raise TenantLicenseDeniedError("The tenant is not eligible.")

        return AuthorizedTenantContext(
            user_id=identity.user_id,
            tenant_id=tenant_id,
        )

    @staticmethod
    def _validate_request_body(
        request_body: Mapping[str, object] | None,
    ) -> None:
        if request_body is None:
            return
        if not isinstance(request_body, Mapping):
            raise InvalidAuthorizationRequestError("Request body must be an object.")
        if request_body:
            raise InvalidAuthorizationRequestError(
                "Client-supplied authorization fields are not accepted."
            )

    @staticmethod
    def _extract_bearer_token(authorization_header: str | None) -> str:
        if not isinstance(authorization_header, str):
            raise AuthenticationRequiredError(
                "A valid authenticated session is required."
            )
        scheme, separator, token = authorization_header.partition(" ")
        if (
            not separator
            or scheme.lower() != "bearer"
            or not token.strip()
            or any(character.isspace() for character in token.strip())
        ):
            raise AuthenticationRequiredError(
                "A valid authenticated session is required."
            )
        return token.strip()
