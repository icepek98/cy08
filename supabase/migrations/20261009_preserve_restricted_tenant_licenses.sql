CREATE OR REPLACE FUNCTION public.prevent_tenant_license_restriction_clearing()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = ''
AS $function$
BEGIN
    IF OLD.license_status IN ('blocked', 'revoked')
       AND NEW.license_status IS DISTINCT FROM OLD.license_status THEN
        RAISE EXCEPTION 'Restricted tenant license requires an authorized transition.'
            USING ERRCODE = '42501';
    END IF;

    RETURN NEW;
END;
$function$;

REVOKE ALL ON FUNCTION public.prevent_tenant_license_restriction_clearing()
    FROM PUBLIC, anon, authenticated, service_role;

COMMENT ON FUNCTION public.prevent_tenant_license_restriction_clearing() IS
    'Fail-closed guard; no transition authority for blocked or revoked licenses is approved or implemented.';

CREATE TRIGGER tenant_licenses_preserve_restrictions
    BEFORE UPDATE OF license_status ON public.tenant_licenses
    FOR EACH ROW
    EXECUTE FUNCTION public.prevent_tenant_license_restriction_clearing();
