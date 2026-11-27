import { createHash } from "node:crypto";
import { signSession, validSession } from "./session.mjs";
const tokenHash = (token) => createHash("sha256").update(token).digest("hex");

export async function createSession(db, secret) {
  const token = signSession(secret);
  const client = await db.connect();
  try {
    await client.query("BEGIN READ WRITE");
    await client.query(
      "DELETE FROM public.dashboard_sessions WHERE expires_at <= now()",
    );
    await client.query(
      "INSERT INTO public.dashboard_sessions(token_hash, expires_at) VALUES ($1, now() + interval '8 hours')",
      [tokenHash(token)],
    );
    await client.query("COMMIT");
    return token;
  } catch (error) {
    await client.query("ROLLBACK");
    throw error;
  } finally {
    client.release();
  }
}

export async function activeSession(db, token, secret) {
  if (!validSession(token, secret)) return false;
  const result = await db.query(
    "SELECT 1 FROM public.dashboard_sessions WHERE token_hash=$1 AND expires_at > now()",
    [tokenHash(token)],
  );
  return result.rowCount === 1;
}

export async function revokeSession(db, token) {
  if (!token) return;
  const client = await db.connect();
  try {
    await client.query("BEGIN READ WRITE");
    await client.query(
      "DELETE FROM public.dashboard_sessions WHERE token_hash=$1",
      [tokenHash(token)],
    );
    await client.query("COMMIT");
  } catch (error) {
    await client.query("ROLLBACK");
    throw error;
  } finally {
    client.release();
  }
}

// shortcut: workspace-wide allowance can deny new logins under attack; add trusted edge controls for hostile load.
export async function allowLogin(db) {
  const client = await db.connect();
  try {
    await client.query("BEGIN READ WRITE");
    const result = await client.query(`
      INSERT INTO public.dashboard_login_limit AS budget (id, window_started, attempts)
      VALUES (true, now(), 1)
      ON CONFLICT (id) DO UPDATE SET
        attempts = CASE WHEN budget.window_started <= now() - interval '1 minute'
          THEN 1 ELSE budget.attempts + 1 END,
        window_started = CASE WHEN budget.window_started <= now() - interval '1 minute'
          THEN now() ELSE budget.window_started END
      WHERE budget.window_started <= now() - interval '1 minute' OR budget.attempts < 20
      RETURNING attempts`);
    await client.query("COMMIT");
    return result.rowCount === 1;
  } catch (error) {
    await client.query("ROLLBACK");
    throw error;
  } finally {
    client.release();
  }
}
