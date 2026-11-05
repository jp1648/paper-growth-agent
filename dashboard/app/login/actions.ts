"use server";
import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { matches, signSession } from "../../lib/session.mjs";
export async function login(form: FormData) {
  const secret = process.env.DASHBOARD_PASSWORD || "";
  if (!matches(String(form.get("password") || ""), secret))
    redirect("/login?error=1");
  (await cookies()).set("trader_session", signSession(secret), {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "strict",
    path: "/",
    maxAge: 8 * 60 * 60,
  });
  redirect("/");
}
export async function logout() {
  (await cookies()).delete("trader_session");
  redirect("/login");
}
