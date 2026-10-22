import unittest
from unittest.mock import Mock
from uuid import UUID

import requests

from tenant_fiscal_config import (
    TenantFiscalConfigInvalidError,
    TenantFiscalConfigRpcError,
    TenantFiscalConfigResolver,
    TenantFiscalConfigResponseError,
    TenantFiscalConfigSupabaseConfigError,
    TenantFiscalConfigTransportError,
)


class TenantFiscalConfigResolverTests(unittest.TestCase):
    TENANT_ID = UUID("66d204ae-87bc-4ea5-8a37-a3d444ec7f34")
    OTHER_TENANT_ID = UUID("887308bf-0b1b-433e-8cbd-4686be14f948")

    def setUp(self):
        self.response = Mock()
        self.response.raise_for_status.return_value = None
        self.response.json.return_value = [self.valid_row()]
        self.http_client = Mock()
        self.http_client.post.return_value = self.response
        self.resolver = TenantFiscalConfigResolver(
            supabase_url="https://example.supabase.co/",
            supabase_anon_key="public-anon-key",
            http_client=self.http_client,
        )

    def valid_row(self, **overrides):
        row = {
            "tenant_id": str(self.TENANT_ID),
            "account_code": "  200  ",
            "tax_type": "OUTPUT",
            "tax_inclusive": False,
            "amount_basis": "subtotal",
            "discount_policy": "ignore",
        }
        row.update(overrides)
        return row

    def test_resolves_valid_config_and_preserves_strings_exactly(self):
        config = self.resolver.get_for_tenant(self.TENANT_ID, "  license-key ")

        self.assertEqual(config.tenant_id, self.TENANT_ID)
        self.assertEqual(config.account_code, "  200  ")
        self.assertEqual(config.tax_type, "OUTPUT")
        self.assertIs(config.tax_inclusive, False)
        self.assertEqual(config.amount_basis, "subtotal")
        self.assertEqual(config.discount_policy, "ignore")

    def test_queries_the_requested_tenant_with_its_license(self):
        self.resolver.get_for_tenant(self.TENANT_ID, "license-key")

        self.http_client.post.assert_called_once()
        args, kwargs = self.http_client.post.call_args
        self.assertEqual(
            args[0],
            "https://example.supabase.co/rest/v1/rpc/get_tenant_fiscal_config",
        )
        self.assertEqual(
            kwargs["json"],
            {
                "p_tenant_id": str(self.TENANT_ID),
                "p_license_key": "license-key",
            },
        )
        self.assertEqual(kwargs["headers"]["apikey"], "public-anon-key")
        self.assertEqual(
            kwargs["headers"]["Authorization"], f"Bearer {'public-anon-key'}"
        )
        self.assertEqual(kwargs["headers"]["Content-Type"], "application/json")

    def test_returns_none_when_authorized_tenant_has_no_configuration(self):
        self.response.json.return_value = []

        self.assertIsNone(
            self.resolver.get_for_tenant(self.TENANT_ID, "license-key")
        )

    def test_rejects_account_code_that_is_empty_after_whitespace_check(self):
        self.response.json.return_value = [
            self.valid_row(account_code="\u00a0\u2003\t\n")
        ]

        with self.assertRaisesRegex(TenantFiscalConfigInvalidError, "account_code"):
            self.resolver.get_for_tenant(self.TENANT_ID, "license-key")

    def test_rejects_empty_tax_type(self):
        self.response.json.return_value = [self.valid_row(tax_type="")]

        with self.assertRaisesRegex(TenantFiscalConfigInvalidError, "tax_type"):
            self.resolver.get_for_tenant(self.TENANT_ID, "license-key")

    def test_rejects_non_boolean_tax_inclusive(self):
        self.response.json.return_value = [self.valid_row(tax_inclusive=1)]

        with self.assertRaisesRegex(TenantFiscalConfigInvalidError, "tax_inclusive"):
            self.resolver.get_for_tenant(self.TENANT_ID, "license-key")

    def test_rejects_unsupported_amount_basis(self):
        self.response.json.return_value = [
            self.valid_row(amount_basis="discounted_subtotal")
        ]

        with self.assertRaisesRegex(TenantFiscalConfigInvalidError, "amount_basis"):
            self.resolver.get_for_tenant(self.TENANT_ID, "license-key")

    def test_rejects_unsupported_discount_policy(self):
        self.response.json.return_value = [
            self.valid_row(discount_policy="apply_automatically")
        ]

        with self.assertRaisesRegex(TenantFiscalConfigInvalidError, "discount_policy"):
            self.resolver.get_for_tenant(self.TENANT_ID, "license-key")

    def test_rejects_malformed_rpc_response_shape(self):
        self.response.json.return_value = {"tenant_id": str(self.TENANT_ID)}

        with self.assertRaises(TenantFiscalConfigResponseError):
            self.resolver.get_for_tenant(self.TENANT_ID, "license-key")

    def test_rejects_response_for_another_tenant(self):
        self.response.json.return_value = [
            self.valid_row(tenant_id=str(self.OTHER_TENANT_ID))
        ]

        with self.assertRaisesRegex(TenantFiscalConfigResponseError, "different tenant"):
            self.resolver.get_for_tenant(self.TENANT_ID, "license-key")

    def test_http_failure_is_reported_as_transport_error(self):
        self.http_client.post.side_effect = requests.Timeout()

        with self.assertRaises(TenantFiscalConfigTransportError):
            self.resolver.get_for_tenant(self.TENANT_ID, "license-key")

    def test_mock_rpc_ambiguity_http_error_is_not_missing_configuration(self):
        """Mocks the RPC error response; this does not execute the SQL function."""
        self.response.raise_for_status.side_effect = requests.HTTPError(
            "Ambiguous active license key."
        )

        with self.assertRaises(TenantFiscalConfigRpcError):
            self.resolver.get_for_tenant(self.TENANT_ID, "license-key")

    def test_missing_supabase_settings_are_reported(self):
        resolver = TenantFiscalConfigResolver(
            supabase_url="",
            supabase_anon_key="public-anon-key",
            http_client=self.http_client,
        )

        with self.assertRaises(TenantFiscalConfigSupabaseConfigError):
            resolver.get_for_tenant(self.TENANT_ID, "license-key")

    def test_empty_license_does_not_make_a_request(self):
        self.assertIsNone(self.resolver.get_for_tenant(self.TENANT_ID, " "))
        self.http_client.post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
