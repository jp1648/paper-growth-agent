// Only the disposable browser-test database may receive these synthetic records.
import { Client } from "pg";
import { randomUUID } from "node:crypto";
const url = new URL(process.env.TEST_OBSERVABILITY_DATABASE_URL || "");
if (
  !["localhost", "127.0.0.1"].includes(url.hostname) ||
  url.pathname !== "/trader_dashboard_test"
) {
  throw Error("Expected isolated local trader_dashboard_test database");
}
const db = new Client({ connectionString: url.toString() });
await db.connect();
try {
  await db.query(
    "CREATE TABLE IF NOT EXISTS telemetry_events(event_id uuid primary key,at timestamptz,run_key text,name text,phase text,span_id text,parent_span_id text,payload jsonb)",
  );
  const snapshot = {
    generated_at: new Date().toISOString(),
    portfolio: {
      as_of: new Date().toISOString(),
      kill_switch: true,
      reconciliation: { ok: true },
      equity: "100000",
      cash: "100000",
      total_pnl: "0",
      model_cost_usd_estimate: "0",
      market_data: { coverage: "Synthetic fixture" },
      limitations: ["Synthetic test data"],
      risk_reviews: [],
    },
    runs: [
      {
        id: "long-fixture",
        status: "failed",
        decision: null,
        reports: {},
        calculations: {},
        cost: "0",
        model_calls: 0,
        tool_calls: 205,
        sources: [],
      },
    ],
    daily: [],
    positions: [],
    open_orders: [],
    research_cost_known_usd: "0",
    research_cost_unknown_calls: 0,
    coverage: "Synthetic fixture",
  };
  await db.query(
    "INSERT INTO telemetry_events VALUES($1,now(),null,'dashboard.snapshot','event',null,null,$2)",
    [randomUUID(), snapshot],
  );
  for (let i = 0; i < 205; i++)
    await db.query(
      "INSERT INTO telemetry_events VALUES($1,$2,'long-fixture','tool.read','end',null,null,$3)",
      [
        randomUUID(),
        new Date(1762444800000 + i),
        {
          index: i,
          detail: "evidence ".repeat(1600),
          end_marker: "FULL_EVENT_END",
        },
      ],
    );
} finally {
  await db.end();
}
