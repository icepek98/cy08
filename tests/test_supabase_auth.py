import unittest
from uuid import UUID

import requests

from supabase_auth import SupabaseAuthClient, SupabaseAuthError


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(response=self)

    def json(self):
        return self.payload


class FakeHttpClient:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


class SupabaseAuthTests(unittest.TestCase):
    USER_ID = "66d204ae-87bc-4ea5-8a37-a3d444ec7f34"

    def test_sign_up_uses_public_key_and_detects_confirmation(self):
        http = FakeHttpClient(FakeResponse({"user": {"id": self.USER_ID}}))
        client = SupabaseAuthClient(
            "https://example.supabase.co", "public-anon-key", http
        )

        result = client.sign_up("user@example.test", "a long password")

        self.assertEqual(result.user_id, UUID(self.USER_ID))
        self.assertTrue(result.confirmation_required)
        self.assertEqual(http.calls[0][0], "https://example.supabase.co/auth/v1/signup")
        self.assertEqual(http.calls[0][1]["headers"]["apikey"], "public-anon-key")
        self.assertNotIn("Authorization", http.calls[0][1]["headers"])

    def test_sign_in_keeps_only_access_token_in_memory(self):
        http = FakeHttpClient(
            FakeResponse(
                {
                    "access_token": "user-access-token",
                    "refresh_token": "refresh-token-not-retained",
                    "user": {"id": self.USER_ID},
                }
            )
        )
        client = SupabaseAuthClient(
            "https://example.supabase.co", "public-anon-key", http
        )

        session = client.sign_in("user@example.test", "a long password")

        self.assertEqual(session.user_id, UUID(self.USER_ID))
        self.assertEqual(session.access_token, "user-access-token")
        self.assertFalse(hasattr(session, "refresh_token"))
        self.assertEqual(
            http.calls[0][0],
            "https://example.supabase.co/auth/v1/token?grant_type=password",
        )

    def test_rejects_missing_configuration_and_invalid_response(self):
        with self.assertRaises(SupabaseAuthError):
            SupabaseAuthClient("", "anon").sign_in("user@example.test", "password")

        http = FakeHttpClient(FakeResponse({"access_token": "token"}))
        with self.assertRaises(SupabaseAuthError):
            SupabaseAuthClient("https://example.supabase.co", "anon", http).sign_in(
                "user@example.test", "password"
            )

    def test_auth_error_does_not_echo_password_or_response_body(self):
        http = FakeHttpClient(FakeResponse({"error": "bad credentials"}, 400))
        client = SupabaseAuthClient("https://example.supabase.co", "anon", http)

        with self.assertRaises(SupabaseAuthError) as raised:
            client.sign_in("user@example.test", "secret-password")

        self.assertNotIn("secret-password", str(raised.exception))
        self.assertNotIn("bad credentials", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
