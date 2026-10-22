# FieldQuo authenticated onboarding

## Implemented boundary

The desktop uses Supabase Auth with the public `SUPABASE_ANON_KEY` to sign up
or sign in. The `supabase_auth.py` client keeps a returned access token only
in memory; it does not persist passwords, access tokens, or refresh tokens.
Account creation requires email confirmation before onboarding can proceed.

After sign-in, `OnboardingClient` sends only the persisted `installation_id`
and the authenticated user's bearer access token to the
`fieldquo-onboarding` Edge Function. It does not accept a tenant ID, license
status, or trial timestamps from the caller.

The Edge Function validates the access token with Supabase Auth, requires a
confirmed email, and calls a service-role-only database function. The
service-role key is read only from the Edge Function environment. The database
function serializes requests for each authenticated user, creates or recovers
that user's tenant, globally associates an installation UUID with at most one
tenant, registers the installation, and inserts a trial only when the tenant
has no entitlement. The existing tenant-license primary key and
`ON CONFLICT DO NOTHING` preserve existing trials, paid states, and revocations.
Database time determines the trial interval (720 elapsed hours).

One Supabase Auth user maps to one tenant in this initial account model.
Different Auth users are not implicitly merged into a company tenant; a
future invitation or verified business-identity policy is needed for shared
company accounts. Verified email and the unique user-to-tenant association do
not prevent someone from creating multiple verified identities. Do not treat
the installation UUID as a hardware identity or an abuse-proof identifier.

## Deployment prerequisites

This repository does not include Supabase CLI/Deno configuration or a local
Supabase instance. The migration and Edge Function have not been applied or
deployed. Before connecting the GUI or enabling trial-based application
access:

1. Review and apply the new migration through the approved Supabase deployment
   process, after checking whether `tenant_installations` already contains
   duplicate installation UUIDs. The new global uniqueness constraint will
   intentionally reject such data.
2. Deploy `supabase/functions/fieldquo-onboarding` with JWT verification
   enabled. Keep the platform's `SUPABASE_SERVICE_ROLE_KEY` only in the Edge
   Function's managed secret environment; never add it to the desktop or
   repository.
3. Configure Supabase Auth email confirmation and its delivery provider.
   Consider enabling Supabase's CAPTCHA protection and rate limits for public
   signup; this code rejects unconfirmed users but does not impose an
   organization-wide identity or per-IP signup limit.
4. Configure the desktop with the Supabase project URL and public anon key.
   These are not service-role credentials. Do not persist user passwords or
   bearer tokens in the installation identity database.
5. GUI signup/sign-in and the server-derived licensed automation gate are
   connected. Configure and deploy the Supabase project before using them.
   The local PDF preview remains independent of onboarding and licensing.

Offline tests validate contracts and migration/function source invariants.
They do not execute PostgreSQL, Supabase Auth, or the deployed Edge Function.
