import "server-only";
import { cookies } from "next/headers";
import { validSession } from "./session.mjs";
export async function authenticated() {
  const token = (await cookies()).get("trader_session")?.value;
  return validSession(token, process.env.DASHBOARD_PASSWORD);
}
