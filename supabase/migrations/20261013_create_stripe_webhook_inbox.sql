CREATE TABLE public.stripe_webhook_events (
    event_id text PRIMARY KEY
        CHECK (
            length(btrim(event_id)) > 0
            AND event_id = btrim(event_id)
        ),
    event_type text NOT NULL
        CHECK (
            event_type IN (
                'customer.subscription.created',
                'customer.subscription.updated',
                'customer.subscription.deleted',
                'invoice.paid',
                'invoice.payment_failed'
            )
        ),
    event_created_at timestamptz NOT NULL,
    stripe_customer_id text NOT NULL
        CHECK (
            length(btrim(stripe_customer_id)) > 0
            AND stripe_customer_id = btrim(stripe_customer_id)
        ),
    stripe_subscription_id text NOT NULL
        CHECK (
            length(btrim(stripe_subscription_id)) > 0
            AND stripe_subscription_id = btrim(stripe_subscription_id)
        ),
    stripe_invoice_id text
        CHECK (
            stripe_invoice_id IS NULL
            OR (
                stripe_invoice_id ~ '^in_[A-Za-z0-9]+$'
                AND stripe_invoice_id = btrim(stripe_invoice_id)
            )
        ),
    processing_status text NOT NULL DEFAULT 'processing'
        CHECK (
            processing_status IN (
                'processing',
                'processed',
                'pending_policy',
                'pending_mapping'
            )
        ),
    attempt_count integer NOT NULL DEFAULT 0
        CHECK (attempt_count >= 0),
    error_code text,
    received_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    processed_at timestamptz
);

CREATE TABLE public.stripe_webhook_customer_leases (
    stripe_customer_id text PRIMARY KEY
        CHECK (
            length(btrim(stripe_customer_id)) > 0
            AND stripe_customer_id = btrim(stripe_customer_id)
        ),
    generation bigint NOT NULL DEFAULT 0
        CHECK (generation >= 0),
    lease_token uuid,
    lease_expires_at timestamptz NOT NULL DEFAULT '-infinity'
);

ALTER TABLE public.stripe_webhook_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.stripe_webhook_customer_leases ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE public.stripe_webhook_events
    FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON TABLE public.stripe_webhook_customer_leases
    FROM PUBLIC, anon, authenticated, service_role;

CREATE OR REPLACE FUNCTION public.resolve_tenant_for_billing(p_user_id uuid)
RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $function$
DECLARE
    matching_tenant_ids uuid[];
BEGIN
    IF COALESCE(auth.role(), '') <> 'service_role' THEN
        RAISE EXCEPTION 'Privileged billing tenant resolution required.'
            USING ERRCODE = '42501';
    END IF;

    IF p_user_id IS NULL THEN
        RAISE EXCEPTION 'Authenticated user is required.'
            USING ERRCODE = '22023';
    END IF;

    SELECT array_agg(account.tenant_id)
      INTO matching_tenant_ids
    FROM public.tenant_accounts AS account
    WHERE account.user_id = p_user_id;

    IF COALESCE(cardinality(matching_tenant_ids), 0) <> 1 THEN
        RETURN NULL;
    END IF;

    RETURN matching_tenant_ids[1];
END;
$function$;

REVOKE ALL ON FUNCTION public.resolve_tenant_for_billing(uuid)
    FROM PUBLIC, anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION public.resolve_tenant_for_billing(uuid)
    TO service_role;

CREATE OR REPLACE FUNCTION public.get_tenant_stripe_checkout_state(p_tenant_id uuid)
RETURNS TABLE (
    stripe_customer_id text,
    stripe_subscription_id text
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $function$
BEGIN
    IF COALESCE(auth.role(), '') <> 'service_role' THEN
        RAISE EXCEPTION 'Privileged billing state lookup required.'
            USING ERRCODE = '42501';
    END IF;

    IF p_tenant_id IS NULL THEN
        RAISE EXCEPTION 'Tenant identifier is required.'
            USING ERRCODE = '22023';
    END IF;

    RETURN QUERY
    SELECT billing.stripe_customer_id,
           billing.stripe_subscription_id
    FROM public.tenant_stripe_billing AS billing
    WHERE billing.tenant_id = p_tenant_id;
END;
$function$;

REVOKE ALL ON FUNCTION public.get_tenant_stripe_checkout_state(uuid)
    FROM PUBLIC, anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION public.get_tenant_stripe_checkout_state(uuid)
    TO service_role;

CREATE OR REPLACE FUNCTION public.begin_stripe_webhook_event(
    p_event_id text,
    p_event_type text,
    p_event_created_at timestamptz,
    p_stripe_customer_id text,
    p_stripe_subscription_id text,
    p_stripe_invoice_id text
)
RETURNS TABLE (
    outcome text,
    lease_token uuid,
    lease_generation bigint
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $function$
DECLARE
    existing_event public.stripe_webhook_events%ROWTYPE;
    acquired_lease public.stripe_webhook_customer_leases%ROWTYPE;
BEGIN
    IF COALESCE(auth.role(), '') <> 'service_role' THEN
        RAISE EXCEPTION 'Privileged Stripe webhook processing required.'
            USING ERRCODE = '42501';
    END IF;

    IF p_event_id IS NULL
       OR p_event_id !~ '^evt_[A-Za-z0-9]+$'
       OR p_event_type IS NULL
       OR p_event_type NOT IN (
            'customer.subscription.created',
            'customer.subscription.updated',
            'customer.subscription.deleted',
            'invoice.paid',
            'invoice.payment_failed'
       )
       OR p_event_created_at IS NULL
       OR p_stripe_customer_id IS NULL
       OR p_stripe_customer_id !~ '^cus_[A-Za-z0-9]+$'
       OR p_stripe_subscription_id IS NULL
       OR p_stripe_subscription_id !~ '^sub_[A-Za-z0-9]+$'
       OR (
            p_event_type IN ('invoice.paid', 'invoice.payment_failed')
            AND (
                p_stripe_invoice_id IS NULL
                OR p_stripe_invoice_id !~ '^in_[A-Za-z0-9]+$'
            )
       )
       OR (
            p_event_type LIKE 'customer.subscription.%'
            AND p_stripe_invoice_id IS NOT NULL
       ) THEN
        RAISE EXCEPTION 'Stripe webhook event identifiers are invalid.'
            USING ERRCODE = '22023';
    END IF;

    INSERT INTO public.stripe_webhook_events (
        event_id,
        event_type,
        event_created_at,
        stripe_customer_id,
        stripe_subscription_id,
        stripe_invoice_id
    )
    VALUES (
        p_event_id,
        p_event_type,
        p_event_created_at,
        p_stripe_customer_id,
        p_stripe_subscription_id,
        p_stripe_invoice_id
    )
    ON CONFLICT (event_id) DO NOTHING;

    SELECT event.*
      INTO existing_event
    FROM public.stripe_webhook_events AS event
    WHERE event.event_id = p_event_id
    FOR UPDATE;

    IF existing_event.event_type IS DISTINCT FROM p_event_type
       OR existing_event.event_created_at IS DISTINCT FROM p_event_created_at
       OR existing_event.stripe_customer_id IS DISTINCT FROM p_stripe_customer_id
       OR existing_event.stripe_subscription_id IS DISTINCT FROM p_stripe_subscription_id
       OR existing_event.stripe_invoice_id IS DISTINCT FROM p_stripe_invoice_id THEN
        RAISE EXCEPTION 'Stripe event identifier was reused with different content.'
            USING ERRCODE = '22023';
    END IF;

    IF existing_event.processing_status IN ('processed', 'pending_policy') THEN
        RETURN QUERY
        SELECT existing_event.processing_status, NULL::uuid, NULL::bigint;
        RETURN;
    END IF;

    PERFORM 1
    FROM public.tenant_stripe_billing AS billing
    WHERE billing.stripe_customer_id = p_stripe_customer_id;

    IF NOT FOUND THEN
        UPDATE public.stripe_webhook_events AS event
        SET processing_status = 'pending_mapping',
            error_code = 'customer_not_linked',
            updated_at = clock_timestamp()
        WHERE event.event_id = p_event_id;

        RETURN QUERY
        SELECT 'pending_mapping'::text, NULL::uuid, NULL::bigint;
        RETURN;
    END IF;

    INSERT INTO public.stripe_webhook_customer_leases AS current_lease (
        stripe_customer_id,
        generation,
        lease_token,
        lease_expires_at
    )
    VALUES (
        p_stripe_customer_id,
        1,
        gen_random_uuid(),
        clock_timestamp() + interval '90 seconds'
    )
    ON CONFLICT (stripe_customer_id)
    DO UPDATE
       SET generation = current_lease.generation + 1,
           lease_token = EXCLUDED.lease_token,
           lease_expires_at = EXCLUDED.lease_expires_at
     WHERE current_lease.lease_expires_at <= clock_timestamp()
    RETURNING *
         INTO acquired_lease;

    IF NOT FOUND THEN
        RETURN QUERY
        SELECT 'busy'::text, NULL::uuid, NULL::bigint;
        RETURN;
    END IF;

    UPDATE public.stripe_webhook_events AS event
    SET processing_status = 'processing',
        attempt_count = event.attempt_count + 1,
        error_code = NULL,
        updated_at = clock_timestamp()
    WHERE event.event_id = p_event_id;

    RETURN QUERY
    SELECT 'started'::text,
           acquired_lease.lease_token,
           acquired_lease.generation;
END;
$function$;

REVOKE ALL ON FUNCTION public.begin_stripe_webhook_event(
    text, text, timestamptz, text, text, text
) FROM PUBLIC, anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION public.begin_stripe_webhook_event(
    text, text, timestamptz, text, text, text
) TO service_role;

CREATE OR REPLACE FUNCTION public.apply_stripe_webhook_snapshot(
    p_event_id text,
    p_stripe_customer_id text,
    p_snapshot_customer_id text,
    p_stripe_subscription_id text,
    p_stripe_price_id text,
    p_subscription_status text,
    p_price_allowed boolean,
    p_latest_invoice_id text,
    p_latest_invoice_paid boolean,
    p_current_period_start timestamptz,
    p_current_period_end timestamptz,
    p_cancel_at_period_end boolean,
    p_lease_token uuid,
    p_lease_generation bigint
)
RETURNS text
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $function$
DECLARE
    current_event public.stripe_webhook_events%ROWTYPE;
    current_lease public.stripe_webhook_customer_leases%ROWTYPE;
    resolved_tenant_id uuid;
    current_license_status text;
    current_license_subscription_id text;
    existing_subscription_id text;
BEGIN
    IF COALESCE(auth.role(), '') <> 'service_role' THEN
        RAISE EXCEPTION 'Privileged Stripe webhook processing required.'
            USING ERRCODE = '42501';
    END IF;

    SELECT event.*
      INTO current_event
    FROM public.stripe_webhook_events AS event
    WHERE event.event_id = p_event_id
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Stripe webhook event was not recorded.'
            USING ERRCODE = 'P0002';
    END IF;

    IF current_event.processing_status IN ('processed', 'pending_policy', 'pending_mapping') THEN
        RETURN current_event.processing_status;
    END IF;

    IF current_event.processing_status <> 'processing'
       OR current_event.stripe_customer_id IS DISTINCT FROM p_stripe_customer_id
       OR current_event.stripe_subscription_id IS DISTINCT FROM p_stripe_subscription_id THEN
        RAISE EXCEPTION 'Stripe webhook event is not eligible for this snapshot.'
            USING ERRCODE = '22023';
    END IF;

    SELECT lease.*
      INTO current_lease
    FROM public.stripe_webhook_customer_leases AS lease
    WHERE lease.stripe_customer_id = p_stripe_customer_id
    FOR UPDATE;

    IF NOT FOUND
       OR current_lease.lease_token IS DISTINCT FROM p_lease_token
       OR current_lease.generation IS DISTINCT FROM p_lease_generation
       OR current_lease.lease_expires_at <= clock_timestamp() THEN
        RETURN 'lease_lost';
    END IF;

    SELECT billing.tenant_id,
           billing.stripe_subscription_id
      INTO resolved_tenant_id,
           existing_subscription_id
    FROM public.tenant_stripe_billing AS billing
    WHERE billing.stripe_customer_id = p_stripe_customer_id
    FOR UPDATE;

    IF NOT FOUND THEN
        UPDATE public.stripe_webhook_events AS event
        SET processing_status = 'pending_mapping',
            error_code = 'customer_not_linked',
            updated_at = clock_timestamp()
        WHERE event.event_id = p_event_id;
        UPDATE public.stripe_webhook_customer_leases AS lease
        SET lease_token = NULL,
            lease_expires_at = clock_timestamp()
        WHERE lease.stripe_customer_id = p_stripe_customer_id;
        RETURN 'pending_mapping';
    END IF;

    IF p_snapshot_customer_id IS DISTINCT FROM p_stripe_customer_id
       OR p_stripe_subscription_id IS NULL
       OR p_subscription_status IS NULL
       OR p_subscription_status NOT IN ('active', 'past_due', 'canceled')
       OR (
            p_subscription_status <> 'past_due'
            AND (
                p_stripe_price_id IS NULL
                OR length(btrim(p_stripe_price_id)) = 0
                OR p_price_allowed IS DISTINCT FROM true
                OR p_current_period_start IS NULL
                OR p_current_period_end IS NULL
                OR p_current_period_end <= p_current_period_start
            )
       )
       OR p_cancel_at_period_end IS NULL THEN
        UPDATE public.stripe_webhook_events AS event
        SET processing_status = 'pending_policy',
            error_code = 'snapshot_not_eligible',
            updated_at = clock_timestamp()
        WHERE event.event_id = p_event_id;
        UPDATE public.stripe_webhook_customer_leases AS lease
        SET lease_token = NULL,
            lease_expires_at = clock_timestamp()
        WHERE lease.stripe_customer_id = p_stripe_customer_id;
        RETURN 'pending_policy';
    END IF;

    SELECT license.license_status,
           license.subscription_id
      INTO current_license_status,
           current_license_subscription_id
    FROM public.tenant_licenses AS license
    WHERE license.tenant_id = resolved_tenant_id
    FOR UPDATE;

    IF NOT FOUND
       OR current_license_status IN ('blocked', 'revoked')
       OR (
            existing_subscription_id IS NOT NULL
            AND existing_subscription_id IS DISTINCT FROM p_stripe_subscription_id
       )
       OR (
            current_license_subscription_id IS NOT NULL
            AND current_license_subscription_id IS DISTINCT FROM p_stripe_subscription_id
       ) THEN
        UPDATE public.stripe_webhook_events AS event
        SET processing_status = 'pending_policy',
            error_code = 'license_transition_pending_decision',
            updated_at = clock_timestamp()
        WHERE event.event_id = p_event_id;
        UPDATE public.stripe_webhook_customer_leases AS lease
        SET lease_token = NULL,
            lease_expires_at = clock_timestamp()
        WHERE lease.stripe_customer_id = p_stripe_customer_id;
        RETURN 'pending_policy';
    END IF;

    IF p_subscription_status = 'past_due'
       AND (
            p_price_allowed IS DISTINCT FROM true
            OR p_stripe_price_id IS NULL
            OR length(btrim(p_stripe_price_id)) = 0
            OR p_current_period_start IS NULL
            OR p_current_period_end IS NULL
            OR p_current_period_end <= p_current_period_start
       ) THEN
        IF current_license_status NOT IN ('trialing', 'active', 'past_due') THEN
            UPDATE public.stripe_webhook_events AS event
            SET processing_status = 'pending_policy',
                error_code = 'past_due_transition_pending_decision',
                updated_at = clock_timestamp()
            WHERE event.event_id = p_event_id;
            UPDATE public.stripe_webhook_customer_leases AS lease
            SET lease_token = NULL,
                lease_expires_at = clock_timestamp()
            WHERE lease.stripe_customer_id = p_stripe_customer_id;
            RETURN 'pending_policy';
        END IF;

        UPDATE public.tenant_licenses AS license
        SET license_status = 'past_due',
            updated_at = clock_timestamp()
        WHERE license.tenant_id = resolved_tenant_id;

        UPDATE public.stripe_webhook_events AS event
        SET processing_status = 'processed',
            error_code = 'past_due_price_not_allowlisted',
            processed_at = clock_timestamp(),
            updated_at = clock_timestamp()
        WHERE event.event_id = p_event_id;

        UPDATE public.stripe_webhook_customer_leases AS lease
        SET lease_token = NULL,
            lease_expires_at = clock_timestamp()
        WHERE lease.stripe_customer_id = p_stripe_customer_id;

        RETURN 'processed';
    END IF;

    IF p_subscription_status = 'active' THEN
        IF p_price_allowed IS DISTINCT FROM true
           OR p_latest_invoice_id IS NULL
           OR p_latest_invoice_id !~ '^in_[A-Za-z0-9]+$'
           OR p_latest_invoice_paid IS DISTINCT FROM true THEN
            UPDATE public.stripe_webhook_events AS event
            SET processing_status = 'pending_policy',
                error_code = 'awaiting_latest_invoice_paid',
                updated_at = clock_timestamp()
            WHERE event.event_id = p_event_id;
            UPDATE public.stripe_webhook_customer_leases AS lease
            SET lease_token = NULL,
                lease_expires_at = clock_timestamp()
            WHERE lease.stripe_customer_id = p_stripe_customer_id;
            RETURN 'pending_policy';
        ELSIF current_license_status = 'trialing'
           AND current_license_subscription_id IS NULL
           AND existing_subscription_id IS NULL THEN
            NULL;
        ELSIF current_license_status = 'active'
              AND current_license_subscription_id = p_stripe_subscription_id
              AND existing_subscription_id = p_stripe_subscription_id THEN
            NULL;
        ELSE
            UPDATE public.stripe_webhook_events AS event
            SET processing_status = 'pending_policy',
                error_code = 'active_transition_pending_decision',
                updated_at = clock_timestamp()
            WHERE event.event_id = p_event_id;
            UPDATE public.stripe_webhook_customer_leases AS lease
            SET lease_token = NULL,
                lease_expires_at = clock_timestamp()
            WHERE lease.stripe_customer_id = p_stripe_customer_id;
            RETURN 'pending_policy';
        END IF;
    ELSIF p_subscription_status = 'past_due' THEN
        IF current_license_status NOT IN ('trialing', 'active', 'past_due') THEN
            UPDATE public.stripe_webhook_events AS event
            SET processing_status = 'pending_policy',
                error_code = 'past_due_transition_pending_decision',
                updated_at = clock_timestamp()
            WHERE event.event_id = p_event_id;
            UPDATE public.stripe_webhook_customer_leases AS lease
            SET lease_token = NULL,
                lease_expires_at = clock_timestamp()
            WHERE lease.stripe_customer_id = p_stripe_customer_id;
            RETURN 'pending_policy';
        END IF;
    ELSIF p_subscription_status = 'canceled' THEN
        IF p_price_allowed IS DISTINCT FROM true
           OR current_license_status <> 'active'
           OR current_license_subscription_id IS DISTINCT FROM p_stripe_subscription_id
           OR existing_subscription_id IS DISTINCT FROM p_stripe_subscription_id THEN
            UPDATE public.stripe_webhook_events AS event
            SET processing_status = 'pending_policy',
                error_code = 'cancellation_transition_pending_decision',
                updated_at = clock_timestamp()
            WHERE event.event_id = p_event_id;
            UPDATE public.stripe_webhook_customer_leases AS lease
            SET lease_token = NULL,
                lease_expires_at = clock_timestamp()
            WHERE lease.stripe_customer_id = p_stripe_customer_id;
            RETURN 'pending_policy';
        END IF;
    END IF;

    PERFORM public.apply_confirmed_tenant_stripe_subscription(
        p_stripe_customer_id,
        p_stripe_subscription_id,
        p_stripe_price_id,
        p_subscription_status,
        p_current_period_start,
        p_current_period_end,
        p_cancel_at_period_end
    );

    UPDATE public.stripe_webhook_events AS event
    SET processing_status = 'processed',
        error_code = NULL,
        processed_at = clock_timestamp(),
        updated_at = clock_timestamp()
    WHERE event.event_id = p_event_id;

    UPDATE public.stripe_webhook_customer_leases AS lease
    SET lease_token = NULL,
        lease_expires_at = clock_timestamp()
    WHERE lease.stripe_customer_id = p_stripe_customer_id;

    RETURN 'processed';
END;
$function$;

REVOKE ALL ON FUNCTION public.apply_stripe_webhook_snapshot(
    text, text, text, text, text, text, boolean, text, boolean,
    timestamptz, timestamptz, boolean, uuid, bigint
) FROM PUBLIC, anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION public.apply_stripe_webhook_snapshot(
    text, text, text, text, text, text, boolean, text, boolean,
    timestamptz, timestamptz, boolean, uuid, bigint
) TO service_role;

COMMENT ON TABLE public.stripe_webhook_events IS
    'Server-only Stripe event inbox. Stores event identifiers and processing metadata only; raw payloads and payment credentials are not retained.';
COMMENT ON FUNCTION public.apply_stripe_webhook_snapshot(
    text, text, text, text, text, text, boolean, text, boolean,
    timestamptz, timestamptz, boolean, uuid, bigint
) IS
    'Atomically applies a canonical Stripe snapshot and completes its inbox event only when the fenced lease is current and the license transition is explicitly allowed.';
