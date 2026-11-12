# Prospective study lifecycle proposal

Status: proposal only; no experiment extension or study activation applied.

The original experiment ends January 21, 2027 at 02:40:02 UTC. Even the
hypothetical November 10 study start requires observations through February 5,
2027 to satisfy both 60 regular sessions and 12 weeks. A later actual start needs
its own authenticated calendar and recalculated endpoint. The November 29 grant
expiry is a separate funding constraint; extending an experiment creates no funds.

## Selected approach

Keep the original experiment ID, start, baseline, expiry, journal, positions,
reservations and loss limits. Add an immutable, explicitly approved lifecycle
amendment in that experiment's journal and scoped documents. Do not reset the
experiment or alter its historical `ends_at` value. A single effective-expiry
resolver must validate the amendment and supply the deadline to every runtime
consumer. Missing, malformed, conflicting, expired or unapproved amendments must
fail closed, not silently extend trading.

An amendment must bind the original experiment ID and original expiry; the
approved study ID and schedule hash; exact new UTC expiry; source/config hashes;
funding evidence references; a reason, approval identity/time, and the prior
amendment hash (or explicit first-amendment marker). Approval must precede the
original effective deadline. Amendments cannot retroactively authorize activity
or remove a latched loss limit. Store the approval and its journal event in one
transaction with a compare-and-set check on the previous effective expiry.
Require trading pause before applying and preserve it afterward; a repeated identical request is idempotent and a changed request
with the same identifier is rejected.

Select the exact deadline only after the actual future schedule is frozen. It
must cover the last required regular-session close and a documented bounded
reconciliation period. The closing procedure must stop new research and orders
at their respective study cutoffs, reconcile outstanding paper orders and fills,
and retain any positions; it must not liquidate automatically. Funding gates
remain independent and apply throughout.

## Implementation and acceptance scope

Update expiry use in Service risk checks and final pre-send checks, Runner's
expiry path, cancellation handling, and paper dispatch's schedule containment.
Resolve from the authoritative ledger on each safety boundary, not a stale
process cache. Existing experiments without amendments retain current behavior.
Keep the original experiment visible in the dashboard and label any effective
expiry and approval separately.

Tests must cover unchanged legacy expiry, valid approval, wrong experiment,
wrong schedule/source/config binding, shortened or malformed timestamps,
post-expiry approval, conflicting amendments, duplicate requests, transaction
failure, concurrent approval, restoration from checkpoint, and expiry crossing
between initial approval and final submission. Verify cancellation remains
available after expiry and never releases reservations before broker evidence.
Re-run the existing risk, runner, dispatch, recovery and dashboard checks.

## Activation remains conditional

A lifecycle amendment alone does not approve the research, establish cash-account
eligibility, fund the study, clear trading pause or authorize live money. Freeze
the opening evidence packet only after independent research acceptance, recovery
acceptance, account terms, cost allocation and funding are substantiated. Preserve
the selected five-company research / three-company execution scope and all
predeclared measurement thresholds. Obtain explicit approval of that concrete
opening packet before installing an active study.

## November 9 implementation status

The single-amendment implementation is now present; it has not been installed on
the executor. `approve-experiment-extension` requires pause, a source/config-bound
receipt, frozen schedule and funding-evidence hash, and an explicit null prior
amendment marker. The original experiment stays unchanged. Subsequent amendment
chains are deliberately unsupported; a later change needs a reviewed migration.
Configuration/source changes invalidate the installed amendment and fail closed.

`execution_end` stops orders and research at the final scheduled close;
`effective_end` permits at most one additional day for reconciliation. Reports
expose both dates alongside the unchanged original expiry and approval. Corrupt
approval data blocks execution but still permits cancellation, without releasing
reservations absent broker confirmation. Tests cover idempotent/concurrent
approval, transactional journal failure, copied-ledger restoration, malformed
receipts, runtime binding, final submission cutoff crossing and grace separation.
Dashboard presentation and actual frozen-schedule installation remain pending.
