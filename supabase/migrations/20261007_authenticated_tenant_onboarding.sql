CREATE TABLE public.tenant_accounts (
    user_id uuid PRIMARY KEY
        REFERENCES auth.users (id)
        ON DELETE CASCADE,
    tenant_id uuid NOT NULL UNIQUE
        REFERENCES public.tenants (tenant_id)
        ON DELETE CASCADE,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

ALTER TABLE public.tenant_accounts ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.tenant_accounts
    FROM PUBLIC, anon, authenticated, service_role;

ALTER TABLE public.tenant_installations
    DROP CONSTRAINT IF EXISTS tenant_installations_tenant_installation_unique;

ALTER TABLE public.tenant_installations
    ADD CONSTRAINT tenant_installations_installation_id_unique
    UNIQUE (installation_id);

CREATE OR REPLACE FUNCTION public.register_or_get_tenant_for_installation(
    p_user_id uuid,
    p_installation_id uuid
)
RETURNS TABLE (
    tenant_id uuid,
    installation_id uuid,
    license_status text,
    trial_started_at timestamptz,
    trial_ends_at timestamptz,
    checked_at timestamptz,
    installation_registered boolean,
    access_allowed boolean,
    block_reason text
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $function$
DECLARE
    user_is_verified boolean;
    resolved_tenant_id uuid;
    registered_tenant_id uuid;
    registered_revoked_at timestamptz;
    resolved_status text;
    resolved_trial_started_at timestamptz;
    resolved_trial_ends_at timestamptz;
    resolved_checked_at timestamptz;
    resolved_access_allowed boolean;
    resolved_block_reason text;
    new_trial_started_at timestamptz;
BEGIN
    IF COALESCE(auth.role(), '') <> 'service_role' THEN
        RAISE EXCEPTION 'Privileged onboarding access required.'
            USING ERRCODE = '42501';
    END IF;

    IF p_user_id IS NULL OR p_installation_id IS NULL THEN
        RAISE EXCEPTION 'User and installation identifiers are required.'
            USING ERRCODE = '22023';
    END IF;

    SELECT auth_user.email_confirmed_at IS NOT NULL
      INTO user_is_verified
    FROM auth.users AS auth_user
    WHERE auth_user.id = p_user_id;

    IF COALESCE(user_is_verified, false) IS NOT TRUE THEN
        RAISE EXCEPTION 'A verified authenticated user is required.'
            USING ERRCODE = '42501';
    END IF;

    PERFORM pg_advisory_xact_lock(
        hashtextextended(p_user_id::text, 0)
    );

    SELECT account.tenant_id
      INTO resolved_tenant_id
    FROM public.tenant_accounts AS account
    WHERE account.user_id = p_user_id
    FOR UPDATE;

    IF resolved_tenant_id IS NULL THEN
        INSERT INTO public.tenants DEFAULT VALUES
        RETURNING public.tenants.tenant_id INTO resolved_tenant_id;

        INSERT INTO public.tenant_accounts (user_id, tenant_id)
        VALUES (p_user_id, resolved_tenant_id);
    END IF;

    INSERT INTO public.tenant_installations (tenant_id, installation_id)
    VALUES (resolved_tenant_id, p_installation_id)
    ON CONFLICT (installation_id) DO NOTHING;

    SELECT installation.tenant_id, installation.revoked_at
      INTO registered_tenant_id, registered_revoked_at
    FROM public.tenant_installations AS installation
    WHERE installation.installation_id = p_installation_id
    FOR UPDATE;

    IF registered_tenant_id IS DISTINCT FROM resolved_tenant_id THEN
        RAISE EXCEPTION 'Installation is already associated with another account.'
            USING ERRCODE = '42501';
    END IF;

    IF registered_revoked_at IS NULL THEN
        UPDATE public.tenant_installations
        SET last_seen_at = clock_timestamp()
        WHERE installation_id = p_installation_id;

        new_trial_started_at := clock_timestamp();
        INSERT INTO public.tenant_licenses (
            tenant_id,
            license_status,
            trial_started_at,
            trial_ends_at
        )
        VALUES (
            resolved_tenant_id,
            'trialing',
            new_trial_started_at,
            new_trial_started_at + interval '720 hours'
        )
        ON CONFLICT (tenant_id) DO NOTHING;
    END IF;

    SELECT license.license_status,
           license.trial_started_at,
           license.trial_ends_at,
           license.checked_at
      INTO resolved_status,
           resolved_trial_started_at,
           resolved_trial_ends_at,
           resolved_checked_at
    FROM public.resolve_tenant_license(resolved_tenant_id) AS license;

    IF registered_revoked_at IS NOT NULL THEN
        resolved_status := COALESCE(resolved_status, 'blocked');
        resolved_checked_at := COALESCE(resolved_checked_at, clock_timestamp());
        resolved_access_allowed := false;
        resolved_block_reason := 'installation_revoked';
    ELSIF resolved_status IS NULL THEN
        resolved_checked_at := clock_timestamp();
        resolved_access_allowed := false;
        resolved_block_reason := 'license_missing';
    ELSIF resolved_status = 'revoked' THEN
        resolved_access_allowed := false;
        resolved_block_reason := 'license_revoked';
    ELSIF resolved_status = 'trialing' OR resolved_status = 'active' THEN
        resolved_access_allowed := true;
        resolved_block_reason := NULL;
    ELSIF resolved_status = 'expired' THEN
        resolved_access_allowed := false;
        IF resolved_trial_ends_at IS NOT NULL
           AND resolved_checked_at >= resolved_trial_ends_at THEN
            resolved_block_reason := 'trial_expired';
        ELSE
            resolved_block_reason := 'license_not_active';
        END IF;
    ELSE
        resolved_access_allowed := false;
        resolved_block_reason := 'license_not_active';
    END IF;

    RETURN QUERY
    SELECT resolved_tenant_id,
           p_installation_id,
           COALESCE(resolved_status, 'blocked'),
           resolved_trial_started_at,
           resolved_trial_ends_at,
           resolved_checked_at,
           true,
           resolved_access_allowed,
           resolved_block_reason;
END;
$function$;

REVOKE ALL ON FUNCTION public.register_or_get_tenant_for_installation(uuid, uuid)
    FROM PUBLIC, anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION public.register_or_get_tenant_for_installation(uuid, uuid)
    TO service_role;

COMMENT ON TABLE public.tenant_accounts IS
    'Maps one verified Supabase Auth user to one tenant; the trusted onboarding RPC owns this association.';

COMMENT ON FUNCTION public.register_or_get_tenant_for_installation(uuid, uuid) IS
    'Service-role-only, transactional onboarding. The trusted Edge Function must validate the bearer JWT and supply its authenticated user ID.';
