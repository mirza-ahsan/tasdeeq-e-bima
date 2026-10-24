import { BAND_LABEL, blocksSubmission, pct, points as fmtPoints } from "@/lib/format";
import type { Check, Field, Result } from "@/lib/types";

/**
 * The one-page summary a clerk files with the claim: printed, or saved as a PDF from the
 * print dialog. It is hidden on screen and is the only thing that prints, in plain black
 * on white whatever the screen theme — paper has no dark mode, and a printout must read
 * without colour.
 */

const STATUS_WORD: Record<Check["status"], string> = {
  issue: "Problem",
  warn: "Worth a look",
  ok: "Passed",
  pending: "Not checked",
};

const SOURCE_WORD: Record<Field["source"], string> = {
  bill: "bill",
  answer: "answered",
  document: "documents",
};

function headline(result: Result): string {
  if (result.stop_reason === "too_few_answers") return "Not enough answered for a verdict";
  if (result.risk_band === "high") return "High risk of rejection";
  if (result.risk_band === "medium") return result.carc ? "Worth a second look" : "Not fully cleared";
  const blocking = result.checks.filter(blocksSubmission).length + (result.fix_first ? 1 : 0);
  if (blocking) return blocking === 1 ? "Low risk — fix one thing first" : "Low risk — fix the flagged problems first";
  if (result.unsure?.length) return "Low risk — confirm the open questions first";
  return "Looks ready to submit";
}

function firstStep(result: Result): string | null {
  const lead = result.carc ?? result.fix_first;
  if (lead) return lead.staff_action;
  const problem = result.checks.find((c) => c.status === "issue") ?? result.checks.find(blocksSubmission);
  if (problem) return problem.detail;
  if (result.unsure?.length) return `Confirm before submitting: ${result.unsure.map((u) => u.label).join(", ")}.`;
  return null;
}

export function PrintSummary({ result, generatedAt }: { result: Result; generatedAt: Date }) {
  const step = firstStep(result);
  // Problems first, as on screen: the reader of a printout scans from the top.
  const order: Check["status"][] = ["issue", "warn", "ok", "pending"];
  const checks = [...result.checks].sort((a, b) => order.indexOf(a.status) - order.indexOf(b.status));
  const known = [...result.claim, ...result.answered];

  return (
    <article className="print-only print-sheet" aria-hidden>
      <header className="ps-head">
        <div>
          <div className="ps-brand">Claim Check · Tasdeeq-e-Bima</div>
          <div className="ps-muted">Pre-submission check summary</div>
        </div>
        <div className="ps-right ps-muted">
          {generatedAt.toLocaleString("en-GB", { dateStyle: "medium", timeStyle: "short" })}
          <br />
          Check {result.session_id.slice(0, 8)}
        </div>
      </header>

      <p className="ps-claim">
        {result.patient.label} · {result.claim.map((f) => f.value_label).join(" · ")}
        {result.documents.length > 0 &&
          ` · read from ${result.documents.map((d) => d.label.toLowerCase()).join(", ")}`}
      </p>

      <section className="ps-verdict">
        <div className="ps-pct">{result.probability_percent}%</div>
        <div>
          <div className="ps-title">{headline(result)}</div>
          <div className="ps-muted">
            Chance of rejection ·{" "}
            {result.stop_reason === "too_few_answers" ? "estimate so far, not a verdict" : BAND_LABEL[result.risk_band]}{" "}
            (average claim {pct(result.base_rate)})
          </div>
          <p className="ps-quote">“{result.explanation.text}”</p>
        </div>
      </section>

      {step && (
        <section className="ps-box">
          <div className="ps-label">Do this before submitting</div>
          <p>{step}</p>
        </section>
      )}

      {result.carc && (
        <section className="ps-block">
          <div className="ps-label">Most likely reason · CARC {result.carc.code}</div>
          <p>{result.carc.plain_english}</p>
          <p className="ps-muted ps-small">X12: {result.carc.official_description}</p>
        </section>
      )}

      <div className="ps-cols">
        <section className="ps-block">
          <div className="ps-label">Rule checks</div>
          <ul>
            {checks.map((c) => (
              <li key={c.id}>
                <b>{c.label}</b> — {STATUS_WORD[c.status]}. <span className="ps-muted">{c.detail}</span>
              </li>
            ))}
          </ul>
        </section>
        <section className="ps-block">
          <div className="ps-label">What the check knew</div>
          <ul>
            {known.map((f) => (
              <li key={f.feature}>
                <b>{f.label}:</b> {f.value_label}{" "}
                <span className="ps-muted">
                  ({SOURCE_WORD[f.source]}
                  {Math.abs(f.contribution_points) >= 1 ? `, ${fmtPoints(f.contribution_points)} pts` : ""})
                </span>
              </li>
            ))}
            {(result.unsure ?? []).map((u) => (
              <li key={u.feature}>
                <b>{u.label}:</b> not sure — <span className="ps-muted">confirm before submitting</span>
              </li>
            ))}
          </ul>
        </section>
      </div>

      <footer className="ps-foot ps-muted">
        A second check for clinic staff, not a replacement for their judgement. The risk comes
        from a model trained on simulated claims; rule checks come from the tool&apos;s rule tables;
        {result.explanation.source === "qwen"
          ? `the sentence was worded by Qwen (${result.explanation.model}) and checked against the figures above`
          : "the sentence comes from a fixed template filled with the figures above"}
        . Points show how far each fact moved the
        estimate from a typical claim for this patient ({pct(result.typical_probability)}).
      </footer>
    </article>
  );
}
