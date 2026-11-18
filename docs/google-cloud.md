# Google Cloud deployment

The dashboard is stateless and can run on Cloud Run. PostgreSQL telemetry belongs
in Cloud SQL; the authoritative trading ledger remains SQLite on one execution
host with persistent storage. Firebase is not required for the current app.

Use a dedicated project and billing account. Enable Cloud Run, Cloud SQL Admin,
Artifact Registry, Cloud Build and Secret Manager through `gcloud`. Build
`dashboard/Dockerfile`, attach a dedicated service account with Cloud SQL Client
and access only to the reader/password secrets, and mount the Cloud SQL connection.
Set server-side `DATABASE_URL`, `DATABASE_SSL` and `DASHBOARD_PASSWORD` as described
in [dashboard setup](../dashboard/README.md). Never put trading keys in the dashboard.

Use the Cloud SQL Auth Proxy for private authenticated database connections.
`deploy/setup-cloudsql.py` expects a private socket at `state/cloudsql/db` and
private password files under `state/gcloud/`. It provisions separate writer and
read-only dashboard roles. Do not run it against unrelated databases or roles.
`deploy/supabase-observability.sql` is the shared PostgreSQL permission policy;
its historical filename does not require Supabase.

Migrate with `deploy/migrate-observability.py --cloud-sql-proxy --target-dsn-file
state/gcloud/admin-dsn`. The migration preserves source history, deduplicates UUIDs
and verifies copied rows. Keep original backups until a restore test passes.
Keep automatic trading paused during cutover; verify fresh snapshots, queue drain,
reader write denial, account binding and unchanged orders before declaring success.

Enable backups, deletion protection, disk alerts and billing alerts. A small
single-zone database has no high availability and incurs ongoing compute/storage
charges. Size it from measured telemetry volume; the outbox grows during outages.
Cloud Run can scale the dashboard to zero, but cannot host this agent's persistent
SQLite process. See [always-on operation](always-on.md).

## Dashboard session migration

Before deploying database-backed sessions, apply `deploy/dashboard-sessions.sql`
as the database owner to the observability database. Both setup scripts include
this migration for new installations. Existing deployments need the migration
separately; do not rerun credential provisioning just to migrate sessions.

The dashboard remains SELECT-only on telemetry. Its existing role gains
SELECT/INSERT/DELETE only on `dashboard_sessions`; session mutations use explicit
read-write transactions while the connection defaults to read-only. Only SHA-256
hashes of randomized signed tokens and expiry timestamps are stored. Login removes
expired rows; logout removes that session before reporting success. Every request
checks the database without a session cache, so revocation applies across Cloud
Run instances. Already-authorized in-flight requests cannot be recalled.

Deploy the migration first, then the application. Previous-format cookies require
login again. If session storage is unavailable, authenticated access fails closed;
failed logout does not claim successful revocation. Password rotation invalidates
all signatures. Do not roll back to the stateless-session application as a security
recovery: it does not enforce the session allowlist. Rate limiting and broader
login-abuse controls remain separate release gates.

### Shared login-attempt limit

The session migration also creates a single `dashboard_login_limit` row. The
known dashboard roles have SELECT/INSERT/UPDATE permission on this table only;
telemetry remains SELECT-only. Reapply the migration before deploying login-limit
code to an existing installation. It is additive and idempotent.

Before checking a password, each login reserves one of twenty attempts in a
one-minute window using an atomic Postgres upsert. All instances share the same
allowance; no client IP headers or per-process state are trusted. Successful and
failed attempts both count. Existing sessions and logout are unaffected. No
passwords, IP addresses or submitted values are stored in the rate-limit table.
If the database fails, login fails closed. Saturation gives a one-minute retry
message; a later attempt resets an expired window automatically.

This is a bound on password checks, not denial-of-service protection. Someone can
consume the workspace-wide allowance and temporarily deny new logins; fixed windows
also permit a boundary burst. Trusted edge throttling/identity-aware access and
sustained hostile-load testing remain required for stronger availability guarantees.
