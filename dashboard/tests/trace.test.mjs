import { test } from "node:test";
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { Client } from "pg";
import { queryTrace } from "../lib/trace-query.mjs";

test("invalid trace identifiers fail before database access", async () => {
  const db = { query: () => assert.fail("Unexpected database access") };
  for (const args of [
    [""],
    ["run", "garbage"],
    ["run", "x".repeat(401)],
    ["run", null, "' OR 1=1"],
  ]) {
    await assert.rejects(queryTrace(db, ...args), TypeError);
  }
});

test(
  "Postgres pagination preserves equal timestamps and full details",
  {
    skip: !process.env.TEST_OBSERVABILITY_DATABASE_URL,
  },
  async () => {
    const db = new Client({
      connectionString: process.env.TEST_OBSERVABILITY_DATABASE_URL,
    });
    await db.connect();
    try {
      await db.query(
        "CREATE TEMP TABLE telemetry_events(event_id uuid primary key,at timestamptz,run_key text,name text,phase text,span_id text,parent_span_id text,payload jsonb)",
      );
      const ids = Array.from({ length: 205 }, () => randomUUID());
      const detail = "source ".repeat(3000);
      for (const [i, id] of ids.entries()) {
        await db.query(
          "INSERT INTO telemetry_events VALUES($1,$2,'long-run','tool.read','end',null,null,$3)",
          [
            id,
            i < 103
              ? "2026-11-06T12:00:00.000001Z"
              : "2026-11-06T12:00:00.000002Z",
            { detail },
          ],
        );
      }
      const seen = [];
      let cursor = null;
      do {
        const page = await queryTrace(db, "long-run", cursor);
        assert.ok(page.events.length <= 100);
        seen.push(...page.events.map((e) => e.event_id));
        assert.ok(
          page.events.every((e) => e.truncated && e.detail.length === 12000),
        );
        cursor = page.next_cursor;
      } while (cursor);
      assert.equal(seen.length, 205);
      assert.equal(new Set(seen).size, 205);
      assert.deepEqual(new Set(seen), new Set(ids));
      assert.equal(
        (await queryTrace(db, "long-run", null, ids[0])).event.payload.detail,
        detail,
      );
      assert.equal(
        (await queryTrace(db, "other-run", null, ids[0])).event,
        null,
      );
    } finally {
      await db.end();
    }
  },
);
