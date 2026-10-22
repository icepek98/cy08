CREATE TABLE public.tenant_stripe_billing (
    tenant_id uuid PRIMARY KEY
        REFERENCES public.tenants (tenant_id)
        ON DELETE CASCADE,
    stripe_customer_id text NOT NULL UNIQUE
        CHECK (
            length(btrim(stripe_customer_id)) > 0
            AND stripe_customer_id = btrim(stripe_customer_id)
        ),
    stripe_subscription_id text UNIQUE,
    stripe_price_id text,
    stripe_subscription_status text
        CHECK (
            stripe_subscription_status IS NULL
            OR stripe_subscription_status IN (
                'trialing',
                'active',
                'past_due',
                'canceled',
                'unpaid',
                'incomplete',
                'incomplete_expired',
                'paused'
            )
        ),
    current_period_start timestamptz,
    current_period_end timestamptz,
    cancel_at_period_end boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT tenant_stripe_billing_subscription_fields_check
        CHECK (
            (
                stripe_subscription_id IS NULL
                AND stripe_price_id IS NULL
                AND stripe_subscription_status IS NULL
                AND current_period_start IS NULL
                AND current_period_end IS NULL
                AND cancel_at_period_end IS FALSE
            )
            OR (
                stripe_subscription_id IS NOT NULL
                AND length(btrim(stripe_subscription_id)) > 0
                AND stripe_subscription_id = btrim(stripe_subscription_id)
                AND stripe_price_id IS NOT NULL
                AND length(btrim(stripe_price_id)) > 0
                AND stripe_price_id = btrim(stripe_price_id)
                AND stripe_subscription_status IS NOT NULL
                AND current_period_start IS NOT NULL
                AND current_period_end IS NOT NULL
                AND current_period_end > current_period_start
            )
        )
);

ALTER TABLE public.tenant_stripe_billing ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.tenant_stripe_billing
    FROM PUBLIC, anon, authenticated, service_role;

CREATE OR REPLACE FUNCTION public.register_tenant_stripe_customer(
    p_tenant_id uuid,
    p_stripe_customer_id text
)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $function$
DECLARE
    existing_customer_id text;
BEGIN
    IF COALESCE(auth.role(), '') <> 'service_role' THEN
        RAISE EXCEPTION 'Privileged Stripe customer association required.'
            USING ERRCODE = '42501';
    END IF;

    IF p_tenant_id IS NULL
       OR p_stripe_customer_id IS NULL
       OR length(btrim(p_stripe_customer_id)) = 0
       OR p_stripe_customer_id <> btrim(p_stripe_customer_id) THEN
        RAISE EXCEPTION 'Tenant and Stripe customer identifiers are required.'
            USING ERRCODE = '22023';
    END IF;

    PERFORM 1
    FROM public.tenants AS tenant
    WHERE tenant.tenant_id = p_tenant_id
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Tenant does not exist.'
            USING ERRCODE = '23503';
    END IF;

    SELECT billing.stripe_customer_id
      INTO existing_customer_id
    FROM public.tenant_stripe_billing AS billing
    WHERE billing.tenant_id = p_tenant_id
    FOR UPDATE;

    IF FOUND THEN
        IF existing_customer_id IS DISTINCT FROM p_stripe_customer_id THEN
            RAISE EXCEPTION 'Tenant already has a different Stripe customer.'
                USING ERRCODE = '23505';
        END IF;
        RETURN;
    END IF;

    INSERT INTO public.tenant_stripe_billing (
        tenant_id,
        stripe_customer_id
    )
    VALUES (p_tenant_id, p_stripe_customer_id);
END;
$function$;

REVOKE ALL ON FUNCTION public.register_tenant_stripe_customer(uuid, text)
    FROM PUBLIC, anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION public.register_tenant_stripe_customer(uuid, text)
    TO service_role;

CREATE OR REPLACE FUNCTION public.apply_confirmed_tenant_stripe_subscription(
    p_stripe_customer_id text,
    p_stripe_subscription_id text,
    p_stripe_price_id text,
    p_subscription_status text,
    p_current_period_start timestamptz,
    p_current_period_end timestamptz,
    p_cancel_at_period_end boolean
)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $function$
DECLARE
    resolved_tenant_id uuid;
    existing_subscription_id text;
    existing_subscription_status text;
    existing_period_end timestamptz;
    current_license_status text;
    current_license_subscription_id text;
    current_trial_started_at timestamptz;
    current_trial_ends_at timestamptz;
    server_now timestamptz := clock_timestamp();
    next_license_status text;
BEGIN
    IF COALESCE(auth.role(), '') <> 'service_role' THEN
        RAISE EXCEPTION 'Privileged Stripe subscription update required.'
            USING ERRCODE = '42501';
    END IF;

    IF p_stripe_customer_id IS NULL
       OR length(btrim(p_stripe_customer_id)) = 0
       OR p_stripe_customer_id <> btrim(p_stripe_customer_id)
       OR p_stripe_subscription_id IS NULL
       OR length(btrim(p_stripe_subscription_id)) = 0
       OR p_stripe_subscription_id <> btrim(p_stripe_subscription_id)
       OR p_stripe_price_id IS NULL
       OR length(btrim(p_stripe_price_id)) = 0
       OR p_stripe_price_id <> btrim(p_stripe_price_id)
       OR p_subscription_status IS NULL
       OR p_subscription_status NOT IN (
            'trialing',
            'active',
            'past_due',
            'canceled',
            'unpaid',
            'incomplete',
            'incomplete_expired',
            'paused'
       )
       OR p_current_period_start IS NULL
       OR p_current_period_end IS NULL
       OR p_current_period_end <= p_current_period_start
       OR p_cancel_at_period_end IS NULL THEN
        RAISE EXCEPTION 'Confirmed Stripe subscription snapshot is invalid.'
            USING ERRCODE = '22023';
    END IF;

    SELECT billing.tenant_id,
           billing.stripe_subscription_id,
           billing.stripe_subscription_status,
           billing.current_period_end
      INTO resolved_tenant_id,
           existing_subscription_id,
           existing_subscription_status,
           existing_period_end
    FROM public.tenant_stripe_billing AS billing
    WHERE billing.stripe_customer_id = p_stripe_customer_id
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Stripe customer is not associated with a tenant.'
            USING ERRCODE = 'P0002';
    END IF;

    IF existing_subscription_id IS NOT NULL
       AND existing_subscription_id IS DISTINCT FROM p_stripe_subscription_id
       AND NOT (
            existing_subscription_status IN ('canceled', 'incomplete_expired')
            AND (
                existing_subscription_status = 'incomplete_expired'
                OR existing_period_end <= server_now
            )
       ) THEN
        RAISE EXCEPTION 'Tenant already has a non-terminal Stripe subscription.'
            USING ERRCODE = '23505';
    END IF;

    SELECT license.license_status,
           license.subscription_id,
           license.trial_started_at,
           license.trial_ends_at
      INTO current_license_status,
           current_license_subscription_id,
           current_trial_started_at,
           current_trial_ends_at
    FROM public.tenant_licenses AS license
    WHERE license.tenant_id = resolved_tenant_id
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Tenant has no license record.'
            USING ERRCODE = 'P0002';
    END IF;

    IF current_license_subscription_id IS NOT NULL
       AND current_license_subscription_id IS DISTINCT FROM p_stripe_subscription_id
       AND current_license_subscription_id IS DISTINCT FROM existing_subscription_id THEN
        RAISE EXCEPTION 'License is associated with a different subscription.'
            USING ERRCODE = '23505';
    END IF;

    IF p_subscription_status = 'trialing' THEN
        IF current_license_status <> 'trialing'
           OR current_trial_started_at IS NULL
           OR current_trial_ends_at IS NULL
           OR p_current_period_end <= current_trial_started_at
           OR server_now >= current_trial_ends_at
           OR p_current_period_end > current_trial_ends_at THEN
            RAISE EXCEPTION 'Stripe trial would extend or duplicate the existing tenant trial.'
                USING ERRCODE = '22023';
        END IF;
        next_license_status := 'trialing';
    ELSIF p_subscription_status = 'active' THEN
        IF p_current_period_end > server_now THEN
            next_license_status := 'active';
        ELSE
            next_license_status := 'expired';
        END IF;
    ELSIF p_subscription_status = 'canceled' THEN
        IF p_current_period_end > server_now THEN
            next_license_status := 'active';
        ELSE
            next_license_status := 'expired';
        END IF;
    ELSIF p_subscription_status = 'past_due' THEN
        next_license_status := 'past_due';
    ELSE
        next_license_status := 'blocked';
    END IF;

    UPDATE public.tenant_stripe_billing
    SET stripe_subscription_id = p_stripe_subscription_id,
        stripe_price_id = p_stripe_price_id,
        stripe_subscription_status = p_subscription_status,
        current_period_start = p_current_period_start,
        current_period_end = p_current_period_end,
        cancel_at_period_end = p_cancel_at_period_end,
        updated_at = server_now
    WHERE tenant_id = resolved_tenant_id;

    UPDATE public.tenant_licenses
    SET license_status = next_license_status,
        subscription_id = p_stripe_subscription_id,
        trial_ends_at = CASE
            WHEN p_subscription_status = 'trialing'
                THEN p_current_period_end
            ELSE trial_ends_at
        END,
        subscription_started_at = CASE
            WHEN current_license_subscription_id IS DISTINCT FROM p_stripe_subscription_id
                THEN p_current_period_start
            ELSE subscription_started_at
        END,
        subscription_ends_at = p_current_period_end,
        updated_at = server_now
    WHERE tenant_id = resolved_tenant_id;
END;
$function$;

REVOKE ALL ON FUNCTION public.apply_confirmed_tenant_stripe_subscription(
    text, text, text, text, timestamptz, timestamptz, boolean
) FROM PUBLIC, anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION public.apply_confirmed_tenant_stripe_subscription(
    text, text, text, text, timestamptz, timestamptz, boolean
) TO service_role;

COMMENT ON TABLE public.tenant_stripe_billing IS
    'Server-only Stripe customer and subscription identifiers plus the last confirmed subscription snapshot; contains no payment credentials.';

COMMENT ON FUNCTION public.apply_confirmed_tenant_stripe_subscription(
    text, text, text, text, timestamptz, timestamptz, boolean
) IS
    'Service-role-only projection. Call only after a trusted backend has verified Stripe and reconciled the canonical subscription; never call from a desktop client or from Checkout return parameters.';
