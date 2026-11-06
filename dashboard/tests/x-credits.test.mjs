import { test } from "node:test";
import assert from "node:assert/strict";
import { readXCredits } from "../lib/x-credits.mjs";
const data = {
  prepaid_balance: 25,
  free_balance: 19.71,
  total_balance: 44.71,
  free_grants: [{ amount: 19.71, expires_at: "2027-02-03T04:19:12Z" }],
};
test("X balance uses read-only endpoint and returns only balance fields", async () => {
  const result = await readXCredits("test-token", async (url, options) => {
    assert.equal(url, "https://api.x.com/2/usage/credits");
    assert.equal(options.headers.Authorization, "Bearer test-token");
    assert.equal(options.redirect, "error");
    assert.equal(options.cache, "no-store");
    return Response.json({ data: { ...data, private_field: "omit" } });
  });
  assert.equal(result.status, "available");
  assert.equal(result.total_balance, 44.71);
  assert.deepEqual(result.free_grants, data.free_grants);
  assert.ok(!JSON.stringify(result).includes("test-token"));
  assert.ok(!JSON.stringify(result).includes("private_field"));
  const negative = await readXCredits("token", async () =>
    Response.json({ data: { ...data, prepaid_balance: -1 } }),
  );
  assert.equal(negative.prepaid_balance, -1);
});
test("missing credentials, provider failures and malformed balances never become zero", async () => {
  assert.equal(
    (
      await readXCredits("", () => {
        throw Error("should not call");
      })
    ).status,
    "not_configured",
  );
  for (const response of [
    new Response("failure", { status: 401 }),
    Response.json({ data: { ...data, total_balance: "44.71" } }),
    Response.json({
      data: { ...data, free_grants: [{ amount: 2, expires_at: "bad" }] },
    }),
    Response.json({ data, errors: [{}] }),
  ]) {
    const result = await readXCredits("token", async () => response);
    assert.equal(result.status, "unavailable");
    assert.equal(result.total_balance, undefined);
  }
  const result = await readXCredits("token", async () => {
    throw new Error("secret error");
  });
  assert.equal(result.status, "unavailable");
  assert.ok(!JSON.stringify(result).includes("secret"));
});
