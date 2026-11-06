# Operations and recovery

## Account authority and persistence

Keep the SQLite database, its WAL during backups, and `control_dir` on persistent local storage. Use a SQLite-consistent backup or stop the service before copying all database files. Do not delete them to recover an error. They contain experiment identities, evidence at decision time, activities, durable order IDs, decisions, costs and interruptions.

One host and OS user must own execution for an account. Account-specific POSIX locks serialize reconciliation/gate/submission and prevent overlapping scheduled runs. An account-to-ledger binding rejects a second database for the same account. This is not a distributed lock; do not deploy multiple hosts or change the control directory to bypass it.

Stop schedulers/listeners before changing experiments or credentials. `init --new-experiment` is the only reset operation. Prepare a fresh dedicated paper account and set its new paper keys; initialization verifies no holdings/orders and cash/equity equal configured initial capital. A new UUID scopes all subsequent records; previous experiment records remain in the same database. The app never resets brokerage balances or losses automatically. Alpaca account creation/reset is an explicit operator action in its dashboard.

## Startup and reconciliation

Startup, reconnect, every minute tick and every submission reconcile the account, positions, open/tracked orders and paginated activities. Account snapshots are not atomic: fills and activities can become visible at different moments. Any quantity/cash/order-fill mismatch pauses new execution until subsequent snapshots agree. A two-cent cash tolerance allows broker rounding; it does not allow spending beyond observed cash.

No open-order event directly debits cash or creates holdings. Only confirmed execution activities enter the audit replay, keyed by immutable broker activity ID. Broker holdings and average cost are reported directly; a separate average-cost realized-P/L audit can differ from Alpaca's basis convention. Activity revisions, missing fills, unknown positions, negative cash, shorts and unexplained corporate actions fail closed.

A failed report can show the last successful snapshot with its timestamp and `reconciliation.ok=false`; that is not a current account claim. Inspect the journal and actual broker account before resuming. Reconciliation retries naturally on subsequent scheduler ticks; the same failure is never silently marked successful.

## Submission uncertainty and order lifecycle

An approved intent is committed before POST and has a deterministic client order ID. Retry with the **same** run/intent key. On an ambiguous timeout/5xx/duplicate response, the app looks up that ID. If absent or the lookup fails, its reservation remains and new trading pauses. Subsequent reconciliation resolves an eventually visible order without issuing another POST.

A process crash between durable preparation and POST is also deliberately ambiguous. A missing broker order cannot prove the POST did not arrive. The first version has no automatic abandonment/retry timer. Investigate with Alpaca; do not hand-edit the database, change the intent key to evade the pause, or claim execution. If the broker can never resolve the ambiguity, stop the old experiment and explicitly create a new dedicated paper account/experiment while preserving its history and canceling any old account orders through the broker.

Partial fills retain only the unfilled-buy reservation plus its fee buffer. Canceled/expired/rejected orders release the unfilled remainder. `pending_cancel`, `pending_replace`, `suspended`, `done_for_day` and unrecognized nonterminal states remain reserved until terminal confirmation. A subsequent confirmed fill after cancellation is still reconciled. Stale order snapshots cannot reduce cumulative filled quantity or resurrect a terminal order.

There is no discretionary reasoning in the execution node. Every attempted trade uses fresh quote/halt/asset/calendar checks and re-fetches account state immediately before the final deterministic gate. Same-symbol outstanding orders block new conflicting intents. The risk gate never uses pending sale proceeds, leveraged buying power or anticipated fills.

## Halts, feeds and downtime

Quotes carry source, feed, coverage, timestamp and delay flags. The initial experiment uses free real-time IEX quotes for US stocks, including NYSE- and Nasdaq-listed companies; coverage is IEX only. Configuration also accepts an explicit `sip` upgrade with the appropriate entitlement. Delayed feeds and quotes from an unexpected feed are rejected. Staleness, halt, limit-price and cash checks apply equally to both feeds. Entitlement failures never trigger a feed switch; they block execution until access is available. The data feed does not direct orders to a particular venue: paper execution remains simulated by Alpaca against NBBO.

Basic quote streaming is limited to 30 symbols. Keep the initial universe within that limit; subscription failures are recorded and bounded REST polling remains the fallback. IEX can have sparse quotes/bars: stale quotes still block orders and missing session marks still fail the benchmark report. Do not relax freshness rules to hide missing data. Review these failures and execution outcomes before upgrading; retain the recorded feed on historical decisions and daily samples.

The public Nasdaq Trader trading-halt bulletin is polled, its publication timestamp and item count checked, and active/scheduled resumptions parsed in New York time. Unknown/stale halt status blocks orders. Bulletin publication latency means this is not a guarantee that a just-announced halt is visible instantaneously; the broker remains the ultimate execution venue gate. A fresh quote alone is never treated as proof that trading is not halted.

Streams connect only to the paper order channel and Alpaca data/news channels. Reconnect backoff is bounded at 60 seconds. REST polling continues if streams fail or news entitlement is unavailable; absent required research/news evidence prevents the affected research run. Quote ticks are coalesced, not sent to an LLM. The fixed polling cadence and account locks bound execution wakeups. A separate `maintain` subprocess reconciles and requests cancellations while finite research runs are in flight; model latency does not block account maintenance. Order events wake maintenance, news events can wake research, and periodic wakeups respect the normal research cadence. Child processes are terminated on timeout/shutdown, with forced termination after a bounded grace period.

Missed monitoring is recorded when heartbeat gaps exceed three polling intervals. Missed daily sessions are listed in later reports, not synthesized. The system cannot know events while the entire host is offline; reconciliation on return discovers broker fills that occurred during the interruption. Use an external service supervisor/alerting system to detect an offline host.

## Pause, cancellation and liquidation

`kill --reason ...` writes the persistent pause flag before waiting for the execution lock and requests cancellation of broker open orders even when reconciliation failed. Subsequent ticks continue cancellation/reconciliation. A POST already in flight can still be accepted, and a cancellation request can still fill. Inspect broker-confirmed terminal status. The flag survives restart.

`resume` clears the pause only after reconciliation succeeds. It does not change risk limits. Liquidation is separate: create a SELL decision and run `execute ... --intent-key ...`, Broker-interface trades are outside the approved-intent ledger and pause automated execution; do not treat them as a transparent substitute for an approved SELL. Whole-share rules, market eligibility, freshness, cooldown and turnover limits still apply to agent SELLs. There is no unconditional emergency market-liquidation API in this version.

## Deposits, withdrawals and corporate actions

Capital movements must be explicit. When a recorded CSD/CSW activity pauses reconciliation, acknowledge its exact signed amount and reason:

```json
{"id":"actual-broker-activity-id","cash":"1000.00","reason":"Explicit additional experimental capital"}
```

```sh
uv run paper-agent acknowledge-adjustment contribution.json
```

Fees/dividends with supported cash activity codes are automatically journaled and replayed. Unsupported corporate actions are recorded and pause trading. Use a broker-verified adjustment referencing that activity, never a fabricated fill:

```json
{
  "id": "actual-split-activity-id",
  "cash": "0",
  "reason": "Broker-confirmed 2-for-1 split; 10 shares become 20, total basis unchanged",
  "positions": {"AAPL": {"quantity_delta": "10", "basis_delta": "0"}}
}
```

The adjustment is logged and must reconcile with broker positions/cash before trading resumes. A corporate action creating fractional holdings may need explicit broker handling; the agent does not submit fractional orders. Keep original activity and adjustment provenance for audit.

## Experiment end and evaluation

At the configured end timestamp, all new execution stops and pending orders are canceled; positions are not liquidated. Reporting/reconciliation can continue. Use the first post-close report at/after the end as the completed regular-session valuation, and retain the end timestamp in the report.

Operational success means correct identity/endpoint enforcement, idempotent lifecycle recovery, coherent reconciliation, useful evidence/decisions and visible failures. Investment performance is a separate observation. Short paper experiments cannot establish reliable long-term alpha; paper fills are not real liquidity or slippage evidence.

Benchmark comparisons require regular-session data for the portfolio and both ETFs on matching dates. When missing, the daily benchmark report fails explicitly. Final-minute closes can differ from official auction closing prices. Daily comparison marks use the same configured feed (initially IEX) for portfolio and benchmarks and identify its coverage; broker-reported equity remains separate. IEX marks are venue observations, not official consolidated closes.

Record data costs with `record-data-cost 25.00 --reason 'Monthly data subscription allocation'`. Set model token rates before research; the Deep Agents team requires them to enforce estimated spend limits. Failed calls with unknown billed usage are reported as unknown costs, never zero. Costs are not deducted from simulated portfolio equity.


## Research team recovery

See [research architecture and APIs](research.md). Each finite run shares model/tool/cost/time budgets across the lead and specialists. Failures produce an audit record and stop before execution. Completed research is saved under the stable run key before submission; retries restore it without repeating model calls and complete any interrupted journal/thesis writes. Interrupted, incomplete research can be investigated again; that may incur additional model cost, and the prior audit events remain. Scratch files are virtual and ephemeral, not a resumable host filesystem.

For targeted news reviews, the catalyst analyst runs first. Fresh business and valuation reports can be reused only if it finds no need for deeper reassessment. Their original timestamps remain unchanged, so repeated news checks cannot extend their freshness. Adversarial review always runs. Full/weekly/deadline reviews rerun all specialists. Do not reuse an old run key for a genuinely new review.

Model/tool/deadline exhaustion, missing specialists, malformed outputs, unknown source IDs, or mismatched calculated valuations prevent new orders. Automatic summarizer calls are disabled so they cannot bypass spend accounting; oversized context stops the run. Limits are engineering defaults, not research-quality or portfolio-performance guarantees. Per-investigation limits are supplemented by `research_daily_cost_usd`, a durable aggregate estimate across symbols, restarts and experiments in the same ledger. A transaction reserves up to the per-run ceiling (or remaining daily allowance) before API work. Completed runs replace reservations with estimated usage; unknown usage and crashed runs retain conservative allowances. Runs are charged to their New York start date. Calls already in flight can finish after midnight, but the investigation cannot initiate further calls on yesterday’s reservation. A fresh date restores that day’s allowance; never delete reservations to recover spend. The cap covers only this service’s configured model usage, not unrelated uses of the API key, subscriptions or tools added later. Actual provider billing can differ. Zero disables new paid research while monitoring/reconciliation/reporting continue. Previously approved deferred decisions can still pass fresh execution checks; use `kill` to disable all new orders.

## Container operation

See the README Docker commands. State and account locks are persistent bind mounts, credentials are a runtime secret, no ports or Docker socket are exposed, and the container runs as the host UID/GID with a read-only root filesystem. Only one container/host execution owner is supported. Keep the Mac awake, connected, and Docker running. Container health checks cover recent monitoring and reconciliation; they do not certify investment quality, every feed’s availability, or model credentials. Docker restart policies act on exit, not an unhealthy status. Push alerts and an external host-offline watchdog are not configured.


## Review hardening and backup recovery

Maintenance attempts expiry/end-of-experiment cancellation before replay, so a reconciliation discrepancy cannot prevent risk-reducing cancellation. Cancellation verifies the broker account identity. Broker order identity, symbol, side, quantity, limit, session eligibility and replacement metadata must match the approved intent. Fills without a matching approved order pause trading. Accounting sorts timezone-aware execution instants, not timestamp strings.

Premarket/weekly runs save a deferred decision without submitting an order. During a regular session the same immutable research can be reused without another model charge only within its cache/review window and with no newer fetched news. Execution still checks fresh quotes, halts, account state and all limits; a stale limit is rejected, never automatically repriced. `research SYMBOL --run-key KEY --defer-execution` uses this deferred path. Failed runs have a debounce interval, held stocks have priority, and other stocks rotate by least recent research attempt to reduce budget starvation.

The pause flag also blocks direct CLI research, but daily reports and maintenance continue. A successful daily report is immutable. Mixing market-data feeds within a benchmark history requires explicit rebaselining; the app refuses to silently compare unlike feeds. Returns through nonpositive comparison equity are null. SQLite reports are read under the account lock. Quote-stream persistence is limited to one update per second per symbol; order hints remain immediate.

Maintenance creates a SQLite-consistent backup once per UTC date under `state/backups/ledger-YYYY-MM-DD.sqlite`. `paper-agent backup` ensures that day's backup exists; it does not overwrite it or promise a current-minute snapshot. Backups include committed WAL data, use private file permissions and pass integrity and experiment-identity checks before publication and on reuse. Symlinks, corrupt files and mismatched experiments fail without overwriting the evidence. Publication flushes the closed database before rename and the containing directory afterward; a flush error is a failure, even if the renamed file already exists. They are local disaster-recovery copies, not protection against loss of the host. Copy backups and the account control directory to operator-managed protected storage. Keep them private: they contain portfolio and research history. No automatic retention/deletion is enabled.

Before each order POST, the executor also publishes a private `backups/intent-<hash>.sqlite` checkpoint containing the exact prepared intent and prior ledger history. Publication and verification must succeed before submission. Disk failure rejects the intent without a POST; a fresh risk check after disk work prevents submitting stale inputs. Checkpoints are immutable per experiment/intent and are not automatically deleted.

For cloud execution, configure `checkpoint_bucket` to require off-host replication before every POST. The GCE service identity needs object create/get access to a private bucket with public access prevention and uniform bucket-level access. No static storage key is used. Uploads use `ifGenerationMatch=0`; existing or ambiguously uploaded objects must pass identity, size and exact-generation SHA-256 read-back verification. No second upload is attempted after a timeout. Storage failure rejects the order, including exits; an operator must address the failure, never bypass it. Without this setting, only the local checkpoint is required, which is insufficient for a live release. Full-ledger checkpoints consume storage/network per order; monitor growth and storage costs separately from model spend. Administrator access and disaster recovery of the cloud project remain separate risks.

`executor_fence = true` additionally requires every broker mutation to match the GCE instance ID, project number and full metadata zone in `executor-owner.json` in the same bucket. Reads remain available when ownership cannot be verified. The object must have integer `version: 1` and string `instance_id`, `project_number`, and `zone` fields. Only the trusted operator may replace this record; the executor's create/read IAM cannot overwrite the existing object. There is no expiry or automatic takeover. Before a generation-conditional ownership transfer, stop the old VM and independently verify its exact instance ID is TERMINATED, restore/reconcile the new ledger while paused, then assign the new VM. A registry update alone does not fence an already-authorized in-flight request. Cross-host fault drills remain a pending deployment prerequisite. The submission callback reruns shared risk checks after ownership I/O, immediately before the broker POST; rejection or check failure produces a rejected intent without a broker request.

Use `uv run python deploy/transfer-executor.py --bucket BUCKET --project PROJECT --zone ZONE --name TARGET_VM` to prepare a read-only ownership plan. The existing owner object must include `project_id` and `instance_name` in addition to its metadata identity. For a different target, the plan requires the exact pinned source VM to be TERMINATED and the target RUNNING. Applying requires both `--apply` and `--expected-generation GENERATION` from the reviewed plan. The command rechecks source shutdown before a conditional object write, retains the previous identity/generation in the new record and verifies read-back. A conflict, timeout or changed identity is a failure, never permission to retry blindly. It does not stop/start VMs, restore state, clear pause or certify target ledger readiness. Coordinate administrators so nobody restarts the old host during transfer; reconcile any in-flight broker calls before resuming.

To recover, stop the container and any other account process. Preserve the damaged database and its WAL/SHM files for investigation. Restore the verified backup/checkpoint to the **original absolute database path**, with the original control-directory account binding and pause flag, and do not run `init`. Start paused; reconcile with the broker before `resume`. A daily backup can predate an in-flight intent: an unknown external order/fill must pause the restored account. Use the newest verified checkpoint/journal containing the approved intent; never submit another trade to compensate for a missing record. A checkpoint may have been written before a POST that never happened. If its prepared intent has no broker order, reconciliation deliberately stays blocked for investigation; the file alone is not permission to retry or release a reservation.

The container has CPU, memory and process limits. The CLI prevents a second listener for the same account/control directory on the same OS. Health remains an operational reconciliation check, not a certificate that OpenAI, research quality, or all data feeds are ready. Unknown-cost API failures retain their reserved daily allowance; a budget reservation is not a confirmed provider charge.

## Telegram fill alerts

Set `TELEGRAM_BOT_TOKEN` and numeric `TELEGRAM_CHAT_ID` in the executor's private
runtime environment. Create the bot with Telegram's BotFather and start its
private chat before linking it. Never put either value in tracked configuration.
The optional notifier activates on the first maintenance cycle with both values
present and sends only broker `FILL` activities timestamped after activation.
Each partial fill has its own activity ID and alert; an alert does not imply the
whole order completed. Alerts identify paper trading explicitly.

The existing ledger records `telegram_notification` delivery status by fill ID,
including returned message IDs. Reconciliation and risk checks run before the
notifier. Notification failures cannot authorize or submit trades.

Delivery favors avoiding duplicate alerts: the attempt is durably recorded before
sending. A timeout or process interruption leaves `unknown`; an explicit API
failure leaves `rejected`. Neither is automatically resent because Telegram's
`sendMessage` endpoint has no idempotency key. Review these journal events when
troubleshooting missed alerts. Repeated reconciliation never resends a recorded
fill. Telegram credentials are redacted from telemetry; raw request URLs and
provider error bodies are never journaled.
