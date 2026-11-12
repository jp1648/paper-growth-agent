import { test, expect } from "@playwright/test";
import { Pool } from "pg";

test("login limit blocks password attempts and recovers after its window", async ({
  page,
}) => {
  const connectionString = process.env.TEST_OBSERVABILITY_DATABASE_URL;
  const password = process.env.TEST_DASHBOARD_PASSWORD;
  test.skip(
    !connectionString || !password,
    "Requires isolated fixture dashboard",
  );
  const url = new URL(connectionString!);
  expect(["localhost", "127.0.0.1"]).toContain(url.hostname);
  expect(url.pathname).toBe("/trader_dashboard_test");
  const db = new Pool({ connectionString });
  try {
    if (
      (
        await db.query(
          "SELECT to_regclass('public.dashboard_login_limit') AS name",
        )
      ).rows[0].name
    )
      await db.query("DELETE FROM dashboard_login_limit");
    await page.goto("/login");
    async function attempt(value: string) {
      await page.getByLabel("Workspace password").fill(value);
      await Promise.all([
        page.waitForResponse(
          (r) => r.request().method() === "POST" && r.url().includes("/login"),
        ),
        page.getByRole("button", { name: "Enter observatory" }).click(),
      ]);
    }
    for (let i = 0; i < 20; i++) {
      await attempt("incorrect-password");
      await expect(page.locator(".error[role=alert]")).toHaveText(
        "Unable to sign in. Check your password.",
      );
    }
    await attempt(password!);
    await expect(page.locator(".error[role=alert]")).toHaveText(
      "Too many sign-in attempts. Try again in one minute.",
    );
    expect((await page.request.get("/api/data")).status()).toBe(401);
    await db.query(
      "UPDATE dashboard_login_limit SET window_started=now()-interval '61 seconds'",
    );
    await attempt(password!);
    await expect(page).toHaveURL(/\/$/);
    expect((await page.request.get("/api/data")).status()).toBe(200);
  } finally {
    await db.end();
  }
});
