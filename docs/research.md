# Research team and APIs

The outer LangGraph owns portfolio context, research, deterministic risk, serialized paper execution, and reconciliation. The research node invokes a local Deep Agents lead with four named compiled subagents. All five have actual model/tool loops. No LLM is continuously running.

| Role | Tools and responsibility |
|---|---|
| Lead / portfolio manager | Plan with `write_todos`, delegate with native `task`, inspect sources, compare with holdings/cash, return the final structured Decision after all four reports exist. |
| Business quality | SEC financial facts, configured peers, original filing passages; growth durability, cash generation, debt, dilution, concentration, accounting quality. |
| Catalysts | Recent vendor news and SEC earnings/material-event exhibits; what changed, dated catalysts, thesis-development horizon, monitoring triggers. |
| Valuation | Uses business/catalyst reports and the Decimal scenario tool; explicit assumptions, bear/base/bull values and sensitivity. |
| Adversarial | Original-source tools and all specialist reports; independently seek contrary evidence, challenge valuation/timing, identify invalidation conditions and unresolved gaps. |

Business and catalyst work can run concurrently. Valuation requires both reports; adversarial review requires all three. The lead may receive cached completed specialist results, but cannot omit mandatory reports. A critique with unresolved material gaps converts BUY/ADD into WATCH. The existing deterministic risk gate still rechecks all execution constraints immediately before submission.

## Model assignments

The configured model routes are deterministic; the LLM cannot change models or the shared budget. Sol handles most specialist work, Astra handles the lead's planning and synthesis, and Terra handles focused news/event review. All roles still pass the same evidence, structured-output and deterministic risk gates.

| Role | API model | Reasoning | Input / output USD per million tokens |
|---|---|---|---|
| Default: business, valuation, adversarial | `gpt-6.1-sol` | medium | $2 / $10 |
| Research lead: planning, delegation, synthesis | `gpt-6-astra` | high | $10 / $50 |
| Catalysts: focused news/event review | `gpt-6.1-terra` | low | $1 / $5 |

Standard short-context rates checked 2026-10-28 against the official [Sol](https://developers.openai.com/api/docs/models/gpt-6.1-sol), [Astra](https://developers.openai.com/api/docs/models/gpt-6-astra) and [Terra](https://developers.openai.com/api/docs/models/gpt-6.1-terra) model pages. Verify rates before running. Each `role_models` override includes its own model, reasoning and rates; unspecified roles inherit the top-level configuration. Requests use the Responses API with Standard processing (`service_tier="default"`), no automatic model substitution, no retry, and no stored responses. Cost events record role, model, reasoning, token counts and applied rates. Reservations and actual-usage estimates share one per-run budget across models.

The selected Terra model is also the user's preference for future computer-use flows. No computer-use/browser worker exists in the trading app yet: choosing a computer-use-capable model does not enable browser automation. Research currently retrieves SEC and news evidence through bounded API tools. It never operates the brokerage UI.

## Required services

| Service | Used for | Access/configuration |
|---|---|---|
| OpenAI Responses API | Lead and specialist reasoning, function calls, structured output | `OPENAI_API_KEY`; configured default and role-specific models supporting tools and structured outputs; current per-model input/output token rates. Usage-based billing is separate from simulated returns. |
| SEC EDGAR data APIs and Archives | Company facts, material filing metadata, full filing and earnings-exhibit passages | Public, no API key. Configure `sec_user_agent` with application name and contact email. Fixed SEC hosts, rate-limited reads, no redirects. |
| Alpaca Trading API, paper endpoint only | Account, positions, orders, fills, calendar, order updates | `ALPACA_PAPER_API_KEY`, `ALPACA_PAPER_SECRET_KEY`; dedicated paper account. |
| Alpaca Market Data and News APIs | IEX quotes, historical session bars, vendor news, price/news streams | Currently the same paper credentials; account-specific entitlements must be verified. Start with free IEX data. SIP is an explicit optional upgrade; no automatic feed switches or delayed feeds. |
| Nasdaq Trader public halt RSS | Existing deterministic eligibility check | No separate key. Unknown/stale halt status blocks orders. |

Deep Agents, LangGraph and SQLite are local libraries/storage. No Deep Agents, LangSmith, vector database, or separate web-search API key is required. LangSmith tracing is disabled for these runs. This change does not select or implement Tradier/Schwab: those remain possible replacements for the separate market-data adapter.

As checked against Alpaca documentation on 2026-10-28, Basic real-time equity data is IEX-only; its all-US-exchanges Algo Trader Plus plan is listed at $99/month. The user chose to begin with the free IEX feed and assess operations and strategy behavior before considering an upgrade. No subscription was purchased. IEX quotes are live venue observations for US stocks, not consolidated quotes or market-wide volume. Model usage remains separately billed.

## Evidence and isolation

Tools can retrieve only configured companies and SEC document URLs discovered from their filing indexes. They cannot fetch arbitrary model-supplied URLs, access local files or run a shell. Each agent receives a state-backed virtual filesystem; it is not the repository or `.env`. Neither lead nor specialists have broker tools, credentials in their context, or tools to change risk configuration. External text is untrusted source material.

Retrieved passages have content-derived evidence IDs, URL, publication and retrieval timestamps, offset and truncation flag. News IDs also include a content hash so edited stories cannot overwrite earlier evidence. Each completed report cites existing evidence IDs; existence checks do not prove a claim is supported. Sources, assumptions, calculations, specialist results and model usage are journaled. Completed results and previous theses are durable in SQLite; scratch files are ephemeral.

For a fresh news-triggered reassessment, the catalyst specialist can conclude that the prior business and valuation reports remain applicable. Reuse requires each report to be younger than `research_cache_hours`; their original timestamps are preserved. Material changes, uncertainty, stale reports, full reviews or weekly reviews require the full team. Adversarial review is never skipped.

## Bounded runs and limitations

Engineering defaults per investigation: 24 model calls, 48 tool calls, 600 seconds, 180,000 bytes of model-request context, 3,000 maximum output tokens per call, and $5 estimated model spend. The lead and all specialists share these limits, including parallel calls. Tool/model checks enforce the deadline between steps; an in-flight HTTP operation can finish after the deadline, but no decision is accepted afterward. OpenAI request timeout is 60 seconds and automatic retries are off.

Before a model request, the budget uses the selected role’s token rates and reserves a conservative input allowance based on UTF-8 payload bytes plus framing overhead and the maximum output tokens. Returned token counts replace that reservation at configured price ceilings. Input ceilings must cover cache writes as well as uncached input; the shipped settings cover Standard short-context 12h writes and deliberately ignore cache-read/incentive savings. These remain estimates until provider reconciliation. Reverify ceilings for different models, context lengths, processing tiers or regional endpoints; this is an estimated guard, not a provider billing cap. Failed calls may be billed with unknown usage. Repeated runs also share `research_daily_cost_usd` (selected $5/day pilot, configurable, zero disables new research). Durable SQLite reservations prevent restarts or concurrent runs from silently resetting the daily allowance. Unknown usage/crashes retain conservative reservations. Dates are New York run-start dates; ongoing investigations stop before making another call after midnight. Reconciliation and reporting continue when the research cap is exhausted; the cap is an application estimate, not an OpenAI billing limit. Hidden summarizer model calls are disabled; excessive context fails closed instead of silently adding cost.

The initial valuation tool discounts per-share annual cash flows and a terminal cash-flow multiple across three scenarios, using Decimal arithmetic. Assumptions remain model judgments. It is not a universal valuation method; unsupported businesses or insufficient evidence should be reported as gaps and cannot justify purchases. No general web browsing, paid transcript feed, automatic universe expansion, or external investor-relations crawler is included. SEC earnings exhibits are available only when filed; news is a bounded vendor feed. Coverage gaps must remain explicit.

## Validation and setup

Offline tests use real compiled Deep Agents graphs, scripted chat models and mocked HTTP responses. They exercise delegation (including parallel specialists), filing passage tools, deterministic calculations, review vetoes, forbidden tools, budgets, targeted reuse, preserved report ages, and restart recovery. An SDK wire-contract test checks `/v1/responses`, function tools, strict output schemas, `store=false`, and refusals without network calls.

Load the saved credentials explicitly with `uv run --env-file .env paper-agent ...` or a service secret manager. Set the model, token rates, SEC contact, stock universe and Alpaca access before running. `research SYMBOL --run-key ID` can submit paper orders after research passes; it is not a read-only demonstration. No paid model run or actual broker fill is claimed by offline tests.

References: [Deep Agents overview](https://docs.langchain.com/oss/python/deepagents/overview), [subagents](https://docs.langchain.com/oss/python/deepagents/subagents), [state-backed filesystems](https://docs.langchain.com/oss/python/deepagents/backends), [OpenAI structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs), [SEC APIs](https://www.sec.gov/search-filings/edgar-application-programming-interfaces), [Alpaca data coverage](https://docs.alpaca.markets/us/docs/about-market-data-api).


### Direct X discovery (current)

The research team now uses X API v2 recent search. Save the app Bearer Token in
`state/x-api.env` as `X_BEARER_TOKEN=...` (private, mode 0600), or supply that
environment variable. `x_enabled`, `x_daily_calls` (default 6), and `x_cache_hours`
(default 4) control discovery. Two one-page queries per symbol retrieve at most
10 posts each: general discussion and contrary claims. No pagination or retries.
The six-attempt daily ceiling bounds retrieval to 60 returned posts, not a dollar
guarantee; X charges separately from model usage. Actual billed cost remains unknown
until reconciled with X usage. Attempts and results have stable operation IDs.
Missing credentials do not consume an attempt. Failed, partial or ambiguous
requests persist a block; crash-pending attempts also prevent further requests.
Review provider usage before explicitly clearing `x_control/blocked` or resolving
an unknown `x_discovery` operation. Do not delete reservations to retry.

Saved evidence includes original text, timestamps, post URLs, and public metrics.
It is untrusted, sampled discussion, not representative sentiment or a price feed.
Flovia is no longer called by research; its historical evidence and blocks remain.
[Official X recent-search guide](https://docs.x.com/x-api/posts/search/quickstart/recent-search).

### X discussion through Flovia (legacy)

Set `FLOVIA_KEY` in the private `.env` and `flovia_x_enabled = true` in config.
Before the research workers start, two fixed searches fetch public cashtag discussion
and contrary claims for the configured stock. Each is one recency-sorted page of
at most 10 English posts from the last seven days, excluding reposts. Sources retain
the original response (including provider attribution, post IDs and timestamps),
query, retrieval time and stable operation ID. The catalyst and adversarial agents
read these saved sources with `read_source` and cite original posts. They distinguish
observed discussion from unverified claims and independently corroborated facts.
No numeric sentiment score or claim of a measured change is fabricated from one sample.

Defaults: four-hour cache, six X search attempts per UTC day across the experiment.
X calls are serialized and journaled separately from model spend and portfolio returns.
The status check requires accepted existing consent, available windows and **zero**
purchased balance; a positive balance disables lookups to avoid paid fallback.
Flovia has no documented per-call free-only flag: do not fund this Flovia account
concurrently with lookups. Free capacity is not guaranteed by a status response.

A refusal or ambiguous submission persistently pauses X requests in
`flovia_control/blocked`; ambiguous operations remain in `flovia_x` with their IDs.
Do not clear this state or create replacement operations until the original has
been reconciled and the provider permits resuming. Provider outages leave explicit
coverage gaps and do not stop SEC/news research. X availability never bypasses
quote freshness, risk checks, or the trading pause. No background X polling is added:
these snapshots run only during otherwise-authorized finite research runs.

Research context bounds: older source-fetch tool replies are replaced only in the
next model request by explicitly labeled 1200-character previews; graph history and
the original evidence remain unchanged. The most recent tool batch and specialist
reports remain intact. Long evidence passages in specialist initial context are
also previews, retrievable with read_source. Each specialist has ten tool calls,
then tools are removed and it must return a structured report with evidence gaps.
The shared 48-tool, model-call, time and dollar guards still apply. A bounded report
is not proof that the evidence is sufficient to trade.
