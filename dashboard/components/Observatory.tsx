"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import Overview from "./Overview";
import Investigation from "./Investigation";
import { Value, money, time } from "./DataViews";
import type { OverviewData, TraceEvent } from "../lib/types";
import { logout } from "../app/login/actions";
export default function Observatory({
  initial,
  initialError,
}: {
  initial: OverviewData;
  initialError: string;
}) {
  const [data, setData] = useState(initial),
    [error, setError] = useState(initialError),
    [tab, setTab] = useState("Overview");
  const [selected, setSelected] = useState(""),
    [events, setEvents] = useState<TraceEvent[]>([]),
    [traceError, setTraceError] = useState(""),
    [search, setSearch] = useState(""),
    [loading, setLoading] = useState(false);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [traceLoading, setTraceLoading] = useState(false);
  const traceController = useRef<AbortController | null>(null);
  async function loadOlder() {
    const controller = traceController.current;
    if (!controller || !nextCursor || traceLoading) return;
    setTraceLoading(true);
    setTraceError("");
    try {
      const response = await fetch(
        "/api/data?run=" +
          encodeURIComponent(selected) +
          "&cursor=" +
          encodeURIComponent(nextCursor),
        { signal: controller.signal },
      );
      if (!response.ok)
        throw Error("Older events unavailable; retry loading them.");
      const page = await response.json();
      if (controller.signal.aborted) return;
      setEvents((current) => [...page.events, ...current]);
      setNextCursor(page.next_cursor ?? null);
    } catch (error) {
      if (!controller.signal.aborted)
        setTraceError(
          error instanceof Error ? error.message : "Trace unavailable",
        );
    } finally {
      if (!controller.signal.aborted) setTraceLoading(false);
    }
  }
  const pendingRefresh = useRef<AbortController | null>(null);
  const refresh = useCallback(async () => {
    pendingRefresh.current?.abort();
    const controller = new AbortController();
    pendingRefresh.current = controller;
    setLoading(true);
    try {
      const r = await fetch("/api/data", {
        cache: "no-store",
        signal: controller.signal,
      });
      if (r.status === 401) {
        location.href = "/login";
        return;
      }
      const d = await r.json();
      if (!r.ok) throw Error(d.error);
      setData(d);
      setError("");
    } catch (e) {
      if (!controller.signal.aborted)
        setError(e instanceof Error ? e.message : "Refresh failed");
    } finally {
      if (pendingRefresh.current === controller) setLoading(false);
    }
  }, []);
  useEffect(() => {
    const id = setInterval(refresh, 30000);
    return () => {
      clearInterval(id);
      pendingRefresh.current?.abort();
    };
  }, [refresh]);
  useEffect(() => {
    if (!selected || tab !== "Traces") return;
    const controller = new AbortController();
    traceController.current = controller;
    setNextCursor(null);
    setTraceLoading(true);
    setEvents([]);
    setTraceError("");
    fetch("/api/data?run=" + encodeURIComponent(selected), {
      signal: controller.signal,
    })
      .then(async (r) => {
        if (!r.ok) throw Error("Trace unavailable");
        return r.json();
      })
      .then((d) => {
        if (controller.signal.aborted) return;
        setEvents(d.events);
        setNextCursor(d.next_cursor ?? null);
      })
      .catch((e) => {
        if (e.name !== "AbortError") setTraceError(e.message);
      })
      .finally(() => {
        if (!controller.signal.aborted) setTraceLoading(false);
      });
    return () => controller.abort();
  }, [selected, tab]);
  const s = data.snapshot,
    p = s?.portfolio,
    runs = s?.runs || [];
  const run = runs.find((r) => r.id === selected);
  const age = p?.as_of ? Date.now() - new Date(p.as_of).getTime() : Infinity;
  const fresh = age >= -5000 && age < 5 * 60 * 1000;
  const filtered = runs.filter((r) =>
    (r.id + " " + r.status).toLowerCase().includes(search.toLowerCase()),
  );
  return (
    <div className="shell">
      <aside>
        <div className="brand">
          <span className="brand-mark">↗</span> trader
          <span className="brand-dot">.</span>
        </div>
        <div className="workspace-label">AGENT WORKSPACE</div>
        <nav>
          {["Overview", "Research", "Traces"].map((t, i) => (
            <button
              aria-label={t}
              key={t}
              onClick={() => setTab(t)}
              className={tab === t ? "active" : ""}
            >
              <span>{["◫", "⌕", "≋"][i]}</span>
              {t}
              {t === "Research" && <small>{runs.length}</small>}
            </button>
          ))}
        </nav>
        <div className="side-bottom">
          <span className="pill dark">PAPER ENVIRONMENT</span>
          <p>
            Every claim needs evidence.
            <br />
            Every action leaves a trace.
          </p>
          <form action={logout}>
            <button className="signout">Sign out ↗</button>
          </form>
        </div>
      </aside>
      <div className="main">
        <header>
          <span className="crumb">
            Workspace <b>/</b> {tab}
          </span>
          <div className="header-right">
            <span className={"dot " + (fresh ? "green" : "amber")} />
            {fresh ? "Telemetry current" : "Awaiting fresh data"}
            <button className="refresh" onClick={refresh} disabled={loading}>
              {loading ? "Refreshing…" : "↻ Refresh"}
            </button>
            <form className="mobile-signout" action={logout}>
              <button aria-label="Sign out" className="refresh">
                ↪
              </button>
            </form>
          </div>
        </header>
        <main>
          <div className="heading">
            <div>
              <div className="eyebrow">THE OBSERVATORY</div>
              <h1>
                {tab === "Overview"
                  ? "Performance, with perspective."
                  : tab === "Research"
                    ? "The evidence behind each call."
                    : "Follow the work, step by step."}
              </h1>
              <p>
                {tab === "Overview"
                  ? "Portfolio health and the quality of the work behind it."
                  : tab === "Research"
                    ? "Inspect saved conclusions, calculations and cited sources."
                    : "Correlated model calls, tools and validation outcomes."}
              </p>
            </div>
            <span className="pill">↗ Paper trading</span>
          </div>
          {error && (
            <div role="alert" className="notice error">
              {error} {s && "Showing the last loaded snapshot."}
            </div>
          )}
          {!s || !p ? (
            <section className="panel empty-state">
              <h2>No agent snapshot yet</h2>
              <p>
                The UI is connected to the observability layer. Start the
                instrumented agent’s maintenance loop to publish a dashboard
                snapshot.
              </p>
              <code>python -m trader.cli --config config.toml maintain</code>
            </section>
          ) : (
            <>
              <div className="status-strip">
                <span
                  className={"dot " + (p.kill_switch ? "amber" : "green")}
                />
                <strong>
                  {p.kill_switch
                    ? "Automatic trading paused"
                    : "Automatic trading enabled"}
                </strong>
                <span className="divider" />
                <span>
                  {p.reconciliation?.ok
                    ? "Broker reconciliation passed"
                    : "Reconciliation requires attention"}
                </span>
                <span className="push muted">
                  Portfolio as of {time(p.as_of)}
                </span>
              </div>
              {tab === "Overview" && (
                <Overview
                  snapshot={s}
                  setTab={setTab}
                  setSelected={setSelected}
                />
              )}
              {tab !== "Overview" && (
                <div className="research-layout">
                  <section className="panel run-list">
                    <div className="panel-title">
                      <h2>Investigations</h2>
                      <span className="tag">{runs.length}</span>
                    </div>
                    <input
                      aria-label="Filter investigations"
                      placeholder="Filter by run or status…"
                      value={search}
                      onChange={(e) => setSearch(e.target.value)}
                    />
                    {filtered.map((r) => (
                      <button
                        className={
                          "run-choice " + (selected === r.id ? "chosen" : "")
                        }
                        onClick={() => setSelected(r.id)}
                        key={r.id}
                      >
                        <span className={"badge " + r.status}>{r.status}</span>
                        <strong>{r.id}</strong>
                        <small>
                          {money(r.cost, 4)} · {r.model_calls} model calls
                        </small>
                      </button>
                    ))}
                    {!filtered.length && (
                      <p className="empty-text">No matching investigations.</p>
                    )}
                  </section>
                  <Investigation
                    key={run?.id}
                    run={run}
                    tab={tab}
                    events={events}
                    traceError={traceError}
                    hasOlder={Boolean(nextCursor)}
                    traceLoading={traceLoading}
                    loadOlder={loadOlder}
                    setTab={setTab}
                  />
                </div>
              )}
              <footer>
                <span>{p.market_data?.coverage} · Alpaca paper account</span>
                <span>
                  Updated {time(s.generated_at)} · refreshes every 30s
                </span>
              </footer>
              <details className="limitations">
                <summary>Data coverage & limitations</summary>
                <p>{s.coverage}</p>
                <Value value={p.limitations} />
                {p.risk_reviews?.length > 0 && <Value value={p.risk_reviews} />}
              </details>
            </>
          )}
        </main>
      </div>
    </div>
  );
}
