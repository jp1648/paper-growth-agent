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
