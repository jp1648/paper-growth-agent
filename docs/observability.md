# Observability

Postgres stores redacted platform events in `telemetry_events`. The existing SQLite
ledger still owns trading accounting and idempotency. No external tracing service,
new model calls, or UI is required.

## Access

Postgres runs in Compose, with persistent volume `observability_pg`, database
`observability`, user `trader`. Host access is **127.0.0.1:55432** only. A SQL client
such as psql can query it directly. The generated password is in the private file
`state/observability/postgres-password`; never paste it into logs or committed files.

Query without copying credentials:

```sh
./deploy/container.sh exec -T postgres psql -U trader -d observability <<'SQL'
SELECT at, run_key, name, phase, payload
FROM telemetry_events ORDER BY at DESC LIMIT 30;
SQL
```

For a new installation:

```sh
python3 deploy/setup-observability.py
./deploy/container.sh up -d --build postgres agent
```

Setup preserves existing credentials. It adds `OBSERVABILITY_DATABASE_URL` to the
private `.env`, using Docker hostname `postgres`. Host-side Python processes must
use the same URL with host `127.0.0.1` and port `55432`. Outside Compose telemetry
is opt-in through that variable; the CLI does not load `.env` itself. Never use
`docker compose down -v` if you want to retain telemetry.

## What is captured

- CLI commands, scheduler subprocesses, graph stages and research runs.
- Specialist delegation, effective model messages/system prompts/tool schemas,
  raw model responses, token usage and estimated model costs.
- Tool arguments/results, report validation inputs (including rejected reports),
  exception types/messages/tracebacks and available provider error responses.
- Paper broker requests/responses, evidence retrieval, reconciliation, risk and
  execution, plus every existing journal event and document update. Stream quotes
  retain the existing one-per-symbol-per-second coalescing; auth packets are excluded.

Each span has a UUID, parent span, trace ID, run key when applicable, and experiment
ID when available. `start` is saved before work; `end` or `error` records its outcome
and duration. Async cancellation is an error. Scheduler children inherit their
parent trace; thread-based agent tools retain context. A successful tool/model call
is not research acceptance or a confirmed fill: inspect validation, decisions and
broker reconciliation events separately. CLI `command` output is its exit code.

Payloads are JSONB, indexed alongside trace, name/time and run key. Known environment
credentials, secret fields, bearer tokens and key/connection-string patterns are
redacted **before local persistence**. Prompts and financial evidence are otherwise
retained in full: treat the database and backups as private financial records.
No traceback local variables, request auth headers or arbitrary object attributes
are collected. Redaction is best effort for unknown free-text secrets.

```sql
-- Follow one investigation, including its failed validation input.
SELECT at, span_id, parent_span_id, name, phase, payload
FROM telemetry_events WHERE run_key = 'YOUR_RUN_KEY' ORDER BY at;

-- Failures with the rejected output available on the corresponding start row.
SELECT e.at, e.name, s.payload->'input' AS input, e.payload AS failure
FROM telemetry_events e JOIN telemetry_events s USING (span_id)
WHERE e.phase = 'error' AND s.phase = 'start' ORDER BY e.at DESC;

-- Starts without a recorded completion: may be running, killed or awaiting export.
SELECT s.at, s.run_key, s.name, s.span_id
FROM telemetry_events s
WHERE s.phase = 'start' AND NOT EXISTS (
  SELECT 1 FROM telemetry_events e WHERE e.span_id = s.span_id
  AND e.phase IN ('end', 'error')) ORDER BY s.at;

-- Real cost records (avoid summing duplicated model response usage).
SELECT run_key, sum((payload->>'estimated_usd')::numeric) AS estimated_usd
FROM telemetry_events WHERE name = 'model_cost' GROUP BY run_key;
```

## Delivery and limits

Each event is synchronously saved to a private, separate SQLite outbox
`state/telemetry-outbox.sqlite`. A background exporter sends batches to Postgres
roughly every two seconds; outages retry every 15 seconds. Postgres commits before
outbox deletion; event UUIDs make crash/retry delivery idempotent. Threads and CLI
processes use independent connections. Short-lived CLI shutdown attempts a flush;
remaining events are replayed by the listener or next configured process.

Postgres outages do not stop risk checks, reconciliation or cancellation. Export
failure/recovery is logged to stderr. A local disk failure logs an error and can
lose telemetry; it does not override the trading ledger. SIGKILL/power loss cannot
produce a genuine completion event: unmatched starts remain visible, never marked
successful. The listener itself is intentionally a long-running unfinished span.

No automatic retention/deletion or historical backfill: capture starts with this
deployment. Old missing model responses cannot be reconstructed. Monitor disk
space and back up Postgres separately from the accounting ledger:

```sh
# The dump contains private research/account data; keep it private.
umask 077
./deploy/container.sh exec -T postgres pg_dump -U trader observability > state/observability.sql
```

Offline tests exercise the real DeepAgents workflow with fixture models/broker,
including linked tool/model completion. The opt-in `TEST_OBSERVABILITY_DATABASE_URL`
test verifies real Postgres insertion, JSONB querying and duplicate replay. Point
it at a **separate test database**, e.g. `observability_test`; it adds fixture rows
and never contacts a model provider or submits broker orders.

## Private dashboard

The [Vercel-deployable observatory](../dashboard/README.md) reads this database with
a SELECT-only login. Agent maintenance emits `dashboard.snapshot` for portfolio
and historical research summaries. Snapshot history is separate from full traces;
no missing trace is reconstructed. Run ad hoc agent commands inside the existing
agent container so SQLite writers share the same filesystem/locking environment.
