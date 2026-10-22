from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "supabase" / "migrations"
ORIGINAL_CONNECTION_MIGRATION = (
    MIGRATIONS / "20261003_create_xero_connections.sql"
)
INITIAL_UNIQUENESS_MIGRATION = (
    MIGRATIONS / "20261010_enforce_global_xero_tenant_uniqueness.sql"
)
NORMALIZATION_MIGRATION = (
    MIGRATIONS / "20261011_align_global_xero_tenant_normalization.sql"
)
POSTGRESQL_TEST = (
    ROOT / "tests" / "sql" / "test_xero_global_organization_uniqueness.sql"
)


class XeroGlobalOrganizationUniquenessMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original_sql = ORIGINAL_CONNECTION_MIGRATION.read_text(
            encoding="utf-8"
        ).lower()
        cls.initial_sql = INITIAL_UNIQUENESS_MIGRATION.read_text(
            encoding="utf-8"
        ).lower()
        cls.normalization_sql = NORMALIZATION_MIGRATION.read_text(
            encoding="utf-8"
        ).lower()
        cls.postgresql_sql = POSTGRESQL_TEST.read_text(
            encoding="utf-8"
        ).lower()

    def test_xero_organization_is_unique_across_tenants(self):
        self.assertIn(
            "create unique index xero_connections_xero_tenant_id_global_unique",
            self.initial_sql,
        )
        self.assertIn(
            "on public.xero_connections (lower(btrim(xero_tenant_id)))",
            self.initial_sql,
        )

    def test_canonical_index_uses_the_original_whitespace_character_set(self):
        trim_characters = re.compile(
            r"btrim\s*\(\s*xero_tenant_id\s*,\s*u&'([^']+)'\s*\)"
        )
        original_match = trim_characters.search(self.original_sql)
        corrected_match = trim_characters.search(self.normalization_sql)

        self.assertIsNotNone(original_match)
        self.assertIsNotNone(corrected_match)
        self.assertEqual(original_match.group(1), corrected_match.group(1))
        self.assertIn(
            "lower(btrim(",
            self.normalization_sql,
        )

    def test_corrected_index_is_created_before_the_weaker_index_is_dropped(self):
        create_index = self.normalization_sql.index(
            "create unique index "
            "xero_connections_xero_tenant_id_canonical_unique"
        )
        drop_index = self.normalization_sql.index(
            "drop index "
            "public.xero_connections_xero_tenant_id_global_unique"
        )

        self.assertLess(create_index, drop_index)
        self.assertNotIn("delete from public.xero_connections", self.normalization_sql)
        self.assertNotIn("truncate", self.normalization_sql)

    def test_postgresql_fixture_exercises_the_migration_expression(self):
        migration_trim_characters = re.search(
            r"btrim\s*\(\s*xero_tenant_id\s*,\s*u&'([^']+)'\s*\)",
            self.normalization_sql,
        )
        fixture_trim_characters = re.search(
            r"btrim\s*\(\s*xero_tenant_id\s*,\s*u&'([^']+)'\s*\)",
            self.postgresql_sql,
        )

        self.assertIsNotNone(migration_trim_characters)
        self.assertIsNotNone(fixture_trim_characters)
        self.assertEqual(
            migration_trim_characters.group(1),
            fixture_trim_characters.group(1),
        )
        for case in (
            "u&'\\2003org-a\\2003'",
            "u&'\\00a0org-a\\00a0'",
            "unique_violation",
            "check_violation",
        ):
            with self.subTest(case=case):
                self.assertIn(case, self.postgresql_sql)

    def test_postgresql_fixture_covers_every_original_whitespace_character(self):
        migration_trim_characters = re.search(
            r"btrim\s*\(\s*xero_tenant_id\s*,\s*u&'([^']+)'\s*\)",
            self.original_sql,
        )
        fixture_codepoints = re.search(
            r"codepoints\s+integer\[\]\s*:=\s*array\[([^\]]+)\]",
            self.postgresql_sql,
        )

        self.assertIsNotNone(migration_trim_characters)
        self.assertIsNotNone(fixture_codepoints)
        original_codepoints = [
            int(value, 16)
            for value in re.findall(
                r"\\([0-9a-f]{4})",
                migration_trim_characters.group(1),
            )
        ]
        tested_codepoints = [
            int(value)
            for value in re.findall(r"\d+", fixture_codepoints.group(1))
        ]
        self.assertEqual(original_codepoints, tested_codepoints)
        self.assertIn(
            "failed index creation modified preexisting rows.",
            self.postgresql_sql,
        )


if __name__ == "__main__":
    unittest.main()
