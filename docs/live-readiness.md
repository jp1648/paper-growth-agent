# Real-money readiness programme

Status: **NO-GO**. User authorized addressing audit findings 1–16. This does not
approve live activation or real-money orders. Preserve paper experiment/history,
reservations, current deployment and approved spending limits. Local changes are
not deployment evidence. Do not claim an investment edge from operational tests.

## Requirement-to-evidence ledger

| # | Requirement | Current evidence and remaining gate |
|---|---|---|
| 1 | Shared decision freshness | Deployed: age/future checks, news/filing invalidation and final pre-send risk callback. Regressions cover stale evidence and ownership-I/O delays. Broader combined provider/time faults remain open. |
| 2 | Broker corrections/busts | Deployed append-only exact-before/after corrections, positive quantity revisions and revision-chain tests. Full zero-quantity bust/reopened-order broker contract remains unsupported and blocked. |
| 3 | Protective exits | Deployed exemptions from discretionary cooldown/turnover; ownership, halt, freshness and execution limits remain enforced. Broader combined-failure and actual broker behavior evidence remains open. |
| 4 | Budget-independent position safety | Deployed overdue/missing/invalid review detection and daily/cumulative loss controls, including working-buy cancellation and fill races. Actual post-rollout health is good. Material-news policy and complete independent outage evidence remain open. |
| 5 | Financial provenance/interpretation | Deployed source-bound annual proxy with matching periods/units and coherent filing basis. Restatement regressions and frozen financial inputs pass. Partial-amendment bridges, debt/SBC normalization and independent economic review remain open. |
| 6 | Independently reviewed research cohort | Not achieved. Deterministic fixture validity is not accepted model research; sufficient-evidence completion and separate semantic reviews still need proof. Preserve holdouts and spending limits. |
| 7 | Execution economics/account restrictions | Deployed spread/depth/participation/notional controls and non-marginable balance checks. Actual settlement/account eligibility, early-close baseline semantics and adverse execution evidence remain open. |
| 8 | Isolated live release boundary | Paper-only enforcement and negative endpoint/credential tests pass. Draft live contract exists; no live adapter, account-specific release or user approval exists. |
| 9 | Crash consistency | Seven subprocess SIGKILL boundaries, atomic adjustments, disk/flush faults and checkpoint-restore regressions pass. Deployed mandatory pre-POST local checkpoint and configured verified off-host replication. Full service/infrastructure combined-failure proof remains open. |
| 10 | Single executor and recovery | Actual two-VM owner transfer/restart and storage IAM-loss drill passed. Saved cloud ledger reconciled against paper broker; off-host generation/hash restored. Deployed owner admission guard enabled. Full restored-service failover, in-flight requests and network partition remain open. |
| 11 | Provider/evidence/stream failure matrix | Future evidence, bounded retrieval, stream failure/backoff and duplicate-hint tests pass. Broader reconnect/backfill, schema drift, throttling, hostile-content and combined-failure matrix remains open. |
| 12 | Complete authenticated traces | Deployed and verified on production: 557 unique events across six pages, full-event authentication, logout, malformed cursor rejection and mobile rendering. Real Postgres equal-timestamp fixture passes. Broader outage/load scenarios remain open under 11/14. |
| 13 | Notifications/independent alerts | Deployed bounded Telegram retries, audited uncertain-delivery resolution and correction notices. Actual health/spend emails previously verified against cloud incidents. Actual metric-publisher outage opened a missing-heartbeat incident and delivered an authenticated email; publishing recovered. Automatic incident closure and matching recovery email verified. Whole-host stop/restart and independent alert delivery are now verified below; replacement-host in-flight recovery remains separate. |
| 14 | CI/security/infrastructure gates | Release CI passes backend/Postgres/browser/secrets checks; 548 offline tests pass with three opt-in skips. Ten safety mutations caught. Executor/dashboard base images pinned and artifacts verified. Broader security/permissions/load/fault coverage remains open; 80% coverage floor is not certification. |
| 15 | Accounting/performance | Independent lifecycle balances cover distributions, withholding, fees, flows, splits, sales and audited return of capital. Unknown actions fail closed. Full broker cost-basis/correction cases, comparable total-return benchmarks and all operating costs remain open. |
| 16 | Investment effectiveness | Not achieved. Predeclared unseen cohorts and forward paper observations after costs, independent review and uncertainty reporting remain required. No demonstrated investment edge. |

This table summarizes the latest evidence; the chronological log below preserves
older states and findings. Both services are deployed, but trading remains paused.

## Release gates

- Every identified critical invariant has positive, negative and recovery evidence.
- No unsupported BUY/ADD in adversarial or insufficient-evidence cases.
- All sufficient-evidence cases receive independent semantic review. Predeclared
  completion target remains at least 90%; insufficient fixture quality must be
  repaired without tuning on the holdout or marking abstention as valuation success.
- Broker/ledger agreement and no duplicate order submission survive fault drills.
- Authenticated complete traces, cost accounting, redaction and delivered alerts.
- Reproducible release manifest, rollback procedure and independently verified
  restore with only one executor able to submit.
- Explicit account-specific live pilot proposal with capital, exposure, loss,
  liquidity, monitoring and stop limits. No automatic activation.
- Forward results include uncertainty, drawdown, turnover, benchmark definitions,
  model/data/trading costs and point-in-time limitations. A no-go is an acceptable
  evidence-backed conclusion; a claim of completion requires the work above.

## Local verification log

- Audit baseline: 261 passed / 3 skipped; statement coverage 82.7%, branch 70.9%.
- First implementation regressions: 7 failed / 2 passed before changes; then 80
  passed across live-readiness, readiness, execution and graph/report suites.
- No paid calls, broker requests, deployment changes or live activation in this step.

- First full implementation run: **276 passed, 3 skipped**; Ruff passed.
  Skips: two opt-in Postgres integrations and the paper-order smoke test.

## Broker revision operator interface (local, not deployed)

`paper-agent --config config.toml acknowledge-revision private-revision.json`
requires the existing trading pause and an active bound experiment. The JSON
contains `reason`, `before` and `after`, each activity copied exactly from audited
history/current broker evidence. Both activities must have the same ID. Fill
corrections also require `order_before` and `order_after` copied exactly from
local/current broker records. The command does not submit or cancel orders and
never clears the pause. Approval is durable before reconciliation; failed
reconciliation remains blocked, and retrying the same approval is idempotent.
Original activity documents remain unchanged. Effective accounting, turnover,
reporting and unsent fill notifications use the approved revisions. Existing sent
notifications are not resent; correction notification handling remains item 13.
This does not yet support zero-quantity busts or changing fill order identity.

- Second implementation batch: **282 passed, 3 skipped**; Ruff lint/format pass.
  Held-position review checks run during maintenance without model calls, emit
  events only when issues change, surface in portfolio risk reviews and health,
  and block fresh exposure. Protective exits remain available. Review resolution
  clears the signal. Tests cover overdue, missing, future and malformed metadata.
- Telegram HTTP 429 with explicit `ok: false` receives up to three attempts with
  bounded retry-after delays. Server failures and transport uncertainty remain
  unknown and are not replayed. No external Telegram requests made in tests.

- Trace/UI batch: 284 Python tests passed with isolated Postgres, one paper-order test skipped; 5 dashboard unit/integration tests passed; 4 browser tests passed; typecheck and production build passed. Temporary database used tmpfs because local Docker storage was full. No production data was accessed or changed.

- Financial provenance batch: full offline suite **297 passed, 3 skipped**; an
  additional future-specialist regression then passed with the 15-case financial
  suite. No model/provider requests. Financial facts select matching annual USD
  CFO/capex and diluted weighted-average shares for the target company; latest
  eligible restatements are selected, conflicting facts rejected, original
  records retained. Same-day filings without availability times are conservatively
  excluded. value_scenarios now requires financial_source_id and
  financial_period_end and preserves those inputs for exact finalization replay.
- This narrow historical proxy is not normalized equity cash flow. Models must
  disclose debt, SBC/dilution, nonrecurring items and method limitations. Unsupported
  inputs still require null valuation; there is no shortcut for text-only evidence.
  Old frozen baseline files are unchanged and must not be described as passing
  this new provenance contract. A new, richer versioned cohort remains necessary.

- Recovery/accounting batch: five subprocesses were actually terminated with
  SIGKILL at durable execution boundaries against an isolated HTTP broker. New
  processes reopened the same SQLite/WAL ledger and verified integrity, original
  experiment ID, reservations and zero duplicate submissions. Cancellation pause
  survived; persisted fills replayed once. This is local process evidence, not a
  cloud outage or multi-host fencing drill.
- Six additional accounting cases use independent expected cash, basis, realized
  P/L and net distributions across splits, reverse splits, flows and withholding.
  Unknown merger/spinoff/delisting/return-of-capital events remain blocked until
  explicit broker-supported adjustment; no invented automatic corporate action.

- Execution-quality batch: full suite **315 passed, 3 skipped**, then 41 adapter
  and readiness tests passed including two added quote-unit cases. Defaults are
  0.5% maximum quoted spread/midpoint, 10% maximum displayed-side participation,
  and 2% equity maximum per-order notional. These are conservative engineering
  guardrails, not optimized or live-validated investment parameters. Missing
  size blocks submission; a sell checks bid depth, a buy ask depth. The system
  does not automatically split a rejected order or liquidate a position.
- Provider units were verified against the [Alpaca latest quote reference](https://docs.alpaca.markets/us/reference/stocklatestquotesingle-1):
  sizes are shares, previously round lots before 2025-11-03. Older quotes expose
  no executable size here; their stale timestamps also prohibit submission.
  IEX venue liquidity is not consolidated liquidity and displayed depth does not
  promise a fill. No additional market-data subscriptions or live access enabled.

- Security/coverage batch: Gitleaks 8.30.1 scanned all 18 available Git commits
  plus a separate copy of tracked/nonignored working-tree files, including new
  source, with no detected leaks. Reports are redacted under ignored state.
  Ignored credential/runtime files were not copied or published by the scan.
- npm audit reports zero known vulnerabilities; pip-audit 2.10.1 reports zero
  known vulnerabilities across 61 locked runtime packages. This is advisory
  matching, not a penetration test or proof of absence of vulnerabilities.
- Fresh branch-instrumented run: **317 passed, 3 skipped**, combined coverage
  rounds to **81%**. CI now enforces a minimum 80% combined statement/branch floor
  to prevent regression, not as a readiness certification. Critical scenario
  coverage and mutation evidence remain separate release requirements.

- Stream/mutation batch: **322 passed, 3 skipped**; Ruff lint passed. Clean
  websocket closures now enter bounded reconnect backoff and wake maintenance.
  Isolated clean-close/auth/subscription/malformed-message tests confirm that
  maintenance remains available; duplicate stream hints cannot book fills or
  submit orders. These are deterministic socket tests, not a cloud network drill.
- Six isolated source mutations disabling cash, ownership, pause, research-age,
  financial-basis and reservation guards were all caught by test assertions
  (baseline passed; no collection errors counted as detection). The script is
  now included in CI. This is a selected critical-guard check, not exhaustive
  mutation coverage. No production source is modified by the mutation runner.

- Revision-chain batch: reproduced a correction → original value → later
  correction failure. New approvals carry a durable sequence; effective replay
  checks the exact predecessor at each step. Older unsequenced approvals retain
  their existing interpretation. Replaying prior approvals remains idempotent,
  original activity records are untouched, and no orders are submitted.
- Candidate corrections now pass accounting replay before becoming durable
  approvals. A zero-quantity FILL is still rejected, with the prior audit and
  pause preserved and no invalid approval recorded. Full-bust recovery is not
  implemented: the provider's [broker trade-events reference](https://docs.alpaca.markets/us/reference/subscribetotradev2sse)
  documents trade_bust/trade_correct, but that is not sufficient evidence of
  the exact historical activity representation on this application's Trading API.
- Full offline suite passed **324 tests, 3 skipped** before the preflight replay
  addition; the affected readiness/execution suites then passed **53 tests**
  with that addition. Ruff lint passed. No broker requests or deployment changes.

- Release-boundary batch: **352 passed, 3 skipped**, Ruff lint passed. Twenty-eight
  new negative tests cover post-startup endpoint changes for GET/POST/DELETE,
  all five redirect statuses even with a redirect-enabled injected client, and
  refusal to fall back from missing paper credentials to generic credentials.
  All requests use isolated mock transports; no live endpoints were contacted.
- `live-pilot-release.md` is a concrete draft approval/release procedure, not
  authorization or evidence that its deployment, settlement, fencing, evaluation
  and independent-review prerequisites have been met. No live adapter added.

- Backup/restore batch: three actual SQLite restores preserve the original
  experiment, account binding and external pause. Prepared-intent backups recover
  accepted orders and subsequent fills without resubmission; backups predating
  the intent block unexplained broker orders rather than invent approval.
  Post-backup source copies are retained during the drills.
- Injected ENOSPC during backup publication exposed a destination connection
  lifetime bug: SQLite's transaction context did not close the connection and
  temporary WAL sidecars survived failure. Backup now explicitly closes the
  destination and switches it to standalone journal mode before publication.
  Success tests require exactly one backup file; failure leaves no partial backup
  and the source integrity remains valid. This is injected filesystem failure,
  not an actual full production disk or cloud snapshot restore.

- Cloud read-only inspection: the project currently lists one RUNNING executor
  VM. Its 20GB state disk has auto-delete disabled, one attached VM and the
  trader-daily snapshot policy (daily 06:00 UTC window, seven-day retention,
  keep snapshots after source deletion). Two snapshots are READY. guestFlush is
  false: these snapshots are not proof of an application-consistent restore.
- Cloud SQL has a successful automated backup completed 2026-11-06T09:07:56Z,
  seven-backup retention and ENCRYPTED_ONLY transport. Public IPv4 is enabled
  with no authorized networks returned. PITR was not verified. Project-level
  executor roles are artifact reader, SQL client, log writer and metric writer;
  inherited/resource-level effective permissions still need inspection.
- IAP SSH confirms always-on.service enabled/active/running and one executor
  container up. Direct SSH did not connect and was stopped before using IAP;
  no firewall changes were made. Inventory is not cross-host fencing proof.
  No restore, failover, deployment, broker request or order occurred in this batch.
  Bounded inspection evidence is retained under state/audit-20261106/cloud-readiness.txt.

- Actual cloud restore drill: created a temporary 20GB standard persistent disk
  from the verified scheduled state-disk snapshot, attached READ_ONLY and
  mounted ext4 ro,noload. No executor was launched from the restored disk and no
  restored credentials were loaded. Private temporary copies of database/WAL/SHM
  files were used for SQLite recovery and integrity checks, then removed.
- All ten SQLite databases passed integrity_check, including the active ledger
  and telemetry outbox. The snapshot ledger contains 85 documents and 22,841
  journal events under the original experiment ID. One account binding matches
  the original ledger path, and one pause marker exists. Full bounded evidence:
  state/audit-20261106/cloud-restore-result.json. This snapshot predates subsequent
  trading/research; no claim is made that it matches today's broker state.
- Cleanup verified: mount removed, temporary inspection script removed, disk
  detached and deleted successfully. Production always-on.service remained
  active/running. Original state disk and snapshot were not modified. The brief
  temporary disk incurs ordinary cloud storage charges; no model or broker calls.
- This proves restoration of the inspected snapshot's contents, not cloud
  failover, current-broker reconciliation or fencing against another host.

- SEC identity/taxonomy batch: seven regression cases failed before the fix.
  Company-facts and submissions responses must now identify the configured CIK;
  absent/malformed/other-company identities fail before caching. US-GAAP tags
  and the DEI entity-share tag are selected only from their expected namespace,
  preventing same-name cross-taxonomy overwrite. CIK and taxonomy mapping are
  retained in source content. Older unverified cache schemas are refetched.
- The [SEC API documentation](https://www.sec.gov/search-filings/edgar-application-programming-interfaces)
  distinguishes concepts by taxonomy and tag and returns multiple units. This
  implementation supports the named US-GAAP/DEI concepts only; IFRS and custom
  concepts are not silently translated. Financial normalization and semantic
  review remain open. Full offline run: **364 passed, 3 skipped**; the final
  strengthened legacy-cache regression also passed. No paid requests/deployment.

- Notification response batch: eight malformed-response regressions failed before
  validation was added. Non-object JSON, invalid success results/message IDs and
  malformed retry parameters now remain unknown without replay; processing can
  continue for later fills. A successful delivery requires a positive integer
  Telegram message ID. Confirmed rate-limit delays must be positive integers
  before applying bounded backoff. No message bodies, URLs or credentials are
  added to error logs. Fifteen notification tests pass; no Telegram calls made.
- Operator resolution of unknown delivery, trade-correction notices and verified
  independent outage alerts are still open; this response fix does not close
  item 13 or claim end-to-end delivery evidence.

## Unknown Telegram delivery resolution (local, not deployed)

`paper-agent --config config.toml resolve-notification private-resolution.json`
records an operator observation without contacting Telegram or the broker and
without changing the original unknown attempt. The JSON contains:

```json
{
  "notification_id": "the-original-fill-id",
  "attempted_at": "exact value copied from the unknown attempt",
  "outcome": "observed_sent",
  "message_id": 123,
  "evidence": "Operator's concise account of the private-chat verification"
}
```

`observed_sent` requires the actual positive message ID the operator verified.
If delivery cannot be established, use `closed_unconfirmed` and omit message_id.
That outcome does not claim delivery. Neither outcome authorizes a resend.
Never put tokens, chat credentials or sensitive message contents in evidence.
The resolution is stored separately with the original attempt and timestamp;
identical retries are idempotent, conflicting replacements are rejected, and the
same lock used by notification delivery prevents racing a send. This is an
operator attestation, not independent API verification of delivery.

- Resolution batch: 24 notification tests pass, including stale/missing attempts,
  invalid evidence/IDs, immutable original records, idempotency and a CLI test
  that fails if broker/data clients are constructed. Dispatch now closes its
  ledger connection on every exit. No real notifications were sent. Correction
  notices and independently verified outage alert delivery remain open.

- Correction-notice batch: confirmed or operator-observed original deliveries
  can now receive a distinct PAPER TRADE CORRECTION showing the current approved
  broker record, explicitly not a new trade. The original notification remains
  unchanged. A stable per-revision ID prevents replay even when a later approved
  correction restores the original quantity. Latest corrections require both
  successful reconciliation and the exact effective fill in the audited fills.
- Correction delivery shares existing bounded confirmed-429 retry and unknown
  delivery handling; ambiguous correction responses are not replayed. Notification
  resolution accepts the correction's notification_id. A first delivery after a
  correction records its revision ID, preventing a redundant correction alert.
  Intermediate revisions not observed between maintenance runs are not claimed
  as individually delivered; the notice identifies the current broker record.
- Twenty-six notification tests pass, including corrected/reverted quantities,
  failed reconciliation, missing audited fills and correction timeouts. All sends
  use mock transports. Independent outage-alert delivery is still unverified.

- Performance-report batch: daily reports now retain both the existing price-only
  series and a flow-adjusted distribution-inclusive series, with separate total
  return drawdown. Net dividends/withholding and broker fees enter through actual
  close equity; deposits and withdrawals are excluded from daily gain. Zero
  prior equity produces null returns rather than fabricated percentages.
- Independent multi-day accounting expectations verify a $100 distribution less
  $30 withholding and $10 fee yields $60 P/L despite a $500 deposit; a later
  $500 withdrawal is not a loss, while a $200 fee is. Chaining uses each prior
  close's capital base and tests the corresponding drawdown.
- These remain approximate end-of-day flow-adjusted returns. They exclude
  separately paid model/data/infrastructure costs and do not establish net alpha.
  Existing SPY/QQQ benchmarks are explicitly price-only and cannot be compared
  directly with the distribution-inclusive return. Dividend-adjusted benchmark
  evidence and forward net-cost effectiveness remain open; past immutable daily
  reports are not rewritten. Report fields are available in newly generated
  report JSON; no dashboard deployment or historical backfill was performed.

- Evaluator-contract batch: financial-provenance-v2 now requires supported
  valuations to reproduce source-bound financial inputs from the frozen original
  source, target period and cutoff, cite that source in valuation, and preserve
  the exact provenance record. Missing/forged facts, overwritten cash-flow inputs,
  unknown sources and absent valuation citations fail. The independent scenario
  arithmetic oracle remains separate; fact selection shares the production
  parser and therefore does not substitute for independent semantic review.
- Evaluation outputs identify their scoring contract. Paired comparisons require
  both outputs rescored under this contract; legacy scores cannot silently be
  used for promotion. Original frozen suite/recording files were not changed.
- Offline CLI replay of the legacy scripted fixture now gives 12/16 mechanical
  passes and zero sufficient-case completion: its four old valuation fixtures
  have no qualifying financial provenance. This is a fixture-contract finding,
  not a new model benchmark. Evidence: state/audit-20261106/evaluation-provenance-v2.json.
- Full suite **391 passed, 3 skipped**, Ruff passed, 29 evaluator tests passed.
  No model calls or broker access. A new richer frozen cohort and independent
  review remain necessary; this change does not declare research ready.

- Trigger-time batch: a future-dated thesis no longer suppresses research through
  an indefinitely negative debounce age; it requests a fresh review under the
  existing budget. Quotes beyond the existing five-second future tolerance or
  stale limit cannot trigger price research. News later than current time cannot
  trigger research, and the evidence provider excludes future and out-of-window
  articles from the seven-day bundle. No missing news is treated as proof that
  no material event exists. Four temporal regressions pass without model/order
  calls; broader timestamp, amendment and provider-failure coverage remains open.

- Independent-alert inspection: all six Cloud Monitoring policies are enabled
  (disk pressure, snapshot failure, health failure, spend threshold, missing
  heartbeat and stale outbox). Each targets the enabled infrastructure-owner
  email channel. Channel verificationStatus was absent, so channel metadata alone
  was not treated as proof of delivery.
- Actual delivered evidence was then verified read-only in relevant Gmail:
  the health incident has an alert at 2026-11-06T19:58:20Z and a resolved
  message at 19:59:14Z. Private message/incident IDs are retained locally.
  Both link to the same incident returned by Cloud Monitoring and pass Gmail
  DKIM/SPF/DMARC checks for google.com. The spending incident also has an
  authenticated delivered alert at 19:26:19Z, matched by its private incident ID.
- This proves independent service-health and spend email delivery for observed
  incidents, not every failure mode or operator response. No matching
  missing-heartbeat email was found in the bounded search after November 5;
  total-monitoring-outage delivery remains unverified. No emails were sent,
  changed, marked read or deleted; no alert policies or runtime state changed.
- Policies currently use a project-global metric series. That is appropriate to
  the observed one-executor deployment but does not distinguish competing hosts;
  host-specific monitoring/fencing must be resolved before a failover release.

- Review checkpoint: current full offline suite **395 passed, 3 skipped**;
  branch-instrumented combined statement/branch coverage **82%** (80% regression
  floor passed). Six selected safety mutations were detected again. Ruff lint,
  formatting and git whitespace checks passed. A fresh redacted Gitleaks scan
  of tracked/nonignored source, including new files, detected no leaks. Ignored
  credentials/runtime data were not copied into the scan or staged for Git.
- This checkpoint remains NO-GO. It does not deploy changes, enable live trading,
  establish investment edge, complete the quality cohort, verify all corporate
  actions/settlement paths, or prove cross-host fencing and failover.

- Remote CI checkpoint: backend and full-history secret scan passed. All four
  browser cases failed before assertions because Ubuntu denied the downloaded
  Chromium binary's sandbox namespaces. CI now installs packaged Chrome, which
  has Ubuntu's AppArmor profile, while retaining chromiumSandbox=true. The Linux
  browser test additionally requires renderer PID isolation, NoNewPrivs and
  seccomp filtering. Remote rerun is required before this gate can pass.

- Remote run 42306993412 passed backend, secrets and dashboard at commit
  60d8934. All four browser tests ran successfully with sandboxed packaged Chrome,
  including Linux PID namespace, NoNewPrivs and seccomp assertions. This verifies
  CI, not the deployed dashboard.
- Filing freshness: research-backed execution now retrieves fresh SEC submissions
  and rejects relevant filings/amendments accepted since evidence collection,
  including during the investigation. Missing, malformed, future-dated or
  unavailable filing evidence blocks execution. The check and matching filings
  are retained in the decision record; existing intents still reconcile without
  a new freshness check. Research results preserve the earliest collection/source
  timestamp instead of treating completion as the start of evidence freshness.
  Legacy results without collection timestamps require new research.
- This covers 10-K/Q, 8-K, 20-F, 6-K and amendments. It is not a guarantee of
  zero publication latency or coverage of every material disclosure. SEC describes
  the submissions API as updated throughout the day with possible processing
  delays: https://www.sec.gov/search-filings/edgar-application-programming-interfaces.
  No new model calls, broker orders or production deployment in this batch.
- Filing-freshness verification: 409 offline tests passed, three opt-in integrations
  skipped. An additional full-graph regression then passed proving the oldest
  source timestamp reaches the execution check. Ruff lint/format and whitespace
  checks passed. New remote CI/deployment verification remains pending.
- SEC freshness response validation now rejects empty histories, unequal/non-array
  columns, blank forms, invalid accessions (including old rows), and cutoffs beyond
  the recent-history window or in the future. Eight regressions prove these
  responses cannot be interpreted as absence of new filings. Current offline
  suite: 418 passed, three integrations skipped; combined coverage 82%, 80% floor
  passed. No production calls or deployment. Remote run 42307462274 for the prior
  filing commit was still running when this local verification completed.

- Remote CI run 42307608296 passed backend, dashboard and secret scanning for
  commit 2a3c6c5, including the filing-response validation changes.
- Research cohort preparation: annual-filings-v2 adds original FY2025 Apple and
  Microsoft annual filing text, exact-accession SEC financial records and two
  labeled missing-evidence variants. Independent fixed-number arithmetic and
  immutable suite/text hashes pass the 30-case evaluator test suite. Apple is
  development; Microsoft remains unrun holdout. No paid models were invoked.
- These are reconstructed historical reasoning exercises with hypothetical
  scenario prices, not archived point-in-time market data or a backtest. Both
  sufficient-case rubrics still need independent review, and two companies are
  insufficient for release promotion. Raw public-source hashes and retrieval
  limitations are documented in evaluations/README.md and its manifest.
- Microsoft original HTML exceeds the production 8 MB retrieval bound. Frozen
  extraction tests cannot establish production retrieval success. That remains
  an explicit ingestion gap; no safety bound was raised. Full goal remains NO-GO.
- Cohort commit 7b13ece passed all remote CI jobs in run 42308031727.
- Semantic-review boundary: found that candidate-embedded human_review could
  produce acceptance. Scoring contract financial-provenance-v3 now rejects that
  path. Independent review is a separate explicit CLI input, bound to exact
  suite, recording, case and candidate hashes; unknown IDs and stale hashes
  reject the replay. Partial reviews leave remaining cases pending, and results
  retain the review content hash. v2 comparisons require rescoring.
- Seven new regressions cover candidate self-approval and mismatched review
  bindings. Full offline suite: 426 passed, three opt-in integrations skipped;
  Ruff lint/format and whitespace checks pass. Operator verification of reviewer
  identity/independence is still required; hashes do not authenticate people.
  No actual semantic approval, model call, deployment or order occurred.
- Account funding gate: new purchases must fit both cash minus local open-order
  reservations and the broker's non_marginable_buying_power, including the fee
  buffer. Missing, malformed, non-finite or negative broker balances reject new
  exposure. Broker-reported holds are not subtracted twice. Owned protective
  exits remain available under all other risk gates when that balance is absent.
  Eleven regressions pass; full offline suite 437 passed, three integrations
  skipped, Ruff/format and whitespace checks pass.
- This is an additional conservative buying ceiling, not an assertion that
  equity settlement or live account eligibility is resolved. Alpaca's published
  support page says Trading API accounts are margin accounts; limiting spending
  to cash does not make the brokerage account a cash account. Its non-marginable
  buying-power formula is described in crypto support documentation. The actual
  account's stock settlement/restriction policy still requires broker-backed
  verification before a live release; no margin or account settings changed.
  References: https://alpaca.markets/support/alpaca-cash-accounts and
  https://alpaca.markets/support/can-i-margin-or-short-with-cryptocurrency.
- Semantic-review-boundary commit 37dd421 passed all remote CI jobs in run
  42308273734. New funding-gate code remains local/pending deployment verification.
- Adjustment approval integrity: exact retries are idempotent and conflicting
  rewrites of an approved activity adjustment are rejected. New approvals require
  the active experiment and explicit trading pause. Before saving any new entry,
  the candidate batch is replayed and matched to fresh broker account identity,
  unchanged/present activities, cash and share quantities. Non-finite deltas are
  rejected before persistence. Originals and the pause remain intact.
- The existing acknowledge-adjustment JSON file may now contain either one object
  or a list of objects for related unresolved activities. A single incomplete
  approval fails without saving; submit all required related adjustments together.
  Retrying the same batch is safe. Approvals and audit receipts now commit as one
  SQLite transaction (see the crash regression below). Approved entries cannot
  be silently edited; a later
  genuine correction requires a separately audited correction design.
- Replay now rejects corporate actions that remove all shares but leave orphaned
  cost basis. Matching cash/shares still does not establish economic correctness
  of a basis allocation, realized gain, merger or spinoff; those need independent
  broker-supported review. Full corporate-action/bust support remains open.
- Full offline suite passed 448 tests with three integrations skipped; an additional
  missing-broker-activity case then passed in the 18-test accounting matrix.
  Ruff/format/whitespace checks passed. Funding-gate commit 05be23f passed all remote
  CI jobs in run 42308483653. New adjustment code is not deployed; no orders made.
- Actual adjustment SIGKILL regression reproduced a missing audit receipt: the
  first approval document survived termination between its write and journal log,
  while exact retry skipped that receipt. Adjustment batches now insert all
  approval documents and journal receipts in one SQLite transaction. Conflicting
  inserts fail instead of overwriting history; telemetry emission follows commit.
- Seven real subprocess crash drills now pass. The two new checkpoints terminate
  during the approval transaction (no approvals or receipts survive) and after
  commit (both approvals and both receipts survive). Reopened databases pass
  integrity checks; exact retries yield two receipts, original experiment/pause
  remain intact and broker submission count stays zero. These are loopback
  process tests, not cloud failover or proof of atomic external telemetry delivery.
- Full offline suite: 451 passed, three opt-in integrations skipped. Ruff lint,
  formatting and whitespace checks pass. No production deployment or orders.
- Cumulative portfolio loss gate: successful reconciliation computes broker equity
  minus original experiment capital and audited external capital flows. A loss of
  10% of original capital (provisional configurable paper default) latches a block
  on new purchases. Deposits cannot dilute the threshold and withdrawals are not
  investment losses. Recovery/restart does not clear the latch; explicit operator
  resume can release it only after reconciliation shows the breach has ended.
  Reports expose the state and independent health checks flag operator attention.
- Eight regressions cover threshold boundaries, deposits, withdrawals, restart,
  explicit recovery, unrealized losses with zero model budget, and protective
  exits. Existing exit gates remain; reconciliation never liquidates positions.
  This gate does not cancel already-working orders, implement a daily loss or
  high-water drawdown limit, or count separately paid operating costs. The 10%
  setting is not an approved live risk parameter. Production remains unchanged.
- Prior adjustment-transaction commit 9595c3a passed remote CI run 42309044206.
- Full offline suite after the loss gate: 459 passed, three opt-in integrations
  skipped; Ruff lint/format and whitespace checks passed. No paid model calls,
  broker orders or deployment occurred. New gate deployment verification pending.
- Daily loss gate now uses broker last_equity and balance_asof matched to the
  prior trading date in the broker calendar, rather than first-observed equity.
  A read-only paper-account check at 2026-11-06T23:08:40Z confirmed these fields
  are supplied and balance_asof matched November 5. Official account reference:
  https://docs.alpaca.markets/us/reference/getaccount-1 (last_equity is prior-day
  16:00 ET equity). This observation does not verify every account/market regime.
- The provisional paper daily threshold is 2% of prior-close equity. Audited
  CSD/CSW flows after the prior close are excluded. Date-only flows on the cutoff
  date, missing/stale/non-finite baselines, unexplained intraday_adjustments and
  prior early closes block new exposure instead of guessing. Prior early-close
  baseline semantics and other transfer activity types still need broker-backed
  evidence. Date-only CSD/CSW uses its broker activity date as the economic date;
  delayed/backdated posting semantics remain a live-release verification gap.
- A valid baseline is frozen per New York date; subsequent broker revisions
  cannot silently reset it, even after an invalid observation. A breach latches
  across restart/day rollover; explicit resume needs a valid unbreached daily
  and cumulative check. Protective exits retain existing gates; this still does
  not cancel working orders or guarantee a maximum realized loss through gaps.
- Eighteen additional regressions cover the daily boundary, unavailable baseline,
  health signal, frozen-baseline revisions, restart/day rollover, weekend/DST
  cutoff and capital movements. Full offline suite: 477 passed, three opt-in
  integrations skipped. Earlier cumulative-loss commit 81e0488 passed remote CI
  runs 42309685494 and 42309677847. Daily changes are not deployed; no paid models
  or orders were used. All sixteen requirements remain active; status NO-GO.
- Eight selected safety mutations were caught, including disabled loss-latch
  entry enforcement and disabled daily-baseline entry enforcement. This tests
  the actual execution rejection path, not just calculation output.
- Working-order loss response: maintenance now requests cancellation of working
  buys when the loss latch is set or the daily baseline is unavailable, including
  a block first detected during that maintenance pass. Working sells remain
  governed by existing expiry/pause rules. Broker pending_cancel suppresses
  duplicate requests; an ambiguous timeout retains the reservation and may be
  retried on the next maintenance pass after another broker open-order query.
- Five new regressions exercise a fill racing the cancellation request, partial
  fill with pending/confirmed cancel, unavailable-baseline cancellation, timeout
  recovery and preserving a working protective sell. Confirmed fills remain held;
  no replacement/liquidation occurs. The scripted broker's cancel timestamps now
  advance from its latest order timestamp, avoiding accidental stale fixture data.
- Full offline suite passed 481 tests with three integrations skipped; adding
  unavailable-baseline coverage then passed all 31 loss-control tests. Ruff and
  whitespace checks passed. Daily-loss commit 7317473 passed remote CI run
  42310234303. These changes remain undeployed and cannot guarantee cancellation
  before a fill; missing/outage detection and broker cancellation latency remain
  real exposure. No production broker mutation or paid model call occurred.
- Nine selected safety mutations were caught; disabling loss-triggered buy
  cancellation caused four test failures. The unmodified mutation baseline passed.
- Recovery audit found existing daily backups were returned without revalidation.
  Reuse now requires a private non-symlink regular file, SQLite integrity and an
  exact active-experiment match. Mismatches/corruption are retained for inspection,
  never overwritten. New publication flushes the closed database before atomic
  rename and its directory afterward. Flush failures propagate, clean temporary
  files and preserve the source; post-rename failure retains the published copy
  for verification on retry. This is not a physical power-loss/storage guarantee.
- Restoring a snapshot from before an intent while the broker has since filled
  that order now has explicit regression evidence: reconciliation and repeated
  execution attempts both reject the unapproved fill; no order is repeated and
  the pause, original experiment and retained post-backup source survive.
- Full offline suite passed 490 tests with three integrations skipped. An added
  broken-symlink case then passed the recovery suite. Ruff/whitespace checks pass.
  Previous cancellation commit 91cf1bd passed remote CI run 42310499036. No new
  code deployed and no broker mutations/models used. Cross-host fencing, actual
  failover and restored-cloud-ledger reconciliation remain open requirements.
- Actual cloud recovery check at 2026-11-06T23:18:59Z found the published daily
  backup predates a subsequently approved/finally filled paper order: the backup
  had zero intents and rejected the broker fill as lacking an approved order.
  Integrity alone therefore did not establish recovery readiness. A current-ledger
  SQLite snapshot reconciled twice successfully with the same broker. No missing
  intent was invented, imported or bypassed to make the old backup pass.
- Preserved a new private recovery-20261106T231945Z.sqlite beside, not over, the
  existing backup. The new file was made by SQLite's backup API, integrity-checked,
  flushed and published without overwriting existing files. At 23:20:01Z a separate
  scratch copy of that saved artifact reconciled twice against the actual paper
  broker; its original experiment remained intact and its reservations were zero.
  The preserved backup hash remained unchanged across verification.
- Both checks used the deployed pinned image and isolated temporary ledgers and
  control directories. A custom HTTP transport rejected all non-GET traffic and
  all hosts except paper-api.alpaca.markets; no model/data/notification calls ran.
  Original ledger, old backup, deployment and production pause were untouched.
  Private response evidence is retained in ignored state/audit-20261106 files.
- This verifies current paper-broker convergence of a specific saved cloud-host
  artifact, not the pending code or a booted disaster-recovery host. The artifact
  remains on the state disk; disk-loss/off-host recovery and cross-host fencing
  remain unverified. The discovered backup recovery-point gap requires durable
  pre-submission intent checkpoints/replication, not merely more frequent reports.
- Local execution checkpoints now close the demonstrated intent-backup gap:
  after persisting the exact prepared intent and decision audit, each new order
  requires a verified, flushed immutable intent checkpoint before its broker POST.
  Existing files must match both original experiment and exact prepared intent.
  Checkpoint failure records a rejected intent without calling the broker; replay
  of that same key does not retry. Shared risk checks run again after disk work.
- A real SQLite restore regression leaves the daily backup older than the order,
  then restores the automatically created checkpoint after a broker fill. Original
  experiment/pause survive; repeated reconciliation/execution imports the fill
  once and never resubmits. Additional regressions prove full-disk and changed-
  checkpoint rejection plus stale-quote/pause rejection after checkpoint work.
- Full offline suite: 496 passed, three opt-in integrations skipped; Ruff and
  whitespace checks passed. Prior recovery-evidence commit 74cfabe passed both
  remote CI runs 42311229341 and 42311221628. New code remains undeployed.
- These checkpoints are on the same state disk and do not establish off-host
  durability, power-loss hardware behavior, cross-host fencing or live readiness.
  A checkpoint can predate a POST that never occurred; an unresolved prepared
  intent remains blocked pending broker/operator investigation. Automatic deletion
  is absent and checkpoint storage growth needs operational monitoring.
- Off-host checkpoint replication implemented as a required step when
  checkpoint_bucket is configured: GCE metadata credentials, fixed GCS endpoints,
  disabled redirects/proxy inheritance, create-only generation precondition,
  exact identity/size verification and streamed SHA-256 read-back of the saved
  generation. Transport timeouts resolve by read verification without a second
  POST. Errors reject before broker submission; receipt is journaled before POST.
- Twelve regressions cover new/existing/ambiguous uploads, missing objects,
  metadata authentication, redirect rejection, corrupt/truncated/oversized data,
  wrong identity, required-storage failure and receipt-before-submission ordering.
  Full offline suite: 508 passed, three integrations skipped; Ruff/whitespace pass.
- Provisioned private regional GCS recovery storage with uniform bucket access,
  enforced public access prevention and seven-day soft deletion. Effective VM
  identity permission check returned only object create/get/list among requested
  permissions: no delete, object update, bucket update or policy update. Project
  owners/admins retain their privileges; this is not retention-locked storage.
- Actual replication of the previously reconciled recovery backup succeeded:
  38,539,264 bytes, exact generation read back and SHA-256 verified. Downloading
  that generation onto the local host independently matched the hash and passed
  SQLite integrity, retaining one experiment, two intents and two activities.
  Private receipts and restored file remain ignored under state/audit-20261106.
- Production trading configuration/image are unchanged; automatic required
  replication is not deployed/enabled yet. Config omission remains local-only for
  offline/local paper use and is explicitly disallowed by the live-release contract.
  Full disk-loss failover, independent executor fencing and project-disaster
  recovery remain unverified. Per-order storage/network cost and ledger growth
  need monitoring; model-spend limits do not cover those infrastructure charges.
- Cloud ownership guard added behind executor_fence (requires checkpoint_bucket).
  Before broker mutations it verifies GCE metadata instance/project/zone against
  a private pinned executor-owner.json. Wrong/unavailable ownership rejects before
  a broker HTTP request; read-only reconciliation remains possible. Known guard
  denials are recorded as rejected intents, not ambiguous submissions. Shared
  metadata-token handling retains fixed endpoints, no redirects/proxy inheritance.
- Eleven regressions cover VM/project/zone mismatch, malformed/missing records,
  timeout/redirect rejection, POST/DELETE transport exclusion, available reads
  and intent/reservation handling. Full offline suite: 519 passed, three skipped;
  Ruff and whitespace checks pass.
- Created the initial owner record with create-only generation precondition for
  the current VM. A read-only execution of the new guard on that VM successfully
  matched its actual metadata identity to the record. Production image/config
  remain unchanged: this is not yet active protection on production submissions.
- No automatic takeover or lease expiry exists. This is an ownership admission
  guard, not proof of complete distributed fencing. Old-host shutdown must be
  verified before any ownership transfer; in-flight broker calls need reconciliation.
  Transfer tooling, cross-host partition/restart drills, and a final risk freshness
  check after ownership-network I/O remain open before deployment/live acceptance.
- Closed the transport-freshness gap above: the broker protocol now supports a
  before_send callback, invoked by the actual Alpaca adapter after cloud ownership
  verification and immediately before HTTP submission. Service reruns shared risk
  checks using the saved evidence and current time/pause state at that boundary.
  Rejections and callback faults are recorded as known pre-submission rejections;
  they never issue the order HTTP request or retain an ambiguous reservation.
- Six actual-adapter/mock-transport regressions advance time or pause/fail the
  risk check during ownership verification. They prove stale broker clock, stale
  quote, closed regular session, pause and risk exceptions cannot reach POST;
  the healthy path submits once. The scripted broker, protocol, wrappers and
  subprocess RPC adapter all honor the callback; seven SIGKILL drills still pass.
- Full offline suite: 525 passed, three integrations skipped; Ruff and whitespace
  checks passed. Previous owner-guard commit 77bd6c8 passed both remote CI runs
  42312717114 and 42312711359. No cloud changes, deployment or paid calls occurred
  in this step. Network transit after dispatch remains outside the local check;
  broker regular-session/limit behavior and cancellation reconciliation still apply.
- Ten selected safety mutations were caught. Disabling the actual adapter's
  final callback caused five failing transport-boundary regressions.
- Added explicit CLI ownership transfer planning/application. It reads the owner
  at an exact object generation, verifies pinned source identity, refuses every
  source state except TERMINATED, requires a RUNNING target, rechecks source
  shutdown immediately before publication and applies with the reviewed generation
  precondition. The new record retains previous identity/generation and the
  command verifies read-back. It never stops/starts a VM or clears a pause.
- Fourteen regressions cover read-only planning, RUNNING/STOPPING/SUSPENDED/
  REPAIRING/UNKNOWN source states, timeout, changed VM identity, source restart,
  stopped target, missing/stale generation, update conflict without retry and
  successful conditional transfer/audit/idempotence. Full offline suite: 539
  passed, three opt-in integrations skipped; Ruff/whitespace checks passed.
- Ran the real CLI in read-only mode against the current VM/owner object; result
  was unchanged at the existing generation. No ownership transfer, shutdown,
  startup, deployment or broker mutation occurred. Prior transport-preflight
  commit d8a9803 passed remote CI runs 42313063339 and 42313058535.
- This is not a completed two-host failover drill or autonomous leader election.
  Operators must coordinate against concurrent old-host restart, verify the new
  paused ledger and reconcile in-flight requests. Cross-host fault evidence and
  production enablement remain open; all sixteen requirements remain active.

- Actual isolated two-VM ownership drill on 2026-11-06 used temporary Debian VMs
  without external IPs or broker credentials, a separate bucket and a dedicated
  service account with only bucket object-read access. The exact current guard
  and its Python dependencies were copied over IAP; private manifests retain
  source hashes, VM identities, transfer generations and raw outcomes.
- Source A passed the guard; standby B failed specifically for identity mismatch.
  The real transfer CLI blocked while A was RUNNING. After stopping A, the CLI
  verified TERMINATED and conditionally transferred the reviewed object generation
  to B. B then passed. Restarting A preserved its identity but the guard denied
  it as the former owner while B continued to pass. An initial SSH boot-readiness
  failure was retained separately and was not counted as a guard rejection.
- Removing the drill identity's bucket-read grant caused an actual GCS 403 and
  ExecutionFenceError on B; restoring the grant restored admission. This is an
  IAM-loss drill, not a network partition or stale-connection/in-flight POST test.
- No production executor stop/start, ownership change, deployment, broker request,
  model call or trading-pause change occurred. This establishes cross-host
  admission and guarded transfer behavior, not complete trading-service failover.
  The remaining release gates above stay open; status remains NO-GO.
- Cleanup verified: both drill VMs and their auto-deleted boot disks are absent;
  the temporary bucket and service account were deleted. The production VM
  remains RUNNING. Raw private evidence remains in ignored state/fencing-drill.
- Daily-loss boundary audit found duplicate calendar rows could select the first
  close and hide a conflicting early close. Calendar dates now require canonical
  dates and unique rows before selecting the baseline. Capital movements exactly
  at the baseline timestamp are rejected because inclusion in last_equity is
  ambiguous. Three new negative cases failed before the fix; five regressions
  now cover these boundaries and actual entry rejection without a broker POST.
- Rechecked [Alpaca account documentation](https://docs.alpaca.markets/us/reference/getaccount-1):
  last_equity is documented at 16:00 ET on the previous trading day. This is not
  evidence that an early-close session should move that timestamp to 13:00.
  Early-close support remains explicitly blocked pending broker evidence; this
  change does not silently relax that limit or certify settlement semantics.
- Full offline verification: 544 passed, three opt-in integrations skipped;
  Ruff and whitespace checks passed. No deployment, model calls or broker
  requests occurred in this boundary fix.
- Financial provenance audit found independent newest-fact selection could mix
  amended CFO with capex/shares from an older filing without evidence that the
  reporting bases remain consistent. The narrow proxy now requires the selected
  annual inputs to share accession, filing date and form, in addition to period
  and units. It does not silently fall back to older pre-amendment numbers.
- Three mixed-basis regressions failed before the fix; a tool-path regression
  also verifies that no calculation is recorded. Complete coherent restatements
  remain supported, and the actual frozen annual-filing cohort still passes its
  deterministic input tests. Research instructions disclose the requirement;
  evaluation scoring contract advances to financial-provenance-v4 so old scores
  cannot be compared as if evaluated under the stronger contract.
- A partial amendment may legitimately leave other figures unchanged. Resolving
  that requires original-document review and an explicit financial bridge; this
  narrow derivation conservatively reports unsupported rather than assuming it.
  This does not complete debt/SBC normalization or independent economic review.
- Full offline verification: 548 passed, three opt-in integrations skipped;
  Ruff/whitespace checks pass. No models, broker requests or deployment occurred.
- Release preparation pinned Python 3.12-slim and uv 0.9.28 to resolved multiarch
  manifest digests. Cloud Build 937bb1e4-5070-448f-8746-945e774f85df built source
  commit 2713a92 from a 31-entry allowlisted Git archive (Dockerfile, Docker ignore,
  lock/project files and trader Python source only). No runtime state or secrets
  were uploaded. Build succeeded; executor image digest is
  sha256:e50f1980c6ebb42aff09f4368dad9eb250703f05a33ac4d68d140d3957b25dcb.
- CI run 42314789529 for that commit passed backend, dashboard and secrets jobs.
  VM image pull initially lacked registry authentication; retry used its metadata
  service identity and a temporary Docker auth directory removed on completion.
  No credential was printed or installed in the image. An isolated container
  with no network/credentials/ledger mounts verified all 26 installed source-file
  hashes against local reviewed source and the paper-only default endpoint.
  An initial validation command used a nonexistent Settings attribute and failed;
  its corrected hash/endpoint check passed. Private build receipts are retained
  under ignored state/release-2713a92. This is artifact verification, not deployment.
- Deployment preflight contradicted the assumed pause state: the active account's
  pause marker was absent. Restored it with exclusive creation and file/directory
  fsync, and journaled readiness_pause_restored without broker/model calls. The
  original ledger and experiment were preserved. No claim is made about why the
  marker was absent; investigate prior runtime/operator events before activation.
  Production remains on the previous image; new code is not yet deployed.
- Pause investigation found journal kill_switch_resumed at 2026-11-06T20:00:47Z,
  following a kill at 19:59:37Z; this predates the readiness goal. The available
  journal supports explicit resume, not unexplained file deletion. The restored
  readiness pause was present on follow-up. Operator identity is not recorded in
  that event, so this does not independently establish who authorized the resume.
- Deployed the CI-verified executor image built from 2713a92 at
  2026-11-06T23:58:02Z, digest
  sha256:e50f1980c6ebb42aff09f4368dad9eb250703f05a33ac4d68d140d3957b25dcb.
  Stopped the old service before changing its pinned image/config. Preserved a
  SQLite integrity-checked ledger backup, complete control directory, old config
  and service environment under private state/release-rollback-2713a92. Original
  experiment/history and pause remain intact. Candidate config validated inside
  the exact image without network, credentials or operational state.
- Enabled executor_fence and checkpoint_bucket for the existing private recovery
  bucket. The deployed guard successfully matched actual VM metadata to ownership.
  Post-start heartbeat/reconciliation at 23:58:22Z succeeded; daily baseline was
  valid and unbreached, cumulative latch false, held-position review issues empty.
  The deployed health command passed. Before/after counts stayed at 8 acceptance
  runs, 2 activities, 2 intents and 1 order; AAPL remained one share and reconciled
  cash was 99655.67. No supervised order test or paid research was initiated.
- This deploys the executor changes, not the dashboard. Actual order-path required
  checkpoint behavior has offline and isolated cloud evidence but no new broker
  submission was used to test it in production. Full service failover, live
  account constraints and independent investment-quality/forward evidence remain
  open. Keep the release paused; overall status remains NO-GO for live money.

- Dashboard release: pinned both Node build/runtime base images to a resolved
  multiarch digest. Cloud Build 5e8028c0-ae78-4ff8-a7ed-c39f917e38e6 built
  reviewed commit 279ba41 from an allowlisted tracked-source archive without
  local environment, dependencies or runtime state. Build image digest:
  sha256:ad9a19e6eae40f84aaed0ffa95139a2c3f7d8950012472b303f56f7b54645dab.
  CI run 42315434755 passed backend, dashboard and secrets checks.
- Staged Cloud Run revision trader-dashboard-00006-vix at zero traffic, preserved
  existing secret references and configuration, then verified actual browser login,
  anonymous API denial, six pages/557 unique events, authenticated full-event access,
  no-store responses, malformed cursor rejection and logout denial. Only then
  promoted it to 100% production traffic; previous revision remains available.
- Repeated checks on the normal production URL passed. Additional rendered-UI
  verification loaded all 557 events through the pagination button and found no
  horizontal overflow at 390px. Checked at 2026-11-07T00:02:57Z. Credentials were
  retrieved into process memory for login, never printed or embedded in the image.
  Private receipts live in ignored state/dashboard-release. No trading settings,
  broker orders or model investigations changed in this dashboard rollout.

- Independent missing-heartbeat drill stopped only the metric publisher at
  2026-11-07T00:04:20Z while the paused executor continued reconciling. A separately
  verified transient twelve-minute restoration timer protected against interrupted
  observation. Existing five-minute absence policy and notification channel were
  unchanged. Cloud metrics confirmed the last healthy sample at 00:03:19Z.
- Missing-heartbeat incident opened at 00:10:31Z, about 7m12s after the last sample.
  The actual received email linked to that exact Cloud Monitoring incident and
  passed Gmail DKIM/SPF/DMARC checks. Only relevant alert/recovery emails were read; no
  manual email send, modification or mark-read operation was performed. Detection was slower
  than the configured window, consistent with additional evaluation latency; do
  not promise five-minute delivery from this configuration.
- Publishing was restored with a successful service invocation and a fresh healthy
  cloud sample at 00:10:49Z. Both executor and health timer are active, pause remains
  present, and the transient restoration timer was stopped after recovery. Private
  receipts live under ignored state/heartbeat-drill. This is publisher-outage
  evidence, not proof of whole-host/zone failure or every notification channel.
- The missing-heartbeat incident closed automatically at 00:13:51Z, about three
  minutes after the first restored sample. A matching RESOLVED email was actually
  received and passed Gmail DKIM/SPF/DMARC checks. No manual incident closure or
  synthetic recovery metric was used. Publisher-outage detection, notification
  delivery, sample recovery and automatic resolution are verified for this drill.

- Operating-cost reporting (local, not deployed): portfolio reports and dashboard
  snapshots now include broker P/L, recorded operating-cost estimates, and P/L
  after those recorded costs. The shared spend breakdown counts model, X, legacy
  Flovia and manual data entries consistently. Broker fees remain included once
  through broker P/L; reservations are not expenses. Unresolved research runs are
  counted across dates for the current experiment, excluding other experiments.
- This is explicitly partial, never an all-in return: unrecorded infrastructure,
  subscriptions and separately logged evaluation costs remain excluded. Manual
  entries must not overlap request charges. Empty cost history does not certify
  zero economic cost. No existing NAV, daily-return history or ledger is rewritten.
- Two new regressions failed before implementation and pass afterward, covering
  fee double-counting, missing costs, reservation scope and empty history. Full
  offline suite: **550 passed, 3 skipped**; Ruff and whitespace checks pass.
  Dashboard rendering, complete cost attribution and deployment remain open.

- Cost-result dashboard (local, not deployed): overview now renders broker P/L,
  recorded operating costs and P/L after those costs in one explicitly partial
  panel. Unpriced requests, unresolved runs and missing infrastructure costs stay
  visible. Older snapshots show unavailable rather than inventing a zero result.
  Production build/typecheck passed; all five dashboard tests passed with isolated
  Postgres; all four browser checks passed, including new cost-result assertions,
  legacy snapshots and mobile layout. Disposable test database removed afterward.
  Complete cost allocation and deployment remain open; no trading state changed.

- Cost reporting deployed from `09c8f78` after CI run `42317767744` passed all
  backend, dashboard and secret checks. Allowlisted source builds excluded runtime
  state and credentials. Agent build `dd98c0c7-765f-46f0-88ad-f83398115469` produced
  digest `5e9dac8663d95ac00a1f64bc3bb37b6cf6a0c307124983b932a04e24638cca73`;
  all 26 installed Python source hashes verified in a network-isolated container.
  Dashboard build `fe386607-0f24-4f1a-b481-09ac5f683a35` produced digest
  `f8f614407f4853950fad5cd6c6ee5d5ad97c6651637e26f60c494fd9802e6dfc`.
- Executor rollout preserved config hash, pause, original experiment and ledger.
  Private rollback files retained; no ledger restore occurred. Post-rollout health
  and reconciliation passed; counts stayed at eight acceptance records, two
  activities, two intents and one order. No model or order test was launched.
- Dashboard revision `trader-dashboard-00008-vib` passed staged authenticated
  acceptance before receiving 100% production traffic. The first check encountered
  an older snapshot without the new field; a subsequent fresh maintenance export
  passed, with no export errors. Production verification confirmed the rendered
  partial-cost panel, arithmetic, mobile width, anonymous denial and logout.
  Snapshot showed $39.000661 recorded operating costs, 13 unpriced requests and
  four unresolved research runs. These are experiment totals, not this rollout's
  costs or a full billing reconciliation. Evidence: `state/cost-release-09c8f78/`.
  Trading remains paused; full cost attribution and the remaining release gates
  are still open.

- Evidence-boundary regressions (local, not deployed): found and reproduced four
  cases where an empty/whitespace filing or offset beyond its end generated a
  registered citation ID. The passage tool now records an explicit evidence gap
  without registering a source. Three further regressions proved already-saved
  empty passages could pass report validation; shared validation now rejects and
  retains those rejected reports without changing the original source history.
- Five hostile-reference cases verify that instructions embedded in source text
  cannot turn read_source/read_document into arbitrary file, metadata-service or
  network fetches. Even an undiscovered SEC URL remains unavailable. Original
  hostile text is retained as evidence. These tests exercise tool boundaries,
  not real-model resistance to misleading claims or independent semantic review.
- Full offline suite: **562 passed, 3 skipped**; Ruff and whitespace checks pass.
  No external provider/model requests or trading-state changes. Deployment and
  the broader adversarial/combined-failure matrix remain open.


- Whole-host monitoring drill: with fresh reconciliation, pause present and no
  working orders, stopped the original VM. Google Cloud confirmed TERMINATED at
  2026-11-07T00:30:31Z. An independent local watchdog bounded restart to twelve
  minutes; the drill observed the unchanged cloud absence policy and restarted
  earlier when a new incident opened. No policy thresholds were shortened.
- Last pre-stop metric was 00:29:14Z. Incident `0.oeq3kfcp7xvb` opened at
  00:36:27Z (about 7m13 after the last sample), and authenticated alert email
  `1a1b36f73c095b62` arrived with that exact incident link. Gmail was read-only.
  Original VM ID 3629391648980512917 returned RUNNING at 00:36:53Z; no replacement
  executor, ledger restore or new order was created. The transient watchdog exited.
- Initial IAP SSH attempt preceded SSH startup; a later connection succeeded.
  Boot cleared the temporary audit script, which was recopied. Both executor and
  health timer became active; health and fresh broker reconciliation passed.
  Pause, one-share AAPL position and counts (acceptance8/activity2/intent2/order1)
  were unchanged. The expected `5e9dac86...` artifact remained deployed.
- Automatic metric publishing recovered (healthy1 at00:40:08Z), respecting the
  configured three-minute monitor boot delay. Incident closed automatically at00:44:01Z; matching authenticated recovery email
  `1a1b376611626b4e` was received. No manual incident closure occurred.
  This demonstrates same-host restart and independent detection, not a zone outage,
  destructive disk failure or replacement-host takeover with an in-flight order.
  Private evidence is retained under `state/host-outage-drill/`.
- Updated stale README claims about prior paper acceptance, cloud monitoring,
  scheduling and protective exits to match current behavior and link the evidence.

- Research timestamp boundary (local, not deployed): ingestion previously filtered
  filings by calendar date while execution freshness used acceptance timestamps.
  Both now share validation of material filing accessions, aware acceptance times
  and consistent submission arrays. Research pins the cutoff before requests;
  delayed requests cannot admit later filings or news into that evidence snapshot.
  Same-day date-only company facts are conservatively excluded and the limitation
  is disclosed in the source. Cache schema3 forces older bundles to be refetched.
- Six negative regressions reproduced invalid/future acceptance and moving-cutoff
  failures. A positive regression preserves a verified same-day accepted filing,
  older facts and pre-cutoff news while excluding same-day date-only facts and
  news arriving during retrieval. Full offline suite: **569 passed, 3 skipped**;
  lint and formatting pass. No provider/model calls, orders or deployment changes.
  This does not establish a historical point-in-time database or prove that later
  vendor edits to old articles are unavailable; that broader provenance gate remains.

- News revision boundary (local, not deployed): two regressions showed execution
  could submit on news published or revised after evidence collection but before
  research completion. Execution now compares returned news against evidence
  collection time. A shared timestamp validator uses updated_at when supplied,
  rejects updates predating publication, and is used by ingestion, scheduler and
  deferred-decision checks. Source records retain original publication and update
  timestamps; post-cutoff revisions cannot enter a research snapshot. Schema4
  invalidates older evidence caches. Original journal history remains unchanged.
- Seven added cases cover publication/revision during research, pre/post-cutoff
  revisions and malformed/naive/reversed timestamps. Full offline suite passed
  **576 tests, 3 skipped**; affected suites passed again after the cache bump.
  No external model/data calls or orders. Alpaca's
  [news reference](https://docs.alpaca.markets/us/reference/news-3) documents sorting
  by update date. These tests prove treatment of returned revisions, not complete
  retrieval: start-filter semantics for old revisions and the current 50-item
  unpaginated query still need verification/remediation before claiming complete
  news freshness coverage. Missing updated_at retains publication-time behavior;
  upstream omission cannot prove that an article was never revised.

- News pagination (local, not deployed): eight new regressions reproduced first-
  page truncation, malformed page acceptance and unhandled continuation bounds.
  Adapter now follows continuation tokens with a fixed end timestamp, validates
  page/list/token shapes, rejects repeated tokens and fails after 100 pages
  instead of returning partial coverage. Valid empty lists remain supported.
  The source coverage note no longer describes a single 50-article page.
- A service-level integration fixture also verifies second-page HTTP failure
  aborts before any prepared intent or broker submission. Full offline suite:
  **585 passed, 3 skipped**; lint, formatting and whitespace pass. Requests use
  isolated mock HTTP transports and fixture credentials, not provider traffic.
  This closes local pagination handling, not vendor completeness: update-window
  semantics for old revisions and entitlement/delivery delays remain unverified.
  The fixed-window pagination is not a provider-guaranteed atomic snapshot.

- Evidence batch deployed from `07b4d71` after CI `42320915120` passed backend,
  database, mutation, dashboard/browser and secret checks. Cloud Build
  `e90de396-cdb4-4657-ad29-f80a5db111eb` used 30 allowlisted tracked files,
  excluding credentials/runtime state, producing agent digest
  `811cadda599e766be764b2a23ba5e2e0c2bcce87ed4e7c5967f777a722ecac15`.
  All 26 installed Python source hashes matched in a network-isolated container.
- Rollout retained config hash `d255b4f99146aeb11df5092a0c73168fed600334e28ea8f792e83235d9fec76a`,
  the pause, original experiment and ledger. Private rollback snapshot retained;
  no ledger restore occurred. New-container health and reconciliation passed at
  2026-11-07T00:58:41Z. Counts remained acceptance8/activity2/intent2/order1,
  with no working orders and one AAPL share. Dashboard image was unchanged.
  Receipts: ignored `state/evidence-release-07b4d71/`.
- This deploys the empty-passage, fixed-cutoff, revised-news and pagination fixes;
  it does not close the outstanding vendor-coverage, independent semantic-review,
  replacement-host in-flight recovery or real-money approval gates. No research
  investigation or order test was launched during the rollout. Trading is paused.

- Financial accession validation (local, not deployed): five negative tests
  demonstrated that arbitrary strings, integers and booleans could be treated as
  an original filing accession when all selected facts matched. Shared annual
  derivation now requires the exact ASCII-digit accession format before selecting
  a fact. Both research calculations and evaluation replay use this validation.
  Amendment tests now use well-formed distinct accessions while preserving their
  partial/mixed-basis rejection checks. Full offline suite: **590 passed, 3 skipped**;
  lint and whitespace pass. No model/provider requests or trading changes.
- Syntactic accession validation does not verify filing existence or establish
  economic normalization. Debt, dilution/SBC, nonrecurring adjustments and separate
  semantic review remain required; no fair-value certification is inferred.

- Dashboard authentication matrix (local, not deployed): a regression reproduced
  acceptance of correctly signed session tokens with trailing empty separators.
  Parser now requires exactly two token components. This was permissive parsing,
  not evidence that an attacker could forge a session without the secret.
- Production build/typecheck and two session unit tests passed. A new HTTP test
  against the actual standalone server verifies eight invalid/expired/future/
  wrong-secret/tampered token cases across overview, trace pagination, full event
  and homepage paths (32 API denials plus eight redirects). A valid signed token
  successfully reaches the homepage as a positive control. Tests used fixture
  credentials and an intentionally unavailable localhost database; no production
  database or account credentials were used. The new test is included in CI.
- Logout currently deletes the client cookie; it does not revoke a copied token
  on the server. Password rotation invalidates all signatures, but per-session
  revocation and online authentication abuse/load handling remain open security
  requirements. This matrix does not constitute a penetration test.

- Forward-paper acceptance protocol declared in `evaluations/forward-paper-v1.json`
  and explained in `docs/evaluation.md`. Fixes previously unspecified observation
  window, information minimums, primary/secondary baselines, cost scope, uncertainty
  method, change handling and inconclusive/stopped outcomes before study results
  exist. Declaration status is not_started, not a passing evaluation. Current
  price-only benchmarks/partial costs cannot satisfy it. No start session selected,
  paid research performed, orders submitted, portfolio reset or pause cleared.
  Total-return/cost and numerical analysis implementation, independent review and
  prospective observations remain open; no investment edge is claimed.

- Normalization gate (local, not deployed): final acceptance previously required
  calculated valuation plus critic approval but no explicit accounting review.
  Added source-cited structured review of cash-flow basis, debt/cash, dilution/SBC
  and nonrecurring items. Missing, blank, uncited or declared unresolved reviews
  block BUY/ADD in final synthesis and cached execution. Evaluator applies the same
  predicate under financial-provenance-v5; old recordings/history are unchanged.
- Regression evidence covers cached legacy/missing reviews, material gaps, blank
  treatment, unknown citations, a lead attempting to override missing/gapped
  review, and evaluator parity with original recordings preserved. Synthetic
  positive controls provide explicit fixture assumptions. Independent semantic
  review and source-bound normalized adjustments remain open: this is not a model
  self-certification of fair value. No paid runs, orders or deployment changes.
- Full offline verification for normalization gate: **597 passed, 3 skipped**;
  lint, format and whitespace checks pass. The three opt-ins remain external
  database/paper-order integrations, not silently claimed as exercised here.

- Normalization review UI (local, not deployed): investigations now highlight
  absent accounting reviews and agent-reported gap status, with expandable
  cash-flow basis, debt/cash, dilution/SBC, nonrecurring treatment and citation IDs.
  Missing fields show Not recorded. The panel explicitly says agent-written
  analysis is not independent verification or proof of fair value. Renamed the
  adversarial agent labels so they no longer imply an independent human reviewer.
- Production build/typecheck passed; all six dashboard unit/database tests passed
  with a disposable local Postgres, and all five browser tests passed. Extended
  browser coverage exercises missing/gapped/no-gap states and visible citations;
  mobile screenshot inspected, without horizontal overflow. Disposable database
  removed in finally. No production data, credentials, deployment or trading state
  changed. Actual independent accounting review remains outstanding.

- Broker-history preflight (local, not deployed): the audit run reproduced two
  failures: contradictory same-ID activities were accepted, and missing historical
  activity did not latch the external pause. Reconciliation now validates the
  entire incoming activity set before importing orders/activities. Identical
  overlaps are deduplicated; conflicting IDs, missing historical IDs and unapproved
  revisions fail with a persistent pause. Existing operator pause text is retained.
- Recovery regressions verify history is unchanged, new activities preceding a
  revision are not partially imported, reservations remain held, restart does not
  clear the pause, and explicit resume requires successful reconciliation. Ordinary
  delayed fill convergence retains its existing automatic reconciliation behavior;
  this change does not permanently latch every transient provider error.
- Full offline suite passed 601 tests with three opt-in skips; the additional
  partial-import regression then passed with all 30 execution tests. Ruff and
  whitespace checks pass. No paid calls, broker requests, deployment or new orders.
  Full trade-bust/reopened-order provider semantics remain unsupported and blocked.

- Broker activity pagination validation (local, not deployed): reproduced malformed
  page handling, including an empty object accepted as empty history, and cyclic
  cursors only reaching the broad page bound. The snapshot adapter now requires
  a list of objects with nonblank string IDs and detects every repeated cursor.
  It retains the existing 1000-page hard bound and never returns a partial snapshot
  at that bound. Reconciliation still owns activity-content/correction validation.
- Eleven added cases cover malformed pages/IDs, a two-cursor cycle, the full page
  bound, and valid empty history. Existing multi-page and 404 lookup positive tests
  pass. Full offline suite: 613 passed, three opt-in skips; Ruff lint/format and
  whitespace checks pass. Mock transports only: no provider/model requests,
  orders or deployment changes. This does not prove provider snapshot atomicity
  or full trade-bust semantics; both remain separate readiness requirements.

- Telemetry commit-boundary drills: two new opt-in tests use real disposable
  Postgres and SIGKILL an actual exporter subprocess immediately before commit
  and immediately after commit but before local acknowledgement. All 201 local
  events survive either interruption. A new exporter drains batches of 200 and 1;
  Postgres contains exactly the original 201 IDs and unchanged payloads. Before-
  commit writes roll back; after-commit replay is deduplicated. No production
  implementation change was needed for these tested boundaries.
- Full suite with isolated Postgres: 617 passed, one explicit paper-order test
  skipped. Ruff and whitespace checks pass. Temporary tmpfs Postgres container
  removed; existing containers untouched. No production database, model or broker
  requests. This proves the two export transaction boundaries, not prolonged
  outage capacity, disk exhaustion, retention or full observability availability.

- Accelerated telemetry backlog recovery: four concurrent producers queue 1000
  events with 4KiB payload bodies across five injected Postgres connection failures.
  A fresh exporter is then stalled while 100 further events are emitted through
  the public instrumentation path. All 1100 events remain observable locally
  before acknowledgement and reach real isolated Postgres exactly once with
  unchanged payloads after recovery, across bounded 200-event batches.
- Full isolated-Postgres suite: 618 passed, one explicit paper-order test skipped;
  Ruff/format/whitespace checks pass. Temporary tmpfs database removed. No provider,
  production database, broker or model calls. This accelerated scenario is not a
  multi-hour capacity/latency benchmark and does not close disk exhaustion or
  retention requirements. No production implementation changes were necessary.
