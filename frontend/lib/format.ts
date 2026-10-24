import type { RiskBand } from "./types";

export const pct = (p: number) => `${Math.round(p * 100)}%`;

export const pkr = (n: number) => `PKR ${Math.round(n).toLocaleString("en-US")}`;

/** Signed percentage points, one decimal, with a real minus sign. */
export const points = (n: number) => {
  const v = Math.round(n * 10) / 10;
  if (v === 0) return "0.0";
  return `${v > 0 ? "+" : "−"}${Math.abs(v).toFixed(1)}`;
};

export const BAND_LABEL: Record<RiskBand, string> = {
  low: "Low risk",
  medium: "Not fully cleared",
  high: "High risk",
};

/** Ratio of this claim's risk to the average claim's, worded for a clerk. */
export function relative(p: number, base: number): string {
  const r = p / base;
  if (r >= 1.15) return `${r.toFixed(1)}× the average claim`;
  if (r <= 0.87) return `${Math.round((1 - r) * 100)}% below the average claim`;
  return "about the average claim";
}

export const STOP_REASON_TEXT: Record<string, string> = {
  confident_low_risk: "Stopped once the estimate settled in the low band.",
  confident_high_risk: "Stopped once the estimate settled in the high band.",
  no_informative_questions_left: "Stopped: no remaining question would move the estimate.",
  all_questions_answered: "Every question was answered.",
  question_limit_reached: "Reached the question limit.",
  too_few_answers: "Too few questions were answered for a verdict.",
};

/** Whether a check keeps a claim from being called ready: any problem, or a required
 * paper missing from the uploads. Mirrors `blocks_submission` in backend/services/checks.py. */
export const blocksSubmission = (c: { id: string; status: string }) =>
  c.status === "issue" || (c.id === "doc_required" && c.status === "warn");
