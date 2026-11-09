export type Amount = string | number | null;
export interface EquitySample {
  date: string;
  equity: Amount;
}
export interface ResearchRun {
  id: string;
  status: "accepted" | "failed" | "incomplete";
  decision: Record<string, unknown> | null;
  reports: Record<string, unknown>;
  calculations: Record<string, unknown>;
  cost: Amount;
  model_calls: number;
  tool_calls: number;
  sources: { id: string; title?: string; type?: string; url?: string }[];
}
export interface TraceEvent {
  event_id?: string;
  at: string;
  name: string;
  phase: string;
  span_id: string | null;
  parent_span_id: string | null;
  detail: string;
  truncated: boolean;
}
export interface Snapshot {
  x_credits?: {
    status: string;
    checked_at: string;
    prepaid_balance?: number;
    free_balance?: number;
    total_balance?: number;
    free_grants?: { amount: number; expires_at: string }[];
  };
  generated_at: string;
  portfolio: {
    as_of: string;
    kill_switch: boolean;
    reconciliation: { ok: boolean } | null;
    equity: Amount;
    total_pnl: Amount;
    execution_quality?: {
      fill_count: number;
      referenced_fill_count: number;
      reference_coverage_complete: boolean;
      fill_notional_usd: Amount;
      aggregate_slippage_usd: Amount;
      aggregate_slippage_bps: Amount;
      referenced_slippage_usd: Amount;
      scope: string;
      limitations: string[];
    };
    economic_result?: {
      broker_pnl_usd: Amount;
      recorded_operating_cost_usd_estimate: Amount;
      pnl_after_recorded_costs_usd_estimate: Amount;
      unknown_cost_requests: number;
      unresolved_research_runs: number;
      research_run_ledger?: {
        provider_reconciled_usd: Amount;
        estimated_usd: Amount;
        unresolved_recorded_usd: Amount;
        settled_runs: number;
        unresolved_runs: number;
        unbounded_unknown_runs: number;
      };
      complete: boolean;
      scope: string;
      limitations: string[];
    };
    cash: Amount;
    model_cost_usd_estimate: Amount;
    market_data: { coverage: string };
    limitations: string[];
    risk_reviews: string[];
  };
  runs: ResearchRun[];
  daily: { daily_equity: EquitySample[]; missing_sessions: string[] }[];
  positions: {
    symbol: string;
    qty: Amount;
    avg_entry_price: Amount;
    market_value: Amount;
    unrealized_pl: Amount;
  }[];
  open_orders: unknown[];
  spend_breakdown?: {
    source: string;
    known_usd: Amount;
    requests: number;
    unknown_requests: number;
  }[];
  research_cost_known_usd: Amount;
  research_cost_unknown_calls: number;
  coverage: string;
}
export interface OverviewData {
  snapshot: Snapshot | null;
  at: string | null;
}
