import unittest
from pathlib import Path


MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "supabase"
    / "migrations"
    / "20261005_create_tenant_installations.sql"
)


class TenantInstallationsMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sql = MIGRATION.read_text(encoding="utf-8").lower()

    def test_installation_is_tenant_scoped_and_unique(self):
        self.assertIn("tenant_id uuid not null", self.sql)
        self.assertIn("references public.tenants (tenant_id)", self.sql)
        self.assertIn("unique (tenant_id, installation_id)", self.sql)

    def test_registry_has_lifecycle_timestamps_and_no_license_secrets(self):
        for field in (
            "created_at timestamptz",
            "last_seen_at timestamptz",
            "revoked_at timestamptz",
        ):
            self.assertIn(field, self.sql)
        for secret_field in ("access_token", "refresh_token", "service_role_key"):
            self.assertNotIn(secret_field, self.sql)

    def test_all_supabase_roles_are_denied_direct_table_access(self):
        self.assertIn(
            "from public, anon, authenticated, service_role",
            self.sql,
        )


if __name__ == "__main__":
    unittest.main()
