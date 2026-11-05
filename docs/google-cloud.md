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
