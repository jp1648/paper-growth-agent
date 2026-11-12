import { test, expect } from "@playwright/test";
import { signSession } from "../lib/session.mjs";

const password = process.env.TEST_DASHBOARD_PASSWORD;
test("invalid sessions cannot read overview, traces, full events or the home page", async ({
  request,
  page,
}) => {
  test.skip(!password, "Requires isolated fixture dashboard");
  const current = Date.now();
  await page.goto("/login");
  await page.getByLabel("Workspace password").fill(password!);
  await page.getByRole("button", { name: "Enter observatory" }).click();
  await expect(page).toHaveURL(/\/$/);
  const valid = (await page.context().cookies()).find(
    (c) => c.name === "trader_session",
  )!.value;
  const authorized = await request.get("/", {
    headers: { Cookie: `trader_session=${valid}` },
    maxRedirects: 0,
  });
  expect(authorized.status()).toBe(200);
  const tokens = [
    "",
    "malformed",
    signSession(password!, current), // Signed but never registered.
    signSession(password!, current - 9 * 60 * 60 * 1000),
    signSession(password!, current + 60 * 60 * 1000),
    signSession("different-fixture-secret-long-enough", current),
    valid + ".",
    valid + ".extra",
    valid.slice(0, -1) + (valid.endsWith("0") ? "1" : "0"),
  ];
  for (const token of tokens) {
    const headers = { Cookie: `trader_session=${token}` };
    for (const path of [
      "/api/data",
      "/api/data?run=long-fixture",
      "/api/data?run=long-fixture&cursor=invalid",
      "/api/data?run=long-fixture&event=00000000-0000-4000-8000-000000000000",
    ]) {
      const response = await request.get(path, { headers });
      expect(response.status()).toBe(401);
      expect(await response.json()).toEqual({
        error: "Authentication required",
      });
    }
    const home = await request.get("/", { headers, maxRedirects: 0 });
    expect(home.status()).toBe(307);
    expect(home.headers().location).toBe("/login");
  }
});

test("logout revokes a copied token without revoking another login", async ({
  page,
  browser,
  request,
}) => {
  test.skip(!password, "Requires isolated fixture dashboard");
  async function login(target: typeof page) {
    await target.goto("/login");
    await target.getByLabel("Workspace password").fill(password!);
    await target.getByRole("button", { name: "Enter observatory" }).click();
    await expect(target).toHaveURL(/\/$/);
    return (await target.context().cookies()).find(
      (c) => c.name === "trader_session",
    )!.value;
  }
  const token = await login(page);
  const otherContext = await browser.newContext();
  try {
    const other = await login(await otherContext.newPage());
    expect(other).not.toBe(token);
    const read = (value: string) =>
      request.get("/api/data", {
        headers: { Cookie: `trader_session=${value}` },
      });
    expect((await read(token)).status()).toBe(200);
    await page.getByRole("button", { name: "Sign out ↗" }).click();
    await expect(page).toHaveURL(/\/login$/);
    expect((await read(token)).status()).toBe(401);
    expect((await read(other)).status()).toBe(200);
  } finally {
    await otherContext.close();
  }
});
