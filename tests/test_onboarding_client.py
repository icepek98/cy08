import unittest
from uuid import UUID

import requests

from installation_registration_contract import InstallationRegistrationRequest
from onboarding_client import OnboardingClient, OnboardingClientError
from supabase_auth import AuthSession


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


class OnboardingClientTests(unittest.TestCase):
    def setUp(self):
        self.installation_id = UUID("ff1610d3-b36a-4c80-83a0-8c2ff785fe9e")
        self.session = AuthSession(
            UUID("66d204ae-87bc-4ea5-8a37-a3d444ec7f34"),
            "user-access-token",
        )
        self.payload = {
            "tenant_id": "66d204ae-87bc-4ea5-8a37-a3d444ec7f34",
            "installation_id": str(self.installation_id),
            "license_status": "trialing",
            "trial_started_at": "2026-10-03T12:00:00Z",
            "trial_ends_at": "2026-11-02T12:00:00Z",
            "checked_at": "2026-10-03T12:00:01Z",
            "installation_registered": True,
            "access_allowed": True,
            "block_reason": None,
        }

    def test_sends_only_installation_id_with_authenticated_bearer(self):
        http = FakeHttpClient(FakeResponse(self.payload))
        client = OnboardingClient("https://example.supabase.co", "public-anon", http)

        result = client.register_installation(
            self.session,
            InstallationRegistrationRequest(self.installation_id),
        )

        url, call = http.calls[0]
        self.assertEqual(
            url,
            "https://example.supabase.co/functions/v1/fieldquo-onboarding",
        )
        self.assertEqual(call["json"], {"installation_id": str(self.installation_id)})
        authorization = call["headers"]["Authorization"]
        self.assertEqual(authorization.split(" ", 1)[0], "Bearer")
        self.assertEqual(authorization.split(" ", 1)[1], self.session.access_token)
        self.assertEqual(
            call["headers"]["Authorization"], "Bearer user-access-token"
        )
        self.assertEqual(call["headers"]["apikey"], "public-anon")
        self.assertTrue(result.access_allowed)

    def test_rejects_server_returning_a_different_installation(self):
        payload = dict(
            self.payload,
            installation_id="22ad825c-89c1-4fde-a2fa-5b5583e53508",
        )
        http = FakeHttpClient(FakeResponse(payload))
        client = OnboardingClient("https://example.supabase.co", "public-anon", http)

        with self.assertRaises(OnboardingClientError):
            client.register_installation(
                self.session,
                InstallationRegistrationRequest(self.installation_id),
            )

    def test_no_session_or_configuration_means_no_access_request(self):
        http = FakeHttpClient(FakeResponse(self.payload))
        client = OnboardingClient("", "public-anon", http)

        with self.assertRaises(OnboardingClientError):
            client.register_installation(
                self.session,
                InstallationRegistrationRequest(self.installation_id),
            )
        self.assertEqual(http.calls, [])

    def test_backend_error_is_not_converted_to_a_license_grant(self):
        http = FakeHttpClient(FakeResponse({"error": "onboarding_unavailable"}, 503))
        client = OnboardingClient("https://example.supabase.co", "public-anon", http)

        with self.assertRaises(OnboardingClientError):
            client.register_installation(
                self.session,
                InstallationRegistrationRequest(self.installation_id),
            )


if __name__ == "__main__":
    unittest.main()
