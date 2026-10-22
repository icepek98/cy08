CREATE UNIQUE INDEX xero_connections_xero_tenant_id_global_unique
    ON public.xero_connections (lower(btrim(xero_tenant_id)));
