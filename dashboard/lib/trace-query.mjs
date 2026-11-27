const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
export async function queryTrace(db, run, cursor = null, event = null) {
  if (!run || run.length > 200) throw new TypeError("Invalid run");
  if (event) {
    if (!uuid.test(event)) throw new TypeError("Invalid event");
    const result = await db.query(
      "SELECT event_id,at,name,phase,span_id,parent_span_id,payload FROM telemetry_events WHERE run_key=$1 AND event_id=$2::uuid",
      [run, event],
    );
    return { event: result.rows[0] ?? null };
  }
  let before = null;
  if (cursor) {
    try {
      if (cursor.length > 400) throw new Error();
      before = JSON.parse(Buffer.from(cursor, "base64url").toString());
      if (
        !Array.isArray(before) ||
        before.length !== 2 ||
        !uuid.test(before[1]) ||
        !/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{6}Z$/.test(before[0]) ||
        !Number.isFinite(Date.parse(before[0]))
      )
        throw new Error();
    } catch {
      throw new TypeError("Invalid cursor");
    }
  }
  const result = await db.query(
    `SELECT event_id,to_char(at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"') AS at,
    name,phase,span_id,parent_span_id,left(payload::text,12000) AS detail,
    length(payload::text)>12000 AS truncated FROM telemetry_events
    WHERE run_key=$1 ${before ? "AND (at,event_id)<($2::timestamptz,$3::uuid)" : ""}
    ORDER BY at DESC,event_id DESC LIMIT 101`,
    before ? [run, ...before] : [run],
  );
  const rows = result.rows.slice(0, 100);
  const last = rows.at(-1);
  return {
    events: rows.reverse(),
    next_cursor:
      result.rows.length > 100
        ? Buffer.from(JSON.stringify([last.at, last.event_id])).toString(
            "base64url",
          )
        : null,
  };
}
