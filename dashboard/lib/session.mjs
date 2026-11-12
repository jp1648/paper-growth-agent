import {
  createHmac,
  createHash,
  timingSafeEqual,
  randomBytes,
} from "node:crypto";
const hash = (value) => createHash("sha256").update(value).digest();
export function matches(value, secret) {
  return (
    Boolean(secret && secret.length >= 24) &&
    timingSafeEqual(hash(value), hash(secret))
  );
}
export function signSession(secret, now = Date.now()) {
  const expiry =
    String(now + 8 * 60 * 60 * 1000) + ":" + randomBytes(32).toString("hex");
  return (
    expiry + "." + createHmac("sha256", secret).update(expiry).digest("hex")
  );
}
export function validSession(token, secret, now = Date.now()) {
  if (!secret || secret.length < 24 || !token) return false;
  const [payload, signature, extra] = token.split(".");
  const [expiry, nonce, extraField] = payload.split(":");
  if (
    extra !== undefined ||
    extraField !== undefined ||
    !/^[0-9a-f]{64}$/.test(nonce || "") ||
    !/^\d+$/.test(expiry) ||
    !signature ||
    Number(expiry) <= now ||
    Number(expiry) > now + 8 * 60 * 60 * 1000
  )
    return false;
  const expected = createHmac("sha256", secret).update(payload).digest("hex");
  return timingSafeEqual(hash(signature), hash(expected));
}
