CREATE TABLE public.xero_connections (
    tenant_id uuid PRIMARY KEY
        REFERENCES public.tenants (tenant_id)
        ON DELETE CASCADE,
    xero_tenant_id text NOT NULL
        CHECK (length(btrim(xero_tenant_id, U&'\0009\000A\000B\000C\000D\001C\001D\001E\001F\0020\0085\00A0\1680\2000\2001\2002\2003\2004\2005\2006\2007\2008\2009\200A\2028\2029\202F\205F\3000')) > 0),
    xero_tenant_name text,
    xero_tenant_type text,
    created_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE public.xero_connections ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.xero_connections FROM PUBLIC, anon, authenticated;

CREATE OR REPLACE FUNCTION public.get_xero_connection(
    p_tenant_id uuid,
    p_license_key text
)
RETURNS TABLE (
    tenant_id uuid,
    xero_tenant_id text,
    xero_tenant_name text,
    xero_tenant_type text
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $function$
DECLARE
    matching_tenants uuid[];
BEGIN
    SELECT array_agg(lic.tenant_id)
      INTO matching_tenants
    FROM public.licencas AS lic
    WHERE lic.chave_licenca = p_license_key
      AND lic.ativo IS TRUE
      AND (
          lic.data_expiracao IS NULL
          OR lic.data_expiracao >= CURRENT_DATE
      );

    IF COALESCE(cardinality(matching_tenants), 0) > 1 THEN
        RAISE EXCEPTION 'Ambiguous active license key.'
            USING ERRCODE = 'P0001';
    END IF;

    IF COALESCE(cardinality(matching_tenants), 0) <> 1
       OR matching_tenants[1] IS DISTINCT FROM p_tenant_id THEN
        RETURN;
    END IF;

    RETURN QUERY
    SELECT connection.tenant_id,
           connection.xero_tenant_id,
           connection.xero_tenant_name,
           connection.xero_tenant_type
    FROM public.xero_connections AS connection
    WHERE connection.tenant_id = p_tenant_id;
END;
$function$;

REVOKE ALL ON FUNCTION public.get_xero_connection(uuid, text)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.get_xero_connection(uuid, text)
    TO anon;
