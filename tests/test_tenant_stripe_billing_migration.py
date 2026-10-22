from pathlib import Path
import unittest


MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "supabase"
    / "migrations"
    / "20261008_create_tenant_stripe_billing.sql"
)


class TenantStripeBillingMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sql = MIGRATION.read_text(encoding="utf-8").lower()

    def test_billing_record_is_one_per_tenant_and_stripe_ids_are_globally_unique(self):
        self.assertIn("tenant_id uuid primary key", self.sql)
        self.assertIn("references public.tenants (tenant_id)", self.sql)
        self.assertIn("stripe_customer_id text not null unique", self.sql)
        self.assertIn("stripe_subscription_id text unique", self.sql)

    def test_billing_data_is_not_directly_accessible_to_client_or_service_roles(self):
        self.assertIn("alter table public.tenant_stripe_billing enable row level security", self.sql)
        self.assertIn(
            "revoke all on table public.tenant_stripe_billing\n"
            "    from public, anon, authenticated, service_role",
            self.sql,
        )
        self.assertIn(
            "grant execute on function public.register_tenant_stripe_customer(uuid, text)\n"
            "    to service_role",
            self.sql,
        )
        self.assertIn(
            "to service_role;",
            self.sql,
        )
        self.assertEqual(self.sql.count("auth.role()"), 2)
        self.assertEqual(self.sql.count("<> 'service_role'"), 2)

    def test_customer_association_is_idempotent_but_cannot_be_reassigned(self):
        self.assertIn("for update", self.sql)
        self.assertIn(
            "existing_customer_id is distinct from p_stripe_customer_id",
            self.sql,
        )
        self.assertIn(
            "tenant already has a different stripe customer",
            self.sql,
        )
        self.assertNotIn("on conflict (tenant_id) do update", self.sql)

    def test_subscription_update_resolves_tenant_from_customer_not_caller_input(self):
        signature = self.sql[
            self.sql.index("create or replace function public.apply_confirmed_tenant_stripe_subscription("):
            self.sql.index("returns void", self.sql.index("create or replace function public.apply_confirmed_tenant_stripe_subscription("))
        ]
        self.assertNotIn("p_tenant_id", signature)
        self.assertIn(
            "where billing.stripe_customer_id = p_stripe_customer_id",
            self.sql,
        )
        self.assertIn("stripe_subscription_id text unique", self.sql)

    def test_trial_projection_cannot_extend_or_duplicate_existing_trial(self):
        self.assertIn("if p_subscription_status = 'trialing' then", self.sql)
        self.assertIn("current_license_status <> 'trialing'", self.sql)
        self.assertIn("p_current_period_end <= current_trial_started_at", self.sql)
        self.assertIn("p_current_period_end > current_trial_ends_at", self.sql)
        self.assertIn("stripe trial would extend or duplicate", self.sql)
        self.assertIn("next_license_status := 'trialing'", self.sql)

    def test_delinquency_blocks_and_cancellation_preserves_paid_period(self):
        self.assertIn("elsif p_subscription_status = 'past_due' then", self.sql)
        self.assertIn("next_license_status := 'past_due'", self.sql)
        self.assertIn("elsif p_subscription_status = 'canceled' then", self.sql)
        self.assertIn("if p_current_period_end > server_now then", self.sql)
        self.assertIn("next_license_status := 'active'", self.sql)
        self.assertIn("next_license_status := 'expired'", self.sql)
        self.assertIn("elsif p_subscription_status = 'active' then", self.sql)
        self.assertIn("next_license_status := 'blocked'", self.sql)

    def test_invalid_status_and_inconsistent_periods_are_rejected(self):
        self.assertIn("p_subscription_status not in (", self.sql)
        self.assertIn("p_current_period_end <= p_current_period_start", self.sql)
        self.assertIn(
            "stripe_subscription_status is null\n"
            "            or stripe_subscription_status in (",
            self.sql,
        )

    def test_trial_activation_and_public_onboarding_contract_are_not_redefined(self):
        self.assertNotIn(
            "create or replace function public.activate_tenant_trial",
            self.sql,
        )
        self.assertNotIn(
            "create or replace function public.register_or_get_tenant_for_installation",
            self.sql,
        )
        self.assertIn("current_trial_ends_at", self.sql)
        self.assertNotIn("trial_ends_at = p_current_period_end", self.sql)
        self.assertIn(
            "when p_subscription_status = 'trialing'\n"
            "                then p_current_period_end",
            self.sql,
        )

    def test_no_payment_credentials_or_card_data_are_persisted(self):
        for forbidden in (
            "card_number",
            "payment_method",
            "secret_key",
            "webhook_secret",
            "access_token",
            "refresh_token",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, self.sql)


if __name__ == "__main__":
    unittest.main()
