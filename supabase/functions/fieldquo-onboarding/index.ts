import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

const responseHeaders = { "content-type": "application/json; charset=utf-8" };

type OnboardingRow = {
  tenant_id: string;
  installation_id: string;
  license_status: string;
  trial_started_at: string | null;
  trial_ends_at: string | null;
  checked_at: string;
  installation_registered: boolean;
  access_allowed: boolean;
  block_reason: string | null;
};

type LimitedBodyResult =
  | { kind: "ok"; text: string }
  | { kind: "too_large" }
  | { kind: "unreadable" };

function respond(status: number, body: Record<string, unknown>): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: responseHeaders,
  });
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

async function readLimitedBody(
  request: Request,
  maxBytes: number,
): Promise<LimitedBodyResult> {
  const reader = request.body?.getReader();
  if (!reader) return { kind: "ok", text: "" };

  const chunks: Uint8Array[] = [];
  let totalBytes = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      totalBytes += value.byteLength;
      if (totalBytes > maxBytes) {
        await reader.cancel();
        return { kind: "too_large" };
      }
      chunks.push(value);
    }
  } catch {
    return { kind: "unreadable" };
  }

  const body = new Uint8Array(totalBytes);
  let offset = 0;
  for (const chunk of chunks) {
    body.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return { kind: "ok", text: new TextDecoder().decode(body) };
}

function isIsoTimestamp(value: unknown): value is string {
  return typeof value === "string" &&
    /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/.test(
      value,
    ) &&
    Number.isFinite(Date.parse(value));
}

function isOnboardingRow(value: unknown): value is OnboardingRow {
  if (!isRecord(value)) return false;
  const expectedKeys = [
    "tenant_id",
    "installation_id",
    "license_status",
    "trial_started_at",
    "trial_ends_at",
    "checked_at",
    "installation_registered",
    "access_allowed",
    "block_reason",
  ];
  if (
    Object.keys(value).length !== expectedKeys.length ||
    !expectedKeys.every((key) => Object.hasOwn(value, key))
  ) {
    return false;
  }

  const statuses = [
    "trialing",
    "active",
    "past_due",
    "canceled",
    "expired",
    "blocked",
    "revoked",
  ];
  const reasons = [
    "installation_revoked",
    "license_revoked",
    "trial_expired",
    "license_not_active",
    "license_missing",
  ];

  return typeof value.tenant_id === "string" &&
    UUID_PATTERN.test(value.tenant_id) &&
    typeof value.installation_id === "string" &&
    UUID_PATTERN.test(value.installation_id) &&
    typeof value.license_status === "string" &&
    statuses.includes(value.license_status) &&
    (value.trial_started_at === null || isIsoTimestamp(value.trial_started_at)) &&
    (value.trial_ends_at === null || isIsoTimestamp(value.trial_ends_at)) &&
    isIsoTimestamp(value.checked_at) &&
    value.installation_registered === true &&
    typeof value.access_allowed === "boolean" &&
    (value.block_reason === null ||
      (typeof value.block_reason === "string" &&
        reasons.includes(value.block_reason))) &&
    (value.license_status !== "trialing" ||
      (value.trial_started_at !== null && value.trial_ends_at !== null)) &&
    (value.access_allowed
      ? value.block_reason === null &&
        (value.license_status === "trialing" ||
          value.license_status === "active")
      : value.block_reason !== null);
}

Deno.serve(async (request: Request): Promise<Response> => {
  if (request.method !== "POST") {
    return respond(405, { error: "method_not_allowed" });
  }

  const contentType = request.headers.get("content-type") ?? "";
  if (!contentType.toLowerCase().startsWith("application/json")) {
    return respond(415, { error: "application_json_required" });
  }

  const authorization = request.headers.get("authorization") ?? "";
  const bearer = /^Bearer ([^\s]+)$/i.exec(authorization);
  if (!bearer) {
    return respond(401, { error: "authentication_required" });
  }

  const body = await readLimitedBody(request, 4096);
  if (body.kind === "too_large") {
    return respond(413, { error: "request_too_large" });
  }
  if (body.kind === "unreadable") {
    return respond(400, { error: "invalid_request_body" });
  }

  let payload: unknown;
  try {
    payload = JSON.parse(body.text);
  } catch {
    return respond(400, { error: "invalid_json" });
  }

  if (
    !isRecord(payload) ||
    Object.keys(payload).length !== 1 ||
    !Object.hasOwn(payload, "installation_id") ||
    typeof payload.installation_id !== "string" ||
    !UUID_PATTERN.test(payload.installation_id)
  ) {
    return respond(400, { error: "invalid_onboarding_payload" });
  }

  const supabaseUrl = Deno.env.get("SUPABASE_URL");
  const serviceRoleKey = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY");
  if (!supabaseUrl || !serviceRoleKey) {
    console.error("Onboarding server configuration is incomplete.");
    return respond(503, { error: "onboarding_unavailable" });
  }

  const supabase = createClient(supabaseUrl, serviceRoleKey, {
    auth: {
      autoRefreshToken: false,
      detectSessionInUrl: false,
      persistSession: false,
    },
  });

  let authResult: Awaited<ReturnType<typeof supabase.auth.getUser>>;
  try {
    authResult = await supabase.auth.getUser(bearer[1]);
  } catch {
    console.error("Onboarding authentication lookup failed.");
    return respond(503, { error: "onboarding_unavailable" });
  }
  const { data: authData, error: authError } = authResult;
  if (authError || !authData.user) {
    return respond(401, { error: "invalid_authentication" });
  }
  if (!authData.user.email_confirmed_at) {
    return respond(403, { error: "email_verification_required" });
  }

  let data: unknown;
  let error: { code?: string } | null;
  try {
    const rpcResult = await supabase.rpc(
      "register_or_get_tenant_for_installation",
      {
        p_user_id: authData.user.id,
        p_installation_id: payload.installation_id.toLowerCase(),
      },
    );
    data = rpcResult.data;
    error = rpcResult.error;
  } catch {
    console.error("Onboarding database operation failed.");
    return respond(503, { error: "onboarding_unavailable" });
  }

  if (error) {
    if (error.code === "42501") {
      return respond(403, { error: "onboarding_not_authorized" });
    }
    console.error("Onboarding database operation failed.", error.code ?? "unknown");
    return respond(503, { error: "onboarding_unavailable" });
  }

  if (
    !Array.isArray(data) ||
    data.length !== 1 ||
    !isOnboardingRow(data[0]) ||
    data[0].installation_id.toLowerCase() !==
      payload.installation_id.toLowerCase()
  ) {
    console.error("Onboarding database response violated its contract.");
    return respond(502, { error: "invalid_onboarding_response" });
  }

  const row = data[0];
  return respond(200, {
    tenant_id: row.tenant_id,
    installation_id: row.installation_id,
    license_status: row.license_status,
    trial_started_at: row.trial_started_at,
    trial_ends_at: row.trial_ends_at,
    checked_at: row.checked_at,
    installation_registered: row.installation_registered,
    access_allowed: row.access_allowed,
    block_reason: row.block_reason,
  });
});
