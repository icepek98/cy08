import unittest
from datetime import datetime, timezone
from uuid import UUID

from license_contract import (
    LicenseContractError,
    LicenseSnapshot,
    LicenseStatus,
    parse_license_snapshot,
)


class LicenseContractTests(unittest.TestCase):
    TENANT_ID = UUID("66d204ae-87bc-4ea5-8a37-a3d444ec7f34")
    CHECKED_AT = "2026-10-03T13:00:00Z"

    def valid_row(self, **overrides):
        row = {
            "tenant_id": str(self.TENANT_ID),
            "license_status": "trialing",
            "trial_started_at": "2026-10-03T12:00:00Z",
            "trial_ends_at": "2026-11-02T12:00:00Z",
            "subscription_id": None,
            "subscription_started_at": None,
            "subscription_ends_at": None,
            "checked_at": self.CHECKED_AT,
        }
        row.update(overrides)
        return row

    def test_parses_server_trial_snapshot_as_immutable_typed_value(self):
        result = parse_license_snapshot([self.valid_row()], self.TENANT_ID)

        self.assertIsInstance(result, LicenseSnapshot)
        self.assertEqual(result.license_status, LicenseStatus.TRIALING)
        self.assertEqual(
            result.trial_started_at,
            datetime(2026, 10, 3, 12, tzinfo=timezone.utc),
        )
        self.assertEqual(
            result.trial_ends_at,
            datetime(2026, 11, 2, 12, tzinfo=timezone.utc),
        )
        with self.assertRaises(AttributeError):
            result.license_status = LicenseStatus.EXPIRED

    def test_parses_each_supported_license_status(self):
        for status in LicenseStatus:
            with self.subTest(status=status):
                row = self.valid_row(license_status=status.value)
                if status is not LicenseStatus.TRIALING:
                    row.update(trial_started_at=None, trial_ends_at=None)
                self.assertEqual(
                    parse_license_snapshot([row], self.TENANT_ID).license_status,
                    status,
                )

    def test_empty_result_means_no_license_record(self):
        self.assertIsNone(parse_license_snapshot([], self.TENANT_ID))

    def test_rejects_rows_for_a_different_tenant(self):
        other_id = UUID("887308bf-0b1b-433e-8cbd-4686be14f948")
        with self.assertRaisesRegex(LicenseContractError, "different tenant"):
            parse_license_snapshot([self.valid_row(tenant_id=str(other_id))], self.TENANT_ID)

    def test_rejects_ambiguous_or_malformed_rpc_responses(self):
        with self.assertRaises(LicenseContractError):
            parse_license_snapshot([self.valid_row(), self.valid_row()], self.TENANT_ID)
        with self.assertRaises(LicenseContractError):
            parse_license_snapshot({"license_status": "active"}, self.TENANT_ID)

    def test_rejects_unknown_fields_including_secret_material(self):
        row = self.valid_row(access_token="should-never-be-returned")
        with self.assertRaisesRegex(LicenseContractError, "unexpected fields"):
            parse_license_snapshot([row], self.TENANT_ID)

    def test_rejects_naive_or_invalid_timestamps(self):
        with self.assertRaisesRegex(LicenseContractError, "timezone"):
            parse_license_snapshot(
                [self.valid_row(checked_at="2026-10-03T13:00:00")], self.TENANT_ID
            )
        with self.assertRaisesRegex(LicenseContractError, "ISO timestamp"):
            parse_license_snapshot(
                [self.valid_row(trial_started_at="not-a-date")], self.TENANT_ID
            )

    def test_rejects_incomplete_trial_dates(self):
        with self.assertRaisesRegex(LicenseContractError, "Trial dates"):
            parse_license_snapshot(
                [self.valid_row(trial_ends_at=None)], self.TENANT_ID
            )


if __name__ == "__main__":
    unittest.main()
