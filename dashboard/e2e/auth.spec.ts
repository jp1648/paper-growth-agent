import { test, expect } from "@playwright/test";
import { signSession } from "../lib/session.mjs";

const password = process.env.TEST_DASHBOARD_PASSWORD;
test("invalid sessions cannot read overview, traces, full events or the home page", async ({
  request,
}) => {
  test.skip(!password, "Requires isolated fixture dashboard");
  const current = Date.now();
  const valid = signSession(password!, current);
  const authorized = await request.get("/", {
    headers: { Cookie: `trader_session=${valid}` },
    maxRedirects: 0,
  });
  expect(authorized.status()).toBe(200);
  const tokens = [
    "",
    "malformed",
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
