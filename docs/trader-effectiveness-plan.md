# Making the trader more effective

Research date: November 6, 2026. Status: proposed implementation plan, not a change to trading behavior.

The recommended investment is in better financial inputs, reliable research completion, and prospective evaluation. We already have a specialist team, contrary evidence, deterministic risk controls, durable traces and paper-broker reconciliation. Adding more agents is not the first priority.

“Effective” means completing evidence-grounded decisions economically and eventually improving risk-adjusted paper outcomes against simple alternatives. An accepted WATCH can be correct. More trades, longer reports and higher model confidence are not success metrics.

## What public projects offer

These are design references, not independently verified profitable strategies. I reviewed primary project documentation and papers; I did not reproduce their experiments or audit their entire implementations.

| Example | Useful idea | How it fits our trader |
|---|---|---|
| [TradingAgents](https://github.com/TauricResearch/TradingAgents) | Specialist analysis, structured opposing arguments, persistent decisions and checkpoint recovery. Its release notes also describe fixes for historical date leakage. | Keep our existing team. Make challenges and recovery more structured, and test information availability explicitly. Do not add an unbounded debate loop. |
| [FinRobot Desktop](https://github.com/AI4Finance-Foundation/FinRobot/blob/master/finrobot_desktop/README.md) | Separates financial computation from narrative and records numeric provenance; offers valuation and audit operators. | The closest fit to our current valuation bottleneck: compute an auditable financial input package before asking the agent to value the business. |
| [FinMem paper](https://arxiv.org/abs/2311.13743), [author repository](https://github.com/pipiku915/FinMem-LLM-StockTrading) | Layered memory for financial decision-making. | Retain dated facts, changing theses and resolved lessons separately, with explicit expiry and evidence references. Start with ordinary records, not a vector database. |
| [FinAgent paper](https://arxiv.org/abs/2402.18485) | Market intelligence, memory retrieval and reflection at multiple levels. | Separate “was the research sound?” from “what subsequently happened?” Use scheduled outcome reviews rather than automatically rewriting prompts after a loss. |
| [AI Hedge Fund](https://github.com/virattt/ai-hedge-fund) | Documents persistent paper-session history and historical replay against benchmarks. The project describes itself as a proof of concept. | Borrow the comparison workflow. Keep our existing authoritative Alpaca paper fills and ledger; simulated research scores must remain separate. |

A further evaluation warning comes from [FinCAD](https://arxiv.org/abs/2605.24564): historical outcomes can already be encoded in model weights. Restricting supplied documents to an old date does not, by itself, make an LLM backtest uncontaminated. We should use historical cases for engineering checks and forward-recorded decisions for stronger evidence of investment usefulness. We are not proposing to implement FinCAD's decoding method.

## What we already have—and what is missing

The following observations come from this repository, rather than assumptions about generic trading agents:

- `trader/research_team.py` already defines business, catalysts, valuation and adversarial specialists. It validates evidence IDs and matches valuation outputs to a deterministic calculation. Its `value_scenarios` tool is a simplified per-share cash-flow model, not a general financial-statement normalization engine.
- `trader/evidence.py` already fetches SEC company facts, filings, passages and news. It warns about restatements and overlapping YTD periods, but still leaves substantial interpretation and normalization to the model. Retrieval currently uses the current date; it is not a complete historical point-in-time data service.
- The accepted AAPL research disclosed unsupported valuation inputs and chose WATCH. That is a defensible refusal to invent value. The opportunity is to resolve the missing inputs, not weaken the acceptance checks.
- Evidence caching, targeted reuse of fresh business/valuation reports, prior theses, stable run keys and finalization recovery already exist. We should extend these rather than build another orchestration system.
- `Runner.research_batch` prioritizes holdings and least-recently-attempted symbols. Its `opportunity_ranking` event does not mean it has a demonstrated investment-opportunity scoring model.
- `trader/reports.py` already computes matching-date SPY/QQQ comparisons and drawdown, with explicit dividend and missing-session limitations. We lack a systematic connection between individual research claims and later outcomes.
- `trader/x_api.py` takes bounded discussion and contrary samples, currently up to ten posts per request. It correctly labels these as sampled discussion, not representative market sentiment.
- The inspected local configuration has a $6 daily model ceiling and $5 per-run ceiling. Verify deployed configuration before any experiment; this plan does not authorize raising either ceiling or restarting automated trading.

## 1. Establish a research evaluation baseline

Implement this first so each subsequent change has an observable effect.

Add `trader/evaluation.py` and a research-only CLI path in `trader/cli.py`. The evaluator must not call the execution graph or expose broker order methods. The existing `research` command can submit paper orders, so it is not an acceptable benchmark harness.

Start with 12–20 frozen cases: adequate financial evidence, ambiguous YTD facts, restatements, negative cash flow, missing sources, contradictory news, stale quotes, malformed specialist output and provider timeout. Label whether evidence supports a decision, what must remain unknown, and the expected reason for abstention. Use offline fixtures for mechanics; model-scored comparisons are separately budgeted, explicit runs.

Persist an evaluation record containing case ID, information cutoff, evidence hashes, code/prompt/model versions, configuration, run ID, stage outcomes, known costs and unknown-cost calls. Compare baseline and candidate on identical inputs. Reserve separate holdout cases; do not tune prompts against their answers.

Track:

- Full research completion, separated from BUY/ADD eligibility.
- Unsupported factual claims and unresolved/incorrect citations, with human spot checks of whether cited passages actually support claims.
- Deterministic calculation correctness and appropriate abstention on missing inputs.
- Median and tail cost, latency, repeated tool calls and context bytes by stage.
- Cost per completed decision, including the failed attempts—not only the successful final call.

**Acceptance:** all mechanical leakage/citation/arithmetic/risk fixtures pass. Proposed first engineering target: at least 90% complete structured decisions on cases deliberately supplied with sufficient evidence, with no acceptance of materially unsupported BUY/ADD cases. This is a pilot target, not a measured result or profitability threshold.

## 2. Build traceable financial inputs before valuation

Inspired by FinRobot; our implementation should be small and focused on the current non-financial common-stock universe.

Add `trader/fundamentals.py`; extend `EvidenceProvider` and the existing research tools. Normalize revenue, operating cash flow, capital expenditure, cash, debt, share count and dilution into records such as:

```text
metric, value, unit, period_start, period_end, period_kind,
accession, filed_at, available_at, retrieved_at, evidence_id,
calculation_version, input_fact_ids, assumptions, missing_reason
```

Use fiscal-period-aware rules for quarter/YTD/annual data. Do not sum overlapping YTD observations or silently select a later restatement. Keep instant share counts separate from weighted-average shares. Attach source passages to adjustments that are not directly represented by XBRL facts.

First provide a transparent historical cash-flow bridge and a reverse valuation: what growth or normalized cash flow would today's reference price require? Then improve forward scenarios only when inputs are supportable. Distinguish FCFE from FCFF; match discount rates and cash/debt treatment to the chosen basis. Do not subtract debt twice or treat operating cash flow minus capex as automatically normalized distributable cash. Label stock compensation/dilution treatment and any exceptional adjustments.

Reuse `value_scenarios` and Decimal arithmetic. Keep unsupported sectors/methods explicitly unavailable. Comparable multiples can be a later cross-check, with dated peer selection, not a substitute for missing fundamentals.

**Acceptance:** hand-calculated fixtures for period overlaps, amendments, units, splits/dilution and capital bridges match exactly; every displayed numeric input has provenance. Insufficient inputs still produce a reasoned WATCH. Demonstrate resolution of a previously blocked input before spending on another full investigation.

## 3. Make completed research reusable and recoverable

TradingAgents' recovery pattern is useful, but we already have partial reuse and finalization recovery. Extend the existing implementation at validated stage boundaries.

In `ResearchDesk.specialist` and `DeepResearchTeam.run`, persist each validated report, its sources/calculations, dependency hashes, timestamps, prompt/schema/model versions and cost records. Use existing `Store` document kinds and append-only journal events. Recover only stages whose inputs, dependency reports and versions still match; new material evidence invalidates dependent stages.

Keep role-specific context: the catalyst agent does not need the complete accounting discussion; the final lead needs validated findings, open issues and resolvable evidence references rather than repeated transcripts. Preserve original artifacts and a read-by-ID path.

Reserve enough of the existing run budget for critique and synthesis before starting discretionary exploration. Choose those stage envelopes from measured pilot costs. If a stage cannot finish within the allowance, stop honestly. Resume must not release unknown reservations, repeat ambiguous requests, or create a second trading intent. Existing freshness, budget-date rollover and single-executor rules still apply.

**Acceptance:** crash after each completed stage and resume without re-running it; invalidate stale/changed dependencies; no duplicate intent; no lost reservations. Proposed comparison target: 30% lower median cost per completed decision on paired cases without degraded evidence quality. Measure rather than promise it.

## 4. Turn the current thesis cache into useful memory

Adapt FinMem's memory idea without importing its infrastructure.

Store three kinds of information through `Store`, exported through the existing telemetry path:

1. **Business facts:** durable but versioned; refreshed when a relevant filing changes them.
2. **Current thesis:** hypotheses, valuation assumptions, open questions, evidence IDs, catalysts and invalidation conditions.
3. **Outcome lessons:** dated observations about a resolved claim, including alternative explanations and uncertainty.

Give each entry `known_at`, source IDs, review/expiry time and supersession link. Retrieval initially uses symbol, topic, recency and unresolved status with a strict size cap. No vector database is required for a small universe. A lesson whose outcome is not yet known must never enter an earlier decision's context.

Extend existing targeted-news reuse to produce “what changed since the last review?” Invalidate the relevant financial or valuation stage when an input changes. The agent should revisit unresolved questions rather than repeatedly rediscover the business.

**Acceptance:** unchanged inputs avoid a full re-investigation; a changed filing invalidates affected stages; expired claims are not treated as current facts; future lessons cannot appear in historical cases.

## 5. Make X a measurable discovery source

Extend `trader/x_api.py` and the existing catalyst/adversarial roles. TradingAgents' sentiment filtering is a useful reference; we can implement a narrower version using our current tools.

Preserve post ID, author ID, timestamp, query/window, retrieval time and original URL. Deduplicate repeated posts and near-identical claims; cap the influence of one author or copied thread. Separate company-relevant facts, opinions and unrelated cashtag noise. Do not treat engagement as credibility or claim to reliably detect bots from sparse metadata.

Produce a small narrative record: claim, observed stance, unique authors/posts, supporting links, contradictory claims, primary-source corroboration and missing coverage. Save comparable sampling windows before describing a change in sentiment. A ten-post recency sample cannot establish population sentiment or statistically meaningful momentum.

Use X leads to select a primary-source verification task, not to bypass valuation or risk controls. Reuse existing request ceilings and caching. The new dollar-balance card remains account-wide; do not infer exact per-investigation spend from balance differences without accounting for other consumers, grants and adjustments.

**Acceptance:** copied posts do not multiply support; off-topic tickers are excluded; a false viral claim remains unverified; no baseline means no claimed sentiment change. Compare matched research with and without X before allocating it more budget.

## 6. Measure outcomes and allocate research intelligently

Adapt FinAgent's reflection concept into a testable review process, not autonomous prompt optimization.

Extend `trader/reports.py`, `Runner.due` and dashboard snapshots with outcome records. At decision time, register explicit claims, an evaluation horizon and a benchmark. At maturity, record thesis events separately from subsequent price returns. Forecast probabilities, if introduced, must refer to a defined event and horizon before applying calibration/Brier scoring; our present generic `confidence` field is not a calibrated probability of profit.

Start with prospective shadow decisions, recording all selected and abstained candidates. For research comparison only, use the first completed session close after a decision is locked as a clearly labeled common reference—not an asserted execution price. Actual strategy P/L must use broker fills, slippage, partial fills and fees. Report research/data costs alongside P/L, without silently altering broker NAV. Use matching-date cash, SPY/QQQ and a simple fixed-rule baseline; preserve dividend conventions or explicitly upgrade all comparisons together.

Use chronological development/holdout windows, point-in-time constituents where needed, publication availability times and immutable data snapshots. Current SEC responses alone are not a complete historical archive. Historical fixture success does not remove model-weight contamination. Forward paper observations are essential, and a short pilot does not establish an edge.

Finally, improve `research_batch` selection with deterministic material-event and review-due priorities, while retaining holdings/risk priority and a starvation limit for neglected symbols. Record why a symbol was selected and which candidates were skipped. Only add a learned opportunity score if forward evidence supports it.

**Acceptance:** no future source or outcome is visible to a decision; all cohorts include abstentions; mature claims reconcile to dated observations; dashboard distinguishes research correctness, shadow outcomes and real paper fills. Prompt/model changes require holdout comparison and review, never automatic promotion after a winning streak.

## Delivery order and implementation effort

Estimates are engineering effort for one developer, not elapsed market validation time. Allow roughly 25% contingency for data edge cases. No new framework, database or paid data subscription is needed for the first increment.

| Order | Deliverable | Estimated effort | Dependency |
|---|---|---:|---|
| 1 | Research-only evaluator, frozen baseline cases and stage metrics | 1–2 days | Existing journal/traces |
| 2 | Financial normalization and cash-flow bridge for current universe | 3–5 days | Baseline fixtures |
| 3 | Stage persistence, dependency invalidation and bounded context | 2–3 days | Stage metrics |
| 4 | Versioned thesis memory and change-only reviews | 1–2 days | Stage persistence |
| 5 | X claim filtering and comparable samples | 1–2 days | Evaluation harness |
| 6 | Outcome cohorts, cost/quality dashboard and research selection | 2–3 days | Versioned decisions and mature observations |

The first useful release is orders 1–3: an evaluation baseline, supported financial inputs and reliable recovery. Subsequent work should be justified by those results. These estimates exclude an exhaustive sector valuation library and the calendar time required for forward outcomes to mature.

Use SQLite as the existing authoritative local research/operational store and Cloud SQL Postgres as its queryable observability mirror. Add event/document kinds and narrowly scoped SQL views where useful; do not create a second mutable trading ledger. Dashboard additions should show stage completion, unresolved inputs, changed thesis assumptions, evidence quality, outcome cohorts and cost per completed decision. Keep full traces available on demand.

Existing paper-fill acceptance remains a separate prerequisite for execution readiness. This research plan neither resumes trading nor replaces that check.

## Deliberately deferred

Do not replace our framework, add celebrity-investor personas, implement high-frequency trading, introduce leverage/options, create automatic self-modifying prompts, or optimize position sizing from uncalibrated model confidence. Portfolio covariance/volatility sizing can be evaluated later after research quality and sufficient forward observations exist; current deterministic risk limits remain authoritative.

No external project was installed or run, no paid trader research was performed, and no runtime/configuration was changed for this review. The expected benefits above are hypotheses to measure, not claims of demonstrated alpha.
