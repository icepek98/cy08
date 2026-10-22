import unittest
from unittest.mock import Mock
from uuid import UUID

import requests

from tenant_identity import TenantResolutionError, TenantResolver


class TenantResolverTests(unittest.TestCase):
    TENANT_ID = UUID("66d204ae-87bc-4ea5-8a37-a3d444ec7f34")

    def setUp(self):
        self.response = Mock()
        self.response.raise_for_status.return_value = None
        self.response.json.return_value = [{"tenant_id": str(self.TENANT_ID)}]
        self.http_client = Mock()
        self.http_client.post.return_value = self.response
        self.resolver = TenantResolver(
            supabase_url="https://example.supabase.co/",
            supabase_anon_key="public-anon-key",
            http_client=self.http_client,
        )

    def test_resolves_only_tenant_id_through_license_rpc(self):
        result = self.resolver.resolve_tenant_id("  license-key  ")

        self.assertEqual(result, self.TENANT_ID)
        self.http_client.post.assert_called_once_with(
            "https://example.supabase.co/rest/v1/rpc/resolve_tenant_for_license",
            json={"p_license_key": "license-key"},
            headers={
                "apikey": "public-anon-key",
                "Authorization": "Bearer public-anon-key",
                "Content-Type": "application/json",
            },
            timeout=15,
        )

    def test_returns_none_for_license_without_a_tenant_mapping(self):
        self.response.json.return_value = []

        self.assertIsNone(self.resolver.resolve_tenant_id("license-key"))

    def test_empty_license_key_does_not_make_a_request(self):
        self.assertIsNone(self.resolver.resolve_tenant_id("  "))
        self.http_client.post.assert_not_called()

    def test_missing_supabase_url_is_reported(self):
        resolver = TenantResolver(
            supabase_url="",
            supabase_anon_key="public-anon-key",
            http_client=self.http_client,
        )

        with self.assertRaisesRegex(TenantResolutionError, "SUPABASE_URL"):
            resolver.resolve_tenant_id("license-key")

    def test_missing_anon_key_is_reported(self):
        resolver = TenantResolver(
            supabase_url="https://example.supabase.co",
            supabase_anon_key="",
            http_client=self.http_client,
        )

        with self.assertRaisesRegex(TenantResolutionError, "SUPABASE_ANON_KEY"):
            resolver.resolve_tenant_id("license-key")

    def test_http_failure_is_reported(self):
        self.http_client.post.side_effect = requests.Timeout()

        with self.assertRaisesRegex(TenantResolutionError, "request failed"):
            self.resolver.resolve_tenant_id("license-key")

    def test_invalid_json_response_is_reported(self):
        self.response.json.side_effect = ValueError("invalid JSON")

        with self.assertRaisesRegex(TenantResolutionError, "invalid tenant"):
            self.resolver.resolve_tenant_id("license-key")

    def test_ambiguous_response_is_rejected(self):
        self.response.json.return_value = [
            {"tenant_id": str(self.TENANT_ID)},
            {"tenant_id": str(self.TENANT_ID)},
        ]

        with self.assertRaisesRegex(TenantResolutionError, "ambiguous"):
            self.resolver.resolve_tenant_id("license-key")

    def test_invalid_tenant_identifier_is_rejected(self):
        self.response.json.return_value = [{"tenant_id": "not-a-uuid"}]

        with self.assertRaisesRegex(TenantResolutionError, "invalid tenant"):
            self.resolver.resolve_tenant_id("license-key")


if __name__ == "__main__":
    unittest.main()
