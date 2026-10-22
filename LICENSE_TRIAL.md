# Tenant license and trial database contract

`tenant_licenses` is the tenant-level entitlement record for the new license
flow. The existing `licencas` table remains the legacy license-key mapping;
this migration does not change or synchronize the desktop's current
license-key validator.

`activate_tenant_trial(tenant_id)` creates one trial record for an existing
tenant. The database clock sets the start time, and the end is exactly 30 days
later (720 elapsed hours, independent of daylight-saving transitions). The
tenant primary key and `ON CONFLICT DO NOTHING` make repeated and
concurrent activation requests return the original entitlement without
restarting the trial. A nonexistent tenant is rejected by the foreign key.

Both `activate_tenant_trial` and `resolve_tenant_license` are `SECURITY
DEFINER` RPCs, verify the Supabase JWT role is `service_role`, and are not
granted to `anon` or `authenticated`. A trusted server must authenticate the
customer and derive the tenant ID before invoking either RPC. Do not put a
service-role key in the desktop application. The repository currently has no
trusted licensing API or authenticated onboarding flow, so these RPCs are not
yet callable by the desktop; no HTTP resolver or onboarding endpoint is wired.

`resolve_tenant_license` calculates trial and subscription expiration using
the database clock and returns `checked_at`. It does not update the stored
status. The Python `LicenseSnapshot` parser accepts only the declared result
fields, checks tenant binding and timezone-aware timestamps, and rejects
unexpected fields (including token fields). The `revoked` status is reserved
for an explicit server-side revocation decision; it does not implement a
payment or revocation workflow.

`InstallationIdentityStore` creates a random UUID once and persists it in a
per-user SQLite database under the operating system's application-data
directory. It stores no personal data, credentials, or license decision.
On POSIX the directory and database are restricted to the current user. The
identifier is stable across application restarts and ordinary reinstalls that
leave the per-user application-data directory intact, but it cannot reliably
identify a computer after that directory/profile is deleted or reset. It is
not a hardware fingerprint and is never proof of authorization. Corrupt
identity data raises an error rather than silently generating a replacement.

`InstallationRegistrationRequest` defines the minimal future client payload:
only `installation_id`. It intentionally excludes `tenant_id`; a trusted
backend must authenticate the customer and derive the tenant from that
authentication context. `tenant_installations` is a server-managed registry
with a unique `(tenant_id, installation_id)` constraint. Direct access is
revoked for every Supabase role, including `service_role`; no registration
RPC has been added because the repository has no trusted customer-auth
boundary from which it could safely derive the tenant.

The existing tenant-license primary key and `ON CONFLICT DO NOTHING` keep
trial activation idempotent per tenant, while the installation unique
constraint prevents duplicate records for the same installation within a
tenant. Neither mechanism independently authenticates a caller. Offline
verification must fail closed and report
`LICENSE_VERIFICATION_UNAVAILABLE`; this change adds no grace period and does
not connect the GUI to an endpoint. The desktop's pre-existing legacy
license-key path is unchanged.

No payment processing, desktop onboarding integration, hardware fingerprint,
or Xero integration is implemented. A future server must not trust a
caller-supplied tenant ID or treat an installation ID as an authenticator.
