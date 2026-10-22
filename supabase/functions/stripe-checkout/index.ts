import { createClient } from "https://esm.sh/@supabase/supabase-js@2";
import Stripe from "npm:stripe@23.0.0";

const responseHeaders = { "content-type": "application/json; charset=utf-8" };
const API_VERSION = "2026-09-30.endive" as Stripe.LatestApiVersion;

type LicenseRow = {
  tenant_id: string;
  license_status: string;
};

type CheckoutReservation = {
  outcome: string;
  lease_token: string | null;
  idempotency_key: string | null;
  integration_identifier: string | null;
  checkout_url: string | null;
  success_url: string | null;
  cancel_url: string | null;
  session_expires_at: string | null;
};

function respond(status: number, body: Record<string, unknown>): Response {
  return new Response(JSON.stringify(body), { status, headers: responseHeaders });
}

function errorResponse(status: number, code: string): Response {
  return respond(status, { error: code });
}

function readHttpsUrl(value: string | undefined, requireCheckoutPlaceholder = false): string | null {
  if (!value) return null;
  try {
    const url = new URL(value);
    if (url.protocol !== "https:" || !url.hostname || url.username || url.password) {
      return null;
    }
    if (requireCheckoutPlaceholder && !value.includes("{CHECKOUT_SESSION_ID}")) {
      return null;
    }
    return value;
  } catch {
    return null;
  }
}

function isLicenseRow(value: unknown): value is LicenseRow {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return false;
  const row = value as Record<string, unknown>;
  return typeof row.tenant_id === "string" &&
    typeof row.license_status === "string";
}

function isCheckoutReservation(value: unknown): value is CheckoutReservation[] {
  if (!Array.isArray(value) || value.length !== 1) return false;
  const row = value[0];
  if (typeof row !== "object" || row === null || Array.isArray(row)) return false;
  const reservation = row as Record<string, unknown>;
  return typeof reservation.outcome === "string" &&
    (typeof reservation.lease_token === "string" || reservation.lease_token === null) &&
    (typeof reservation.idempotency_key === "string" ||
      reservation.idempotency_key === null) &&
    (typeof reservation.integration_identifier === "string" ||
      reservation.integration_identifier === null) &&
    (typeof reservation.checkout_url === "string" || reservation.checkout_url === null) &&
    (typeof reservation.success_url === "string" || reservation.success_url === null) &&
    (typeof reservation.cancel_url === "string" || reservation.cancel_url === null) &&
    (typeof reservation.session_expires_at === "string" ||
      reservation.session_expires_at === null);
}

function secretConfiguration(): {
  supabaseUrl: string;
  anonKey: string;
  serviceRoleKey: string;
  stripeSecretKey: string;
  priceId: string;
  successUrl: string;
  cancelUrl: string;
} | null {
  const supabaseUrl = Deno.env.get("SUPABASE_URL");
  const anonKey = Deno.env.get("SUPABASE_ANON_KEY");
  const serviceRoleKey = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY");
  const stripeSecretKey = Deno.env.get("STRIPE_SECRET_KEY");
  const priceId = Deno.env.get("STRIPE_PRICE_ID");
  const successUrl = readHttpsUrl(Deno.env.get("STRIPE_SUCCESS_URL"), true);
  const cancelUrl = readHttpsUrl(Deno.env.get("STRIPE_CANCEL_URL"));

  if (
    !supabaseUrl || !anonKey || !serviceRoleKey || !stripeSecretKey ||
    !priceId || !/^price_[A-Za-z0-9]+$/.test(priceId) ||
    !successUrl || !cancelUrl
  ) {
    return null;
  }
  return {
    supabaseUrl,
    anonKey,
    serviceRoleKey,
    stripeSecretKey,
    priceId,
    successUrl,
    cancelUrl,
  };
}

Deno.serve(async (request: Request): Promise<Response> => {
  if (request.method !== "POST") {
    return errorResponse(405, "method_not_allowed");
  }

  const contentType = request.headers.get("content-type") ?? "";
  if (!contentType.toLowerCase().startsWith("application/json")) {
    return errorResponse(415, "application_json_required");
  }

  let body: unknown;
  try {
    const rawBody = await request.text();
    if (rawBody.length > 1024) return errorResponse(413, "request_too_large");
    body = JSON.parse(rawBody);
  } catch {
    return errorResponse(400, "invalid_json");
  }
  if (
    typeof body !== "object" || body === null || Array.isArray(body) ||
    Object.keys(body).length !== 0
  ) {
    return errorResponse(400, "empty_request_required");
  }

  const authorization = request.headers.get("authorization") ?? "";
  const bearer = /^Bearer\s+(\S+)$/i.exec(authorization);
  if (!bearer) return errorResponse(401, "authentication_required");

  const config = secretConfiguration();
  if (!config) {
    console.error("Stripe Checkout server configuration is incomplete.");
    return errorResponse(503, "billing_unavailable");
  }

  const authClient = createClient(config.supabaseUrl, config.anonKey, {
    auth: { autoRefreshToken: false, persistSession: false, detectSessionInUrl: false },
  });
  let authResult: Awaited<ReturnType<typeof authClient.auth.getUser>>;
  try {
    authResult = await authClient.auth.getUser(bearer[1]);
  } catch {
    console.error("Stripe Checkout authentication lookup failed.");
    return errorResponse(503, "billing_unavailable");
  }
  if (authResult.error || !authResult.data.user) {
    return errorResponse(401, "invalid_authentication");
  }

  const admin = createClient(config.supabaseUrl, config.serviceRoleKey, {
    auth: { autoRefreshToken: false, persistSession: false, detectSessionInUrl: false },
  });

  try {
    const { data: tenantId, error: tenantError } = await admin.rpc(
      "resolve_tenant_for_billing",
      { p_user_id: authResult.data.user.id },
    );
    if (tenantError) {
      console.error("Stripe Checkout tenant lookup failed.", tenantError.code ?? "unknown");
      return errorResponse(503, "billing_unavailable");
    }
    if (typeof tenantId !== "string") {
      return errorResponse(403, "tenant_not_authorized");
    }

    const { data: licenses, error: licenseError } = await admin.rpc(
      "resolve_tenant_license",
      { p_tenant_id: tenantId },
    );
    if (licenseError) {
      console.error("Stripe Checkout license lookup failed.", licenseError.code ?? "unknown");
      return errorResponse(503, "billing_unavailable");
    }
    if (
      !Array.isArray(licenses) || licenses.length !== 1 ||
      !isLicenseRow(licenses[0]) || licenses[0].tenant_id !== tenantId
    ) {
      return errorResponse(403, "license_not_eligible");
    }
    if (licenses[0].license_status !== "trialing") {
      return errorResponse(409, "checkout_not_available_for_license_state");
    }

    const { data: billingRows, error: billingError } = await admin.rpc(
      "get_tenant_stripe_checkout_state",
      { p_tenant_id: tenantId },
    );
    if (billingError) {
      console.error("Stripe Checkout billing lookup failed.", billingError.code ?? "unknown");
      return errorResponse(503, "billing_unavailable");
    }
    if (!Array.isArray(billingRows) || billingRows.length > 1) {
      console.error("Stripe Checkout billing response violated its contract.");
      return errorResponse(503, "billing_unavailable");
    }
    const billing = billingRows[0];
    if (
      billing !== undefined &&
      (typeof billing.stripe_customer_id !== "string" ||
        (billing.stripe_subscription_id !== null &&
          typeof billing.stripe_subscription_id !== "string"))
    ) {
      console.error("Stripe Checkout billing response violated its contract.");
      return errorResponse(503, "billing_unavailable");
    }
    if (billing?.stripe_subscription_id) {
      return errorResponse(409, "subscription_already_linked");
    }

    const stripe = new Stripe(config.stripeSecretKey, {
      apiVersion: API_VERSION,
      httpClient: Stripe.createFetchHttpClient(),
    });

    let customerId = billing?.stripe_customer_id;
    if (typeof customerId !== "string" || !customerId) {
      const customer = await stripe.customers.create(
        {},
        { idempotencyKey: `cy08-customer-${tenantId}` },
      );
      customerId = customer.id;
      const { error: registerError } = await admin.rpc(
        "register_tenant_stripe_customer",
        {
          p_tenant_id: tenantId,
          p_stripe_customer_id: customerId,
        },
      );
      if (registerError) {
        console.error("Stripe Customer association failed.", registerError.code ?? "unknown");
        return errorResponse(503, "billing_unavailable");
      }
    }

    const random = new Uint8Array(16);
    let identifierSuffix = "";
    while (identifierSuffix.length < 8) {
      crypto.getRandomValues(random);
      for (const byte of random) {
        if (byte < 234) identifierSuffix += String.fromCharCode(65 + (byte % 26));
        if (identifierSuffix.length === 8) break;
      }
    }

    const { data: reservationRows, error: reservationError } = await admin.rpc(
      "begin_tenant_stripe_checkout",
      {
        p_tenant_id: tenantId,
        p_stripe_customer_id: customerId,
        p_stripe_price_id: config.priceId,
        p_success_url: config.successUrl,
        p_cancel_url: config.cancelUrl,
        p_candidate_idempotency_key: `cy08-checkout-${crypto.randomUUID()}`,
        p_candidate_integration_identifier: `cy08-checkout-${identifierSuffix}`,
      },
    );
    if (reservationError) {
      console.error("Stripe Checkout reservation failed.", reservationError.code ?? "unknown");
      return errorResponse(503, "billing_unavailable");
    }
    if (!isCheckoutReservation(reservationRows)) {
      console.error("Stripe Checkout reservation response violated its contract.");
      return errorResponse(503, "billing_unavailable");
    }
    const reservation = reservationRows[0];
    if (reservation.outcome === "reuse") {
      if (!reservation.checkout_url) {
        console.error("Stripe Checkout reuse response omitted its URL.");
        return errorResponse(503, "billing_unavailable");
      }
      return respond(200, { checkout_url: reservation.checkout_url });
    }
    if (reservation.outcome === "busy") {
      return errorResponse(503, "checkout_in_progress");
    }
    if (reservation.outcome === "subscription_linked") {
      return errorResponse(409, "subscription_already_linked");
    }
    if (reservation.outcome === "price_mismatch") {
      return errorResponse(409, "checkout_configuration_changed");
    }
    if (
      reservation.outcome === "not_eligible" ||
      reservation.outcome === "customer_mismatch"
    ) {
      return errorResponse(409, "checkout_not_available");
    }
    const reservationExpiry = reservation.session_expires_at
      ? Date.parse(reservation.session_expires_at)
      : NaN;
    if (
      reservation.outcome !== "started" ||
      !reservation.lease_token ||
      !reservation.idempotency_key ||
      !reservation.integration_identifier ||
      !reservation.success_url ||
      !reservation.cancel_url ||
      !Number.isFinite(reservationExpiry)
    ) {
      console.error("Stripe Checkout reservation was not eligible for creation.");
      return errorResponse(503, "billing_unavailable");
    }

    const session = await stripe.checkout.sessions.create(
      {
        mode: "subscription",
        customer: customerId,
        line_items: [{ price: config.priceId, quantity: 1 }],
        success_url: reservation.success_url,
        cancel_url: reservation.cancel_url,
        expires_at: Math.floor(reservationExpiry / 1000),
        integration_identifier: reservation.integration_identifier,
      },
      { idempotencyKey: reservation.idempotency_key },
    );

    if (!session.url) {
      console.error("Stripe Checkout returned no hosted URL.");
      return errorResponse(502, "checkout_creation_failed");
    }

    const { data: completed, error: completionError } = await admin.rpc(
      "complete_tenant_stripe_checkout",
      {
        p_tenant_id: tenantId,
        p_stripe_customer_id: customerId,
        p_lease_token: reservation.lease_token,
        p_stripe_session_id: session.id,
        p_checkout_url: session.url,
      },
    );
    if (completionError || completed !== true) {
      console.error(
        "Stripe Checkout session persistence failed.",
        completionError?.code ?? "lease_lost",
      );
      return errorResponse(503, "billing_unavailable");
    }

    return respond(200, { checkout_url: session.url });
  } catch (error) {
    console.error(
      "Stripe Checkout operation failed.",
      error instanceof Stripe.errors.StripeError ? error.type : "internal",
    );
    return errorResponse(502, "checkout_creation_failed");
  }
});
