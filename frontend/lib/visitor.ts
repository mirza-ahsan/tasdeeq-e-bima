/**
 * The visitor's address, as the reverse proxy in front of this app recorded it: the
 * *rightmost* X-Forwarded-For entry. Anything to its left was sent by the visitor and
 * proves nothing — forwarding the leftmost entry let one client pose as thousands and
 * walk straight past the API's per-client limits.
 *
 * Only when `trusted`: with no proxy of ours in front, Next.js passes a visitor-supplied
 * header straight through, so the header is ignored and the API falls back to the
 * connection it sees.
 */
export function visitor(headers: Headers, trusted = true): string {
  if (!trusted) return "";
  const hops = (headers.get("x-forwarded-for") ?? "")
    .split(",")
    .map((h) => h.trim())
    .filter(Boolean);
  return hops.at(-1) ?? headers.get("x-real-ip") ?? "";
}
