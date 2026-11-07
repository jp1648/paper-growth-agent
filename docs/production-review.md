# Code review and release checks

This file preserves the original review baseline. Current release evidence and
remaining gates are maintained in [live-readiness.md](live-readiness.md); the
counts below are historical, not the current suite or deployment status.

Review scope: Python research/execution/accounting, provider adapters, durable
telemetry, Next.js dashboard, deployment scripts and public release hygiene.
Private operational journals are intentionally excluded from the public repository.

## Findings fixed

- SEC requests now validate HTTPS, exact SEC origin, credentials and port at the
  transport boundary, not just in callers. Redirects remain disabled.
- Telemetry exporters share a process lock and batch Postgres inserts, reducing
  duplicate competing export work and round trips. Commit-before-delete and UUID
  idempotence preserve recovery; database outages retain local events.
- Recursive telemetry redaction reads secret environment values once per event,
  while still refreshing between events so newly configured secrets are covered.
- Missing account eligibility fields fail closed. Broker time-in-force must match
  the saved approved intent. Symbol configuration rejects unsafe path/query text.
- Dashboard rendering is split into typed overview, investigation and shared data
  views. Refresh cancellation prevents stale requests overwriting newer data;
  traces load only when visible. Timestamp serialization is explicit.
- Credential creation uses private permissions; dashboard credential file creation
  is exclusive. Runtime data, secrets and private deployment notes are excluded.

## Verification

Backend: 186 offline tests passed, three external opt-in checks skipped. Isolated
Postgres telemetry integration: ten tests passed, including duplicate replay and
real offline agent graph trace delivery. Ruff checks pass. Dashboard authentication
unit test, TypeScript checks and production build pass. Two isolated browser tests
passed for login, API access control, research/traces, mobile layout, logout and
Chromium OS sandbox enforcement. Dependency audits found
no known Python or npm vulnerabilities at review time.

## Remaining limits

This review does not certify every possible bug absent. Independent investment-quality acceptance and broader broker failure/recovery
checks remain separate gates. The recorded one-share paper fill establishes only
that bounded smoke test, not live readiness. Telemetry can
contain sensitive source/account data despite credential redaction: keep databases
and dashboards private. The single-workspace dashboard password is not multi-user
IAM; database session revocation and shared login throttling are now deployed, while
stronger identity-aware access and edge abuse protection remain necessary before
wider use. See [always-on requirements](always-on.md).
