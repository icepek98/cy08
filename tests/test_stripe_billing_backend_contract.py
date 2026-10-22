from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
CHECKOUT = (ROOT / "supabase/functions/stripe-checkout/index.ts").read_text(
    encoding="utf-8"
)
WEBHOOK = (ROOT / "supabase/functions/stripe-webhook/index.ts").read_text(
    encoding="utf-8"
)
MIGRATION = (
    ROOT / "supabase/migrations/20261013_create_stripe_webhook_inbox.sql"
).read_text(encoding="utf-8").lower()
CHECKOUT_MIGRATION = (
    ROOT / "supabase/migrations/20261014_reserve_stripe_checkout_sessions.sql"
).read_text(encoding="utf-8").lower()
FUNCTION_CONFIG = (ROOT / "supabase/config.toml").read_text(encoding="utf-8")
DOCUMENTATION = (
    ROOT / "docs/CY08-stripe-billing-backend.md"
).read_text(encoding="utf-8").lower()


class StripeBillingBackendContractTests(unittest.TestCase):
    def test_checkout_requires_authenticated_identity_and_derives_tenant(self):
        self.assertIn("auth.getuser(bearer[1])", CHECKOUT.lower())
        self.assertIn('"resolve_tenant_for_billing"', CHECKOUT)
        self.assertIn("p_user_id: authResult.data.user.id", CHECKOUT)
        self.assertIn("object.keys(body).length !== 0", CHECKOUT.lower())
        self.assertIn("p_tenant_id: tenantId", CHECKOUT)

    def test_checkout_uses_one_server_price_and_fixed_quantity(self):
        self.assertIn('Deno.env.get("STRIPE_PRICE_ID")', CHECKOUT)
        self.assertIn("line_items: [{ price: config.priceId, quantity: 1 }]", CHECKOUT)
        self.assertNotIn("payment_method_types", CHECKOUT)
        self.assertNotIn("trial_period_days", CHECKOUT)

    def test_customer_and_checkout_creation_are_idempotent(self):
        self.assertIn("cy08-customer-${tenantId}", CHECKOUT)
        self.assertIn("p_candidate_idempotency_key", CHECKOUT)
        self.assertIn("reservation.idempotency_key", CHECKOUT)
        self.assertIn('"register_tenant_stripe_customer"', CHECKOUT)

    def test_checkout_returns_only_hosted_url_and_not_payment_confirmation(self):
        self.assertIn("checkout_url: session.url", CHECKOUT)
        response = CHECKOUT.split("return respond(200", 1)[-1].split(";", 1)[0]
        self.assertNotIn("serviceRoleKey", response)
        self.assertNotIn("stripeSecretKey", response)
        self.assertIn("retorno do checkout não autoriza acesso", DOCUMENTATION)

    def test_checkout_requires_trusted_https_return_urls(self):
        self.assertIn('url.protocol !== "https:"', CHECKOUT)
        self.assertIn("{CHECKOUT_SESSION_ID}", CHECKOUT)
        self.assertIn('Deno.env.get("STRIPE_SUCCESS_URL")', CHECKOUT)
        self.assertIn('Deno.env.get("STRIPE_CANCEL_URL")', CHECKOUT)

    def test_webhook_verifies_raw_body_before_parsing_or_processing(self):
        self.assertIn("request.arrayBuffer()", WEBHOOK)
        self.assertIn("constructEventAsync(", WEBHOOK)
        self.assertIn("Stripe.createSubtleCryptoProvider()", WEBHOOK)
        self.assertLess(
            WEBHOOK.index("constructEventAsync("),
            WEBHOOK.index("event.data.object"),
        )
        self.assertIn("invalid_signature", WEBHOOK)

    def test_webhook_rejects_malformed_supported_event_identifiers(self):
        self.assertIn(
            'return respond(400, { error: "invalid_event" });',
            WEBHOOK,
        )
        self.assertIn("const suffix = id.slice(prefix.length)", WEBHOOK)
        self.assertIn("return /^[A-Za-z0-9]+$/.test(suffix) ? id : null", WEBHOOK)
        self.assertIn("!Number.isSafeInteger(event.created)", WEBHOOK)
        self.assertIn("!Number.isFinite(new Date(event.created * 1000).getTime())", WEBHOOK)
        self.assertIn('if (isSubscriptionEvent && !subscriptionId)', WEBHOOK)
        self.assertIn('if (!isSubscriptionEvent && !invoiceId)', WEBHOOK)
        self.assertIn(
            "if (!isSubscriptionEvent && hasInvoiceSubscription && !subscriptionId)",
            WEBHOOK,
        )
        self.assertIn("function invoiceHasSubscriptionReference", WEBHOOK)
        self.assertIn("if (!subscriptionId) {\n    return respond(200", WEBHOOK)

    def test_webhook_supports_only_lifecycle_and_invoice_events(self):
        for event in (
            "customer.subscription.created",
            "customer.subscription.updated",
            "customer.subscription.deleted",
            "invoice.paid",
            "invoice.payment_failed",
        ):
            with self.subTest(event=event):
                self.assertIn(event, WEBHOOK)
        self.assertNotIn("checkout.session.completed", WEBHOOK)

    def test_webhook_retrieves_canonical_subscription_and_latest_invoice(self):
        self.assertIn("stripe.subscriptions.retrieve(subscriptionId)", WEBHOOK)
        self.assertIn("stripe.invoices.retrieve(latestInvoiceId)", WEBHOOK)
        self.assertIn('latestInvoice.status === "paid"', WEBHOOK)
        self.assertIn("invoiceSubscriptionId(latestInvoice)", WEBHOOK)
        self.assertIn("subscriptionItem?.current_period_start", WEBHOOK)
        self.assertIn("subscriptionItem?.current_period_end", WEBHOOK)

    def test_webhook_uses_database_customer_mapping_and_fenced_lease(self):
        self.assertIn('"begin_stripe_webhook_event"', WEBHOOK)
        self.assertIn('"apply_stripe_webhook_snapshot"', WEBHOOK)
        self.assertIn("generation = current_lease.generation + 1", MIGRATION)
        self.assertIn("current_lease.lease_expires_at <= clock_timestamp()", MIGRATION)
        self.assertIn("current_lease.generation is distinct from p_lease_generation", MIGRATION)

    def test_inbox_is_private_idempotent_and_does_not_store_raw_payload(self):
        self.assertIn("event_id text primary key", MIGRATION)
        self.assertIn("enable row level security", MIGRATION)
        self.assertIn("from public, anon, authenticated, service_role", MIGRATION)
        self.assertIn("on conflict (event_id) do nothing", MIGRATION)
        self.assertNotIn("raw_payload", MIGRATION)
        self.assertNotIn("event_body jsonb", MIGRATION)

    def test_projection_and_event_completion_share_one_rpc_transaction(self):
        projection = MIGRATION.index(
            "perform public.apply_confirmed_tenant_stripe_subscription("
        )
        completion = MIGRATION.index(
            "set processing_status = 'processed'", projection
        )
        self.assertLess(projection, completion)
        self.assertIn("returns text", MIGRATION)
        self.assertIn("security definer", MIGRATION)
        self.assertIn("set search_path = ''", MIGRATION)
        self.assertIn("coalesce(auth.role(), '') <> 'service_role'", MIGRATION)

    def test_pending_policy_never_reactivates_restricted_licenses(self):
        self.assertIn("current_license_status in ('blocked', 'revoked')", MIGRATION)
        self.assertIn("processing_status = 'pending_policy'", MIGRATION)
        self.assertIn("current_license_status = 'trialing'", MIGRATION)
        self.assertIn("current_license_status = 'active'", MIGRATION)
        self.assertIn("p_subscription_status = 'past_due'", MIGRATION)
        self.assertIn("current_license_status = 'active'", MIGRATION)

    def test_active_entitlement_requires_latest_paid_invoice(self):
        self.assertIn("p_latest_invoice_paid is distinct from true", MIGRATION)
        self.assertIn("awaiting_latest_invoice_paid", MIGRATION)
        self.assertIn("p_subscription_status = 'active'", MIGRATION)
        self.assertIn("past_due_price_not_allowlisted", MIGRATION)
        self.assertIn("set license_status = 'past_due'", MIGRATION)

    def test_rpc_contracts_check_scalar_and_set_return_shapes(self):
        self.assertIn("returns uuid", MIGRATION)
        self.assertIn("returns table (\n    stripe_customer_id text", MIGRATION)
        self.assertIn("returns table (\n    outcome text", MIGRATION)
        self.assertIn("returns text\nlanguage plpgsql", MIGRATION)
        self.assertIn('typeof tenantId !== "string"', CHECKOUT)
        self.assertIn("!array.isarray(billingrows)", CHECKOUT.lower())
        self.assertIn("!iseventlease(leaserows)", WEBHOOK.lower())
        self.assertIn('typeof applyrows !== "string"', WEBHOOK.lower())

    def test_checkout_reservation_stabilizes_stripe_idempotency_parameters(self):
        self.assertIn('"begin_tenant_stripe_checkout"', CHECKOUT)
        self.assertIn('"complete_tenant_stripe_checkout"', CHECKOUT)
        self.assertIn("reservation.idempotency_key", CHECKOUT)
        self.assertIn("reservation.integration_identifier", CHECKOUT)
        self.assertIn("reservation.success_url", CHECKOUT)
        self.assertIn("reservation.cancel_url", CHECKOUT)
        self.assertIn("!reservation.success_url", CHECKOUT)
        self.assertIn("!reservation.cancel_url", CHECKOUT)
        self.assertIn("expires_at: Math.floor(reservationExpiry / 1000)", CHECKOUT)
        self.assertIn("for update", CHECKOUT_MIGRATION)
        self.assertIn("tenant_id uuid primary key", CHECKOUT_MIGRATION)
        self.assertIn("lease_expires_at = server_now + interval '90 seconds'", CHECKOUT_MIGRATION)
        self.assertIn("current_reservation.idempotency_key", CHECKOUT_MIGRATION)
        self.assertIn("current_reservation.integration_identifier", CHECKOUT_MIGRATION)
        self.assertIn("current_reservation.checkout_url", CHECKOUT_MIGRATION)
        self.assertIn("current_reservation.success_url", CHECKOUT_MIGRATION)
        self.assertIn("current_reservation.cancel_url", CHECKOUT_MIGRATION)
        self.assertIn("reservation.lease_token = p_lease_token", CHECKOUT_MIGRATION)

    def test_retryable_rpc_failures_do_not_return_success(self):
        self.assertIn('return errorResponse(503, "billing_unavailable")', CHECKOUT)
        self.assertIn('return respond(503, { error: "retry_later" })', WEBHOOK)
        self.assertIn('return respond(503, { error: "webhook_unavailable" })', WEBHOOK)

    def test_checkout_does_not_accept_a_client_selected_tenant_or_price(self):
        self.assertIn("object.keys(body).length !== 0", CHECKOUT.lower())
        self.assertIn("p_user_id: authResult.data.user.id", CHECKOUT)
        self.assertIn('Deno.env.get("STRIPE_PRICE_ID")', CHECKOUT)
        self.assertNotIn("body.tenant_id", CHECKOUT.lower())
        self.assertNotIn("body.price_id", CHECKOUT.lower())

    def test_missing_server_configuration_fails_closed(self):
        self.assertIn('if (!config) {', CHECKOUT)
        self.assertIn('if (!config) {', WEBHOOK)
        self.assertIn('Deno.env.get("STRIPE_WEBHOOK_SECRET")', WEBHOOK)
        self.assertIn('Deno.env.get("STRIPE_PRICE_ID")', WEBHOOK)

    def test_webhook_canonical_snapshot_guards_customer_price_and_license(self):
        self.assertIn("const allowedprice = priceid === config.allowedpriceid", WEBHOOK.lower())
        self.assertIn("p_snapshot_customer_id is distinct from p_stripe_customer_id", MIGRATION)
        self.assertIn("p_price_allowed is distinct from true", MIGRATION)
        self.assertIn("current_license_status in ('blocked', 'revoked')", MIGRATION)
        self.assertIn("p_latest_invoice_paid is distinct from true", MIGRATION)

    def test_webhook_duplicate_and_busy_lease_paths_are_retry_safe(self):
        self.assertIn("on conflict (event_id) do nothing", MIGRATION)
        self.assertIn("existing_event.processing_status in ('processed', 'pending_policy')", MIGRATION)
        self.assertIn("'busy'::text", MIGRATION)
        self.assertIn("return 'lease_lost'", MIGRATION)
        self.assertIn('lease.outcome === "busy"', WEBHOOK)
        self.assertIn('return respond(503, { error: "retry_later" })', WEBHOOK)

    def test_jwt_verification_is_disabled_only_for_signed_webhook(self):
        self.assertIn("[functions.stripe-checkout]\nverify_jwt = true", FUNCTION_CONFIG)
        self.assertIn("[functions.stripe-webhook]\nverify_jwt = false", FUNCTION_CONFIG)

    def test_documentation_keeps_remote_deployment_and_business_gates_open(self):
        self.assertIn("p-01", DOCUMENTATION)
        self.assertIn("p-06", DOCUMENTATION)
        self.assertIn("não pronta para produção", DOCUMENTATION)
        self.assertIn("não usar `supabase db push`", DOCUMENTATION)


if __name__ == "__main__":
    unittest.main()
