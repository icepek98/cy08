import re
import unittest
from pathlib import Path


MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "supabase"
    / "migrations"
    / "20261002_create_tenant_fiscal_configs.sql"
)


class TenantFiscalConfigMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sql = MIGRATION.read_text(encoding="utf-8").lower()

    def test_license_ambiguity_check_counts_all_authorized_tenants(self):
        match = re.search(
            r"select count\(distinct lic\.tenant_id\)"
            r".*?from public\.licencas as lic"
            r"(.*?)if matching_tenant_count > 1 then",
            self.sql,
            flags=re.DOTALL,
        )

        self.assertIsNotNone(match)
        self.assertRegex(match.group(1), r"where lic\.chave_licenca = p_license_key")
        self.assertRegex(match.group(1), r"and lic\.ativo is true")
        self.assertRegex(match.group(1), r"lic\.data_expiracao >= current_date")
        self.assertRegex(match.group(1), r"and lic\.tenant_id is not null")
        self.assertNotIn("p_tenant_id", match.group(1))
        self.assertIn("raise exception 'ambiguous active license key.'", self.sql)
        self.assertIn("if matching_tenant_count = 0 then", self.sql)
        self.assertRegex(self.sql, r"authorized_tenant_id is distinct from p_tenant_id")
        self.assertIn(
            "raise exception 'license is not authorized for requested tenant.'",
            self.sql,
        )

    def test_rpc_returns_only_the_fiscal_configuration_contract(self):
        expected_columns = (
            "tenant_id",
            "account_code",
            "tax_type",
            "tax_inclusive",
            "amount_basis",
            "discount_policy",
        )
        returns_table = re.search(
            r"returns table\s*\((.*?)\)\s*language",
            self.sql,
            flags=re.DOTALL,
        )

        self.assertIsNotNone(returns_table)
        for column in expected_columns:
            self.assertRegex(returns_table.group(1), rf"\b{column}\b")
        self.assertEqual(
            len(re.findall(r"\b[a-z_]+\s+(?:uuid|text|boolean)\b", returns_table.group(1))),
            len(expected_columns),
        )


if __name__ == "__main__":
    unittest.main()
