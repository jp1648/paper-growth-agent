# Research evaluation evidence

`baseline-v1.json` remains the original synthetic orchestration suite. Its old
valuation fixtures do not satisfy the financial-provenance-v2 scoring contract.
Do not rewrite that baseline or interpret scripted replay as model performance.

## Annual filings v2 — candidate cohort, not a promotion result

`annual-filings-v2.json` adds four research-only cases: an original FY2025 annual
filing case and a deliberately incomplete evidence variant for each of Apple
(development) and Microsoft (holdout). The complete cases contain the extracted
full original filing text and financial records selected by exact accession from
SEC companyfacts. The manifest records original download hashes, extracted-text
hashes, accession, acceptance timestamp and independent arithmetic inputs.

These are **reconstructed historical exercises**, not archived point-in-time
market data or an investment backtest. The downloads were obtained on November 6,
2026. Exact-accession filtering excludes later restatement records, but does not
prove what the API or extraction pipeline exposed at the historical cutoff.
The reference prices (Apple $200, Microsoft $400) are explicitly hypothetical
scenario inputs. They cannot support historical returns, alpha or fill claims.
Raw downloads are retained locally under ignored
`state/audit-20261106/cohort-sources/`; the frozen extracted evidence is in Git.
No provider credentials or user data are included.

The Microsoft original HTML is 8,158,186 bytes, above the production SEC retrieval
limit of 8,000,000 bytes. Its frozen extracted text can exercise reasoning, but
cannot establish that live retrieval succeeds. Do not raise a safety bound just
to pass the cohort; separately test bounded document access or honest abstention.

## Predeclared review and use

- Never use Microsoft candidate outputs for prompt tuning. Inspecting the fixed
  input and rubric for construction is not a model evaluation. Changes after
  candidate outputs are observed require a new cohort/version and new holdout.
- The complete-case rubric is provisional until an independent reviewer confirms
  sufficiency. An annual report can support a valuation exercise; it does not
  prove a contemporary catalyst or justify a purchase. WATCH is acceptable even
  with a supported valuation. No action is predetermined as financially correct.
- Required semantic checks: exact period/units and original fact support; avoid
  overlapping YTD sums; distinguish historical proxy from normalized cash flow;
  justify growth/discount/terminal assumptions; discuss debt, dilution/SBC,
  nonrecurring items and capital commitments; cite contrary evidence and explicit
  invalidation conditions. Unsupported or material gaps must block BUY/ADD.
- Apple review must address product/customer/geographic concentration, services
  economics and capital returns using the filing. Microsoft review must address
  cloud/AI capital intensity, lease commitments and SBC/dilution using the filing.
  These are questions for review, not asserted conclusions or forced forecasts.
- Missing-evidence variants remove capex and the filing itself. They must finish
  WATCH with an explicit material gap and no fabricated valuation. Their modified
  sources are labeled controlled fixtures, never original complete SEC responses.
- Report mechanical validity and independent semantic acceptance separately.
  Development run `annual-dev-20261107-2` collected Apple outputs: the complete
  case mechanically passed with WATCH; the missing-evidence variant failed.
  Neither has independent semantic approval. See `docs/live-readiness.md`.
- Two companies and two sufficient cases are a starter cohort, not enough to meet
  the release gate. Expand the declared universe/regimes with independently
  reviewed unseen cases before any promotion; retain the >=90% completion target
  and zero unsupported BUY/ADD requirement. Do not pool easy abstentions into the
  sufficient-case denominator.
- Run only through the research evaluator with explicit model-run opt-in, stable
  run IDs and the approved daily budget. No broker or execution graph is involved.

## Separate semantic reviews

The current scoring contract is `financial-provenance-v5`. A candidate's embedded
`human_review` is rejected; a model output cannot approve itself. A reviewer must
supply a separate JSON file to `paper-agent evaluate --reviews reviews.json`
alongside the existing `--suite`, `--responses`, `--output` and `--run-id` options.
The evaluator reads this file without changing the original recording.

The review file has `version: 1`, `suite_hash`, `recording_hash`, and a `cases`
object keyed by reviewed case IDs. Each review contains `case_hash`,
`candidate_hash`, a nonempty `reviewer`, timezone-aware `reviewed_at`, explicit
booleans `independent`, `claims_supported`, `abstention_reason_correct`, and
nonempty `notes`. Approval requires all three booleans to be true and all hashes
to match. Hashes use `trader.evaluation.fingerprint` on the parsed JSON objects;
they are not hashes of pretty-printed file bytes. Partial review is allowed;
unreviewed cases remain pending. An invalid or mismatched review cannot produce
an accepted result. Results retain the review-file content hash for audit.

Hash binding prevents stale approvals from being silently reused; it does not
authenticate the named reviewer. Before promotion, the operator must verify the
reviewer's identity and independence and retain the original review file. No
review has been supplied or claimed by this implementation. Results from earlier scoring contracts must be rescored into new output files
before comparison under the current contract; old results are not rewritten.
