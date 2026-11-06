import { queryTrace } from "./trace-query.mjs";
import "server-only";
import { readXCredits } from "./x-credits.mjs";
import type { OverviewData, Snapshot } from "./types";
import { Pool } from "pg";
let creditCheck:
  { expires: number; result: ReturnType<typeof readXCredits> } | undefined;
function xCredits() {
  if (!creditCheck || Date.now() >= creditCheck.expires) {
    creditCheck = {
      expires: Date.now() + 5 * 60_000,
      result: readXCredits(process.env.X_BEARER_TOKEN),
    };
  }
  return creditCheck.result;
}
const globalDB = globalThis as unknown as { dashboardPool?: Pool };
function pool() {
  if (!process.env.DATABASE_URL) throw new Error("Database not configured");
  return (globalDB.dashboardPool ??= new Pool({
    connectionString: process.env.DATABASE_URL,
    max: 2,
    idleTimeoutMillis: 10000,
    connectionTimeoutMillis: 5000,
    statement_timeout: 8000,
    options: "-c default_transaction_read_only=on",
    ssl:
      process.env.DATABASE_SSL === "true"
        ? {
            rejectUnauthorized: true,
            ...(process.env.DATABASE_SSL_CA
              ? { ca: process.env.DATABASE_SSL_CA.replace(/\\n/g, "\n") }
              : {}),
          }
        : undefined,
  }));
}
export async function overview(): Promise<OverviewData> {
  const db = pool();
  const latest = await db.query<{
    at: Date;
    payload: Snapshot;
  }>(`SELECT at, payload FROM telemetry_events
    WHERE name='dashboard.snapshot' ORDER BY at DESC LIMIT 1`);
  if (!latest.rows.length) return { snapshot: null, at: null };
  return {
    snapshot: { ...latest.rows[0].payload, x_credits: await xCredits() },
    at: latest.rows[0].at.toISOString(),
  };
}
export async function trace(
  run: string,
  cursor: string | null,
  event: string | null,
) {
  return queryTrace(pool(), run, cursor, event);
}
