import type { Pool } from "pg";
import type { TraceEvent } from "./types";
export function queryTrace(
  db: Pool,
  run: string,
  cursor?: string | null,
  event?: string | null,
): Promise<{
  events?: TraceEvent[];
  next_cursor?: string | null;
  event?: unknown;
}>;
