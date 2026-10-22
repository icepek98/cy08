from pathlib import Path
import unittest


class AuthenticatedOnboardingMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        project_root = Path(__file__).resolve().parents[1]
        cls.migration = (
            project_root
            / "supabase"
            / "migrations"
            / "20261007_authenticated_tenant_onboarding.sql"
        ).read_text(encoding="utf-8").lower()
        cls.edge_function = (
            project_root
            / "supabase"
            / "functions"
            / "fieldquo-onboarding"
            / "index.ts"
        ).read_text(encoding="utf-8").lower()

    def test_database_rpc_is_privileged_and_search_path_is_fixed(self):
        self.assertIn("security definer", self.migration)
        self.assertIn("set search_path = ''", self.migration)
        self.assertIn("auth.role()", self.migration)
        self.assertIn("<> 'service_role'", self.migration)
        self.assertIn("from public, anon, authenticated, service_role", self.migration)
        self.assertIn("to service_role", self.migration)

    def test_account_and_installation_associations_are_unique(self):
        self.assertIn("user_id uuid primary key", self.migration)
        self.assertIn("tenant_id uuid not null unique", self.migration)
        self.assertIn("unique (installation_id)", self.migration)
        self.assertIn("pg_advisory_xact_lock", self.migration)
        self.assertIn("on conflict (installation_id) do nothing", self.migration)
        self.assertIn("on conflict (tenant_id) do nothing", self.migration)

    def test_trial_uses_database_time_and_is_not_reset(self):
        self.assertIn("clock_timestamp()", self.migration)
        self.assertIn("interval '720 hours'", self.migration)
        self.assertIn("new_trial_started_at := clock_timestamp()", self.migration)
        self.assertIn(
            "new_trial_started_at + interval '720 hours'", self.migration
        )
        self.assertIn("on conflict (tenant_id) do nothing", self.migration)
        self.assertNotIn("trial_started_at =", self.migration)

    def test_edge_function_derives_identity_and_rejects_client_tenant(self):
        self.assertIn('supabase.auth.getuser(', self.edge_function)
        self.assertIn("authdata.user.id", self.edge_function)
        self.assertIn("p_user_id: authdata.user.id", self.edge_function)
        self.assertIn("p_installation_id:", self.edge_function)
        self.assertNotIn("payload.tenant_id", self.edge_function)
        self.assertNotIn("payload.trial_started_at", self.edge_function)
        self.assertIn("email_confirmed_at", self.edge_function)

    def test_service_role_is_read_only_from_server_environment(self):
        self.assertIn('deno.env.get("supabase_service_role_key")', self.edge_function)
        self.assertNotIn("supabase_service_role_key", self.migration)
        project_root = Path(__file__).resolve().parents[1]
        for module in ("onboarding_client.py", "supabase_auth.py", "main.py"):
            source = (project_root / module).read_text(encoding="utf-8").lower()
            with self.subTest(module=module):
                self.assertNotIn("supabase_service_role_key", source)
                self.assertNotIn("service_role", source)

    def test_edge_function_uses_post_and_checks_jwt_before_rpc(self):
        method_check = self.edge_function.index('request.method !== "post"')
        auth_validation = self.edge_function.index("supabase.auth.getuser(")
        rpc_call = self.edge_function.index('supabase.rpc(')
        self.assertLess(method_check, auth_validation)
        self.assertLess(auth_validation, rpc_call)
        self.assertIn(
            "const bearer = /^bearer ([^\\s]+)$/i.exec(authorization);",
            self.edge_function,
        )

    def test_edge_function_bounds_body_and_validates_rpc_identity(self):
        self.assertIn("readlimitedbody(request, 4096)", self.edge_function)
        self.assertIn('body.kind === "too_large"', self.edge_function)
        self.assertIn('body.kind === "unreadable"', self.edge_function)
        self.assertIn("catch {", self.edge_function)
        self.assertIn("data[0].installation_id.tolowercase()", self.edge_function)
        self.assertIn("payload.installation_id.tolowercase()", self.edge_function)

    def test_client_rpc_accepts_only_installation_identity(self):
        project_root = Path(__file__).resolve().parents[1]
        client_source = (project_root / "onboarding_client.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("request.to_payload()", client_source)
        self.assertNotIn('"tenant_id":', client_source)
        self.assertNotIn('"trial_started_at":', client_source)


if __name__ == "__main__":
    unittest.main()
