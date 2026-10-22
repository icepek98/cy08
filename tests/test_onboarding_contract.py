import unittest
from datetime import datetime, timezone
from uuid import UUID

from license_contract import LicenseStatus
from onboarding_contract import (
    OnboardingContractError,
    parse_onboarding_result,
)


class OnboardingContractTests(unittest.TestCase):
    def setUp(self):
        self.payload = {
            "tenant_id": "66d204ae-87bc-4ea5-8a37-a3d444ec7f34",
            "installation_id": "ff1610d3-b36a-4c80-83a0-8c2ff785fe9e",
            "license_status": "trialing",
            "trial_started_at": "2026-10-03T12:00:00Z",
            "trial_ends_at": "2026-11-02T12:00:00Z",
            "checked_at": "2026-10-03T12:00:01Z",
            "installation_registered": True,
            "access_allowed": True,
            "block_reason": None,
        }

    def test_parses_valid_server_authorized_trial(self):
        result = parse_onboarding_result(self.payload)

        self.assertEqual(
            result.tenant_id,
            UUID("66d204ae-87bc-4ea5-8a37-a3d444ec7f34"),
        )
        self.assertEqual(
            result.installation_id,
            UUID("ff1610d3-b36a-4c80-83a0-8c2ff785fe9e"),
        )
        self.assertEqual(result.license_status, LicenseStatus.TRIALING)
        self.assertTrue(result.access_allowed)
        self.assertEqual(result.checked_at.tzinfo, timezone.utc)

    def test_rejects_unexpected_authorization_fields(self):
        for field in (
            "user_id",
            "service_role",
            "trial_started_at_local",
            "tenant_id_from_request",
        ):
            with self.subTest(field=field):
                payload = dict(self.payload, **{field: "unexpected"})
                with self.assertRaises(OnboardingContractError):
                    parse_onboarding_result(payload)

    def test_rejects_inconsistent_access_or_trial_dates(self):
        payload = dict(self.payload, access_allowed=False)
        with self.assertRaises(OnboardingContractError):
            parse_onboarding_result(payload)

        payload = dict(
            self.payload,
            checked_at="2026-11-02T12:00:00Z",
        )
        with self.assertRaises(OnboardingContractError):
            parse_onboarding_result(payload)

        payload = dict(self.payload, trial_ends_at="2026-10-03T11:00:00Z")
        with self.assertRaises(OnboardingContractError):
            parse_onboarding_result(payload)

    def test_parses_blocked_or_paid_results(self):
        blocked_payload = dict(
            self.payload,
            license_status="expired",
            trial_started_at=None,
            trial_ends_at=None,
            access_allowed=False,
            block_reason="license_not_active",
        )
        self.assertFalse(parse_onboarding_result(blocked_payload).access_allowed)

        paid_payload = dict(
            self.payload,
            license_status="active",
            trial_started_at=None,
            trial_ends_at=None,
            access_allowed=True,
        )
        self.assertTrue(parse_onboarding_result(paid_payload).access_allowed)

    def test_rejects_naive_timestamps_and_invalid_identifiers(self):
        invalid_payloads = [
            dict(self.payload, checked_at="2026-10-03T12:00:01"),
            dict(self.payload, tenant_id="not-a-uuid"),
            dict(self.payload, installation_registered=False),
            dict(self.payload, license_status="unknown"),
        ]
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(OnboardingContractError):
                    parse_onboarding_result(payload)

    def test_checked_at_is_not_compared_to_the_local_clock(self):
        payload = dict(
            self.payload,
            checked_at=datetime(2000, 1, 1, tzinfo=timezone.utc).isoformat(),
        )

        self.assertTrue(parse_onboarding_result(payload).access_allowed)


if __name__ == "__main__":
    unittest.main()
