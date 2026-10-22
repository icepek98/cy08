import os
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlparse

from xero.config import DEFAULT_REDIRECT_URI, XeroConfig, XeroConfigurationError
from xero.oauth import SCOPES, OAuthError, XeroOAuthClient, generate_state, validate_state


def response(payload, status=200):
    result = Mock()
    result.raise_for_status.return_value = None
    result.json.return_value = payload
    if status >= 400:
        from requests import HTTPError

        result.raise_for_status.side_effect = HTTPError("HTTP failure")
    return result


class XeroOAuthTests(unittest.TestCase):
    def test_configuration_requires_client_id(self):
        with self.assertRaises(XeroConfigurationError):
            XeroConfig.from_env({"XERO_CLIENT_SECRET": "secret"})

    def test_configuration_requires_client_secret(self):
        with self.assertRaises(XeroConfigurationError):
            XeroConfig.from_env({"XERO_CLIENT_ID": "client"})

    def test_configuration_valid_and_default_redirect(self):
        secret = "my-real-secret"
        config = XeroConfig.from_env({"XERO_CLIENT_ID": "client", "XERO_CLIENT_SECRET": secret})
        self.assertEqual(config.redirect_uri, DEFAULT_REDIRECT_URI)
        self.assertNotIn(secret, repr(config))

    def test_authorization_url_contains_only_approved_accounting_scopes(self):
        client = XeroOAuthClient(XeroConfig("client", "secret", "http://localhost/callback"))
        request = client.create_authorization_request()
        query = parse_qs(urlparse(request.url).query)
        self.assertTrue(request.state)
        self.assertEqual(query["state"], [request.state])
        self.assertEqual(query["scope"], [" ".join(SCOPES)])
        self.assertEqual(
            query["scope"][0].split(),
            [
                "accounting.settings.read",
                "accounting.contacts.read",
                "accounting.invoices.read",
            ],
        )

    def test_state_mismatch_is_rejected(self):
        with self.assertRaises(OAuthError):
            validate_state(generate_state(), generate_state())

    def test_invalid_oauth_response_is_rejected(self):
        client = XeroOAuthClient(XeroConfig("client", "secret"))
        with patch("xero.oauth.requests.post", return_value=response({"token_type": "Bearer"})):
            with self.assertRaisesRegex(OAuthError, "access_token"):
                client.exchange_code("code")

    def test_http_error_is_handled_without_response_body(self):
        client = XeroOAuthClient(XeroConfig("client", "secret"))
        with patch("xero.oauth.requests.post", return_value=response({}, 500)):
            with self.assertRaisesRegex(OAuthError, "token request failed"):
                client.exchange_code("code")

    def test_refresh_token_uses_mocked_response(self):
        client = XeroOAuthClient(XeroConfig("client", "secret"))
        mocked = response(
            {"access_token": "new-access", "refresh_token": "new-refresh", "expires_in": 1800}
        )
        with patch("xero.oauth.requests.post", return_value=mocked) as post:
            token = client.refresh_access_token("old-refresh")
        self.assertEqual(token.access_token, "new-access")
        self.assertEqual(token.refresh_token, "new-refresh")
        self.assertEqual(post.call_args.kwargs["data"]["grant_type"], "refresh_token")

    def test_connections_response_is_returned(self):
        client = XeroOAuthClient(XeroConfig("client", "secret"))
        with patch(
            "xero.oauth.requests.get",
            return_value=response([{"tenantId": "tenant"}]),
        ) as get_call:
            self.assertEqual(client.get_connections("access-token"), [{"tenantId": "tenant"}])
        self.assertEqual(
            get_call.call_args.kwargs["headers"]["Authorization"],
            "Bearer access-token",
        )
        self.assertNotIn("secret", get_call.call_args.kwargs["headers"]["Authorization"])

    def test_connections_sends_the_supplied_bearer_token(self):
        client = XeroOAuthClient(XeroConfig("client", "client-secret"))
        with patch(
            "xero.oauth.requests.get",
            return_value=response([{"tenantId": "tenant"}]),
        ) as get_call:
            client.get_connections("expected-access-token")

        headers = get_call.call_args.kwargs["headers"]
        self.assertEqual(headers["Authorization"], "Bearer expected-access-token")
        self.assertNotIn("client-secret", " ".join(headers.values()))

    def test_secrets_are_not_logged_or_exposed_in_errors(self):
        secret = os.environ.get("TEST_XERO_SECRET", "test-secret")
        client = XeroOAuthClient(XeroConfig("client", secret))
        with patch("xero.oauth.requests.post", return_value=response({}, 500)):
            with self.assertRaises(OAuthError) as error:
                client.exchange_code("code")
        self.assertNotIn(secret, str(error.exception))


if __name__ == "__main__":
    unittest.main()
