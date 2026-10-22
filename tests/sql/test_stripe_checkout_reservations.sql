BEGIN;

SET LOCAL request.jwt.claim.role = 'service_role';

DO $test$
DECLARE
    test_tenant_id uuid;
    unpaid_tenant_id uuid;
    revoked_tenant_id uuid;
    first_result record;
    busy_result record;
    retry_result record;
    reuse_result record;
    price_result record;
    linked_result record;
    webhook_result record;
    duplicate_event_result record;
    unpaid_lease record;
    revoked_lease record;
    begin_checkout_function oid;
    complete_checkout_function oid;
    function_is_hardened boolean;
    test_user_id uuid := gen_random_uuid();
    webhook_created_at timestamptz := clock_timestamp();
    apply_outcome text;
    resolved_tenant_id uuid;
    license_row_count integer;
    billing_row_count integer;
BEGIN
    INSERT INTO public.tenants DEFAULT VALUES
    RETURNING tenant_id INTO test_tenant_id;

    INSERT INTO public.tenant_licenses (
        tenant_id,
        license_status,
        trial_started_at,
        trial_ends_at
    )
    VALUES (
        test_tenant_id,
        'trialing',
        clock_timestamp(),
        clock_timestamp() + interval '7 days'
    );

    INSERT INTO auth.users (id, email_confirmed_at)
    VALUES (test_user_id, clock_timestamp());
    INSERT INTO public.tenant_accounts (user_id, tenant_id)
    VALUES (test_user_id, test_tenant_id);

    INSERT INTO public.tenant_stripe_billing (
        tenant_id,
        stripe_customer_id
    )
    VALUES (test_tenant_id, 'cus_checkoutreservationtest');

    SELECT public.resolve_tenant_for_billing(test_user_id)
      INTO resolved_tenant_id;
    IF resolved_tenant_id IS DISTINCT FROM test_tenant_id THEN
        RAISE EXCEPTION 'Scalar tenant-resolution RPC returned an unexpected value.';
    END IF;

    SELECT count(*)
      INTO license_row_count
    FROM public.resolve_tenant_license(test_tenant_id);
    SELECT count(*)
      INTO billing_row_count
    FROM public.get_tenant_stripe_checkout_state(test_tenant_id);
    IF license_row_count <> 1 OR billing_row_count <> 1 THEN
        RAISE EXCEPTION 'Set-returning tenant license or billing RPC shape was unexpected.';
    END IF;

    PERFORM public.register_tenant_stripe_customer(
        test_tenant_id,
        'cus_checkoutreservationtest'
    );

    SELECT *
      INTO first_result
    FROM public.begin_tenant_stripe_checkout(
        test_tenant_id,
        'cus_checkoutreservationtest',
        'price_checkouttest',
        'https://app.example.test/checkout/success?session_id={CHECKOUT_SESSION_ID}',
        'https://app.example.test/checkout/cancel',
        'cy08-checkout-candidate-first',
        'cy08-checkout-ABCDEFGH'
    );

    IF first_result.outcome <> 'started'
       OR first_result.lease_token IS NULL
       OR first_result.idempotency_key <> 'cy08-checkout-candidate-first'
       OR first_result.integration_identifier <> 'cy08-checkout-ABCDEFGH'
       OR first_result.session_expires_at <= clock_timestamp() THEN
        RAISE EXCEPTION 'Expected a new started reservation with stable parameters.';
    END IF;

    SELECT *
      INTO busy_result
    FROM public.begin_tenant_stripe_checkout(
        test_tenant_id,
        'cus_checkoutreservationtest',
        'price_checkouttest',
        'https://app.example.test/checkout/success?session_id={CHECKOUT_SESSION_ID}',
        'https://app.example.test/checkout/cancel',
        'cy08-checkout-candidate-second',
        'cy08-checkout-IJKLMNOP'
    );

    IF busy_result.outcome <> 'busy' THEN
        RAISE EXCEPTION 'A concurrent reservation must be reported as busy.';
    END IF;

    UPDATE public.stripe_checkout_reservations
    SET lease_expires_at = clock_timestamp() - interval '1 second'
    WHERE tenant_id = test_tenant_id;

    SELECT *
      INTO retry_result
    FROM public.begin_tenant_stripe_checkout(
        test_tenant_id,
        'cus_checkoutreservationtest',
        'price_checkouttest',
        'https://app.example.test/checkout/success?session_id={CHECKOUT_SESSION_ID}',
        'https://app.example.test/checkout/cancel',
        'cy08-checkout-candidate-third',
        'cy08-checkout-QRSTUVWX'
    );

    IF retry_result.outcome <> 'started'
       OR retry_result.lease_token IS NULL
       OR retry_result.lease_token = first_result.lease_token
       OR retry_result.idempotency_key <> first_result.idempotency_key
       OR retry_result.integration_identifier <> first_result.integration_identifier
       OR retry_result.success_url <> first_result.success_url
       OR retry_result.cancel_url <> first_result.cancel_url
       OR retry_result.session_expires_at <> first_result.session_expires_at THEN
        RAISE EXCEPTION 'Retry must use the same Stripe parameters and a new fence.';
    END IF;

    IF public.complete_tenant_stripe_checkout(
        test_tenant_id,
        'cus_checkoutreservationtest',
        first_result.lease_token,
        'cs_test_stale',
        'https://checkout.stripe.com/c/pay/stale'
    ) THEN
        RAISE EXCEPTION 'A stale reservation owner must not finalize a session.';
    END IF;

    IF NOT public.complete_tenant_stripe_checkout(
        test_tenant_id,
        'cus_checkoutreservationtest',
        retry_result.lease_token,
        'cs_test_current',
        'https://checkout.stripe.com/c/pay/current'
    ) THEN
        RAISE EXCEPTION 'Current reservation owner should finalize the session.';
    END IF;

    SELECT *
      INTO reuse_result
    FROM public.begin_tenant_stripe_checkout(
        test_tenant_id,
        'cus_checkoutreservationtest',
        'price_checkouttest',
        'https://app.example.test/checkout/success?session_id={CHECKOUT_SESSION_ID}',
        'https://app.example.test/checkout/cancel',
        'cy08-checkout-candidate-fourth',
        'cy08-checkout-YZABCDEF'
    );

    IF reuse_result.outcome <> 'reuse'
       OR reuse_result.checkout_url <> 'https://checkout.stripe.com/c/pay/current'
       OR reuse_result.idempotency_key <> retry_result.idempotency_key THEN
        RAISE EXCEPTION 'An unexpired session must be reused.';
    END IF;

    SELECT *
      INTO price_result
    FROM public.begin_tenant_stripe_checkout(
        test_tenant_id,
        'cus_checkoutreservationtest',
        'price_othertest',
        'https://app.example.test/checkout/success?session_id={CHECKOUT_SESSION_ID}',
        'https://app.example.test/checkout/cancel',
        'cy08-checkout-candidate-fifth',
        'cy08-checkout-GHIJKLMN'
    );

    IF price_result.outcome <> 'price_mismatch' THEN
        RAISE EXCEPTION 'An active reservation must not be reused for another Price.';
    END IF;

    SELECT *
      INTO webhook_result
    FROM public.begin_stripe_webhook_event(
        'evt_checkouttest',
        'customer.subscription.created',
        webhook_created_at,
        'cus_checkoutreservationtest',
        'sub_checkouttest',
        NULL
    );

    IF webhook_result.outcome <> 'started'
       OR webhook_result.lease_token IS NULL
       OR webhook_result.lease_generation IS NULL THEN
        RAISE EXCEPTION 'Set-returning webhook lease RPC returned an unexpected row.';
    END IF;

    SELECT public.apply_stripe_webhook_snapshot(
        'evt_checkouttest',
        'cus_checkoutreservationtest',
        'cus_checkoutreservationtest',
        'sub_checkouttest',
        'price_checkouttest',
        'active',
        true,
        'in_checkoutinvoice',
        true,
        clock_timestamp() - interval '1 day',
        clock_timestamp() + interval '1 month',
        false,
        webhook_result.lease_token,
        webhook_result.lease_generation
    )
    INTO apply_outcome;

    IF apply_outcome <> 'processed' THEN
        RAISE EXCEPTION 'Scalar webhook application RPC returned an unexpected status.';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM public.tenant_licenses
        WHERE tenant_id = test_tenant_id
          AND license_status = 'active'
          AND subscription_id = 'sub_checkouttest'
    ) THEN
        RAISE EXCEPTION 'Paid active snapshot was not projected to the license.';
    END IF;

    SELECT *
      INTO duplicate_event_result
    FROM public.begin_stripe_webhook_event(
        'evt_checkouttest',
        'customer.subscription.created',
        webhook_created_at,
        'cus_checkoutreservationtest',
        'sub_checkouttest',
        NULL
    );

    IF duplicate_event_result.outcome <> 'processed' THEN
        RAISE EXCEPTION 'Duplicate processed event was not acknowledged idempotently.';
    END IF;

    SELECT *
      INTO linked_result
    FROM public.begin_tenant_stripe_checkout(
        test_tenant_id,
        'cus_checkoutreservationtest',
        'price_checkouttest',
        'https://app.example.test/checkout/success?session_id={CHECKOUT_SESSION_ID}',
        'https://app.example.test/checkout/cancel',
        'cy08-checkout-candidate-sixth',
        'cy08-checkout-OPQRSTUV'
    );

    IF linked_result.outcome <> 'subscription_linked' THEN
        RAISE EXCEPTION 'A linked subscription must block another Checkout.';
    END IF;

    INSERT INTO public.tenants DEFAULT VALUES
    RETURNING tenant_id INTO unpaid_tenant_id;
    INSERT INTO public.tenant_licenses (
        tenant_id,
        license_status,
        trial_started_at,
        trial_ends_at
    )
    VALUES (
        unpaid_tenant_id,
        'trialing',
        clock_timestamp(),
        clock_timestamp() + interval '7 days'
    );
    INSERT INTO public.tenant_stripe_billing (tenant_id, stripe_customer_id)
    VALUES (unpaid_tenant_id, 'cus_unpaidchecktest');

    SELECT *
      INTO unpaid_lease
    FROM public.begin_stripe_webhook_event(
        'evt_unpaidchecktest',
        'customer.subscription.created',
        clock_timestamp(),
        'cus_unpaidchecktest',
        'sub_unpaidchecktest',
        NULL
    );

    SELECT public.apply_stripe_webhook_snapshot(
        'evt_unpaidchecktest',
        'cus_unpaidchecktest',
        'cus_unpaidchecktest',
        'sub_unpaidchecktest',
        'price_unpaidchecktest',
        'active',
        true,
        'in_unpaidchecktest',
        false,
        clock_timestamp() - interval '1 day',
        clock_timestamp() + interval '1 month',
        false,
        unpaid_lease.lease_token,
        unpaid_lease.lease_generation
    )
    INTO apply_outcome;

    IF apply_outcome <> 'pending_policy'
       OR NOT EXISTS (
           SELECT 1
           FROM public.tenant_licenses
           WHERE tenant_id = unpaid_tenant_id
             AND license_status = 'trialing'
       ) THEN
        RAISE EXCEPTION 'Unpaid active snapshot must not grant access.';
    END IF;

    INSERT INTO public.tenants DEFAULT VALUES
    RETURNING tenant_id INTO revoked_tenant_id;
    INSERT INTO public.tenant_licenses (tenant_id, license_status)
    VALUES (revoked_tenant_id, 'revoked');
    INSERT INTO public.tenant_stripe_billing (tenant_id, stripe_customer_id)
    VALUES (revoked_tenant_id, 'cus_revokedchecktest');

    SELECT *
      INTO revoked_lease
    FROM public.begin_stripe_webhook_event(
        'evt_revokedchecktest',
        'customer.subscription.created',
        clock_timestamp(),
        'cus_revokedchecktest',
        'sub_revokedchecktest',
        NULL
    );

    SELECT public.apply_stripe_webhook_snapshot(
        'evt_revokedchecktest',
        'cus_revokedchecktest',
        'cus_revokedchecktest',
        'sub_revokedchecktest',
        'price_revokedchecktest',
        'active',
        true,
        'in_revokedchecktest',
        true,
        clock_timestamp() - interval '1 day',
        clock_timestamp() + interval '1 month',
        false,
        revoked_lease.lease_token,
        revoked_lease.lease_generation
    )
    INTO apply_outcome;

    IF apply_outcome <> 'pending_policy'
       OR NOT EXISTS (
           SELECT 1
           FROM public.tenant_licenses
           WHERE tenant_id = revoked_tenant_id
             AND license_status = 'revoked'
       ) THEN
        RAISE EXCEPTION 'Revoked license must remain restricted.';
    END IF;

    IF NOT (
        SELECT relrowsecurity
        FROM pg_class
        WHERE oid = 'public.stripe_checkout_reservations'::regclass
    ) THEN
        RAISE EXCEPTION 'Checkout reservation table must have RLS enabled.';
    END IF;

    IF has_table_privilege('service_role', 'public.stripe_checkout_reservations', 'SELECT')
       OR has_table_privilege('authenticated', 'public.stripe_checkout_reservations', 'SELECT')
       OR has_table_privilege('anon', 'public.stripe_checkout_reservations', 'SELECT') THEN
        RAISE EXCEPTION 'Checkout reservation table must not grant direct SELECT.';
    END IF;

    begin_checkout_function := to_regprocedure(
        'public.begin_tenant_stripe_checkout(uuid,text,text,text,text,text,text)'
    );
    complete_checkout_function := to_regprocedure(
        'public.complete_tenant_stripe_checkout(uuid,text,uuid,text,text)'
    );

    IF begin_checkout_function IS NULL OR complete_checkout_function IS NULL THEN
        RAISE EXCEPTION 'Expected Checkout RPC signatures were not installed.';
    END IF;

    IF NOT has_function_privilege('service_role', begin_checkout_function, 'EXECUTE')
       OR has_function_privilege('anon', begin_checkout_function, 'EXECUTE')
       OR has_function_privilege('authenticated', begin_checkout_function, 'EXECUTE')
       OR NOT has_function_privilege('service_role', complete_checkout_function, 'EXECUTE')
       OR has_function_privilege('anon', complete_checkout_function, 'EXECUTE')
       OR has_function_privilege('authenticated', complete_checkout_function, 'EXECUTE') THEN
        RAISE EXCEPTION 'Checkout RPC grants do not match the service-role-only contract.';
    END IF;

    SELECT bool_and(
        procedure.prosecdef
        AND procedure.proconfig @> ARRAY['search_path=""']
    )
      INTO function_is_hardened
    FROM pg_proc AS procedure
    WHERE procedure.oid IN (begin_checkout_function, complete_checkout_function);

    IF function_is_hardened IS DISTINCT FROM true THEN
        RAISE EXCEPTION 'Checkout RPCs must be SECURITY DEFINER with an empty search_path.';
    END IF;
END;
$test$;

ROLLBACK;
