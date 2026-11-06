# Single Google Cloud execution VM cutover

This is an operator runbook, not an automatic provisioning script. Use the
existing `docs/always-on.md` acceptance gates. The service runs `listen`, which
already schedules ticks and maintenance; do not install the old tick timer too.
The new executor must remain paused throughout migration and acceptance.

## Resources

Choose `PROJECT`, `REGION` and `ZONE` near the existing Cloud SQL instance. One Debian 12 `e2-medium` VM named
`trader-executor`, a 20 GiB boot disk and a separate 50 GiB balanced persistent
disk `trader-state`. Set the data disk **not** to auto-delete, enable VM deletion
protection, Shielded VM and OS Login. Use an execution-only service account with
Cloud SQL Client and access only to required secrets. Never grant Editor.
Use IAP SSH (ingress TCP 22 only from `35.235.240.0/20`), no public application
ports. A private VM requires Cloud NAT for broker/model/X internet access; for
the smallest setup, an external IP with no public ingress also works. Confirm
ongoing VM, disk, backup, network and Cloud SQL costs before creation.

Use explicit `--project` on every command. Enable `compute.googleapis.com`
when preparing the project.

## Private template rendering

The checked-in units are templates. Render `@TRADER_ROOT@`, `@TRADER_HOME@` and
`@TRADER_CONTROL@` to the original absolute paths into a private directory outside
version control, then install those rendered units. Never install unrendered
units. Preserve the source resolved paths and account-binding file exactly.
Replace PROJECT/REGION/ZONE in command examples with the chosen deployment.

## Prepare the destination without starting execution

1. Install Docker and Cloud SQL Auth Proxy at `/usr/local/bin/cloud-sql-proxy`.
   Create non-login user/group `paper-agent` with UID/GID 501, home `@TRADER_HOME@`
   and no sudo access. The container runs as that non-root identity.
2. Format only the newly created empty data disk, mount it at `/srv/trader`, and
   persist its UUID mount in `/etc/fstab`. Store `repo/` and `control/` on it.
   Bind-mount `/srv/trader/repo` at `@TRADER_ROOT@` and
   `/srv/trader/control` at `@TRADER_CONTROL@` through
   fstab, ordered after the data disk mount. Do not use symlinks: `Path.resolve()`
   must still yield the original absolute ledger path. A missing disk must fail
   the service rather than create an empty replacement ledger on the boot disk.
3. Transfer the reviewed source checkout (including authorized local edits),
   config and private `.env` into `repo/`. Exclude the Mac `.venv`, caches and
   `.git` credentials. Use the reviewed Cloud Build image pinned by digest; do not copy a Mac runtime. Protect
   `.env` and config with mode 0600, directories with 0700, owned by paper-agent.
4. Install `always-on-proxy.service` and create mode-0600 root-owned
   `/etc/always-on.env` with `TRADER_IMAGE=<registry image digest>` and
   `CLOUD_SQL_INSTANCE=<project:region:instance>`. The proxy unit creates `/run/trader-cloudsql` owned by UID/GID 501;
   the executor mounts this directory read-only. The proxy uses VM service account credentials. Match the Unix
   socket path in the existing observability DSN to the instance connection name. Keep sockets accessible
   only to the trader user/group. Preserve the outbox and Cloud SQL history.
   Do not copy a person's gcloud ADC/token files. Verify database connectivity
   before enabling the executor; do not run database setup/migration again.
5. Install `deploy/always-on.service` as
   `/etc/systemd/system/always-on.service`, then `systemctl daemon-reload`.
   Install `always-on-health.service` and `.timer` too. Inspect
   `systemd-analyze verify` and `findmnt` output. Enable the health timer only
   after the paused executor is running. A failed health unit must feed the
   external alert; this timer does not automatically resume/restart trading. Do not start it yet.

## Stop, copy, verify, then start paused

1. Confirm the current paused state and record experiment ID, ledger resolved
   path, journal/document counts, outbox counts, account-binding file contents
   and broker positions/open orders. Keep sensitive evidence private.
2. Stop and disable every old executor/launcher, including listeners, timers,
   containers and user login/launch jobs. Stop outbox writers before copying.
   Local file locks do **not** prevent a second executor on another host.
3. With all writers stopped, take a consistent backup of the entire repository
   `state/` and complete control directory, including hidden files, ledger WAL
   sidecars, telemetry outbox, backups, experiment history, `.ledger` and
   `.paused` files. Transfer over SSH to the prepared data disk. Never run `init`
   or edit the ledger binding. Retain the stopped source as a rollback copy.
4. Compare source/destination file hashes and inventory; check every SQLite DB
   with read-only `PRAGMA integrity_check`. Check active experiment/counts and
   exact `.ledger` contents against the destination's resolved ledger path.
   Verify the pause marker exists. Adjust ownership, not account binding.
5. Take an encrypted data-disk snapshot with an explicit retention policy. Test
   restoring it to a separate disk without starting an executor. Backups must
   cover control files and the ledger together. Never boot a restored trading
   service while the original executor runs.
6. Only after confirming the old host cannot restart, enable/start
   `always-on.service`. Confirm the pause remains set, reconciliation matches
   broker state, and Cloud SQL telemetry/outbox drain works. Inspect protected
   service logs without printing keys or DSNs. Run the documented acceptance
   gates before a deliberate operator resume. No paid research or order test is
   implied by installing or starting this service.

Configure external service/staleness, disk capacity, outbox and spend alerts.
Restart only handles process exits; an unhealthy live process needs investigation.
A single-zone VM is not highly available. Account locks protect one filesystem,
not two hosts. Rollback requires stopping/disabling the VM first and copying all
new ledger/outbox/control changes back; never resume an obsolete source copy.

## Scheduled backups

Attach a daily data-disk snapshot schedule (example: 06:00 UTC, 7-day retention)
so ledger, outbox, WAL files, config and control state stay on one captured disk:

```sh
gcloud compute resource-policies create snapshot-schedule trader-daily \
  --project=PROJECT --region=REGION \
  --daily-schedule --start-time=06:00 --max-retention-days=7 \
  --on-source-disk-delete=keep-auto-snapshots --storage-location=REGION
gcloud compute disks add-resource-policies trader-state \
  --project=PROJECT --zone=ZONE \
  --resource-policies=trader-daily
```

Snapshots of the running service are crash-consistent, rather than a coordinated
application checkpoint. SQLite recovers committed transactions from the captured
WAL; preserve its WAL beside each database. Restore a clone with filesystem journal
replay enabled before inspecting files; a read-only `noload` mount can hide committed
metadata that still resides in the filesystem journal. The existing trader daily SQLite
backup provides an additional ledger copy, but does not replace the full disk
backup or Cloud SQL backups. On restore, keep execution disabled, verify every
SQLite database's integrity and experiment/binding/pause files, then reconcile
broker state before resuming. Test the initial snapshot restore and periodically
repeat it. Verify the first scheduled snapshot completes and alert on failures.
Never delete audit history from the live disk to match backup retention.

See Google's [snapshot schedule documentation](https://docs.cloud.google.com/compute/docs/disks/scheduled-snapshots).

## Debian bootstrap commands

Run as root on the prepared VM. The example proxy binary is pinned to a verified
release and AMD64, matching e2-medium. Review a newer release before upgrading.

```sh
apt-get update
apt-get install -y docker.io ca-certificates curl
systemctl enable --now docker
groupadd --gid 501 paper-agent
useradd --uid 501 --gid 501 --home-dir @TRADER_HOME@ --no-create-home \
  --shell /usr/sbin/nologin paper-agent
curl --fail --location --output /tmp/cloud-sql-proxy \
  https://storage.googleapis.com/cloud-sql-connectors/cloud-sql-proxy/v2.27.0/cloud-sql-proxy.linux.amd64
printf '%s  %s\n' 39ed2abcaa073c3b4714585e36a904e2b1d92218c5e07c18224fb73ef4bedde5 /tmp/cloud-sql-proxy | sha256sum --check
install -o root -g root -m 0755 /tmp/cloud-sql-proxy /usr/local/bin/cloud-sql-proxy
```

The checksum comes from Google's [signed proxy release](https://github.com/GoogleCloudPlatform/cloud-sql-proxy/releases/tag/v2.27.0).
Install units after the persistent mounts and private configuration exist:

```sh
install -o root -g root -m 0644 PRIVATE_RENDER_DIRECTORY/always-on.service \
  PRIVATE_RENDER_DIRECTORY/always-on-proxy.service \
  PRIVATE_RENDER_DIRECTORY/always-on-health.service \
  PRIVATE_RENDER_DIRECTORY/always-on-health.timer /etc/systemd/system/
systemctl daemon-reload
systemd-analyze verify /etc/systemd/system/always-on*.service /etc/systemd/system/always-on-health.timer
```

Authenticate Docker to Artifact Registry using short-lived VM service-account
credentials before pulling the reviewed image digest. Do not grant the trader
container access to Docker's socket. The executor unit's root-owned Docker client
starts an explicitly non-root container; the trader user needs no docker group.

Set the observability DSN socket host to `/run/trader-cloudsql/INSTANCE_CONNECTION_NAME`.
Check the full socket pathname including `/.s.PGSQL.5432` is at most 107 bytes
before starting; long project/instance names may require a shorter socket alias.

## Supervised acceptance pause guard

Render `deploy/acceptance.service` with the exact bound `@PAUSE_PATH@` and a
private root-owned `@ACCEPTANCE_SCRIPT@`. Install the guard script alongside the
reviewed acceptance script. The shell script must stop the ordinary listener,
run the single `trader-acceptance` container synchronously, and contain **no**
listener restart or EXIT trap: only the unit's chained cleanup may restart it.
Set a deliberate `RuntimeMaxSec` for the supervised run if needed; it is an
operator timeout, not a model spending quota.

The unit first saves the exact pause marker durably and disables/stops the
listener so an interrupted host reboot cannot start it unpaused. Cleanup stops a surviving
child, atomically restores/fsyncs the original marker, then enables/starts the listener.
Any cleanup failure prevents the restart. A host reboot still needs operator
inspection and guarded cleanup after an interrupted smoke; the listener stays
disabled until its original pause is restored. Pending orders must be reconciled/cancelled by
the restored paused listener; never blindly resubmit the smoke intent.
