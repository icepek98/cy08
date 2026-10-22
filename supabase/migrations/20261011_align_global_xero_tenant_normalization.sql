CREATE UNIQUE INDEX xero_connections_xero_tenant_id_canonical_unique
    ON public.xero_connections (
        lower(btrim(
            xero_tenant_id,
            U&'\0009\000A\000B\000C\000D\001C\001D\001E\001F\0020\0085\00A0\1680\2000\2001\2002\2003\2004\2005\2006\2007\2008\2009\200A\2028\2029\202F\205F\3000'
        ))
    );

DROP INDEX public.xero_connections_xero_tenant_id_global_unique;
