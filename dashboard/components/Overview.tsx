import { Curve, money } from "./DataViews";
import type { Snapshot } from "../lib/types";
export default function Overview({
  snapshot: s,
  setTab,
  setSelected,
}: {
  snapshot: Snapshot;
  setTab: (tab: string) => void;
  setSelected: (id: string) => void;
}) {
  const p = s.portfolio,
    runs = s.runs,
    daily = s.daily[0];
  const samples = daily?.daily_equity || [],
    missing = daily?.missing_sessions || [];
  const accepted = runs.filter((r) => r.status === "accepted").length;
  return (
    <>
      <div className="metrics">
        {[
          [
            "Portfolio equity",
            money(p.equity),
            "Broker-reported simulated equity",
          ],
          [
            "Portfolio P/L",
            money(p.total_pnl),
            "Net of external capital flows",
          ],
          ["Cash balance", money(p.cash), "Before reserved buy commitments"],
          [
            "Research cost",
            money(s.research_cost_known_usd ?? p.model_cost_usd_estimate, 4),
            s.research_cost_unknown_calls
              ? `Known estimate · ${s.research_cost_unknown_calls} unpriced calls`
              : "Estimated lifetime model spend",
          ],
        ].map(([title, value, caption]) => (
          <section className="metric" key={title}>
            <span>{title}</span>
            <strong>{value}</strong>
            <small>{caption}</small>
          </section>
        ))}
      </div>
      <div className="overview-grid">
        <section className="panel">
          <div className="panel-title">
            <div>
              <h2>Portfolio trajectory</h2>
              <p>Recorded daily closes · simulated USD</p>
            </div>
            <span className="tag">{samples.length} observations</span>
          </div>
          <Curve samples={samples} />
          <div className="chart-footer">
            <span>
              <i /> Agent equity
            </span>
            <span>Model costs tracked separately</span>
          </div>
          {missing.length > 0 && (
            <p className="notice">Missing sessions: {missing.join(", ")}</p>
          )}
        </section>
        <section className="panel research-health">
          <div className="panel-title">
            <div>
              <h2>Research readiness</h2>
              <p>Latest {runs.length} saved investigations</p>
            </div>
            <span>◎</span>
          </div>
          <div className="readiness">
            <strong>
              {accepted}
              <small> / {runs.length}</small>
            </strong>
            <span>accepted investigations</span>
          </div>
          <div className="progress">
            <i
              style={{
                width: `${runs.length ? (accepted / runs.length) * 100 : 0}%`,
              }}
            />
          </div>
          <p>
            {accepted
              ? "Accepted research passed the report gates. Execution still requires risk checks and a confirmed broker fill."
              : "No complete investment decision has passed all research gates yet."}
          </p>
          <div className="mini-stat">
            <span>Failed / incomplete</span>
            <b>{runs.length - accepted}</b>
          </div>
          <div className="mini-stat">
            <span>Open paper orders</span>
            <b>{s.open_orders.length}</b>
          </div>
          <button className="text-button" onClick={() => setTab("Research")}>
            Inspect research →
          </button>
        </section>
      </div>
      <section className="panel">
        <div className="panel-title">
          <div>
            <h2>Current positions</h2>
            <p>Reconciled broker holdings</p>
          </div>
          <span className="tag">{s.positions.length} positions</span>
        </div>
        {s.positions.length ? (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  {[
                    "Symbol",
                    "Shares",
                    "Average entry",
                    "Market value",
                    "Unrealized P/L",
                  ].map((h) => (
                    <th key={h}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {s.positions.map((x) => (
                  <tr key={x.symbol}>
                    <td>
                      <b>{x.symbol}</b>
                    </td>
                    <td>{x.qty}</td>
                    <td>{money(x.avg_entry_price)}</td>
                    <td>{money(x.market_value)}</td>
                    <td>{money(x.unrealized_pl)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="position-empty">
            <span>⊞</span>
            <div>
              <strong>No positions held</strong>
              <p>
                Capital remains in cash. Research activity is not trading
                performance.
              </p>
            </div>
            <b>{money(p.cash)}</b>
          </div>
        )}
      </section>
      <section className="panel recent">
        <div className="panel-title">
          <div>
            <h2>Latest investigations</h2>
            <p>Saved outcomes, including unsuccessful attempts</p>
          </div>
          <button className="text-button" onClick={() => setTab("Research")}>
            View all →
          </button>
        </div>
        {runs.slice(0, 4).map((r) => (
          <button
            className="run-row"
            key={r.id}
            onClick={() => {
              setSelected(r.id);
              setTab("Research");
            }}
          >
            <span className="run-icon">⌕</span>
            <span className="run-info">
              <strong>{r.id}</strong>
              <small>
                {r.model_calls} model calls · {r.tool_calls} tool calls
              </small>
            </span>
            <span className={"badge " + r.status}>{r.status}</span>
            <b>{money(r.cost, 4)}</b>
            <span>↗</span>
          </button>
        ))}
        {!runs.length && (
          <p className="empty-text">No research audits recorded.</p>
        )}
      </section>
    </>
  );
}
