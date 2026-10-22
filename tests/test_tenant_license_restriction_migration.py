from pathlib import Path
import unittest


MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "supabase"
    / "migrations"
    / "20261009_preserve_restricted_tenant_licenses.sql"
)


class TenantLicenseRestrictionMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sql = MIGRATION.read_text(encoding="utf-8").lower()

    def test_restrictions_cannot_be_cleared_by_any_license_writer(self):
        self.assertIn(
            "before update of license_status on public.tenant_licenses",
            self.sql,
        )
        self.assertIn(
            "old.license_status in ('blocked', 'revoked')",
            self.sql,
        )
        self.assertIn(
            "new.license_status is distinct from old.license_status",
            self.sql,
        )
        self.assertIn(
            "raise exception 'restricted tenant license requires an authorized transition.'",
            self.sql,
        )
        self.assertIn("using errcode = '42501'", self.sql)

    def test_trigger_function_is_not_exposed_to_application_roles(self):
        self.assertIn(
            "revoke all on function "
            "public.prevent_tenant_license_restriction_clearing()\n"
            "    from public, anon, authenticated, service_role",
            self.sql,
        )


if __name__ == "__main__":
    unittest.main()
