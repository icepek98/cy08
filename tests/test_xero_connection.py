import unittest
from unittest.mock import Mock
from uuid import UUID

import requests

from xero_connection import (
    XeroConnectionAmbiguousLicenseError,
    XeroConnectionInvalidError,
    XeroConnectionResolver,
    XeroConnectionResponseError,
    XeroConnectionRpcError,
    XeroConnectionSupabaseConfigError,
    XeroConnectionTransportError,
)


class XeroConnectionResolverTests(unittest.TestCase):
    TENANT_ID = UUID("66d204ae-87bc-4ea5-8a37-a3d444ec7f34")
    OTHER_TENANT_ID = UUID("887308bf-0b1b-433e-8cbd-4686be14f948")

    def setUp(self):
        self.response = Mock()
        self.response.raise_for_status.return_value = None
        self.response.json.return_value = [self.valid_row()]
        self.http_client = Mock()
        self.http_client.post.return_value = self.response
        self.resolver = XeroConnectionResolver(
            supabase_url="https://example.supabase.co/",
            supabase_anon_key="public-anon-key",
            http_client=self.http_client,
        )

    def valid_row(self, **overrides):
        row = {
            "tenant_id": str(self.TENANT_ID),
            "xero_tenant_id": "  Xero-Org-ID  ",
            "xero_tenant_name": "Example Organization",
            "xero_tenant_type": "ORGANISATION",
        }
        row.update(overrides)
        return row

    def test_resolves_connection_and_preserves_both_tenant_identifiers(self):
        connection = self.resolver.get_for_tenant(self.TENANT_ID, "license-key")

        self.assertEqual(connection.tenant_id, self.TENANT_ID)
        self.assertEqual(connection.xero_tenant_id, "  Xero-Org-ID  ")
        self.assertNotEqual(str(connection.tenant_id), connection.xero_tenant_id)
        self.assertEqual(connection.xero_tenant_name, "Example Organization")
        self.assertEqual(connection.xero_tenant_type, "ORGANISATION")

    def test_sends_tenant_license_and_public_bearer_credentials_to_rpc(self):
        self.resolver.get_for_tenant(self.TENANT_ID, "  license-key  ")

        self.http_client.post.assert_called_once_with(
            "https://example.supabase.co/rest/v1/rpc/get_xero_connection",
            json={
                "p_tenant_id": str(self.TENANT_ID),
                "p_license_key": "license-key",
            },
            headers={
                "apikey": "public-anon-key",
                "Authorization": "Bearer public-anon-key",
                "Content-Type": "application/json",
            },
            timeout=15,
        )

    def test_empty_license_is_rejected_without_a_request(self):
        with self.assertRaises(XeroConnectionInvalidError):
            self.resolver.get_for_tenant(self.TENANT_ID, "  ")

        self.http_client.post.assert_not_called()

    def test_zero_rpc_rows_returns_none_without_claiming_authorization(self):
        self.response.json.return_value = []

        self.assertIsNone(
            self.resolver.get_for_tenant(self.TENANT_ID, "license-key")
        )

    def test_tenant_a_cannot_receive_tenant_b_connection(self):
        self.response.json.return_value = [
            self.valid_row(tenant_id=str(self.OTHER_TENANT_ID))
        ]

        with self.assertRaisesRegex(
            XeroConnectionResponseError, "different tenant"
        ):
            self.resolver.get_for_tenant(self.TENANT_ID, "license-key")

    def test_missing_xero_tenant_id_is_rejected(self):
        row = self.valid_row()
        del row["xero_tenant_id"]
        self.response.json.return_value = [row]

        with self.assertRaises(XeroConnectionResponseError):
            self.resolver.get_for_tenant(self.TENANT_ID, "license-key")

    def test_empty_xero_tenant_id_is_rejected(self):
        self.response.json.return_value = [
            self.valid_row(xero_tenant_id="\u00a0\u2003\t\n")
        ]

        with self.assertRaisesRegex(XeroConnectionInvalidError, "xero_tenant_id"):
            self.resolver.get_for_tenant(self.TENANT_ID, "license-key")

    def test_invalid_field_types_are_rejected(self):
        invalid_rows = (
            (self.valid_row(tenant_id=42), XeroConnectionResponseError),
            (self.valid_row(xero_tenant_id=42), XeroConnectionInvalidError),
            (self.valid_row(xero_tenant_name=42), XeroConnectionInvalidError),
            (self.valid_row(xero_tenant_type=42), XeroConnectionInvalidError),
        )
        for row, error_type in invalid_rows:
            with self.subTest(row=row):
                self.response.json.return_value = [row]
                with self.assertRaises(error_type):
                    self.resolver.get_for_tenant(self.TENANT_ID, "license-key")

    def test_malformed_rpc_response_is_rejected(self):
        self.response.json.return_value = {"tenant_id": str(self.TENANT_ID)}

        with self.assertRaises(XeroConnectionResponseError):
            self.resolver.get_for_tenant(self.TENANT_ID, "license-key")

    def test_invalid_json_is_reported_as_malformed_response(self):
        self.response.json.side_effect = ValueError("invalid JSON")

        with self.assertRaises(XeroConnectionResponseError):
            self.resolver.get_for_tenant(self.TENANT_ID, "license-key")

    def test_http_failure_is_reported(self):
        self.http_client.post.side_effect = requests.HTTPError(
            response=Mock(status_code=503)
        )

        with self.assertRaises(XeroConnectionRpcError):
            self.resolver.get_for_tenant(self.TENANT_ID, "license-key")

    def test_transport_failure_is_reported(self):
        self.http_client.post.side_effect = requests.Timeout()

        with self.assertRaises(XeroConnectionTransportError):
            self.resolver.get_for_tenant(self.TENANT_ID, "license-key")

    def test_ambiguous_license_rpc_error_is_reported_explicitly(self):
        error_response = Mock()
        error_response.status_code = 400
        error_response.json.return_value = {
            "code": "P0001",
            "message": "Ambiguous active license key.",
        }
        self.response.raise_for_status.side_effect = requests.HTTPError(
            response=error_response
        )

        with self.assertRaises(XeroConnectionAmbiguousLicenseError):
            self.resolver.get_for_tenant(self.TENANT_ID, "license-key")

    def test_missing_supabase_settings_are_reported(self):
        resolver = XeroConnectionResolver(
            supabase_url="",
            supabase_anon_key="public-anon-key",
            http_client=self.http_client,
        )
        with self.assertRaises(XeroConnectionSupabaseConfigError):
            resolver.get_for_tenant(self.TENANT_ID, "license-key")

        resolver = XeroConnectionResolver(
            supabase_url="https://example.supabase.co",
            supabase_anon_key="",
            http_client=self.http_client,
        )
        with self.assertRaises(XeroConnectionSupabaseConfigError):
            resolver.get_for_tenant(self.TENANT_ID, "license-key")

    def test_response_rejects_token_fields_and_model_does_not_store_tokens(self):
        self.response.json.return_value = [
            self.valid_row(access_token="token", refresh_token="refresh")
        ]

        with self.assertRaises(XeroConnectionResponseError):
            self.resolver.get_for_tenant(self.TENANT_ID, "license-key")

        self.response.json.return_value = [self.valid_row()]
        connection = self.resolver.get_for_tenant(self.TENANT_ID, "license-key")
        self.assertFalse(hasattr(connection, "access_token"))
        self.assertFalse(hasattr(connection, "refresh_token"))


if __name__ == "__main__":
    unittest.main()
