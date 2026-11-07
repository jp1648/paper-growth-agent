import { test, expect } from "@playwright/test";
import { createHash } from "node:crypto";
import { Pool } from "pg";
import {
  createSession,
  activeSession,
  revokeSession,
} from "../lib/session-store.mjs";
import { signSession } from "../lib/session.mjs";

for (const role of ["trader_dashboard", "dashboard_reader"])
  test(`session revocation and least privilege: ${role}`, async () => {
    const connectionString = process.env.TEST_OBSERVABILITY_DATABASE_URL;
    test.skip(!connectionString, "Requires isolated fixture database");
    const url = new URL(connectionString!);
    expect(["localhost", "127.0.0.1"]).toContain(url.hostname);
    expect(url.pathname).toBe("/trader_dashboard_test");
    const options = `-c role=${role} -c default_transaction_read_only=on`;
    const admin = new Pool({ connectionString });
    const first = new Pool({ connectionString, options });
    const second = new Pool({ connectionString, options });
    const secret = "isolated-browser-fixture-password";
    try {
      const token = await createSession(first, secret);
      expect(await activeSession(second, token, secret)).toBe(true);
      expect(await activeSession(second, signSession(secret), secret)).toBe(
        false,
      );
      expect(await activeSession(second, token, secret + "changed")).toBe(
        false,
      );
      await revokeSession(second, token);
      await revokeSession(first, token);
      expect(await activeSession(first, token, secret)).toBe(false);
      const expired = await createSession(first, secret);
      const hash = createHash("sha256").update(expired).digest("hex");
      await admin.query(
        "UPDATE dashboard_sessions SET expires_at=now()-interval '1 second' WHERE token_hash=$1",
        [hash],
      );
      expect(await activeSession(second, expired, secret)).toBe(false);
      await createSession(second, secret);
      expect(
        (
          await admin.query(
            "SELECT 1 FROM dashboard_sessions WHERE token_hash=$1",
            [hash],
          )
        ).rowCount,
      ).toBe(0);
      const client = await first.connect();
      try {
        await client.query("BEGIN READ WRITE");
        await expect(
          client.query("DELETE FROM telemetry_events"),
        ).rejects.toMatchObject({ code: "42501" });
        await client.query("ROLLBACK");
      } finally {
        client.release();
      }
      await expect(
        activeSession(
          {
            query: async () => {
              throw Error("database unavailable");
            },
          },
          await createSession(first, secret),
          secret,
        ),
      ).rejects.toThrow("database unavailable");
    } finally {
      await admin.end();
      await first.end();
      await second.end();
    }
  });
