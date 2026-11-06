# Research-only evaluation

The evaluator exercises the existing research team against frozen evidence and scores its recorded outputs. It never constructs a broker, invokes the execution graph, submits an order, or changes the trading pause. A BUY research output is **not execution eligibility**. Trading risk gates remain a separate requirement.

## Offline baseline

Run from the repository root:

```sh
uv run paper-agent evaluate \
  --suite evaluations/baseline-v1.json \
  --responses tests/fixtures/evaluation/recording.json \
  --output state/evaluations/mechanics-v1.json --run-id mechanics-v1
```

The supplied recording is handwritten and labeled `offline_script`. Its zero costs and successful answers are test fixtures, not measured model behavior. The suite has 16 synthetic cases: 12 development and four holdout cases, covering supported cash flow, contrary evidence, ambiguous YTD, restatements, negative cash flow, missing shares/primary passages, contradictory news, stale references, inconsistent units, future amendments, provider timeouts, malformed reports and unknown citations. Three fault cases test rejection; 13 cases call for completed decisions, including appropriate abstention.

The scoring oracle checks schema completeness, evidence IDs, symbol/review date, independent Decimal valuation arithmetic, known cash-flow input, and refusal to accept BUY/ADD with material gaps. Sufficient-evidence cases require an actual valuation; returning WATCH with no calculation cannot game completion. Source and document availability must precede the cutoff. Newly read passages must match the frozen document text, offset, URL and content hash. Rubrics and split labels are excluded from model inputs.

## Explicit model evaluations

A model run is a separate, opt-in paid action. Use the **authoritative executor's current config and shared daily ledger**, never a stale laptop copy to obtain another allowance. Verify current provider credit and token rates first. Keep the operational trader paused. No billing changes are made by this command.

```sh
uv run --env-file .env paper-agent --config config.toml evaluate-run \
  --suite evaluations/baseline-v1.json \
  --output-dir state/evaluations/baseline-dev-001 \
  --run-id baseline-dev-001 --split development --max-cost 1 --allow-paid
```

The example is a $1 maximum for the **whole selected cohort**, not per case; it is not a promise that the cohort can complete for $1. The cap must fit the configured per-run and daily ceilings. An initialized shared ledger is mandatory for paid runs. The evaluator reserves the available allowance before calling models and settles estimated costs afterward, including failed attempts. Unknown charges consume the remaining allowance and stop subsequent cases. Day rollover, context/tool/call limits and deadlines remain enforced. A crash retains the reservation; do not delete reservations or invent a new run ID to retry an ambiguous request.

The paid command selects only model cases, never the three synthetic fault cases. `--split development` selects nine; `--split holdout` selects four. Freeze the candidate code/prompts before running holdout. Do not tune against those answers; after inspecting them for tuning, create a new versioned holdout for future claims. The repository split is procedural separation, not access control.

Each run gets an exclusive directory containing `manifest.json`, `suite.json`, incrementally and atomically saved `recording.json`, `results.json`, and an isolated `ledger.sqlite`. The shared operational ledger receives only evaluation run identity and budget records. The original experiment, intents and history are preserved. Duplicate IDs and existing output directories are rejected. An interrupted run is not automatically resumed or retried.

Provenance includes code-content and prompt hashes, configured model versions/rates, configuration, source/input hashes, cutoff, stable run/case IDs and stage outcomes. Model versions identify the requested models; provider aliases may change their underlying implementation. Frozen synthetic cases avoid supplying future documents but cannot prove absence of model pretraining contamination.

## Compare and review

Score recordings into new result paths, then compare:

```sh
uv run paper-agent evaluate-compare \
  --baseline state/evaluations/baseline-dev-001/results.json \
  --candidate state/evaluations/candidate-dev-001/results.json \
  --output state/evaluations/comparison-001.json
```

Both runs must use identical suite/input hashes and splits and the same mode. Scripted fixtures cannot be compared to paid model outputs. Comparisons report paired pass/regression, cost, latency and context differences; they never promote a candidate automatically.

Results distinguish:

- `structurally_complete`: decision plus all four valid specialist schemas.
- `complete`: structurally complete and mechanically valid against the case rubric.
- `mechanical_pass`: includes correct rejection of a fault case; a rejected case is not a completed decision.
- `acceptance`: pending human review, passed, or failed. Citation existence alone never certifies factual support.

A human reviewer must inspect the exact cited passages against business claims, valuation assumptions and contrary evidence. For abstention, check the explanation against the rubric's expected reason, not just the WATCH action. Spot-check every BUY/ADD and every adequate-evidence case, plus examples of each gap type. Record the reviewer, timestamp and concrete claim/passage notes in a **copy** of the recording, then replay to a new result path:

```json
"human_review": {
  "reviewer": "reviewer name",
  "reviewed_at": "2026-11-06T18:00:00Z",
  "claims_supported": true,
  "abstention_reason_correct": true,
  "notes": "Identify the checked claims, exact evidence IDs/passages and any assumptions. Explain why the abstention reason is correct or not applicable."
}
```

These are explicit human attestations, not automatically verified identities. Never have the evaluated model certify its own review. Missing reviews remain pending; a negative review fails acceptance even if mechanics pass. Preserve the original recording and its hash.

Summaries include median/p95 known cost and latency, repeated tools, peak context and timing by stage, unknown-cost calls, and total known cost per valid completed decision. All failed attempts stay in the numerator. Costs are token-rate estimates, not invoices; unknown charges mean the total is incomplete. Stage model seconds can overlap because specialists may run concurrently. Repeated calls count identical tool name/argument pairs actually entering the guarded tool path; blocked/cached delegations are excluded.

## Completion boundary and checks

`uv run pytest -q` includes frozen-case scoring, an actual ResearchDesk/Deep Agents execution with a scripted model, timeout and interruption reservations, duplicate-run protection, CLI isolation, passage validation and the existing risk/execution regressions. These checks validate the harness without paid API calls or orders.

Step 1's harness is the prerequisite for measuring improvements. The proposed ≥90% sufficient-evidence completion target must be measured in a separately budgeted **real model** cohort, followed by semantic review; fixture results do not meet that quality target. Neither synthetic evaluations nor historical LLM replays establish investment returns. No dashboard, scheduler, automatic tuning, new database or dependency is added.

### Step 1 implementation audit

| Requirement | Delivered evidence |
|---|---|
| Research-only entry point | `evaluate`, `evaluate-run`, `evaluate-compare` return before operational dispatch; broker-construction and CLI-process tests pass. |
| 12–20 labeled frozen cases | 16 versioned synthetic cases with cutoffs, expected outcomes, required citations and abstention reasons. |
| Offline mechanics and separately budgeted model mode | Scripted replay plus actual ResearchDesk integration test; paid CLI requires explicit flag, initialized shared ledger and bounded reservation. |
| Reproducible records | Exclusive run identity/directory, code/prompt/model/config metadata, input/evidence hashes, complete or failed stage records and incremental recording. |
| Paired comparisons and holdout | Identical input/suite/mode checks, regression output, explicit development/holdout selection. |
| Completion, citation, arithmetic and abstention metrics | Schema/citation/math/gap checks; sufficient cases require supported valuation; semantic review remains a separate explicit attestation. |
| Cost and operational metrics | Known/unknown charges, failed-attempt costs, median/p95 cost and latency, repeated tools, per-stage timings and peak context. |
| No execution or lost budget on failure | No broker/graph construction, duplicate IDs rejected, unknown charge and interruption reservation tests; existing risk tests remain green. |

Initial implementation verification on November 6, 2026: 255 tests passed, three opt-in tests skipped; Ruff and diff whitespace checks passed. The CLI replay and paired self-comparison completed successfully. No paid model calls, broker requests, deployment, scheduler changes or pause changes were made for this implementation. Those implementation checks did not measure real-model completion. Subsequent paid results are recorded below.


### First real-model checks, November 6

The first production-limit cohort (`baseline-dev-20261106-2`) completed no final
decisions in its first three attempted cases. All four specialist reports completed,
but final synthesis failed on structured output, the 24-call ceiling, and a provider
timeout, respectively. Known cost for those cases was $3.186111; one timeout had
unknown cost, so the evaluator conservatively consumed the remaining $5 cohort
allowance and skipped later cases. This is failure evidence, not a passing baseline.

A separate candidate cohort uses a 40-call ceiling and 5,000 output tokens. WATCH
now permits a zero holding period when no holding is proposed, while other actions
still require a positive holding period. Its first case produced a structured final
answer but correctly failed because it placed a derived calculation ID in the
original-source citation list. A subsequent prompt/schema clarification explicitly
separates calculation references from original evidence; that clarification is not
part of the already-running candidate cohort and is not yet model-validated.

The latest local verification is 256 tests passed, three opt-in checks skipped.
Production readiness and held-out model acceptance remain incomplete. The separate
one-share paper execution check confirmed a broker fill; it does not establish
research quality or investment performance.


### Completed paid cohorts

| Cohort | Mechanical completion | Sufficient-evidence completion | Known model cost | Unknown-cost calls |
| --- | --- | --- | --- | --- |
| Original development baseline | 0/9 | 0/3 | $3.186111 | 1 |
| First development candidate | 5/9 | 0/3 | $6.795062 | 0 |
| Held-out candidate | 2/4 | 0/1 | $4.008954 | 0 |

The original baseline stopped after three attempts; six skipped cases are included
in its denominator, not represented as attempted model failures. Its unknown call
keeps the full $5 cohort allowance charged conservatively. Costs are configured
rate estimates, not billing totals. The paired development comparison has no
passing-to-failing regressions, but does **not** recommend promotion. The proposed
90% sufficient-evidence target has not been met.

The development candidate predates bounded citation correction and the text-source
filing-lookup fix. The holdout used those fixes with the frozen 40-model-call,
48-tool configuration; its unit-mismatch case exhausted the remaining cohort cost
allowance (next-call estimate $1.931130 versus $1.919016 available), at 21/40
model calls. Normal-operation testing
subsequently increased model-call headroom to 60 while retaining the 48-tool and
$5 run ceilings. Do not attribute the old cohort scores to that changed runtime
configuration or claim an independent human semantic review.

Citation correction preserves rejected output and allows one additional attempt
within the existing budget after an unknown-source citation. A second invalid
citation still fails; provider failures, arithmetic validation failures and exhausted
budgets are not retried by that correction path. Filing lookup handles text sources
without treating them as filing metadata and never fetches an undiscovered accession.

Latest offline verification: 258 tests passed, three external opt-in checks skipped;
Ruff and whitespace checks passed. Normal AAPL research and operational resume are
tracked separately from these synthetic cohort results.

### Recovering completed normal-run research

If a failed normal research run saved all four completed reports, an explicit new
run can finalize those reports with a fresh broker quote:

```sh
uv run --env-file .env paper-agent --config config.toml research AAPL \
  --run-key NEW_UNIQUE_RUN --finalize-from FAILED_PARENT --defer-execution
```

This is an operational research command, distinct from the frozen evaluator. It
requires the usual operator-controlled pause window and existing account binding.
The parent must be failed research for the same symbol in the active experiment;
a successful cached result is not eligible. All four reports must be fresh and
nonfuture, every original citation must resolve, and saved calculations are
recomputed before finalization. The parent audit and reservations remain unchanged.
The child gets its own daily reservation and trace. It makes no new evidence
requests; it does not automatically resume trading or bypass fresh execution gates.
Finalization with `--defer-execution` preserves the review step before execution.

Verification after extending recovery to normal runs: 259 offline tests passed,
three external opt-in checks skipped; Ruff and whitespace checks passed.

### Normal final synthesis context

Once all four specialists finish, the ordinary lead request replaces planning and
delegation history with one context containing the portfolio, quote, completed
reports and timestamps, deterministic calculations and source previews. Tools are
disabled for that synthesis. Original messages and source bodies remain in the
audit; this transformation changes only the provider request. Citation correction
still retains the rejected decision and feedback, and all shared limits apply.

Prior thesis source bodies are not copied into every new prompt. They remain in
the saved thesis and are loaded when reports are reused; historical evidence IDs
are explicitly distinguished from current verified evidence. The prior decision,
reports and valuation remain available for comparison.

Offline verification: 261 tests passed, three external opt-in checks skipped.
This does not change historical cohort scores or make a recovery-only success
proof that an ordinary full investigation completes within budget.
