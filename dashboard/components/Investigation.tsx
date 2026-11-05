import { Value, label, money, time } from "./DataViews";
import type { ResearchRun, TraceEvent } from "../lib/types";
export default function Investigation({
  run,
  tab,
  events,
  traceError,
  setTab,
}: {
  run: ResearchRun | undefined;
  tab: string;
  events: TraceEvent[];
  traceError: string;
  setTab: (tab: string) => void;
}) {
  return (
    <section className="panel investigation">
      {!run ? (
        <div className="chart-empty">
          <span className="empty-icon">⌕</span>
          <strong>Select an investigation</strong>
          <p>
            See what the agent concluded,
            <br />
            what it cited and where it stopped.
          </p>
        </div>
      ) : (
        <>
          <div className="panel-title">
            <div>
              <span className={"badge " + run.status}>{run.status}</span>
              <h2 className="run-heading">{run.id}</h2>
              <p>
                {run.model_calls} model calls · {run.tool_calls} tool calls ·{" "}
                {money(run.cost, 4)} estimated
              </p>
            </div>
          </div>
          {tab === "Research" ? (
            <>
              <div className="decision-box">
                <div className="eyebrow">INVESTMENT DECISION</div>
                {run.decision ? (
                  <Value value={run.decision} />
                ) : (
                  <>
                    <h3>No accepted decision</h3>
                    <p>
                      This investigation did not produce a verified investment
                      decision. Partial reports are shown as research only.
                    </p>
                  </>
                )}
              </div>
              <h3>Specialist reports</h3>
              {Object.entries(run.reports).map(([role, report]) => (
                <details key={role} className="report" open>
                  <summary>{label(role)}</summary>
                  <Value value={report} />
                </details>
              ))}
              {!Object.keys(run.reports).length && (
                <p className="muted">No specialist report was saved.</p>
              )}
              <details className="report">
                <summary>Valuation calculations</summary>
                <Value value={run.calculations} />
              </details>
              <details className="report">
                <summary>Cited evidence · {run.sources.length} sources</summary>
                {run.sources.map((source, i) => (
                  <div className="source" key={i}>
                    <code>{source.id}</code>
                    <p>{source.title || source.type || "Recorded evidence"}</p>
                    {typeof source.url === "string" &&
                      /^https?:\/\//.test(source.url) && (
                        <a href={source.url} target="_blank" rel="noreferrer">
                          Open source ↗
                        </a>
                      )}
                  </div>
                ))}
              </details>
              <button className="text-button" onClick={() => setTab("Traces")}>
                Inspect execution trace →
              </button>
            </>
          ) : (
            <>
              <p className="muted">
                Latest 100 events for this run. A start without an end may be
                interrupted or still running.
              </p>
              {traceError && (
                <p role="alert" className="error">
                  {traceError}
                </p>
              )}
              {events.map((e, i) => (
                <details className="trace-row" key={i}>
                  <summary>
                    <span
                      className={
                        "dot " +
                        (e.phase === "error"
                          ? "red"
                          : e.phase === "end"
                            ? "green"
                            : "gray")
                      }
                    />
                    <span>
                      <b>{e.name}</b>
                      <small>{time(e.at)}</small>
                    </span>
                    <span className="tag">{e.phase}</span>
                  </summary>
                  <div className="trace-meta">
                    Span {e.span_id || "—"}
                    <br />
                    Parent {e.parent_span_id || "—"}
                  </div>
                  <pre>{e.detail}</pre>
                  {e.truncated && (
                    <p className="notice">
                      Preview limited to 12,000 characters. Full event remains
                      in Postgres.
                    </p>
                  )}
                </details>
              ))}
              {!events.length && !traceError && (
                <div className="empty-text">
                  No detailed traces available for this run. Older runs predate
                  full observability.
                </div>
              )}
            </>
          )}
        </>
      )}
    </section>
  );
}
