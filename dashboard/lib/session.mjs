import { createHmac, createHash, timingSafeEqual } from "node:crypto";
const hash = (value) => createHash("sha256").update(value).digest();
export function matches(value, secret) {
  return (
    Boolean(secret && secret.length >= 24) &&
    timingSafeEqual(hash(value), hash(secret))
  );
}
export function signSession(secret, now = Date.now()) {
  const expiry = String(now + 8 * 60 * 60 * 1000);
  return (
    expiry + "." + createHmac("sha256", secret).update(expiry).digest("hex")
  );
}
export function validSession(token, secret, now = Date.now()) {
  if (!secret || secret.length < 24 || !token) return false;
  const [expiry, signature, extra] = token.split(".");
  if (
    extra !== undefined ||
    !/^\d+$/.test(expiry) ||
    !signature ||
    Number(expiry) <= now ||
    Number(expiry) > now + 8 * 60 * 60 * 1000
  )
    return false;
  const expected = createHmac("sha256", secret).update(expiry).digest("hex");
  return timingSafeEqual(hash(signature), hash(expected));
}
