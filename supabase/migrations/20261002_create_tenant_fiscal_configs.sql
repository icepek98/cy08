CREATE TABLE public.tenant_fiscal_configs (
    tenant_id uuid PRIMARY KEY
        REFERENCES public.tenants (tenant_id)
        ON DELETE CASCADE,
    account_code text NOT NULL
        CHECK (length(btrim(account_code, U&'\0009\000A\000B\000C\000D\001C\001D\001E\001F\0020\0085\00A0\1680\2000\2001\2002\2003\2004\2005\2006\2007\2008\2009\200A\2028\2029\202F\205F\3000')) > 0),
    tax_type text NOT NULL
        CHECK (length(btrim(tax_type, U&'\0009\000A\000B\000C\000D\001C\001D\001E\001F\0020\0085\00A0\1680\2000\2001\2002\2003\2004\2005\2006\2007\2008\2009\200A\2028\2029\202F\205F\3000')) > 0),
    tax_inclusive boolean NOT NULL,
    amount_basis text NOT NULL
        CHECK (amount_basis IN ('subtotal', 'total')),
    discount_policy text NOT NULL
        CHECK (discount_policy IN ('ignore', 'line_discount'))
);

ALTER TABLE public.tenant_fiscal_configs ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.tenant_fiscal_configs FROM PUBLIC, anon, authenticated;

CREATE OR REPLACE FUNCTION public.get_tenant_fiscal_config(
    p_tenant_id uuid,
    p_license_key text
)
RETURNS TABLE (
    tenant_id uuid,
    account_code text,
    tax_type text,
    tax_inclusive boolean,
    amount_basis text,
    discount_policy text
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $function$
DECLARE
    matching_tenant_count bigint;
    authorized_tenant_id uuid;
BEGIN
    SELECT count(DISTINCT lic.tenant_id)
      INTO matching_tenant_count
    FROM public.licencas AS lic
    WHERE lic.chave_licenca = p_license_key
      AND lic.ativo IS TRUE
      AND (
          lic.data_expiracao IS NULL
          OR lic.data_expiracao >= CURRENT_DATE
      )
      AND lic.tenant_id IS NOT NULL;

    IF matching_tenant_count > 1 THEN
        RAISE EXCEPTION 'Ambiguous active license key.';
    END IF;

    IF matching_tenant_count = 0 THEN
        RETURN;
    END IF;

    SELECT DISTINCT lic.tenant_id
      INTO authorized_tenant_id
    FROM public.licencas AS lic
    WHERE lic.chave_licenca = p_license_key
      AND lic.ativo IS TRUE
      AND (
          lic.data_expiracao IS NULL
          OR lic.data_expiracao >= CURRENT_DATE
      )
      AND lic.tenant_id IS NOT NULL;

    IF authorized_tenant_id IS DISTINCT FROM p_tenant_id THEN
        RAISE EXCEPTION 'License is not authorized for requested tenant.';
    END IF;

    RETURN QUERY
    SELECT config.tenant_id,
           config.account_code,
           config.tax_type,
           config.tax_inclusive,
           config.amount_basis,
           config.discount_policy
    FROM public.tenant_fiscal_configs AS config
    WHERE config.tenant_id = authorized_tenant_id;
END;
$function$;

REVOKE ALL ON FUNCTION public.get_tenant_fiscal_config(uuid, text)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.get_tenant_fiscal_config(uuid, text)
    TO anon;
