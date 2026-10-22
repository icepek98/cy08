import { createClient } from "https://esm.sh/@supabase/supabase-js@2";
import Stripe from "npm:stripe@23.0.0";

const responseHeaders = { "content-type": "application/json; charset=utf-8" };
const API_VERSION = "2026-09-30.endive" as Stripe.LatestApiVersion;
const MAX_BODY_BYTES = 1_048_576;
const SUPPORTED_EVENTS = new Set([
  "customer.subscription.created",
  "customer.subscription.updated",
  "customer.subscription.deleted",
  "invoice.paid",
  "invoice.payment_failed",
]);

type EventLease = {
  outcome: string;
  lease_token: string | null;
  lease_generation: number | null;
};

function respond(status: number, body: Record<string, unknown>): Response {
  return new Response(JSON.stringify(body), { status, headers: responseHeaders });
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function stripeId(value: unknown, prefix: string): string | null {
  const id = typeof value === "string"
    ? value
    : isRecord(value) && typeof value.id === "string"
    ? value.id
    : null;
  if (!id || !id.startsWith(prefix)) return null;
  const suffix = id.slice(prefix.length);
  return /^[A-Za-z0-9]+$/.test(suffix) ? id : null;
}

function invoiceSubscriptionId(value: unknown): string | null {
  if (!isRecord(value)) return null;
  const legacy = stripeId(value.subscription, "sub_");
  if (legacy) return legacy;
  if (!isRecord(value.parent) || !isRecord(value.parent.subscription_details)) return null;
  return stripeId(value.parent.subscription_details.subscription, "sub_");
}

function invoiceHasSubscriptionReference(value: unknown): boolean {
  if (!isRecord(value)) return false;
  if (value.subscription !== undefined && value.subscription !== null) return true;
  return isRecord(value.parent) &&
    isRecord(value.parent.subscription_details) &&
    value.parent.subscription_details.subscription !== undefined &&
    value.parent.subscription_details.subscription !== null;
}

function toTimestamp(value: unknown): string | null {
  return typeof value === "number" && Number.isFinite(value)
    ? new Date(value * 1000).toISOString()
    : null;
}

function recordConfig(): {
  supabaseUrl: string;
  serviceRoleKey: string;
  stripeSecretKey: string;
  webhookSecret: string;
  allowedPriceId: string;
} | null {
  const supabaseUrl = Deno.env.get("SUPABASE_URL");
  const serviceRoleKey = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY");
  const stripeSecretKey = Deno.env.get("STRIPE_SECRET_KEY");
  const webhookSecret = Deno.env.get("STRIPE_WEBHOOK_SECRET");
  const allowedPriceId = Deno.env.get("STRIPE_PRICE_ID");
  if (
    !supabaseUrl || !serviceRoleKey || !stripeSecretKey || !webhookSecret ||
    !allowedPriceId || !/^price_[A-Za-z0-9]+$/.test(allowedPriceId)
  ) return null;
  return { supabaseUrl, serviceRoleKey, stripeSecretKey, webhookSecret, allowedPriceId };
}

function isEventLease(value: unknown): value is EventLease[] {
  if (!Array.isArray(value) || value.length !== 1 || !isRecord(value[0])) return false;
  const row = value[0];
  return typeof row.outcome === "string" &&
    (typeof row.lease_token === "string" || row.lease_token === null) &&
    (typeof row.lease_generation === "number" || row.lease_generation === null);
}

Deno.serve(async (request: Request): Promise<Response> => {
  if (request.method !== "POST") return respond(405, { error: "method_not_allowed" });
  const signature = request.headers.get("stripe-signature");
  if (!signature) return respond(400, { error: "missing_signature" });

  let rawBody: Uint8Array;
  try {
    if (Number(request.headers.get("content-length") ?? "0") > MAX_BODY_BYTES) {
      return respond(413, { error: "request_too_large" });
    }
    rawBody = new Uint8Array(await request.arrayBuffer());
  } catch {
    return respond(400, { error: "invalid_request_body" });
  }
  if (rawBody.byteLength === 0 || rawBody.byteLength > MAX_BODY_BYTES) {
    return respond(413, { error: "request_too_large" });
  }

  const config = recordConfig();
  if (!config) {
    console.error("Stripe webhook server configuration is incomplete.");
    return respond(503, { error: "webhook_unavailable" });
  }

  const stripe = new Stripe(config.stripeSecretKey, {
    apiVersion: API_VERSION,
    httpClient: Stripe.createFetchHttpClient(),
  });

  let event: Stripe.Event;
  try {
    event = await stripe.webhooks.constructEventAsync(
      rawBody,
      signature,
      config.webhookSecret,
      undefined,
      Stripe.createSubtleCryptoProvider(),
    );
  } catch {
    return respond(400, { error: "invalid_signature" });
  }

  if (!SUPPORTED_EVENTS.has(event.type)) {
    return respond(200, { received: true, ignored: true });
  }
  if (
    !Number.isSafeInteger(event.created) ||
    event.created < 0 ||
    !Number.isFinite(new Date(event.created * 1000).getTime())
  ) {
    return respond(400, { error: "invalid_event" });
  }

  const eventObject: unknown = event.data.object;
  if (!isRecord(eventObject)) return respond(400, { error: "invalid_event" });

  const customerId = stripeId(eventObject.customer, "cus_");
  const isSubscriptionEvent = event.type.startsWith("customer.subscription.");
  const hasInvoiceSubscription = !isSubscriptionEvent &&
    invoiceHasSubscriptionReference(eventObject);
  const subscriptionId = isSubscriptionEvent
    ? stripeId(eventObject.id, "sub_")
    : invoiceSubscriptionId(eventObject);
  const invoiceId = isSubscriptionEvent ? null : stripeId(eventObject.id, "in_");
  if (!/^evt_[A-Za-z0-9]+$/.test(event.id) || !customerId) {
    return respond(400, { error: "invalid_event" });
  }
  if (isSubscriptionEvent && !subscriptionId) {
    return respond(400, { error: "invalid_event" });
  }
  if (!isSubscriptionEvent && !invoiceId) {
    return respond(400, { error: "invalid_event" });
  }
  if (!isSubscriptionEvent && hasInvoiceSubscription && !subscriptionId) {
    return respond(400, { error: "invalid_event" });
  }
  if (!subscriptionId) {
    return respond(200, { received: true, ignored: true });
  }

  const admin = createClient(config.supabaseUrl, config.serviceRoleKey, {
    auth: { autoRefreshToken: false, persistSession: false, detectSessionInUrl: false },
  });
  const { data: leaseRows, error: beginError } = await admin.rpc(
    "begin_stripe_webhook_event",
    {
      p_event_id: event.id,
      p_event_type: event.type,
      p_event_created_at: new Date(event.created * 1000).toISOString(),
      p_stripe_customer_id: customerId,
      p_stripe_subscription_id: subscriptionId,
      p_stripe_invoice_id: invoiceId,
    },
  );
  if (beginError) {
    console.error("Stripe webhook event persistence failed.", beginError.code ?? "unknown");
    return respond(503, { error: "webhook_unavailable" });
  }
  if (!isEventLease(leaseRows)) {
    console.error("Stripe webhook lease response violated its contract.");
    return respond(503, { error: "webhook_unavailable" });
  }

  const lease = leaseRows[0];
  if (lease.outcome === "processed" || lease.outcome === "pending_policy" ||
      lease.outcome === "pending_mapping") {
    return respond(200, { received: true });
  }
  if (lease.outcome === "busy") return respond(503, { error: "retry_later" });
  if (lease.outcome !== "started" || !lease.lease_token ||
      lease.lease_generation === null) {
    return respond(503, { error: "webhook_unavailable" });
  }

  try {
    const subscription = await stripe.subscriptions.retrieve(subscriptionId);
    const priceItems = subscription.items.data;
    const subscriptionItem = priceItems.length === 1 ? priceItems[0] : null;
    const priceId = subscriptionItem?.price.id ?? null;
    const periodStart = toTimestamp(subscriptionItem?.current_period_start);
    const periodEnd = toTimestamp(subscriptionItem?.current_period_end);
    const snapshotCustomerId = stripeId(subscription.customer, "cus_");
    const allowedPrice = priceId === config.allowedPriceId;
    let latestInvoiceId: string | null = null;
    let latestInvoicePaid = false;
    if (subscription.status === "active") {
      latestInvoiceId = stripeId(subscription.latest_invoice, "in_");
      if (latestInvoiceId) {
        const latestInvoice = await stripe.invoices.retrieve(latestInvoiceId);
        latestInvoicePaid = latestInvoice.status === "paid" &&
          stripeId(latestInvoice.customer, "cus_") === customerId &&
          invoiceSubscriptionId(latestInvoice) === subscriptionId;
      }
    }

    const { data: applyRows, error: applyError } = await admin.rpc(
      "apply_stripe_webhook_snapshot",
      {
        p_event_id: event.id,
        p_stripe_customer_id: customerId,
        p_snapshot_customer_id: snapshotCustomerId,
        p_stripe_subscription_id: subscription.id,
        p_stripe_price_id: priceId,
        p_subscription_status: subscription.status,
        p_price_allowed: allowedPrice,
        p_latest_invoice_id: latestInvoiceId,
        p_latest_invoice_paid: latestInvoicePaid,
        p_current_period_start: periodStart,
        p_current_period_end: periodEnd,
        p_cancel_at_period_end: subscription.cancel_at_period_end,
        p_lease_token: lease.lease_token,
        p_lease_generation: lease.lease_generation,
      },
    );

    if (applyError) {
      console.error("Stripe webhook reconciliation failed.", applyError.code ?? "unknown");
      return respond(503, { error: "retry_later" });
    }
    if (typeof applyRows !== "string" ||
        !["processed", "pending_policy", "pending_mapping"].includes(applyRows)) {
      return respond(503, { error: "retry_later" });
    }
    return respond(200, { received: true });
  } catch (error) {
    console.error(
      "Stripe subscription retrieval failed.",
      error instanceof Stripe.errors.StripeError ? error.type : "internal",
    );
    return respond(503, { error: "retry_later" });
  }
});
