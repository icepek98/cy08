CREATE TABLE public.tenants (
    tenant_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE public.licencas
    ADD COLUMN tenant_id uuid REFERENCES public.tenants (tenant_id);

ALTER TABLE public.tenants ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.tenants FROM anon, authenticated;

CREATE OR REPLACE FUNCTION public.resolve_tenant_for_license(p_license_key text)
RETURNS TABLE (tenant_id uuid)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $function$
DECLARE
    matched_tenants uuid[];
BEGIN
    SELECT array_agg(lic.tenant_id)
    INTO matched_tenants
    FROM public.licencas AS lic
    WHERE lic.chave_licenca = p_license_key
      AND lic.ativo IS TRUE
      AND lic.tenant_id IS NOT NULL
      AND (
          lic.data_expiracao IS NULL
          OR lic.data_expiracao >= CURRENT_DATE
      );

    IF COALESCE(cardinality(matched_tenants), 0) <> 1 THEN
        RETURN;
    END IF;

    RETURN QUERY SELECT matched_tenants[1];
END;
$function$;

REVOKE ALL ON FUNCTION public.resolve_tenant_for_license(text)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.resolve_tenant_for_license(text)
    TO anon;
