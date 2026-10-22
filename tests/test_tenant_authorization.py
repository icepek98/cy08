import os
import unittest
from unittest.mock import Mock, patch
from uuid import UUID

from auth import LicenseValidator
from tenant_authorization import (
    AuthenticatedUser,
    AuthenticationBackendError,
    AuthenticationRequiredError,
    AuthorizedTenantContext,
    InvalidAuthorizationRequestError,
    TenantAssociationAmbiguousError,
    TenantAssociationBackendError,
    TenantAssociationNotFoundError,
    TenantConnectionAuthorizer,
    TenantLicenseBackendError,
    TenantLicenseDeniedError,
)


class FakeAuthenticator:
    def __init__(self, identity):
        self.identity = identity
        self.error = None
        self.received_token = None

    def authenticate(self, access_token):
        self.received_token = access_token
        if self.error is not None:
            raise self.error
        return self.identity


class FakeTenantResolver:
    def __init__(self, tenant_ids):
        self.tenant_ids = tenant_ids
        self.error = None
        self.received_user_id = None

    def resolve_tenants_for_user(self, user_id):
        self.received_user_id = user_id
        if self.error is not None:
            raise self.error
        return self.tenant_ids


class FakeLicenseAuthorizer:
    def __init__(self, result):
        self.result = result
        self.error = None
        self.received_tenant_id = None

    def validate_tenant_license(self, tenant_id):
        self.received_tenant_id = tenant_id
        if self.error is not None:
            raise self.error
        return self.result


class TenantConnectionAuthorizerTests(unittest.TestCase):
    USER_ID = UUID("d0d3a64b-21d0-4d5a-8e72-1b751f16ed21")
    TENANT_ID = UUID("de420f83-ecbd-432e-b0a8-38c038614146")
    OTHER_TENANT_ID = UUID("911857db-8601-4599-9e7f-373751cfb850")

    def make_authorizer(
        self,
        *,
        identity=...,
        tenant_ids=(),
        license_result=(True, "ok"),
    ):
        self.authenticator = FakeAuthenticator(
            AuthenticatedUser(self.USER_ID) if identity is ... else identity
        )
        self.tenant_resolver = FakeTenantResolver(tenant_ids)
        self.license_authorizer = FakeLicenseAuthorizer(license_result)
        return TenantConnectionAuthorizer(
            authenticator=self.authenticator,
            tenant_resolver=self.tenant_resolver,
            license_authorizer=self.license_authorizer,
        )

    def test_resolves_tenant_from_authenticated_user_then_checks_license(self):
        authorizer = self.make_authorizer(tenant_ids=(self.TENANT_ID,))

        result = authorizer.authorize("Bearer session-token")

        self.assertEqual(
            result,
            AuthorizedTenantContext(self.USER_ID, self.TENANT_ID),
        )
        self.assertEqual(self.authenticator.received_token, "session-token")
        self.assertEqual(self.tenant_resolver.received_user_id, self.USER_ID)
        self.assertEqual(self.license_authorizer.received_tenant_id, self.TENANT_ID)
        self.assertNotIn("token", repr(result).lower())

    def test_missing_or_malformed_session_is_rejected(self):
        authorizer = self.make_authorizer(tenant_ids=(self.TENANT_ID,))

        for header in (None, "", "Basic session-token", "Bearer ", "Bearer a b"):
            with self.subTest(header=header):
                with self.assertRaises(AuthenticationRequiredError):
                    authorizer.authorize(header)

        self.assertIsNone(self.authenticator.received_token)

    def test_invalid_session_is_rejected_before_tenant_or_license_lookup(self):
        authorizer = self.make_authorizer(identity=None)

        with self.assertRaises(AuthenticationRequiredError):
            authorizer.authorize("Bearer invalid-session")

        self.assertIsNone(self.tenant_resolver.received_user_id)
        self.assertIsNone(self.license_authorizer.received_tenant_id)

    def test_authenticator_timeout_fails_closed_without_leaking_details(self):
        authorizer = self.make_authorizer(tenant_ids=(self.TENANT_ID,))
        self.authenticator.error = TimeoutError("secret-auth-detail")

        with self.assertRaisesRegex(
            AuthenticationBackendError, "Authentication backend unavailable"
        ) as error:
            authorizer.authorize("Bearer session-token")

        self.assertNotIn("secret-auth-detail", str(error.exception))
        self.assertIsNone(self.tenant_resolver.received_user_id)
        self.assertIsNone(self.license_authorizer.received_tenant_id)

    def test_unexpected_authenticator_exception_is_sanitized(self):
        authorizer = self.make_authorizer(tenant_ids=(self.TENANT_ID,))
        self.authenticator.error = RuntimeError("secret-auth-detail")

        with self.assertRaises(AuthenticationBackendError) as error:
            authorizer.authorize("Bearer session-token")

        self.assertNotIn("secret-auth-detail", str(error.exception))
        self.assertIsNone(error.exception.__cause__)

    def test_user_without_tenant_is_rejected_before_license_validation(self):
        authorizer = self.make_authorizer(tenant_ids=())

        with self.assertRaises(TenantAssociationNotFoundError):
            authorizer.authorize("Bearer session-token")

        self.assertIsNone(self.license_authorizer.received_tenant_id)

    def test_ambiguous_tenant_association_is_rejected(self):
        authorizer = self.make_authorizer(
            tenant_ids=(self.TENANT_ID, self.OTHER_TENANT_ID)
        )

        with self.assertRaises(TenantAssociationAmbiguousError):
            authorizer.authorize("Bearer session-token")

        self.assertIsNone(self.license_authorizer.received_tenant_id)

    def test_malformed_tenant_association_is_rejected(self):
        authorizer = self.make_authorizer(tenant_ids=("untrusted-tenant-id",))

        with self.assertRaises(TenantAssociationBackendError):
            authorizer.authorize("Bearer session-token")

        self.assertIsNone(self.license_authorizer.received_tenant_id)

    def test_tenant_resolver_timeout_fails_closed_without_leaking_details(self):
        authorizer = self.make_authorizer(tenant_ids=(self.TENANT_ID,))
        self.tenant_resolver.error = TimeoutError("secret-tenant-detail")

        with self.assertRaisesRegex(
            TenantAssociationBackendError,
            "Tenant association backend unavailable",
        ) as error:
            authorizer.authorize("Bearer session-token")

        self.assertNotIn("secret-tenant-detail", str(error.exception))
        self.assertIsNone(self.license_authorizer.received_tenant_id)

    def test_unexpected_tenant_resolver_exception_is_sanitized(self):
        authorizer = self.make_authorizer(tenant_ids=(self.TENANT_ID,))
        self.tenant_resolver.error = RuntimeError("secret-tenant-detail")

        with self.assertRaises(TenantAssociationBackendError) as error:
            authorizer.authorize("Bearer session-token")

        self.assertNotIn("secret-tenant-detail", str(error.exception))
        self.assertIsNone(error.exception.__cause__)
        self.assertIsNone(self.license_authorizer.received_tenant_id)

    def test_invalid_license_is_denied_after_identity_resolution(self):
        authorizer = self.make_authorizer(
            tenant_ids=(self.TENANT_ID,),
            license_result=(False, "license inactive"),
        )

        with self.assertRaises(TenantLicenseDeniedError):
            authorizer.authorize("Bearer session-token")

        self.assertEqual(self.license_authorizer.received_tenant_id, self.TENANT_ID)

    def test_malformed_license_result_fails_closed(self):
        authorizer = self.make_authorizer(
            tenant_ids=(self.TENANT_ID,),
            license_result=(1, "not a bool"),
        )

        with self.assertRaises(TenantLicenseBackendError):
            authorizer.authorize("Bearer session-token")

    def test_license_timeout_fails_closed_without_leaking_details(self):
        authorizer = self.make_authorizer(tenant_ids=(self.TENANT_ID,))
        self.license_authorizer.error = TimeoutError("secret-license-detail")

        with self.assertRaisesRegex(
            TenantLicenseBackendError, "Tenant license backend unavailable"
        ) as error:
            authorizer.authorize("Bearer session-token")

        self.assertNotIn("secret-license-detail", str(error.exception))
        self.assertEqual(
            self.license_authorizer.received_tenant_id, self.TENANT_ID
        )

    def test_unexpected_license_exception_is_sanitized(self):
        authorizer = self.make_authorizer(tenant_ids=(self.TENANT_ID,))
        self.license_authorizer.error = RuntimeError("secret-license-detail")

        with self.assertRaises(TenantLicenseBackendError) as error:
            authorizer.authorize("Bearer session-token")

        self.assertNotIn("secret-license-detail", str(error.exception))
        self.assertIsNone(error.exception.__cause__)

    def test_client_cannot_supply_or_forge_tenant_id(self):
        authorizer = self.make_authorizer(tenant_ids=(self.TENANT_ID,))

        with self.assertRaises(InvalidAuthorizationRequestError):
            authorizer.authorize(
                "Bearer session-token",
                {"tenant_id": str(self.OTHER_TENANT_ID)},
            )

        self.assertIsNone(self.authenticator.received_token)
        self.assertIsNone(self.license_authorizer.received_tenant_id)

    def test_access_is_scoped_to_the_server_resolved_tenant(self):
        authorizer = self.make_authorizer(tenant_ids=(self.TENANT_ID,))

        result = authorizer.authorize("Bearer session-token")

        self.assertEqual(result.tenant_id, self.TENANT_ID)
        self.assertNotEqual(result.tenant_id, self.OTHER_TENANT_ID)
        self.assertEqual(self.license_authorizer.received_tenant_id, result.tenant_id)

    def test_tenant_license_authorizer_uses_bool_and_message_result_shape(self):
        authorizer = self.make_authorizer(
            tenant_ids=(self.TENANT_ID,),
            license_result=(True, "license valid"),
        )

        result = authorizer.authorize("Bearer session-token")

        self.assertIsInstance(result, AuthorizedTenantContext)
        self.assertEqual(self.license_authorizer.result, (True, "license valid"))

    def test_existing_license_validator_still_returns_bool_and_message(self):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = [
            {"ativo": True, "data_expiracao": None}
        ]

        with patch.dict(
            os.environ,
            {
                "SUPABASE_URL": "https://offline.example",
                "SUPABASE_ANON_KEY": "offline-public-key",
            },
        ), patch("auth.requests.get", return_value=response):
            result = LicenseValidator().validate_license("offline-license")

        self.assertIsInstance(result, tuple)
        self.assertEqual(len(result), 2)
        self.assertIs(type(result[0]), bool)
        self.assertIsInstance(result[1], str)
        self.assertTrue(result[0])


if __name__ == "__main__":
    unittest.main()
