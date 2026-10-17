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
