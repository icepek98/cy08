CREATE TABLE public.stripe_checkout_reservations (
    tenant_id uuid PRIMARY KEY
        REFERENCES public.tenants (tenant_id)
        ON DELETE CASCADE,
    stripe_customer_id text NOT NULL UNIQUE
        CHECK (
            stripe_customer_id ~ '^cus_[A-Za-z0-9]+$'
            AND stripe_customer_id = btrim(stripe_customer_id)
        ),
    stripe_price_id text NOT NULL
        CHECK (
            stripe_price_id ~ '^price_[A-Za-z0-9]+$'
            AND stripe_price_id = btrim(stripe_price_id)
        ),
    success_url text NOT NULL CHECK (success_url LIKE 'https://%'),
    cancel_url text NOT NULL CHECK (cancel_url LIKE 'https://%'),
    idempotency_key text NOT NULL UNIQUE,
    integration_identifier text NOT NULL
        CHECK (integration_identifier ~ '^cy08-checkout-[A-Z]{8}$'),
    lease_token uuid,
    lease_expires_at timestamptz NOT NULL DEFAULT '-infinity',
    stripe_session_id text UNIQUE
        CHECK (
            stripe_session_id IS NULL
            OR stripe_session_id ~ '^cs_[A-Za-z0-9_]+$'
        ),
    checkout_url text,
    session_expires_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT stripe_checkout_reservation_session_fields_check
        CHECK (
            (stripe_session_id IS NULL AND checkout_url IS NULL)
            OR (
                stripe_session_id IS NOT NULL
                AND checkout_url IS NOT NULL
                AND checkout_url LIKE 'https://%'
            )
        )
);

ALTER TABLE public.stripe_checkout_reservations ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.stripe_checkout_reservations
    FROM PUBLIC, anon, authenticated, service_role;

CREATE OR REPLACE FUNCTION public.begin_tenant_stripe_checkout(
    p_tenant_id uuid,
    p_stripe_customer_id text,
    p_stripe_price_id text,
    p_success_url text,
    p_cancel_url text,
    p_candidate_idempotency_key text,
    p_candidate_integration_identifier text
)
RETURNS TABLE (
    outcome text,
    lease_token uuid,
    idempotency_key text,
    integration_identifier text,
    checkout_url text,
    success_url text,
    cancel_url text,
    session_expires_at timestamptz
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $function$
DECLARE
    server_now timestamptz;
    current_license public.tenant_licenses%ROWTYPE;
    current_billing public.tenant_stripe_billing%ROWTYPE;
    current_reservation public.stripe_checkout_reservations%ROWTYPE;
    new_lease_token uuid;
BEGIN
    IF COALESCE(auth.role(), '') <> 'service_role' THEN
        RAISE EXCEPTION 'Privileged Stripe Checkout reservation required.'
            USING ERRCODE = '42501';
    END IF;

    IF p_tenant_id IS NULL
       OR p_stripe_customer_id IS NULL
       OR p_stripe_customer_id !~ '^cus_[A-Za-z0-9]+$'
       OR p_stripe_price_id IS NULL
       OR p_stripe_price_id !~ '^price_[A-Za-z0-9]+$'
       OR p_success_url IS NULL
       OR p_success_url NOT LIKE 'https://%'
       OR p_cancel_url IS NULL
       OR p_cancel_url NOT LIKE 'https://%'
       OR p_candidate_idempotency_key IS NULL
       OR p_candidate_idempotency_key !~ '^cy08-checkout-[A-Za-z0-9_-]{1,200}$'
       OR p_candidate_integration_identifier IS NULL
       OR p_candidate_integration_identifier !~ '^cy08-checkout-[A-Z]{8}$' THEN
        RAISE EXCEPTION 'Stripe Checkout reservation parameters are invalid.'
            USING ERRCODE = '22023';
    END IF;

    SELECT billing.*
      INTO current_billing
    FROM public.tenant_stripe_billing AS billing
    WHERE billing.tenant_id = p_tenant_id
    FOR UPDATE;

    IF NOT FOUND
       OR current_billing.stripe_customer_id IS DISTINCT FROM p_stripe_customer_id THEN
        RETURN QUERY
        SELECT 'customer_mismatch'::text, NULL::uuid, NULL::text, NULL::text,
               NULL::text, NULL::text, NULL::text, NULL::timestamptz;
        RETURN;
    END IF;

    IF current_billing.stripe_subscription_id IS NOT NULL THEN
        RETURN QUERY
        SELECT 'subscription_linked'::text, NULL::uuid, NULL::text, NULL::text,
               NULL::text, NULL::text, NULL::text, NULL::timestamptz;
        RETURN;
    END IF;

    SELECT license.*
      INTO current_license
    FROM public.tenant_licenses AS license
    WHERE license.tenant_id = p_tenant_id
    FOR UPDATE;

    IF NOT FOUND
       OR current_license.license_status <> 'trialing'
       OR current_license.trial_ends_at IS NULL
       OR clock_timestamp() >= current_license.trial_ends_at THEN
        RETURN QUERY
        SELECT 'not_eligible'::text, NULL::uuid, NULL::text, NULL::text,
               NULL::text, NULL::text, NULL::text, NULL::timestamptz;
        RETURN;
    END IF;
    server_now := clock_timestamp();

    SELECT reservation.*
      INTO current_reservation
    FROM public.stripe_checkout_reservations AS reservation
    WHERE reservation.tenant_id = p_tenant_id
    FOR UPDATE;

    IF FOUND THEN
        IF current_reservation.stripe_customer_id IS DISTINCT FROM p_stripe_customer_id THEN
            RETURN QUERY
            SELECT 'customer_mismatch'::text, NULL::uuid, NULL::text, NULL::text,
                   NULL::text, NULL::text, NULL::text, NULL::timestamptz;
            RETURN;
        END IF;

        IF current_reservation.session_expires_at > server_now THEN
            IF current_reservation.stripe_price_id IS DISTINCT FROM p_stripe_price_id
               OR current_reservation.success_url IS DISTINCT FROM p_success_url
               OR current_reservation.cancel_url IS DISTINCT FROM p_cancel_url THEN
                RETURN QUERY
                SELECT 'price_mismatch'::text, NULL::uuid, NULL::text, NULL::text,
                       NULL::text, NULL::text, NULL::text, NULL::timestamptz;
                RETURN;
            END IF;

            IF current_reservation.checkout_url IS NOT NULL THEN
                RETURN QUERY
                SELECT 'reuse'::text, NULL::uuid,
                       current_reservation.idempotency_key,
                       current_reservation.integration_identifier,
                       current_reservation.checkout_url,
                       current_reservation.success_url,
                       current_reservation.cancel_url,
                       current_reservation.session_expires_at;
                RETURN;
            END IF;

            IF current_reservation.lease_expires_at > server_now THEN
                RETURN QUERY
                SELECT 'busy'::text, NULL::uuid,
                       current_reservation.idempotency_key,
                       current_reservation.integration_identifier,
                       NULL::text,
                       current_reservation.success_url,
                       current_reservation.cancel_url,
                       current_reservation.session_expires_at;
                RETURN;
            END IF;

            new_lease_token := gen_random_uuid();
            UPDATE public.stripe_checkout_reservations AS reservation
            SET lease_token = new_lease_token,
                lease_expires_at = server_now + interval '90 seconds',
                updated_at = server_now
            WHERE reservation.tenant_id = p_tenant_id;

            RETURN QUERY
            SELECT 'started'::text, new_lease_token,
                   current_reservation.idempotency_key,
                   current_reservation.integration_identifier,
                   NULL::text,
                   current_reservation.success_url,
                   current_reservation.cancel_url,
                   current_reservation.session_expires_at;
            RETURN;
        END IF;

        new_lease_token := gen_random_uuid();
        UPDATE public.stripe_checkout_reservations AS reservation
        SET stripe_price_id = p_stripe_price_id,
            success_url = p_success_url,
            cancel_url = p_cancel_url,
            idempotency_key = p_candidate_idempotency_key,
            integration_identifier = p_candidate_integration_identifier,
            lease_token = new_lease_token,
            lease_expires_at = server_now + interval '90 seconds',
            stripe_session_id = NULL,
            checkout_url = NULL,
            session_expires_at = server_now + interval '23 hours',
            updated_at = server_now
        WHERE reservation.tenant_id = p_tenant_id;

        RETURN QUERY
        SELECT 'started'::text, new_lease_token,
               p_candidate_idempotency_key,
               p_candidate_integration_identifier,
               NULL::text,
               p_success_url,
               p_cancel_url,
               server_now + interval '23 hours';
        RETURN;
    END IF;

    new_lease_token := gen_random_uuid();
    INSERT INTO public.stripe_checkout_reservations (
        tenant_id,
        stripe_customer_id,
        stripe_price_id,
        success_url,
        cancel_url,
        idempotency_key,
        integration_identifier,
        lease_token,
        lease_expires_at,
        session_expires_at
    )
    VALUES (
        p_tenant_id,
        p_stripe_customer_id,
        p_stripe_price_id,
        p_success_url,
        p_cancel_url,
        p_candidate_idempotency_key,
        p_candidate_integration_identifier,
        new_lease_token,
        server_now + interval '90 seconds',
        server_now + interval '23 hours'
    );

    RETURN QUERY
    SELECT 'started'::text, new_lease_token,
           p_candidate_idempotency_key,
           p_candidate_integration_identifier,
           NULL::text,
           p_success_url,
           p_cancel_url,
           server_now + interval '23 hours';
END;
$function$;

CREATE OR REPLACE FUNCTION public.complete_tenant_stripe_checkout(
    p_tenant_id uuid,
    p_stripe_customer_id text,
    p_lease_token uuid,
    p_stripe_session_id text,
    p_checkout_url text
)
RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $function$
BEGIN
    IF COALESCE(auth.role(), '') <> 'service_role' THEN
        RAISE EXCEPTION 'Privileged Stripe Checkout completion required.'
            USING ERRCODE = '42501';
    END IF;

    IF p_tenant_id IS NULL
       OR p_stripe_customer_id IS NULL
       OR p_lease_token IS NULL
       OR p_stripe_session_id IS NULL
       OR p_stripe_session_id !~ '^cs_[A-Za-z0-9_]+$'
       OR p_checkout_url IS NULL
       OR p_checkout_url NOT LIKE 'https://%' THEN
        RAISE EXCEPTION 'Stripe Checkout completion parameters are invalid.'
            USING ERRCODE = '22023';
    END IF;

    UPDATE public.stripe_checkout_reservations AS reservation
    SET stripe_session_id = p_stripe_session_id,
        checkout_url = p_checkout_url,
        lease_token = NULL,
        lease_expires_at = clock_timestamp(),
        updated_at = clock_timestamp()
    WHERE reservation.tenant_id = p_tenant_id
      AND reservation.stripe_customer_id = p_stripe_customer_id
      AND reservation.lease_token = p_lease_token
      AND reservation.lease_expires_at > clock_timestamp()
      AND reservation.session_expires_at > clock_timestamp()
      AND reservation.checkout_url IS NULL;

    RETURN FOUND;
END;
$function$;

REVOKE ALL ON FUNCTION public.begin_tenant_stripe_checkout(
    uuid, text, text, text, text, text, text
) FROM PUBLIC, anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION public.begin_tenant_stripe_checkout(
    uuid, text, text, text, text, text, text
) TO service_role;

REVOKE ALL ON FUNCTION public.complete_tenant_stripe_checkout(
    uuid, text, uuid, text, text
) FROM PUBLIC, anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION public.complete_tenant_stripe_checkout(
    uuid, text, uuid, text, text
) TO service_role;

COMMENT ON TABLE public.stripe_checkout_reservations IS
    'Private per-tenant Checkout reservation. Persists the Stripe idempotency parameters and hosted URL to serialize retries and avoid duplicate open sessions.';

COMMENT ON FUNCTION public.begin_tenant_stripe_checkout(
    uuid, text, text, text, text, text, text
) IS
    'Atomically verifies trial eligibility and Customer mapping, then starts or reuses one unexpired Checkout reservation for a tenant.';

COMMENT ON FUNCTION public.complete_tenant_stripe_checkout(
    uuid, text, uuid, text, text
) IS
    'Stores a hosted Checkout Session only when the caller still owns the unexpired reservation lease.';
