# Resumable research (deployed; readiness review in progress)

Temporal coordinates durable research stages around the existing Deep Agents /
LangGraph research team. It does not submit broker orders or activate trading.
Services are deployed with trading paused. Fixture recovery checks passed, but
paid end-to-end production research acceptance is not established.

## Boundaries

The workflow history contains a run ID, frozen-manifest hash, stage names and
receipt references. Private inputs, exact sources, reports, model responses,
LangGraph checkpoints and budget state stay on the worker's persistent volume.
The current storage design supports worker replacement on that same volume;
it does not provide multi-host failover without a shared durable storage design.

Stages are acquisition, business, catalysts, valuation, adversarial review,
lead decision and commit. The existing bounded correction branch remains part of
the same logical run. Completed stage receipts skip work on redelivery. Each
role's LangGraph checkpoint resumes its pending node, while an operation journal
replays already saved model/tool responses without another provider call.

An operation that started but has no saved response is ambiguous. Temporal must
not blindly repeat it: the workflow blocks for reconciliation. A resume signal
does not erase that ambiguity. The journal also saves the ResearchDesk state
because graph messages alone do not contain source text, calculations or budget
grants. A process lease prevents two workers from executing the same run locally.

Durable OpenAI calls additionally save the exact HTTP request body and raw
response before SDK parsing. If the wire response was saved but the outer model
operation was interrupted, the same request can replay that response through the
SDK and existing validation/accounting code. This recovery uses the original
pre-call state and forbids new network requests. Changed request bodies,
credentials or project routing fail closed. A missing wire response still blocks;
this does not claim exactly-once external delivery.

Production acquisition records the chosen cache result, each SEC JSON response,
each news page and each X perspective separately. The original cutoff, pagination
parameters and retrieval timestamps survive restart. Completed subrequests are
replayed without another fetch; an interrupted unsaved subrequest remains ambiguous.

Cancellation is cooperative. Activity heartbeats deliver cancellation; an
in-flight operation finishes and saves its response before ownership is released.
The next operation is blocked. A hard process kill still leaves a potentially
ambiguous operation to reconcile.

## Frozen authority

The manifest binds the original context, experiment, configuration, Python source
and key installed dependency versions. Changed bindings fail closed. Resuming
does not create another spending reservation, reset counters, increase limits,
clear failures, or renew daily authority after midnight. Funding validation can
recognize the original reserved allowance without demanding a second allocation.
Research completion remains distinct from investment acceptance and execution.

## Operator interface

The module entry point is `python -m trader.research_runtime`. Its `prepare`
command refreshes stale paper-account reconciliation and freezes an existing
research-only JSON context without model calls.
It returns a run key and manifest hash. Inputs live under
`state/research-runs/<sha256-of-run-key>/` with private permissions.

```sh
uv run python -m trader.research_runtime --config config.toml prepare --context state/research-context.json
uv run python -m trader.research_runtime --config config.toml worker --allow-paid
uv run python -m trader.research_runtime --config config.toml start --run-key RUN --manifest-hash HASH --allow-paid
uv run python -m trader.research_runtime status --run-key RUN --manifest-hash HASH
uv run python -m trader.research_runtime inspect --run-key RUN --manifest-hash HASH
uv run python -m trader.research_runtime --config config.toml resume --run-key RUN --manifest-hash HASH --allow-paid
uv run python -m trader.research_runtime cancel --run-key RUN --manifest-hash HASH
```

These are interface examples, not authorization to spend. Start, resume and worker
commands require explicit paid-work opt-in and remain subject to funding/budget
gates. During an active paper study, the worker requires its original approved
dispatcher invocation and slot or held-position review claim. Scheduled slot claims
and their invocation payloads are saved atomically. Pending scheduled and safety
jobs retain their original payload rather than making a fresh research admission.
Supervised acceptance also starts or polls this path when Temporal is configured;
it releases account locks while research continues and does not rewrite the pause.
Its worker uses the frozen configured limits, without legacy per-call overrides.
The default server address is
loopback; remote connections require TLS. The deployed server is accessible only on host loopback and uses dedicated Cloud SQL
storage. Do not expose its unauthenticated frontend; remote access requires a separate
authentication and TLS design. Multi-host recovery and restore acceptance remain separate checks.

`inspect` reads an existing private operation journal under its exclusive lease,
without contacting Temporal or a provider. It returns completed-operation count,
pending operation identities, request hashes and saved pre-dispatch state hashes.
For model attempts it also exposes the saved client request ID and projected
request hash. Before dispatch, the private journal retains the model route,
configured output limit and projected messages/tools/schema, and sends that
attempt's ID as `X-Client-Request-Id`. A verified unsent retry gets a new attempt
ID while preserving the original operation and earlier attempt record. The
projection is not a byte-for-byte copy of the SDK's final HTTP request.
It does not print prompts, sources or responses, create a missing journal, resolve
an operation, or authorize a retry. A pre-dispatch snapshot is not proof that a
provider did not receive the request or charge for it.

If the local submission marker exists but Temporal never received the request,
`authorize-resubmit --run-key RUN --manifest-hash HASH --proof PATH --allow-paid`
records one recovery authorization; it does not itself submit work. The proof JSON
must contain `verified_by`, `reason`, `observed_at`, `evidence_sha256`, `namespace`,
`workflow_id` and `manifest_hash`. The operator supplies this reviewed evidence;
the command independently checks Temporal history is absent and no local research
journal or spending reservation exists. The next poll may submit only the same
workflow ID. Existing terminal workflows and uncertain provider calls do not
qualify for this recovery path.

## Verification so far

- Real LangGraph model/tool checkpoint replay and a real Deep Agents specialist
  restart preserve saved responses without duplicate calls.
- Journal tests exercise pending calls, changed request hashes, transaction
  failure, file privacy and process/thread exclusion.
- Real local Temporal server tests cover worker replacement, history replay,
  duplicate workflow IDs, bounded retries, correction-stage transitions and
  server-delivered cancellation.
- Manifest tests reject changed runtime/input identity and public or symlink files.
- Reservation/funding tests preserve original authority and reject unknown
  liabilities, changed configuration, midnight renewal and insufficient funding.
- A real local Temporal server completes the operator stage handler with the
  actual funding checks, simulated providers, settled spending and no orders.
- November 11 local regression: 1,532 passed, 8 skipped. Ruff lint and formatting
  passed; the exported locked runtime dependencies had no known vulnerabilities
  reported by pip-audit 2.10.1. These are local results, not deployed acceptance.

Independent review identified expiry-accounting gaps between operations, during
non-model requests and after partial final publication. The local fixes close the
original reservation using saved usage, or retain the full unknown allowance.
An empty journal alone can prove zero usage before the first checkpoint. Closed
runs cannot gain a commit receipt or enter the legacy research path; durable
submission markers also block legacy fallback before reservation. Focused regressions
pass; these final fixes were deployed as `b1d0510` after independent review.
No paid research or broker orders have been run to validate this implementation.

After a research deadline expires, use
`uv run python -m trader.research_runtime close-accounting --run-key RUN --manifest-hash HASH`
to close an incomplete run's original reservation from its saved provider response.
This offline operation writes an immutable accounting receipt before updating the
original spending row; it does not renew research authority or publish a decision.
Verified usage settles the estimated cost. Missing or ambiguous usage retains at
least the full allowance as an unknown liability, blocking further funding.
Repeated closes reuse the same receipt, including recovery from a crash between
the receipt and ledger update. Ordinary same-authority replay is separately tested
for one network send, one model count and one model-cost event.

Deployment image inspected locally: `temporalio/server:1.32.2` at
`sha256:7bf69188a6232fea14869b9cef3d94aa82a1601d99517f7e86c71f22da4e0670`.
It requires an explicit server config file; the image contains no legacy
`/etc/temporal/config` directory. Environment variables alone did not produce a
usable server configuration. This initial image inspection preceded the permanent
deployment recorded below.

`deploy/temporal-server.yaml` renders against that pinned image with required
database credentials, four history shards, two connections per SQL pool and
loopback-only default listeners. Verify with
`TEMPORAL_DEPLOY_TEST=1 uv run pytest -q tests/test_temporal_deployment.py`.
The pinned image's `render-config` command required `--config /etc/temporal --env server`;
`--config-file` alone did not select the file for that command.
`deploy/temporal.compose.yaml` is isolated local verification infrastructure, not
the production Cloud SQL deployment. Its unauthenticated frontend is published
only on host loopback and its databases have no published ports. Supply a private
environment file with `TEMPORAL_POSTGRES_USER` and `TEMPORAL_POSTGRES_PASSWORD`;
there are no default credentials.

Local PostgreSQL initialization and schema-tools download failed due to full
Docker storage. The newly created failed verification container, network and
empty database volume were removed; unrelated Docker resources were preserved.
The executor's boot disk also had only 1.2 GiB free when checked. Its separate
`/mnt/trader-state` disk has 16 GiB available (rechecked November 11), providing a
possible isolated verification location without removing existing images.
Full deployed fixture recovery checks are recorded below; investment acceptance
and the paper-readiness trial remain separate gates.

The official Linux AMD64 1.32.2 release archive is staged in the executor's private
`/mnt/trader-state/temporal-verification` directory. Its SHA-256 matched the
release's `checksums.txt`. It includes `temporal-server` and `temporal-sql-tool`,
so isolated verification can avoid downloading the larger admin-tools image to
the nearly full boot disk. This staging does not start a server or research worker.
Both binaries report version 1.32.2. The matching PostgreSQL v12 schema directory
is staged from the official `v1.32.2` source archive; the SQL tool's embedded
schemas cover MySQL only, so PostgreSQL setup must use that explicit directory.

November 11 database provisioning: Cloud SQL now contains dedicated databases
`trader_temporal` (schema 1.19, 40 tables) and `trader_temporal_visibility` (schema
1.14, 3 tables), owned by restricted login `trader_temporal`. PUBLIC database access
was revoked. The generated password is kept in private state, never in this repo.
Temporary administrator role membership used for ownership assignment was revoked
and verified absent. Existing observability tables were not modified.

The SQL tool rejected the existing Unix socket's colon-containing host path.
Schema setup and migration instead used a separate Cloud SQL proxy bound to
`127.0.0.1:15432`; that temporary proxy was stopped afterward. The original socket
proxy was left running.

November 11 persistent-server verification passed using bounded transient services
and a private SSH tunnel. A synthetic `ResearchWorkflow` in namespace
`trader-verification` stopped at business after acquisition completed. After an
actual server restart, a replacement local worker observed that same state,
resumed it, and completed all seven stages. Acquisition and every subsequent
completed stage ran once; business ran twice because its first receipt explicitly
blocked. Evidence and call counts are saved under `state/temporal-verification/`.
No models, broker calls, or trading-ledger writes were involved. This proves
Cloud SQL workflow persistence, not full deployed research-worker acceptance.
The verification server and separate proxy were stopped afterward.

The release binary resolved the absolute config directory relative to its working
directory during verification. Setting WorkingDirectory to the private config
directory and passing `--config . --env server` loaded the config successfully.

The permanent coordinator and dedicated proxy are now installed and enabled as
`temporal-server.service` and `temporal-proxy.service` on `trader-executor`.
Templates are in `deploy/`; replace `@TEMPORAL_ROOT@` with
`/mnt/trader-state/temporal`. The directory and pinned binary/config are root-owned,
readable by the dedicated `trader-temporal` system account. Credentials live in
root-only `/etc/trader-temporal.env`. Both units passed `systemd-analyze verify`;
the running coordinator passed the SDK health check and returned the earlier
seven-stage workflow result from Cloud SQL. Evidence is saved as
`state/temporal-verification/permanent-service-check.json`.
At this initial coordinator check, no research worker was enabled and Trader's
configuration and pause were unchanged. The coordinator alone cannot make model
calls or submit broker orders. The later paused cutover is recorded below.

Worker shutdown now handles SIGTERM/SIGINT by leaving Temporal's worker context
and awaiting SDK shutdown, rather than terminating immediately. Existing activity
cancellation signals the stage thread to stop before its next dispatch while
allowing an in-flight response to be recorded. Owned model HTTP transports close
after each stage on success or failure. A worker service must allow longer than
the 180-second lead-request timeout before forced termination; abrupt host loss
still leaves any unsaved provider outcome ambiguous and blocks blind retries.

`deploy/temporal-worker.service` uses a separate
digest-pinned image setting in `/etc/trader-research-worker.env`, the existing
private application environment and original ledger/control paths,
with one activity at a time. Its Docker stop grace is 240 seconds and service stop
timeout 260 seconds. The rendered unit passed systemd validation on the executor.
Control-directory access remains writable so existing reconciliation can persist
a stop marker on a fault. Run the full deployed recovery check before enabling
normal research submissions.

Worker startup acceptance passed on November 11 using image
`sha256:f0fc66ed7da92dd9146dad83c175801fc5575f297188ffc7c69c8a0305c86d42`
from commit `bc923bb7a7c3`. All 49 source/dependency files in the image matched the
clean build context. In the empty `trader-research` namespace, the real container
polled both workflow and activity queues, with no workflow submissions. It was
then stopped for the remaining full-stage recovery checks; boot enablement remains
off. The existing trading executor's image/configuration were not changed.

Executor full-stage fixture acceptance also passed: the deployed image ran
`test_temporal_worker_restart_replays_saved_stage_receipt` against the persistent
server in namespace `trader-verification`, run
`executor-full-stage-recovery-20261111-1`. After a deliberately lost business
receipt acknowledgement, a replacement SDK Worker completed the real stage engine
without repeating business model work or evidence acquisition. The test verified
one spend record, a WATCH result, and no thesis, graph decision or order intent.
It used a separate fixture ledger and fake model/provider responses; no application
credentials or real ledger were mounted. Worker replacement here occurs within
one fixture process, not an abrupt container kill. Server-process restart was
verified separately. Evidence: `state/temporal-verification/executor-stage-recovery.json`.

Abrupt container-loss acceptance then passed in fixture run
`executor-container-kill-20261111-2`. After the business receipt was durable, the
worker killed itself with SIGKILL; Docker reported exit 137. A fresh container
loaded the same journal/ledger and completed all seven stages: 3 model calls
before the kill, 5 afterward, zero repeated acquisitions or completed business
calls, one spending row, and no execution artifacts. Evidence:
`state/temporal-verification/container-crash-recovery.json`; fixture source is
`state/temporal-verification/crash_worker.py`. The first attempt (`...-1`) is
preserved but is not crash evidence: without Docker `--init`, Python was PID 1,
its self-sent SIGKILL was ignored, and the workflow completed uninterrupted.
All responses were fixtures; no provider credentials or production ledger were
mounted. This does not certify investment quality or resolve unknown provider
outcomes when a response never reaches the local journal.

## Documentation reviewed

Provider recovery remains a separate boundary. OpenAI's
[request debugging documentation](https://developers.openai.com/api/reference/overview)
describes `X-Client-Request-Id` for support correlation; it does not establish an
idempotent-create guarantee. Its
[response retrieval API](https://developers.openai.com/api/reference/resources/responses/methods/retrieve)
requires a response ID. [Background mode](https://developers.openai.com/api/docs/guides/background)
can return that ID before completion and supports polling; retained background
responses require explicit `store=true` beyond the short polling retention period.
The current model configuration still uses `store=False` and has not been changed
to background mode. A crash before a provider response ID is durably recorded
therefore still needs independent reconciliation, not a repeated create request.

The remaining recovery review concerns responses not captured in the local wire
journal and operator verification of provider evidence. Never import an
operator-authored post-state or infer a model answer from a billing record.

The [Temporal LangGraph integration](https://docs.temporal.io/develop/python/integrations/langgraph)
is a preview, and the [Deep Agents integration](https://docs.temporal.io/develop/python/integrations/deepagents)
requires workflow-safe models and tools. The existing mutable ResearchDesk and
provider I/O are not a drop-in workflow. Stable Temporal activities around the
existing team preserve its behavior with fewer changes.
Temporal's [error-handling guidance](https://docs.temporal.io/develop/python/best-practices/error-handling)
requires retry-safe activities; a workflow alone does not make paid calls
exactly-once. [Self-hosting guidance](https://docs.temporal.io/self-hosted-guide)
distinguishes local development from a persistent production service.

## Paused production cutover

After the fixture recovery checks, the dispatcher and worker were switched to
image `sha256:f0fc66ed7da92dd9146dad83c175801fc5575f297188ffc7c69c8a0305c86d42`.
The existing remote config was preserved, adding only `temporal_address` set to
`127.0.0.1:7233` and namespace `trader-research`. The worker is enabled; the
coordinator, worker and dispatcher all reported active with zero restarts.
The trading pause remains set. Before cutover there was no active study, pending
job, or unfinished durable submission.

Backups of config, old image setting and a consistent ledger snapshot are under
`/mnt/trader-state/temporal/cutover-bc923bb7a7c3` (private). Post-start comparisons
verified identical experiment data, pause content, all 62 spending records, both
research results, and zero order intents. No paid research or orders were started.
Evidence: `state/temporal-verification/paused-cutover.json`.

November 11 recovery-fix release `b1d0510`: Cloud Build succeeded; all 49 installed
source/dependency file hashes match the committed build. Image digest
`sha256:db4262f6e192729c99edc70bae71393ae92603891d1340c96fa508381787e498`
is now used by the worker and dispatcher. The seven-stage cloud fixture passed
again using simulated providers and isolated storage. Original experiment, pause,
configuration, 62 spending records, two research results and zero order intents
were preserved across deployment. All four services are running with zero restarts.
No production workflows were active at cutover; no paid model calls or broker
orders were made. The independent reviewer closed the four reported recovery
findings. This does not establish investment-quality acceptance.
