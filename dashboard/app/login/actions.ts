"use server";
import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { matches } from "../../lib/session.mjs";
import { createSession, revokeSession } from "../../lib/session-store.mjs";
import { pool } from "../../lib/db";
export async function login(form: FormData) {
  const secret = process.env.DASHBOARD_PASSWORD || "";
  if (!matches(String(form.get("password") || ""), secret))
    redirect("/login?error=1");
  (await cookies()).set("trader_session", await createSession(pool(), secret), {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "strict",
    path: "/",
    maxAge: 8 * 60 * 60,
  });
  redirect("/");
}
export async function logout() {
  const jar = await cookies();
  await revokeSession(pool(), jar.get("trader_session")?.value);
  jar.delete("trader_session");
  redirect("/login");
}
