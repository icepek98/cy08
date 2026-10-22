CREATE TABLE public.tenant_installations (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL
        REFERENCES public.tenants (tenant_id)
        ON DELETE CASCADE,
    installation_id uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    last_seen_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    revoked_at timestamptz,
    CONSTRAINT tenant_installations_tenant_installation_unique
        UNIQUE (tenant_id, installation_id)
);

ALTER TABLE public.tenant_installations ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.tenant_installations
    FROM PUBLIC, anon, authenticated, service_role;

COMMENT ON TABLE public.tenant_installations IS
    'Server-managed installation registry. A trusted backend must authenticate the customer and derive tenant_id; installation_id is not authorization.';
