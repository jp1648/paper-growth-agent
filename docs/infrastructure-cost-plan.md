# Infrastructure cost evidence — proposed export, not enabled

The read-only check on 2026-11-08 found billing enabled for
`trader-observatory-20261105`, but `bq ls` and `bq ls --all=true` returned no visible
datasets in that project. This does not establish that the billing account has no
export in a different project. No billing settings or resources were changed.

First reuse an existing export if the operator supplies its fully qualified table.
Otherwise the proposed configuration, subject to the user's billing-change approval:

- Existing project `trader-observatory-20261105`; no new project or billing account.
- Private dataset `trader_billing`, US multi-region, Google-managed encryption,
  no table expiration. No public access or broad new service-account permissions.
- Detailed usage-cost export only. No redundant standard/FOCUS export or pricing
  transfer setup. Preserve the account's existing export if one is discovered.
- Keep the dataset private; use a read-only project-filtered cost view if the app
  later needs access. Do not expose billing-account-wide records through the UI.
- Inspect query estimates/dry runs and bound billed bytes before cost queries.
  BigQuery storage/query charges are infrastructure costs too. No promises of zero
  cost or a hard spending cap from a budget alert.

Google documents export setup in the Billing console. Prefer supported CLI
operations where available; do not use undocumented APIs to bypass that setup.
[Official setup instructions](https://docs.cloud.google.com/billing/docs/how-to/export-data-bigquery-setup)
cover the permissions, supported locations, potential export/backfill delays and
BigQuery charges. An empty newly enabled table is not evidence of free operation.

## Before accepting any cost result

1. Verify the export's billing account, currency and project identity; record the
   dataset/table, retrieval time and immutable source extract hash.
2. Restrict attribution to the trader project. Include executor, Cloud SQL,
   dashboard, logging, networking, backups, artifact storage and build resources.
   Separate historical development/recovery-drill costs from prospective operating
   costs without hiding either or retroactively improving study returns.
3. Report gross usage cost, credits and net billed estimate separately. Do not
   unnest credit rows in a way that multiplies each usage row's base cost.
4. Use the predeclared study window and common time zone. Keep service usage times,
   invoice month and export availability distinct. Delayed corrections/credits need
   versioned reconciliation; no silent replacement of prior accepted extracts.
5. Establish resource sharing, recurring charges and boundary-period allocation
   before study activation. Unallocated amounts remain explicit blockers, not zero.
6. Reconcile export totals to provider billing evidence as available. Metered
   exports can change and are not necessarily final invoices. Keep estimated versus
   provider-reconciled states visible in the paper-trial report.

This plan supplies neither complete cost attribution nor approval to enable an
export. Complete total-return benchmarks and independent research review remain
separate trial prerequisites.
