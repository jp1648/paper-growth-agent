# Acceptance evidence

All automated execution examples are offline fixtures unless `PAPER_SMOKE=1` is explicitly set. A fixture is not an Alpaca execution.

| Requirement | Evidence |
|---|---|
| Initialize, account identity, distinct explicit experiments | `test_no_automatic_reset_and_explicit_new_experiment`, `test_new_account_new_experiment_preserves_old_history` |
| Structured research, valuation, adversarial review, decision journal | `test_full_graph_research_risk_execution_and_report`, `test_unknown_evidence_cannot_generate_order` |
| Deep Agents delegation, original filing retrieval, calculated valuation, role permissions, budget stops, targeted review and restart recovery | `tests/test_research_team.py` (real agent graphs, offline model/HTTP fixtures) |
| Deterministic pass/fail, long-only cash and exposure controls | `test_risk_rejects`, `test_no_short_sell`, `test_pending_buy_included_in_cash_and_exposure` |
| Broker submit is not a fill; restart preserves history | `test_submit_is_not_fill_and_restart_deduplicates` |
| Partial fills, duplicate/out-of-order observations | `test_partial_fills_duplicate_and_out_of_order` |
| Cancellation races, late fills, rejection/expiration | `test_cancel_race_keeps_reservation_and_accepts_fill`, `test_late_fill_after_terminal_cancel_refreshes_order`, `test_terminal_releases_unfilled_reservation` |
| Ambiguous timeout, eventual visibility | `test_timeout_looks_up_accepted_order_without_retry`, `test_unknown_timeout_pauses_and_preserves_reservation`, `test_recovered_unknown_order_then_fill_survives_restart` |
| Broker activity delay and reconciliation drift | `test_fill_event_before_activity_pauses_then_recovers`, `test_cash_drift_blocks_execution` |
| Reassess/close when thesis invalidates | `test_exit_thesis_invalidated_closes_position`, `test_review_deadline_reassesses_does_not_sell` |
| Avoid conflicting concurrent submissions | `test_concurrent_executors_cannot_submit_conflicting_orders`, `test_second_database_same_account_is_rejected` |
| Fees/capital/corporate action audit boundaries | `test_cash_movement_requires_explicit_acknowledgment`, `test_corporate_action_pauses_until_audited_adjustment` |
| Daily equity, benchmarks, separate costs, initial drawdown | `test_daily_benchmarks_matching_dates_and_costs_separate`, `test_first_day_loss_counts_toward_experiment_drawdown` |
| Cheap triggers and interruption records | `test_price_tick_below_threshold_does_not_research`, `test_scheduler_records_interruption_without_backfill`, `test_preopen_candidate_rechecked_at_open` |
| Paper endpoint, decimal API parsing, pagination, coverage and session closes | `tests/test_adapters.py`, `tests/test_risk.py` |
| Actual broker-confirmed simulated fill | `tests/test_paper_smoke.py` — **not run without explicit opt-in and credentials** |

## Required before starting the broker experiment

1. Dedicated Alpaca paper account with configured initial cash and no positions/open orders.
2. `ALPACA_PAPER_API_KEY` and `ALPACA_PAPER_SECRET_KEY` from that account.
3. Alpaca Basic real-time IEX data access for the initial free-data experiment, plus timely quote, halt and regular-session bars. IEX-only coverage is labeled in quotes and reports; stale or delayed data remains rejected. Optional SIP requires an explicit configuration change and entitlement.
4. `OPENAI_API_KEY`, a model supporting Responses API tools and structured outputs, current input/output token rates, and SEC application/contact user-agent.
5. Operator-reviewed common-stock universe, sector mappings and initial engineering risk limits.
6. Persistent database/control directory on a single execution host, and an externally configured scheduler.
7. Run offline tests, `init`, `reconcile`, then explicitly opt into the one-share paper smoke test during market hours if desired.

The automated suite validates operational behavior against fixtures and mocked HTTP contracts. It does not validate current account entitlements, live WebSocket delivery, model investment judgment or broker-fill realism. A successful live paper smoke test is required to claim an actual simulated broker fill.

Container checks: `tests/test_operations.py` covers durable daily reservations, atomic concurrent admission, retained unknown usage, date rollover, continued reconciliation with zero research budget, health staleness, maintenance during blocked research, and timed-out child cleanup. Docker build and a live paper-account maintenance command must also pass before deployment; neither places an order.

The 2026-10-28 iterative readiness review added `tests/test_readiness.py` for cancellation under reconciliation failure, account/order identity, cached execution recovery, premarket budget reuse, news invalidation, paused reporting/research, timezone replay, feed consistency, zero-equity returns, backup restoration/privacy, cooldowns, SQL filtering, date-bound budgets, safe API diagnostics and immutable execution-time risk evidence. See [production review](production-review.md) for fixes, measured validation and outstanding external gates.
