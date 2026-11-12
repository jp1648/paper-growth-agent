# Always-on operation

The dashboard and database alone do not run the agent. The execution process,
SQLite ledger, telemetry outbox and account-binding/control files require one
persistent execution host. Do not scale the trader horizontally.

1. Provision one Linux VM with a persistent disk. Run the existing container or
   systemd service under a dedicated non-root user; enable restart on boot.
2. Stop the old executor before moving. Back up and transfer the ledger, outbox,
   experiment history and control directory, preserving absolute account bindings,
   permissions and pause state. Never initialize a replacement experiment to solve
   a path mismatch. Reconcile before enabling any execution.
3. Use a VM service account for Cloud SQL Auth Proxy rather than a person's expiring
   CLI session. Keep broker, X and model keys in Secret Manager or protected runtime
   files; the dashboard receives only its reader and login secrets.
4. Add external health/staleness, reconciliation, disk/outbox and spend alerts.
   Docker restart policies restart exits, not unhealthy running containers. Alert
   and investigate instead of automatically resuming trading after a failure.
5. Enable encrypted backups for SQLite/control state and Cloud SQL; test restoring
   them. Set retention and capacity from actual telemetry growth, without silently
   deleting audit history. Set provider budgets as well as local estimated limits.
6. Complete an end-to-end real research acceptance and the explicitly opted-in
   one-share paper fill check during broker regular hours. Inspect evidence,
   valuation, adversarial review and actual fills. Offline tests are not this gate.

Keep trading paused until the operator deliberately resumes after these gates.
Single-zone infrastructure still has outage risk; paper results do not establish
live investment performance. This repository does not provision a VM automatically.

## Research funding receipts

Default funding still requires provider credit evidence no older than 30 minutes
and an unchanged spending ledger. Unattended scheduling may instead use an
explicitly approved bounded allocation in the global `research_funding` record;
installing the code does not create one or authorize its use.

The provider receipt retains `basis`, `evidence_sha256`, `verified_by`,
`observed_at`, `expires_at`, `available_usd`, `buffer_usd` and `spending_sha256`,
and additionally identifies `account_id` and `project_id`. Its optional
`allocation` contains:

- `status: operator_approved`, `approved_by`, `approved_at`, `valid_until`, `usd`.
- Matching `account_id`, `project_id` and `funding_evidence_sha256`.
- `exclusive_account_use_evidence_sha256`: retained explicit confirmation that
  unrelated applications cannot consume the account's credits during validity.
  A project-specific local ledger is not such evidence. Shared or uncertain
  account usage must retain the default fresh-evidence rule until account-wide
  reconciliation is implemented; this allocation path does not support it.
- `spending_baseline`: exact global research-spend document keys mapped to their
  original serialized bodies, all settled. The sorted key/body pairs must match
  the provider receipt's `spending_sha256`.

Approve against evidence at most 30 minutes old. Allocation validity must cover
the whole bounded request and end no later than credit expiry. Keep at least the
reviewed $10 buffer, conservatively deduct recent charges not yet reflected by
provider billing, and never treat projected refills as confirmed funds.
Every new global charge or reservation, including other experiments and tests,
reduces the allocation. A settlement replaces its reservation in the same record;
unknown costs, edited/deleted baseline records or malformed amounts block work.
Renewal requires fresh credit evidence and a complete settled baseline, not a
reset of prior consumption. The existing reservation transaction checks funding
and inserts the new reservation atomically. Ordinary $20/day and $20/run study
limits still apply, as do acceptance, pause, ownership and broker risk gates.
No receipt is installed by this document. Funding through one allocation does not
prove funding through the full study or authorize a different study scope.

This gate verifies that baseline records are unchanged; later spending still
relies on the operational ledger's preserved history. It cannot infer a charge
whose post-baseline record was deleted outside supported workflows. Do not grant
external writers or reset/delete spending history; add append-only attestations
before introducing such writers. Evidence hashes are references to reviewed
artifacts, not cryptographic proof that an operator's assertion is true.
