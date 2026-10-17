/**
 * A whole number as typed by a clerk: plain digits ("185000") or correctly grouped
 * thousands ("185,000"). Anything else — decimals, misplaced commas, signs, letters — is
 * null, so the field can say what is wrong instead of quietly reading something else.
 */
export function parseWhole(text: string): number | null {
  const t = text.trim();
  if (!/^\d+$/.test(t) && !/^\d{1,3}(,\d{3})+$/.test(t)) return null;
  return Number(t.replace(/,/g, ""));
}

/**
 * The amount field's next value after an edit. A clean number is shown grouped. Grouping
 * as you type means the field holds "2,400" when the next digit arrives, and "2,4000" is
 * not valid grouping — so an edit that only added or removed digits in a value that was
 * already clean is regrouped from its digits (the commas were ours). Anything else,
 * including a comma the clerk typed, is left exactly as typed so the field can say what
 * is wrong with it.
 */
export function regroupAmount(prev: string, raw: string): string {
  const n = parseWhole(raw);
  if (n !== null) return n.toLocaleString("en-US");
  const commas = (s: string) => s.split(",").length - 1;
  if (parseWhole(prev) !== null && /^[\d,]*$/.test(raw) && commas(raw) <= commas(prev)) {
    const digits = raw.replace(/,/g, "");
    return digits === "" ? "" : Number(digits).toLocaleString("en-US");
  }
  return raw;
}
