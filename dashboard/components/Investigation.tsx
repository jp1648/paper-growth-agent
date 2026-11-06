import { useState } from "react";
import { Value, label, money, time } from "./DataViews";
import type { ResearchRun, TraceEvent } from "../lib/types";

const reportNames: Record<string, string> = {
  business: "Business quality & financial health",
  catalysts: "Catalysts & timing",
  valuation: "Valuation & assumptions",
  adversarial: "Independent challenge & risks",
};
const text = (value: unknown) => (typeof value === "string" ? value : "");
const failure = (e: TraceEvent) =>
  e.phase === "error" || /failed|error/.test(e.name);
function payload(detail: string) {
  try {
    return JSON.stringify(JSON.parse(detail), null, 2);
  } catch {
    return detail;
  }
}
function Finding({ title, value }: { title: string; value: unknown }) {
  if (value == null || value === "") return null;
  return (
    <section className="finding">
      <h3>{title}</h3>
      <Value value={value} />
    </section>
  );
}
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
  const [filter, setFilter] = useState("All events");
  if (!run)
    return (
      <section className="panel investigation">
        <div className="chart-empty">
          <strong>Select an investigation</strong>
          <p>
            Review the decision, its evidence, and how the agent reached it.
          </p>
        </div>
      </section>
    );
  const decision = run.decision;
  const action = text(decision?.action);
  const noTrade = action === "WATCH" || action === "HOLD";
  const review = run.reports.adversarial;
  const critique = (
    review && typeof review === "object" && !Array.isArray(review) ? review : {}
  ) as Record<string, unknown>;
  const gaps = critique.unresolved_material_gaps === true;
  const cited = new Set(
    Array.isArray(decision?.evidence_ids) ? decision.evidence_ids : [],
  );
  const visible = events.filter(
    (e) =>
      filter === "All events" ||
      (filter === "Failures"
        ? failure(e)
        : e.name.startsWith(filter === "Models" ? "model." : "tool.")),
  );
  return (
    <section className="panel investigation">
      <div className="panel-title investigation-heading">
        <div>
          <span className={"badge " + run.status}>
            {run.status === "accepted" ? "Research accepted" : run.status}
          </span>
          <h2>
            {text(decision?.symbol)
              ? `${text(decision?.symbol)} investigation`
              : "Research investigation"}
          </h2>
        </div>
        <button
          className="text-button"
          onClick={() => setTab(tab === "Research" ? "Traces" : "Research")}
        >
          {tab === "Research"
            ? "Inspect execution trace →"
            : "← Back to research"}
        </button>
      </div>
      <div className="investigation-stats">
        <span>
          <strong>{money(run.cost, 4)}</strong> recorded estimate
        </span>
        <span>
          <strong>{run.model_calls}</strong> model calls
        </span>
        <span>
          <strong>{run.tool_calls}</strong> tool calls
        </span>
        <span>
          <strong>{Object.keys(run.reports).length}/4</strong> specialist
          reports
        </span>
      </div>
      <details className="run-metadata">
        <summary>Run identifier</summary>
        <code>{run.id}</code>
      </details>
      {tab === "Research" ? (
        <>
          <div className="decision-brief">
            <div className="eyebrow">RECOMMENDATION</div>
            <h3>
              {decision
                ? `${action || "Decision recorded"}${noTrade ? " — no trade proposed" : " — proposal only"}`
                : "No accepted decision"}
            </h3>
            <p>
              {decision
                ? text(decision.why_now) || "See the saved decision below."
                : "This run did not produce an accepted decision. The reports below are partial research, not an approved trade."}
            </p>
            {decision && (
              <p className="decision-footnote">
                Research acceptance does not confirm an order or fill.
                {text(decision.next_review) && (
                  <>
                    {" "}
                    Next review:{" "}
                    <strong>{time(text(decision.next_review))}</strong>.
                  </>
                )}
              </p>
            )}
          </div>
          {gaps && (
            <div className="research-warning">
              <strong>Material evidence gaps remain</strong>
              <p>
                The independent reviewer flagged unresolved issues. Research
                acceptance does not mean the investment thesis is proven.
              </p>
            </div>
          )}
          {decision && (
            <div className="finding-grid">
              <Finding
                title="Why the business is interesting"
                value={decision.business_attraction}
              />
              <Finding
                title="Is the price attractive?"
                value={decision.price_attraction}
              />
            </div>
          )}
          <div className="research-section">
            <h3>What happens next</h3>
            {decision ? (
              <>
                <Finding
                  title="Expected outcome"
                  value={decision.expected_outcome}
                />
                <details className="research-disclosure">
                  <summary>Conditions to act or reconsider</summary>
                  <Finding
                    title="Entry / add conditions"
                    value={decision.add_if}
                  />
                  <Finding
                    title="Reduce conditions"
                    value={decision.reduce_if}
                  />
                  <Finding
                    title="Exit / thesis invalidation"
                    value={decision.exit_if}
                  />
                  <Finding
                    title="Catalysts to monitor"
                    value={decision.catalysts}
                  />
                </details>
              </>
            ) : (
              <p className="muted">
                Inspect the saved reports and failure trace to see where this
                investigation stopped.
              </p>
            )}
          </div>
          <div className="research-section">
            <h3>Risks & missing evidence</h3>
            {text(critique.conclusion) ? (
              <p>{text(critique.conclusion)}</p>
            ) : (
              <p className="muted">No independent conclusion was recorded.</p>
            )}
            <details className="research-disclosure">
              <summary>Review objections and assumptions</summary>
              <Finding
                title="Independent objections"
                value={critique.objections}
              />
              <Finding
                title="What could break the thesis"
                value={critique.thesis_breakers}
              />
              <Finding
                title="Decision assumptions"
                value={decision?.assumptions}
              />
            </details>
          </div>
          <div className="research-section">
            <h3>Specialist reports</h3>
            <p className="muted">
              Open a report to read its complete findings.
            </p>
            {Object.entries(run.reports).map(([role, report]) => (
              <details key={role} className="research-disclosure">
                <summary>{reportNames[role] || label(role)}</summary>
                <Value value={report} />
              </details>
            ))}
            {!Object.keys(run.reports).length && (
              <p className="muted">No specialist report was saved.</p>
            )}
            <details className="research-disclosure">
              <summary>
                Valuation calculations · {Object.keys(run.calculations).length}
              </summary>
              {Object.keys(run.calculations).length ? (
                <Value value={run.calculations} />
              ) : (
                <p>
                  No calculated valuation was saved. This does not establish a
                  fair value.
                </p>
              )}
            </details>
          </div>
          <div className="research-section">
            <h3>Evidence library</h3>
            <p className="muted">
              {run.sources.filter((s) => cited.has(s.id)).length} decision
              citations · {run.sources.length} recorded sources
            </p>
            <details className="research-disclosure">
              <summary>Browse evidence and original sources</summary>
              {run.sources.map((source, i) => (
                <div className="source" key={i}>
                  {cited.has(source.id) && (
                    <span className="tag">Cited in decision</span>
                  )}
                  <p>
                    <strong>
                      {source.title || source.type || "Recorded evidence"}
                    </strong>
                  </p>
                  <code>{source.id}</code>
                  {typeof source.url === "string" &&
                    /^https?:\/\//.test(source.url) && (
                      <p>
                        <a href={source.url} target="_blank" rel="noreferrer">
                          Open original source ↗
                        </a>
                      </p>
                    )}
                </div>
              ))}
              {!run.sources.length && <p>No sources were saved.</p>}
            </details>
            {decision && (
              <details className="research-disclosure">
                <summary>Complete saved decision</summary>
                <Value value={decision} />
              </details>
            )}
          </div>
        </>
      ) : (
        <>
          <div className="trace-toolbar">
            <div role="group" aria-label="Filter trace events">
              {["All events", "Failures", "Models", "Tools"].map((f) => (
                <button
                  key={f}
                  aria-pressed={filter === f}
                  onClick={() => setFilter(f)}
                >
                  {f}
                </button>
              ))}
            </div>
            <p>
              {visible.length} of {events.length} loaded events · latest 100
              available here
            </p>
          </div>
          <p className="muted">
            Events are shown chronologically. A start without an end can mean
            interrupted or still running.
          </p>
          {traceError && (
            <p role="alert" className="error">
              {traceError}
            </p>
          )}
          {visible.map((e, i) => (
            <details className="trace-row" key={`${e.span_id}-${e.at}-${i}`}>
              <summary>
                <span
                  className={
                    "dot " +
                    (failure(e) ? "red" : e.phase === "end" ? "green" : "gray")
                  }
                />
                <span>
                  <b>{label(e.name)}</b>
                  <small>{time(e.at)}</small>
                </span>
                <span className="tag">{e.phase}</span>
              </summary>
              <div className="trace-meta">
                Span {e.span_id || "—"}
                <br />
                Parent {e.parent_span_id || "—"}
              </div>
              <pre>{payload(e.detail)}</pre>
              {e.truncated && (
                <p className="notice">
                  Preview limited to 12,000 characters. Full event remains in
                  Postgres.
                </p>
              )}
            </details>
          ))}
          {!visible.length && !traceError && (
            <p className="empty-text">
              {events.length
                ? "No events match this filter."
                : "No detailed traces available. Older runs may predate full observability."}
            </p>
          )}
        </>
      )}
    </section>
  );
}
