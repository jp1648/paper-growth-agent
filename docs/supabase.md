# Optional hosted Postgres migration

Google Cloud is the primary deployment target. This optional procedure supports a compatible hosted PostgreSQL service.

This keeps the existing Postgres protocol and password-protected Next.js UI. No
Supabase JavaScript client, anon key, service-role API key or Supabase Auth rewrite
is required. The trading accounting ledger stays local and authoritative.

## Access needed

Connect the Supabase integration and select the intended project. Provisioning a
new project, paid plan or changing an existing project's database password is not
performed implicitly. Database connection credentials must be supplied privately,
not pasted into a chat or committed to source control.

## Migration procedure

1. Obtain the project's admin direct/session PostgreSQL connection string and root
   certificate from its Connect/Database settings. Use session mode on port 5432
   when IPv4 compatibility is needed. Store the DSN in a private file under state/,
   mode 0600. Require `sslmode=verify-full` and `sslrootcert` pointing to the
   downloaded certificate. Keep the original local database available.
2. Run from the repo root:

   ```sh
   uv run python deploy/migrate-observability.py --target-dsn-file state/supabase-admin-dsn
   ```

   The command creates the telemetry schema, applies
   `deploy/supabase-observability.sql`, copies in 200-event batches and compares
   every copied row against its source. UUID replay is idempotent. Existing events
   are never overwritten. A differing event aborts its batch; prior verified batches
   can be replayed. This does not change running configuration or remove local data.
3. Provision unique private passwords for `trader_writer` and `trader_dashboard`,
   enabling LOGIN on these dedicated roles. Grant CONNECT to the target database
   if its defaults do not already allow it. The writer has SELECT/INSERT only; the
   dashboard has SELECT only. Runtime code no longer attempts schema DDL when the
   telemetry table exists. Never use the project admin role in the dashboard.
4. Verify each connection with a rollback-only probe: writer can insert/read,
   dashboard can read but cannot insert/update/delete. Verify anon/authenticated
   roles cannot read this table. RLS is enabled and no public API policies/grants
   are added. Stop and review any pre-existing policies/role memberships if the
   destination table or dedicated roles already existed for another application.
5. During a brief maintenance window, stop the agent listener, copy the final delta
   using the same command, then update only its `OBSERVABILITY_DATABASE_URL` to
   the hosted writer session connection. Mount/place the root certificate in the
   shared state directory so `sslrootcert` resolves inside the container. Recreate
   the agent, keep trading paused, and verify the durable queue drains to Supabase.
6. Set the dashboard's server variables to the reader connection, `DATABASE_SSL=true`
   and, when required, `DATABASE_SSL_CA` containing the project's root certificate
   PEM (literal newlines or escaped `\n`). Retain the existing workspace password.
   Restart locally / redeploy on Vercel. For the initial small single-user deployment,
   use the session pooler with the existing two-connection pool. The shared pooler
   custom-role username is `ROLE.PROJECT_REF`; copy the project-specific hostname.
7. Verify a fresh dashboard snapshot, portfolio/research views and trace query,
   and confirm orders, model spend, experiment ID and pause state did not change.
   Keep local telemetry as a backup; do not delete its volume.

The application uses startup session settings. Do not substitute a transaction-mode
pooler without validating those settings and prepared-statement behavior first.
Do not put `sslmode=no-verify` or `rejectUnauthorized:false` into either application.

If cutover fails, restore the old application connection settings and restart.
New cloud-only events must be copied back before calling that a complete rollback;
never silently discard either database's history.

Official references: [database connections](https://supabase.com/docs/guides/database/connecting-to-postgres),
[custom roles](https://supabase.com/docs/guides/database/postgres/roles),
and [row-level security](https://supabase.com/docs/guides/database/postgres/row-level-security).
