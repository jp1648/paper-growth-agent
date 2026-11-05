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
