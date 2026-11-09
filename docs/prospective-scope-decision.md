# Prospective study scope and funding decision — draft

Status: research-only expansion selected under the user’s November 9 delegation and explicitly confirmed by the user; trial not activated.
This resolves feasibility before the opening manifest is frozen. The existing
protocol remains five distinct reviewed companies, 30 independently reviewed
completed investigations, ten closed position episodes, and at least 60 regular
sessions AND 12 calendar weeks. Insufficient observations mean INCONCLUSIVE;
never force trades, loosen thresholds after results, or count historical exercises.

## Scope choice

| Option | Research coverage | Permitted execution | Consequence |
|---|---|---|---|
| Recommended for review | Five explicitly named companies, including current AAPL/MSFT/AMZN | Current three only | Two additional research-only companies can broaden reasoning evaluation without expanding execution authority. Freeze their identities, sectors and inclusion rule before activation. Independent review must confirm the protocol counts research-only investigations. |
| Expand both | Five explicitly named companies | Five only after account/asset/risk review and explicit approval | Changes candidate universe and exposure; requires a new frozen configuration and prospective window. |
| Retain three | Current three | Current three | Cannot meet the current five-company requirement. A separately justified, versioned protocol revision would need explicit approval before any outcomes; do not silently lower the threshold. |

Selected: research AAPL, MSFT, AMZN, PG and CAT; execution remains AAPL, MSFT and
AMZN. PG (Procter & Gamble, CIK 80424) adds Consumer Staples; CAT (Caterpillar,
CIK 18230) adds Industrials. Selection is for business-model diversity before any
candidate outputs or prospective returns, not a buy recommendation. Issuer identity
was verified from [PG’s SEC filing](https://www.sec.gov/Archives/edgar/data/80424/000008042425000076/pg-20250630.htm)
and [CAT’s SEC filing](https://www.sec.gov/Archives/edgar/data/18230/000001823026000008/cat-20251231.htm).
The current five-company requirement remains intact.
SPY/QQQ passive benchmark calculations are not additional company investigations.
The new research-only mapping is enforced separately from the order universe.
Graph execution explicitly refuses research-only symbols; direct order requests
still face the unchanged execution allowlist. Prospective research-only reviews
may count toward research coverage, but never closed-position episodes or evidence
that five symbols are tradable. Deployment evidence is recorded separately.

For any expansion, select the additional companies by a documented pre-outcome
sampling rule, with supported filings and business/sector coverage. Do not choose
symbols because previous model answers or later returns looked favorable. Frozen
historical development and holdout exercises remain separate from prospective
company reviews and cannot satisfy the prospective count.

## Investigation schedule and cost feasibility

Selected sampling design; exact calendar remains to be frozen before activation: three scheduled
investigations per week over twelve weeks, yielding 36 opportunities. Rotate the
five reviewed companies in a frozen order (seven each, with one predeclared eighth
opportunity), spread across the study.
Fix the exact regular-session dates using the broker calendar before activation;
retain canceled, delayed, failed and abstaining attempts in the record. Thirty-six
opportunities leave some failure capacity but do not guarantee 30 accepted completions. If the information minimum
is not reached, report INCONCLUSIVE rather than automatically extending the window.
Safety-triggered reviews take priority and still consume the same budget.

The separate sufficient-evidence completion threshold is at least 90%. If all
36 scheduled attempts are preclassified as sufficient-evidence cases, at least
33 must complete (ceiling of 32.4), so at most three failures fit that threshold.
Reaching 30 independently reviewed completed investigations alone does not satisfy
33-of-36 completion. Freeze sufficient/missing-evidence classification and the
inclusion rules for safety-triggered attempts before outcomes. Missing-evidence
abstentions cannot pad the sufficient-case denominator; failures, delays and
safety reviews remain recorded. Never reclassify an unsuccessful sufficient case
after seeing its result merely to improve the completion rate. Independently
reviewed economic acceptance remains distinct from mechanical completion.

The current authorized model caps are $6 per New York day and $5 per run.
Thirty-six runs each consuming the maximum would cost $180 in model usage. This is
scenario arithmetic, not a forecast, new budget approval or guaranteed successful
research. Two $5 runs cannot fit the same $6 day. Schedule at most one full-cap
attempt per day; any additional calls must fit the actual remaining allowance,
confirmed provider credit and conservative buffer. Unknown charges/reservations
remain liabilities. Failed attempts and required independent review costs count.
At 60 sessions the daily ceilings sum to $360, but that is neither a funding
balance nor authorization to spend that total. Existing per-call/run safeguards
continue to control every attempt; do not stretch capped runs to meet a quota.

The retained confirmed $100 grant expires November 29, 2026. It is partly used,
not $100 presently available. Even an unspent grant would not fund the above
$180 maximum-use scenario and does not establish funding throughout a twelve-week
study beginning in November. Do not assume renewal or purchase credits. Before
activation, document a realistic funded schedule and what happens when funding
expires; prove available credit before each paid run. This document does not
certify the current provider balance or costs outside this project.

Model caps exclude infrastructure and data-provider charges. The Google Cloud
export metadata on November 9 now reports eight rows, but the bounded November
1–10 UTC query for this project returned no rows. That is missing attributed cost
evidence, not free operation. Required costs include executor, database,
dashboard, logging, backups, storage, networking, builds and paid data; allocate
shared resources before activation and retain gross cost separately from credits.
Cash interest/fees require the actual account's terms and enrollment evidence.
The pending account-terms question is not answered by an advertised yield.

## Activation checklist for this decision

1. Record the operator's chosen scope and exact symbols/sectors; obtain independent
   agreement on how research-only companies count if that option is selected.
2. Implement and verify any required research/execution separation without changing
   the approved execution universe implicitly. Freeze code, prompts and configuration.
3. Freeze calendar, sampling rule, planned attempts and funding/expiry contingency;
   do not erase failed attempts or fund retries by deleting reservations.
4. Resolve account, cost and research-acceptance prerequisites; then obtain explicit
   approval of the opening manifest. None of this document starts the clock.

No broker orders, live credentials, automatic resume, funding purchase, protocol
threshold reduction or historical-result rewrite is authorized by this draft.


The existing held-first runner is not the proposed fixed study schedule. It cannot
claim fair budget access across five companies. Before trial activation, freeze
and implement the calendar/rotation with explicit treatment of safety reviews;
keep current monitoring priority for held-position safety. Enabling five research
symbols does not complete scheduling, activate the trial or resume trading.

## Offline schedule draft

`paper-agent draft-paper-schedule --input schedule-input.json --output schedule-draft.json`
produces a reviewable calendar/rotation artifact without loading broker credentials,
calling providers, writing to the ledger or enabling the runner. The input contains
`study_id` (bounded lowercase letters/digits/hyphens), `start_session` (a future
canonical date), and `calendar` (retained broker rows with date/open/close).
The output binds the complete input with SHA-256 and refuses to overwrite a file.
Calendar authenticity and completeness still require source review.

The proposed deterministic selection is first, middle (index floor(n/2)), and last
regular session in each of twelve seven-day blocks anchored on the starting
session. A four-session holiday week uses its first, third and fourth sessions;
a three-session week uses all three. A block with fewer than three supplied
sessions is rejected for explicit exception review. NY timestamps retain early
closes and daylight-saving offsets. Rotate AAPL/MSFT/AMZN/PG/CAT across the 36 slots:
AAPL has eight and each other company seven, fixed before candidate outcomes.

The observation window ends at the later of session 60 and the first session at
or after start plus 84 days, as already declared in `forward-paper-v1`. The 36
scheduled investigations occupy the first twelve seven-day blocks; observation
continues through the precomputed endpoint. Holidays cannot silently shorten the
minimum window, and later outcome insufficiency cannot extend it automatically.

Missed, failed and safety-displaced slots remain in the record; no automatic
rescheduling or extra paid catchup is implied. Safety reviews retain priority and
must be recorded separately, under the same daily budget. This builder does not
classify evidence sufficiency, authorize funding or implement runner dispatch.
Its output is always `draft_not_activated`. Authenticate the actual calendar,
review/freeze the exact dates and classification, implement dispatch/accounting,
and approve the complete opening manifest before activation. No actual study
start date has been selected by adding this command.

Any observation tail beyond the twelve scheduling blocks has monitoring and
safety reviews but no additional scheduled investigations. AAPL's eighth slot is
a predeclared unequal convenience allocation, not a representative or randomized
sample; per-company coverage must remain visible in reporting. Before dispatch
integration, freeze the invocation time and missed-slot cutoff, the treatment of
safety attempts in each denominator, and the sufficiency classification rules.
The slot list itself proves none of those semantic decisions.

## Selected dispatch and denominator rules — November 9

Independent review approved these conservative rules for implementation, not
activation. Each scheduled slot may be claimed from regular open plus 30 minutes
(inclusive) until regular close minus 30 minutes (exclusive). A run claimed in
that window may finish under its existing bounded run deadline; retain actual
start/end times. An atomic durable claim precedes graph invocation, and a separate
invocation claim prevents repeated graph starts after failure. Crashes and unknown
responses consume the attempt; no same-slot paid retry or later-date catchup.

The fixed scheduled denominator is 36. Require at least 33 mechanical completions
and 30 independently economically accepted scheduled investigations, covering all
five companies. Before opening, verify primary-source coverage and freeze a
sufficient-case template for each company. This does not claim future packets or
provider availability are already verified. Retrieval failures remain failures,
not retrospective reasons to shrink the denominator. Safety reviews have their
own recorded stratum; their costs/failures/actionable-decision reviews stay in
overall reporting but do not pad either scheduled numerator. No pause state
permits scheduled or safety model research; reconciliation/monitoring continues.

Runtime dispatch is dormant unless the experiment ledger contains an explicitly
operator-approved `paper_study/active` record. Bind experiment, full source digest,
configuration digest, schedule digest, approval identity/time and acceptance
packet digest. These fields record an operator attestation; their mere presence
cannot substantiate account eligibility, research acceptance or source authenticity.
No production approval record has been created. The approved schedule must fit
inside the original experiment expiry; there is no implicit extension or reset.

Funding requires a separate retained provider-confirmed available-credit record,
verified within 30 minutes, unexpired beyond the bounded run deadline, with a
minimum $10 buffer and a digest of the current settled global model-spend ledger.
Every active-study investigation, including safety research, rechecks this inside
the atomic model-budget reservation. Spending changes invalidate the receipt;
unknown charges block. A full capped run must fit both the remaining $6 daily
allowance and available credit; unbounded research is forbidden. No runtime code
purchases credits or infers renewal. Ordinary manual graph calls cannot bypass the
study route. Calendar/clock/config/source changes fail closed; existing execution,
reconciliation, risk and paper-only controls remain authoritative.

Missing funding, pause and safety displacement reasons are retained separately
and attached to missed receipts. The runner preserves post-close reporting and
original experiment-expiry cancellation behavior. The study record does not
resume trading. A production installation/activation procedure still needs the
complete substantive opening packet and explicit approval.
