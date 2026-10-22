BEGIN;

CREATE TEMP TABLE cy08_xero_connections_test (
    tenant_id text PRIMARY KEY,
    xero_tenant_id text NOT NULL
        CHECK (
            length(
                btrim(
                    xero_tenant_id,
                    U&'\0009\000A\000B\000C\000D\001C\001D\001E\001F\0020\0085\00A0\1680\2000\2001\2002\2003\2004\2005\2006\2007\2008\2009\200A\2028\2029\202F\205F\3000'
                )
            ) > 0
        )
);

CREATE UNIQUE INDEX cy08_xero_connections_test_canonical_unique
    ON cy08_xero_connections_test (
        lower(
            btrim(
                xero_tenant_id,
                U&'\0009\000A\000B\000C\000D\001C\001D\001E\001F\0020\0085\00A0\1680\2000\2001\2002\2003\2004\2005\2006\2007\2008\2009\200A\2028\2029\202F\205F\3000'
            )
        )
    );

INSERT INTO cy08_xero_connections_test (tenant_id, xero_tenant_id)
VALUES ('tenant-a', '  Org-A  '), ('tenant-b', 'Org-B');

DO $test$
BEGIN
    BEGIN
        INSERT INTO cy08_xero_connections_test
        VALUES ('tenant-a-case-duplicate', 'org-a');
        RAISE EXCEPTION 'Case-insensitive duplicate was accepted.';
    EXCEPTION
        WHEN unique_violation THEN NULL;
    END;

    BEGIN
        INSERT INTO cy08_xero_connections_test
        VALUES ('tenant-a-unicode-space-duplicate', U&'\2003Org-A\2003');
        RAISE EXCEPTION 'Unicode-whitespace duplicate was accepted.';
    EXCEPTION
        WHEN unique_violation THEN NULL;
    END;

    BEGIN
        INSERT INTO cy08_xero_connections_test
        VALUES ('tenant-a-nbsp-duplicate', U&'\00A0Org-A\00A0');
        RAISE EXCEPTION 'Non-breaking-space duplicate was accepted.';
    EXCEPTION
        WHEN unique_violation THEN NULL;
    END;

    BEGIN
        INSERT INTO cy08_xero_connections_test
        VALUES ('tenant-empty', '');
        RAISE EXCEPTION 'Empty organization identifier was accepted.';
    EXCEPTION
        WHEN check_violation THEN NULL;
    END;

    BEGIN
        INSERT INTO cy08_xero_connections_test
        VALUES ('tenant-whitespace-only', U&'\2003\0009\00A0');
        RAISE EXCEPTION 'Whitespace-only organization identifier was accepted.';
    EXCEPTION
        WHEN check_violation THEN NULL;
    END;
END;
$test$;

DO $test$
DECLARE
    cp integer;
    codepoints integer[] := ARRAY[
        9, 10, 11, 12, 13, 28, 29, 30, 31, 32, 133, 160, 5760,
        8192, 8193, 8194, 8195, 8196, 8197, 8198, 8199, 8200, 8201,
        8202, 8232, 8233, 8239, 8287, 12288
    ];
    whitespace_character text;
BEGIN
    FOREACH cp IN ARRAY codepoints LOOP
        whitespace_character := chr(cp);

        BEGIN
            INSERT INTO cy08_xero_connections_test (tenant_id, xero_tenant_id)
            VALUES (
                'duplicate-whitespace-' || cp,
                whitespace_character || 'Org-A' || whitespace_character
            );
            RAISE EXCEPTION
                'Canonical uniqueness accepted duplicate with whitespace U+%.',
                upper(to_hex(cp));
        EXCEPTION
            WHEN unique_violation THEN NULL;
        END;

        BEGIN
            INSERT INTO cy08_xero_connections_test (tenant_id, xero_tenant_id)
            VALUES ('whitespace-only-' || cp, whitespace_character);
            RAISE EXCEPTION
                'Original check constraint accepted whitespace-only U+%.',
                upper(to_hex(cp));
        EXCEPTION
            WHEN check_violation THEN NULL;
        END;
    END LOOP;

    IF (SELECT count(*) FROM cy08_xero_connections_test) <> 2
       OR NOT EXISTS (
            SELECT 1
            FROM cy08_xero_connections_test
            WHERE tenant_id = 'tenant-a'
              AND xero_tenant_id = '  Org-A  '
       )
       OR NOT EXISTS (
            SELECT 1
            FROM cy08_xero_connections_test
            WHERE tenant_id = 'tenant-b'
              AND xero_tenant_id = 'Org-B'
       ) THEN
        RAISE EXCEPTION 'Whitespace tests changed existing test rows.';
    END IF;
END;
$test$;

CREATE TEMP TABLE cy08_xero_preexisting_duplicates_test (
    tenant_id text PRIMARY KEY,
    xero_tenant_id text NOT NULL
);

INSERT INTO cy08_xero_preexisting_duplicates_test
VALUES ('tenant-a', 'Org-C'), ('tenant-b', U&'\2003org-c\2003');

CREATE UNIQUE INDEX cy08_xero_preexisting_duplicates_legacy_unique
    ON cy08_xero_preexisting_duplicates_test (
        lower(btrim(xero_tenant_id))
    );

DO $test$
BEGIN
    BEGIN
        EXECUTE $sql$
            CREATE UNIQUE INDEX
                cy08_xero_preexisting_duplicates_canonical_unique
            ON cy08_xero_preexisting_duplicates_test (
                lower(
                    btrim(
                        xero_tenant_id,
                        U&'\0009\000A\000B\000C\000D\001C\001D\001E\001F\0020\0085\00A0\1680\2000\2001\2002\2003\2004\2005\2006\2007\2008\2009\200A\2028\2029\202F\205F\3000'
                    )
                )
            )
        $sql$;
        RAISE EXCEPTION 'Canonical index accepted preexisting duplicates.';
    EXCEPTION
        WHEN unique_violation THEN NULL;
    END;

    IF to_regclass(
        'pg_temp.cy08_xero_preexisting_duplicates_canonical_unique'
    ) IS NOT NULL THEN
        RAISE EXCEPTION 'Failed canonical index creation left an index behind.';
    END IF;

    IF to_regclass(
        'pg_temp.cy08_xero_preexisting_duplicates_legacy_unique'
    ) IS NULL THEN
        RAISE EXCEPTION 'Failed canonical index creation removed the old index.';
    END IF;

    IF (SELECT count(*) FROM cy08_xero_preexisting_duplicates_test) <> 2
       OR NOT EXISTS (
            SELECT 1
            FROM cy08_xero_preexisting_duplicates_test
            WHERE tenant_id = 'tenant-a'
              AND xero_tenant_id = 'Org-C'
       )
       OR NOT EXISTS (
            SELECT 1
            FROM cy08_xero_preexisting_duplicates_test
            WHERE tenant_id = 'tenant-b'
              AND xero_tenant_id = U&'\2003org-c\2003'
       ) THEN
        RAISE EXCEPTION 'Failed index creation modified preexisting rows.';
    END IF;
END;
$test$;

ROLLBACK;
