# Trader observatory

Password-protected, read-only Next.js dashboard for the paper agent. Deploy on Cloud Run or Vercel. Keep database and workspace credentials server-side. See [Google Cloud](../docs/google-cloud.md).

## Local use

From the repository root, with Postgres already running:

```sh
uv run python deploy/setup-dashboard.py
cd dashboard
npm ci
npm run dev
```

Open http://127.0.0.1:3000. The generated workspace password is in the private
`dashboard/.env.local` file (`DASHBOARD_PASSWORD`); it is never sent to client code.
Setup creates `dashboard_reader` with SELECT permission on `telemetry_events`.
It preserves existing credentials instead of silently rotating them.

The agent's existing maintenance loop publishes `dashboard.snapshot` from the
accounting ledger. It contains portfolio marks, pause/reconciliation status,
positions, known model cost (with unknown-call count), latest daily performance,
and the last 30 saved research audits with reports, calculations and evidence links.
Historical audits are included without inventing historical full traces.
The UI refreshes every 30 seconds and marks portfolio data stale after five minutes.

## Deploy to Vercel

The application is ready to build on Vercel using the Next.js preset. It has not
been published automatically. Vercel **cannot connect to your laptop's localhost**.
Use a reachable hosted Postgres database (for example, one of the providers in the
[Vercel Postgres marketplace](https://vercel.com/docs/postgres)).

1. Provision hosted Postgres and initialize the telemetry schema there. Preserve
   the local database; optionally copy its history using `pg_dump`/`pg_restore`.
   See `../docs/observability.md` for the existing schema and backup instructions.
2. Set the agent's **private** `OBSERVABILITY_DATABASE_URL` to the hosted writer
   connection and recreate the agent container. Its existing exporter sends future
   telemetry and dashboard snapshots to that database. Keep trading paused during
   infrastructure changes. Do not point a cloud app at `127.0.0.1` or publish the
   laptop's database port publicly.
3. On the hosted database, create a separate login with only CONNECT, schema USAGE
   and SELECT on `telemetry_events`; use the provider's console to set a unique
   password. Example permissions (replace database name):

   ```sql
   GRANT CONNECT ON DATABASE your_database TO dashboard_reader;
   GRANT USAGE ON SCHEMA public TO dashboard_reader;
   GRANT SELECT ON telemetry_events TO dashboard_reader;
   ALTER ROLE dashboard_reader SET default_transaction_read_only = on;
   ```

4. Import the repository into Vercel and set **Root Directory = `dashboard`**,
   Framework = Next.js, build command `npm run build`. Use this repository as the source.
5. Configure server environment variables for each intended environment:
   - `DATABASE_URL`: hosted **reader** connection, preferably the provider's pooled endpoint.
   - `DATABASE_SSL=true`: certificate-verified TLS for remote PostgreSQL. Use the
     provider's verified connection configuration; do not disable certificate verification.
   - `DASHBOARD_PASSWORD`: a random secret of at least 24 characters. Generate it
     locally with `openssl rand -hex 32`. Never use a `NEXT_PUBLIC_` variable.
6. Deploy and verify login, authenticated `/api/data`, a fresh snapshot and query
   access. The app fails closed when its password is absent/too short. Database
   failure shows an error without showing a connection string. Consider Vercel's
   deployment protection as another access boundary for previews.

## Views and limits

- **Overview:** equity, flow-adjusted dollar P/L, cash, known estimated model spend,
  recorded daily closes, research acceptance and current positions.
- **Research:** saved specialist conclusions, cited evidence, deterministic valuation
  calculations and accepted investment decisions. Failed/partial reports are labeled.
- **Traces:** latest 100 events for a selected run, including model/tool/validation
  outputs and failures. Each payload preview is capped at 12,000 characters; full
  records remain in Postgres. Old runs may have no detailed trace.

The app has no trading controls. Paper returns are not evidence of live execution
or investment skill. Charts need at least two recorded closes; missing data stays
missing. Research costs are separate from portfolio P/L, and unknown charges are
explicitly counted rather than assumed zero. Login uses an eight-hour signed,
HttpOnly, SameSite cookie (Secure on production HTTPS). No public demo data is mixed
with the real account. This is a single-workspace password, not multi-user IAM.

## Verification

```sh
npm test
npm run typecheck
npm run build
# With local setup and npm run dev already running:
npx playwright install chromium
TEST_DASHBOARD_PASSWORD=your-test-password npx playwright test
```

Use an isolated fixture database with a paused snapshot and a failed research run.
Set TEST_DASHBOARD_URL to override the default localhost URL. Never use production
credentials for CI. Browser tests cover unauthenticated API denial, login, filtering, research/trace
navigation, mobile overflow and logout. Screenshots go to the private `../state/`
directory. Backend snapshot tests use offline fixtures and do not submit orders.

### Other Postgres hosts

See the optional [hosted migration procedure](../docs/supabase.md).

The Overview spend table groups current-experiment model usage by model and data
requests by provider, across all recorded attempts (not only the latest 30 runs).
Known estimates exclude unpriced requests and budget reservations. X/Flovia costs
remain unknown when the provider response does not report a charge. Cloud hosting,
market-data subscriptions, SEC access and Telegram billing are not integrated.
