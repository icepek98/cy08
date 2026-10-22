import unittest
from pathlib import Path


MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "supabase"
    / "migrations"
    / "20261004_create_tenant_licenses.sql"
)


class LicenseMigrationContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sql = MIGRATION.read_text(encoding="utf-8").lower()

    def test_one_license_record_per_existing_tenant_is_enforced(self):
        self.assertIn("tenant_id uuid primary key", self.sql)
        self.assertIn("references public.tenants (tenant_id)", self.sql)

    def test_trial_uses_database_clock_and_fixed_thirty_day_duration(self):
        self.assertIn("server_now timestamptz := clock_timestamp()", self.sql)
        self.assertIn("server_now + interval '720 hours'", self.sql)
        self.assertNotIn("p_trial_ends_at", self.sql)

    def test_activation_is_idempotent_under_unique_constraint_conflicts(self):
        self.assertIn("on conflict (tenant_id) do nothing", self.sql)
        self.assertIn("insert into public.tenant_licenses", self.sql)

    def test_expiration_is_computed_by_the_server_clock(self):
        self.assertIn("server_now >= license.trial_ends_at", self.sql)
        self.assertIn("server_now >= license.subscription_ends_at", self.sql)

    def test_public_roles_cannot_read_table_or_execute_license_rpcs(self):
        self.assertIn(
            "revoke all on table public.tenant_licenses\n"
            "    from public, anon, authenticated, service_role",
            self.sql,
        )
        self.assertEqual(self.sql.count("to service_role;"), 2)
        self.assertEqual(self.sql.count("auth.role()"), 2)
        self.assertNotIn("to anon;", self.sql)
        self.assertNotIn("to authenticated;", self.sql)

    def test_no_payment_secrets_are_in_the_license_model(self):
        self.assertNotIn("card_number", self.sql)
        self.assertNotIn("payment_secret", self.sql)
        self.assertNotIn("access_token", self.sql)
        self.assertNotIn("refresh_token", self.sql)

    def test_revoked_status_is_added_without_changing_the_trial_contract(self):
        migration = (
            MIGRATION.parent / "20261006_add_revoked_tenant_license_status.sql"
        ).read_text(encoding="utf-8").lower()

        self.assertIn(
            "drop constraint if exists tenant_licenses_license_status_check",
            migration,
        )
        self.assertIn("'revoked'", migration)


if __name__ == "__main__":
    unittest.main()
