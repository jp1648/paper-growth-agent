# Live pilot release contract — draft, not authorization

The current application is paper-only. There is no `live=true` option, and this
work does not introduce one. A live pilot requires a separately reviewed release
and explicit account-specific user approval after the readiness evidence passes.
A paper fill, a green test suite, or a budget approval is not live authorization.

## Evidence required before an approval request

Use the sixteen requirements in `live-readiness.md` as the acceptance ledger.
The release reviewer must inspect the actual evidence, not just a PASS flag:

- Exact source commit, dependency lock hashes, immutable image digest, CI run,
  security reports and configuration hash. Verify the deployed artifact matches.
- Independently reviewed unseen research cases, including exact financial input
  provenance, insufficient-evidence abstentions and adversarial counterexamples.
- Forward paper report with benchmark, cash flows, distributions, transaction and
  model/data costs, drawdown and uncertainty. Predeclare the observation period
  and decision criteria before collecting the evaluation sample. Do not select
  the best period retrospectively or describe paper fills as live liquidity proof.
- Broker-supported account eligibility and settlement evidence. Cash balance
  alone is insufficient proof of settled funds. Validate the actual account's
  restrictions, permissions, funding and market-data entitlements. Unknown or
  unsupported restrictions must block new exposure.
- Complete correction/bust reconciliation, restart, ambiguous submission,
  cancel/fill-race and backup restore evidence. A restored executor must remain
  paused and reconcile before it can submit.
- Required off-host pre-submission checkpoint replication with verified object
  generation/hash, restrictive storage IAM and an independent-host restore drill.
  A missing checkpoint_bucket or successful same-disk backup is insufficient.
- Verified single-writer fencing across hosts, plus independent alert delivery
  while the executor is unavailable. A filesystem lock on one VM is insufficient.

## Approval record to prepare for the user

Every field below must have a concrete value and supporting evidence. An empty
field is a no-go; this document supplies no default real-money allocation.

| Field | Required scope |
|---|---|
| Approval identity and expiry | Named approver, timestamp, unique release ID and expiry |
| Broker account | Exact dedicated account ID and verified account type/restrictions |
| Artifact | Immutable image digest and exact reviewed source commit |
| Capital | Maximum funded capital available to this pilot |
| Exposure | Per-order, per-symbol, sector, gross exposure and open-order limits |
| Loss limits | Daily and cumulative loss limits, including realized/unrealized treatment and flows |
| Instruments | Explicit allowed universe; shorting, leverage, derivatives and fractional trading disabled unless separately approved |
| Data/execution | Entitlement, freshness, spread, depth, participation and regular-hours rules |
| Settlement | Broker-verified available settled funds and applicable account restrictions |
| Spend | Model/data daily caps, reservations and unknown-charge handling |
| Pilot window | Start/end, supervised hours and required observation sample |
| Operator | Named monitor, independent alerts, response process and emergency stop |
| Recovery | Fencing owner, tested backup/restore, reconciliation and rollback procedure |

Approval must bind the account, artifact and limits together. Changing any of
these invalidates the approval until reviewed again. Do not infer approval from
possession of credentials or from requests to improve the agent.

## Isolation and launch sequence

1. Provision a separate live ledger, experiment ID, control namespace and secret
   scope. Do not copy paper orders, holdings, reservations or credentials into it.
   Preserve paper history for comparison. The dashboard and notifications must
   clearly distinguish environments in every order and fill view.
2. Independently review the new live adapter and its endpoint/account binding.
   Re-run endpoint escape, redirect, wrong-account and credential-separation tests
   against that adapter. The existing paper adapter remains paper-only.
3. Deploy paused with order submission disabled. Verify read-only account identity,
   initial cash/holdings, settled-funds policy, data entitlements, time/calendar,
   alert delivery and single-executor fencing against the approved release record.
4. Obtain explicit user approval of the completed record. Only then consider a
   supervised minimum-sized live test within the approved capital and exposure.
   No live test is authorized by this draft.
5. Observe and reconcile the actual broker outcome, including partial fills,
   rejects, cancellation races and costs. Never equate submission with execution.
   Restore the pause after the supervised window, even on failure.
6. Expansion requires a new review of actual live evidence. A successful smoke
   order is not evidence of positive expected returns or permission to scale.

## Stop and recovery behavior

Identity drift, broken reconciliation, uncertain submit status, lost fencing,
missing mandatory data, loss-limit breach or inaccessible monitoring stops new
orders. Preserve unresolved reservations. Do not reset an experiment, delete
history or automatically liquidate to make a check pass. An operator-approved
protective exit still needs verified ownership, current data and execution gates.
If those gates cannot pass, alert the operator with the reason.

Rollback first stops submissions and fences the old process. Broker truth and the
current ledger must be reconciled before another process is eligible to submit;
restoring an older database alone is never a trading rollback. Any unsupported
correction remains paused for broker-backed resolution.
