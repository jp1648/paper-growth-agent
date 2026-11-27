import "server-only";
import { cookies } from "next/headers";
import { activeSession } from "./session-store.mjs";
import { pool } from "./db";
export async function authenticated() {
  const token = (await cookies()).get("trader_session")?.value;
  if (!token) return false;
  return activeSession(pool(), token, process.env.DASHBOARD_PASSWORD);
}
