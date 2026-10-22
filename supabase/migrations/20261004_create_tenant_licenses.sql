CREATE TABLE public.tenant_licenses (
    tenant_id uuid PRIMARY KEY
        REFERENCES public.tenants (tenant_id)
        ON DELETE CASCADE,
    license_status text NOT NULL DEFAULT 'trialing'
        CHECK (
            license_status IN (
                'trialing',
                'active',
                'past_due',
                'canceled',
                'expired',
                'blocked'
            )
        ),
    trial_started_at timestamptz,
    trial_ends_at timestamptz,
    subscription_id text,
    subscription_started_at timestamptz,
    subscription_ends_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT tenant_licenses_trial_dates_check
        CHECK (
            license_status <> 'trialing'
            OR (
                trial_started_at IS NOT NULL
                AND trial_ends_at IS NOT NULL
                AND trial_ends_at > trial_started_at
            )
        )
);

ALTER TABLE public.tenant_licenses ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.tenant_licenses
    FROM PUBLIC, anon, authenticated, service_role;

CREATE OR REPLACE FUNCTION public.resolve_tenant_license(p_tenant_id uuid)
RETURNS TABLE (
    tenant_id uuid,
    license_status text,
    trial_started_at timestamptz,
    trial_ends_at timestamptz,
    subscription_id text,
    subscription_started_at timestamptz,
    subscription_ends_at timestamptz,
    checked_at timestamptz
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $function$
DECLARE
    server_now timestamptz := clock_timestamp();
BEGIN
    IF COALESCE(auth.role(), '') <> 'service_role' THEN
        RAISE EXCEPTION 'Privileged license access required.'
            USING ERRCODE = '42501';
    END IF;

    RETURN QUERY
    SELECT license.tenant_id,
           CASE
               WHEN license.license_status = 'trialing'
                    AND server_now >= license.trial_ends_at
                   THEN 'expired'
               WHEN license.license_status = 'active'
                    AND license.subscription_ends_at IS NOT NULL
                    AND server_now >= license.subscription_ends_at
                   THEN 'expired'
               ELSE license.license_status
           END,
           license.trial_started_at,
           license.trial_ends_at,
           license.subscription_id,
           license.subscription_started_at,
           license.subscription_ends_at,
           server_now
    FROM public.tenant_licenses AS license
    WHERE license.tenant_id = p_tenant_id;
END;
$function$;

REVOKE ALL ON FUNCTION public.resolve_tenant_license(uuid)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.resolve_tenant_license(uuid)
    TO service_role;

CREATE OR REPLACE FUNCTION public.activate_tenant_trial(p_tenant_id uuid)
RETURNS TABLE (
    tenant_id uuid,
    license_status text,
    trial_started_at timestamptz,
    trial_ends_at timestamptz,
    subscription_id text,
    subscription_started_at timestamptz,
    subscription_ends_at timestamptz,
    checked_at timestamptz
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $function$
DECLARE
    server_now timestamptz := clock_timestamp();
BEGIN
    IF COALESCE(auth.role(), '') <> 'service_role' THEN
        RAISE EXCEPTION 'Privileged license activation required.'
            USING ERRCODE = '42501';
    END IF;

    INSERT INTO public.tenant_licenses (
        tenant_id,
        license_status,
        trial_started_at,
        trial_ends_at
    )
    VALUES (
        p_tenant_id,
        'trialing',
        server_now,
        server_now + interval '720 hours'
    )
    ON CONFLICT (tenant_id) DO NOTHING;

    RETURN QUERY
    SELECT resolved.tenant_id,
           resolved.license_status,
           resolved.trial_started_at,
           resolved.trial_ends_at,
           resolved.subscription_id,
           resolved.subscription_started_at,
           resolved.subscription_ends_at,
           resolved.checked_at
    FROM public.resolve_tenant_license(p_tenant_id) AS resolved;
END;
$function$;

REVOKE ALL ON FUNCTION public.activate_tenant_trial(uuid)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.activate_tenant_trial(uuid)
    TO service_role;
