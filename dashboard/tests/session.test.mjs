import { test } from "node:test";
import assert from "node:assert/strict";
import { matches, signSession, validSession } from "../lib/session.mjs";
const secret = "test-password-with-at-least-24-characters";
test("authentication fails closed and sessions expire", () => {
  assert.equal(matches("", ""), false);
  assert.equal(matches("x", "short"), false);
  assert.equal(matches(secret, secret), true);
  assert.equal(matches("incorrect", secret), false);
  const token = signSession(secret, 1000);
  assert.equal(validSession(token, secret, 1001), true);
  assert.equal(validSession(token, secret, 1000 + 8 * 60 * 60 * 1000), false);
  assert.equal(validSession(token + "x", secret, 1001), false);
  assert.equal(
    validSession(token, "different-secret-with-24-chars", 1001),
    false,
  );
  assert.equal(validSession(undefined, secret), false);
  assert.equal(validSession("Infinity.x", secret), false);
});

test("sessions require canonical signed tokens", () => {
  const issued = 1000;
  const token = signSession(secret, issued);
  for (const malformed of [
    token + ".",
    token + "..",
    token + ".extra",
    " " + token,
    token + " ",
  ]) {
    assert.equal(validSession(malformed, secret, issued + 1), false, malformed);
  }
});
