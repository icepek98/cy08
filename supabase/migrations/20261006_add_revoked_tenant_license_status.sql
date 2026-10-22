ALTER TABLE public.tenant_licenses
    DROP CONSTRAINT IF EXISTS tenant_licenses_license_status_check;

ALTER TABLE public.tenant_licenses
    ADD CONSTRAINT tenant_licenses_license_status_check
    CHECK (
        license_status IN (
            'trialing',
            'active',
            'past_due',
            'canceled',
            'expired',
            'blocked',
            'revoked'
        )
    );
