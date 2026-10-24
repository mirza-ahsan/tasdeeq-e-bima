"use client";

import { useEffect, useRef, useState } from "react";
import { Checks } from "@/components/Checks";
import { IconCheckCircle, IconFile, IconFlag, IconRestart, IconSparkle } from "@/components/icons";
import { Ledger } from "@/components/Ledger";
import { PrintSummary } from "@/components/PrintSummary";
import { BAND_TONE, BandBadge } from "@/components/RiskPanel";
import { GLOSSARY, Term } from "@/components/Term";
import { Waterfall } from "@/components/Waterfall";
import { ApiError, api } from "@/lib/api";
import { pct, relative, STOP_REASON_TEXT } from "@/lib/format";
import type { Question, Result } from "@/lib/types";

/** "Likely to be rejected" only where the number supports it: a calibrated 40% is not. */
function headline(result: Result): { title: string; sub: string } {
  if (result.risk_band === "high") {
    return {
      title: "High risk of rejection",
      sub:
        result.probability >= 0.5
          ? "More likely to be rejected than accepted as it stands."
          : "Well above the average claim. Resolve the flagged issue before submitting.",
    };
  }
  if (result.risk_band === "medium") {
    return result.carc
      ? { title: "Worth a second look", sub: "A known problem raises the risk above the average claim." }
      : {
          title: "Not fully cleared",
          sub: "Nothing here rules it out, but not every common rejection reason could be ruled out either.",
        };
  }
  if (result.fix_first) {
    return { title: "Low risk — fix one thing first", sub: "The claim is in good shape apart from the problem below." };
  }
  // A rule or document problem the model cannot see still keeps a claim from being "ready".
  if (result.checks.some((c) => c.status === "issue")) {
    return { title: "Low risk — resolve the flagged problem", sub: "The model sees little risk, but a rule check below found something to fix first." };
  }
  const open = result.unsure?.length ?? 0;
  if (open) {
    return {
      title: `Low risk — confirm ${open === 1 ? "one detail" : `${open} details`} first`,
      sub: `Nothing in what was answered points to a rejection, but ${open === 1 ? "one question was" : `${open} questions were`} left as not sure.`,
    };
  }
  return { title: "Looks ready to submit", sub: "No common rejection trigger found in this claim." };
}

function Feedback({ sessionId }: { sessionId: string }) {
  const [open, setOpen] = useState(false);
  const [note, setNote] = useState("");
  const [state, setState] = useState<"idle" | "sending" | "sent">("idle");
  const [error, setError] = useState<string | null>(null);

  async function send() {
    setState("sending");
    setError(null);
    try {
      await api.feedback(sessionId, note);
      setState("sent");
    } catch (e) {
      setState("idle");
      setError(e instanceof ApiError ? e.message : "The correction could not be logged. Try again.");
    }
  }

  if (state === "sent") {
    return (
      <p role="status" className="flex items-start gap-2.5 text-[13px] leading-relaxed text-ink-2">
        <IconCheckCircle size={17} className="mt-0.5 shrink-0 text-good-ink" />
        Logged as a labelled example. Nothing is retrained live — real systems close this
        loop from insurer remittance files over weeks.
      </p>
    );
  }

  return (
    <div>
      {!open ? (
        <button
          type="button"
          onClick={() => setOpen(true)}
          className="inline-flex h-11 items-center gap-2 rounded-full border border-line-strong px-5 text-[13.5px] text-ink-2 transition hover:border-ink-3 hover:text-ink"
        >
          <IconFlag size={15} /> This prediction is wrong
        </button>
      ) : (
        <div className="fade max-w-[520px]">
          <label className="block text-[13px] font-medium" htmlFor="fb-note">
            What looks wrong? <span className="font-normal text-ink-3">Optional</span>
          </label>
          <textarea
            id="fb-note"
            value={note}
            onChange={(e) => setNote(e.target.value.slice(0, 500))}
            rows={2}
            placeholder="e.g. this insurer waived pre-authorisation for emergencies"
            className="mt-2 w-full resize-none rounded-xl border border-line-strong bg-surface px-3.5 py-2.5 text-[14px] outline-none focus:border-brand"
          />
          <div className="mt-2 flex items-center gap-2">
            <button
              type="button"
              disabled={state === "sending"}
              onClick={send}
              className="h-10 rounded-full bg-ink px-5 text-[13.5px] font-medium text-bg transition disabled:opacity-50"
            >
              {state === "sending" ? "Logging…" : "Log correction"}
            </button>
            <button type="button" onClick={() => setOpen(false)} className="h-10 px-3 text-[13px] text-ink-3 hover:text-ink">
              Cancel
            </button>
          </div>
        </div>
      )}
      {error && <p role="alert" className="mt-2 text-[12.5px] text-crit-ink">{error}</p>}
    </div>
  );
}

function questionCount(result: Result) {
  const read = result.answered.filter((a) => a.source === "document").length;
  const asked = result.n_answered - read + (result.unsure?.length ?? 0);
  const q = `${asked} question${asked === 1 ? "" : "s"} asked`;
  return read ? `${q}, ${read} answer${read === 1 ? "" : "s"} read from the documents.` : `${q}.`;
}

export function ResultView({
  result,
  questions,
  busy,
  onEdit,
  onRestart,
}: {
  result: Result;
  questions: Record<string, Question>;
  busy: boolean;
  onEdit: (feature: string, value: unknown) => void;
  onRestart: () => void;
}) {
  const tone = BAND_TONE[result.risk_band];
  const { title, sub } = headline(result);
  const action = result.carc ?? result.fix_first;
  const problem = result.checks.find((c) => c.status === "issue");
  const unsure = result.unsure ?? [];
  const heading = useRef<HTMLHeadingElement>(null);
  // The verdict takes focus when it arrives, so keyboard and screen-reader users land on it.
  useEffect(() => heading.current?.focus({ preventScroll: true }), []);
  const [generatedAt] = useState(() => new Date());

  function printSummary() {
    // The print dialog offers the page title as the PDF's file name.
    const title = document.title;
    const proc = result.claim.find((f) => f.feature === "procedure_category")?.value_label ?? "claim";
    document.title = `Claim check — ${result.patient.label}, ${proc} — ${generatedAt.toISOString().slice(0, 10)}`;
    window.addEventListener("afterprint", () => (document.title = title), { once: true });
    window.print();
  }

  return (
    <>
    <PrintSummary result={result} generatedAt={generatedAt} />
    <div className="screen-only space-y-6">
      {/* Verdict */}
      <section className="rise card overflow-hidden">
        <div className="h-1.5" style={{ background: tone.mark }} />
        <div className="grid grid-cols-1 gap-8 p-6 sm:p-8 lg:grid-cols-[auto_1fr]">
          <div className="flex flex-col items-start">
            <span className="eyebrow">Rejection risk</span>
            <div className="mt-2 flex items-baseline gap-1">
              <span className="text-[84px] font-semibold leading-[0.85] tracking-[-0.05em]">{result.probability_percent}</span>
              <span className="text-[30px] font-medium text-ink-3">%</span>
            </div>
            <div className="mt-4"><BandBadge band={result.risk_band} /></div>
            <p className="mt-3 text-[12.5px] text-ink-3">{relative(result.probability, result.base_rate)}</p>
          </div>

          <div className="min-w-0">
            <h1 ref={heading} tabIndex={-1} className="text-[30px] font-semibold leading-tight tracking-[-0.03em] outline-none sm:text-[34px]">
              {title}
            </h1>
            <p className="mt-1.5 text-[14.5px] text-ink-2">{sub}</p>

            <figure className="mt-6 rounded-2xl bg-surface-2 p-5">
              <blockquote className="font-display text-[21px] leading-[1.45] text-ink [text-wrap:pretty]">
                “{result.explanation.text}”
              </blockquote>
              <figcaption className="mt-3 flex items-start gap-2 text-[12px] leading-relaxed text-ink-3">
                <IconSparkle size={14} className="mt-0.5 shrink-0" />
                <span>
                  {result.explanation.source === "qwen"
                    ? `Worded by Qwen (${result.explanation.model}) from the figures on this page, and checked before display.`
                    : "Worded by the built-in template (Qwen was unavailable or its reply failed a check)."}{" "}
                  It cannot change the risk, the reason code, or which fields were flagged.
                </span>
              </figcaption>
            </figure>

            {action ? (
              <div className="mt-4 rounded-2xl border p-5" style={{ borderColor: `color-mix(in oklab, ${tone.mark} 35%, transparent)`, background: tone.wash }}>
                <span className="text-[11.5px] font-semibold uppercase tracking-[0.06em]" style={{ color: tone.ink }}>
                  Do this before submitting
                </span>
                <p className="mt-1.5 text-[15.5px] font-medium leading-relaxed text-ink">{action.staff_action}</p>
                {result.fix_first && (
                  <p className="mt-1 text-[13px] text-ink-2">{result.fix_first.plain_english}</p>
                )}
              </div>
            ) : problem ? (
              // A rule or document problem with no reason code behind it still comes first.
              <div className="mt-4 rounded-2xl border border-line-strong bg-surface-2 p-5">
                <span className="text-[11.5px] font-semibold uppercase tracking-[0.06em] text-ink-2">
                  Do this before submitting
                </span>
                <p className="mt-1.5 text-[15px] font-medium leading-relaxed text-ink">{problem.detail}</p>
                <p className="mt-1 text-[12.5px] text-ink-3">{problem.label} · found by a rule check, not the model</p>
              </div>
            ) : unsure.length > 0 ? (
              <div className="mt-4 rounded-2xl border border-line-strong bg-surface-2 p-5">
                <span className="text-[11.5px] font-semibold uppercase tracking-[0.06em] text-ink-2">
                  Confirm before submitting
                </span>
                <p className="mt-1.5 text-[15px] font-medium leading-relaxed text-ink">
                  {unsure.map((u) => u.label).join(", ")} — left as not sure. Answer {unsure.length === 1 ? "it" : "them"} in the list below.
                </p>
              </div>
            ) : (
              result.risk_band === "low" && (
                <p className="mt-4 flex items-center gap-2 text-[13.5px] text-ink-2">
                  <IconCheckCircle size={17} className="text-good-ink" /> Nothing to fix before submitting.
                </p>
              )
            )}
          </div>
        </div>
      </section>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[1.25fr_1fr]">
        {/* Why */}
        <section className="rise rise-1 card p-6 sm:p-7">
          <Waterfall
            typical={result.typical_probability}
            probability={result.probability}
            drivers={result.drivers}
            other={result.other_points}
            patientLabel={result.patient.label}
          />
        </section>

        {/* Reason */}
        <section className="rise rise-2 card p-6 sm:p-7">
          <span className="eyebrow">Most likely reason</span>
          {result.carc ? (
            <>
              <div className="mt-3 flex flex-wrap items-center gap-3">
                <span className="rounded-lg bg-ink px-2.5 py-1 font-mono text-[13px] text-bg">
                  <Term definition={GLOSSARY.carc}>CARC {result.carc.code}</Term>
                </span>
                <span className="tnum text-[12.5px] text-ink-3">
                  {pct(result.carc.confidence)} confidence
                </span>
              </div>
              <p className="mt-3 text-[16px] leading-relaxed">{result.carc.plain_english}</p>
              <p className="mt-4 border-l-2 border-line-strong pl-3 font-mono text-[11.5px] leading-relaxed text-ink-3 [overflow-wrap:anywhere]">
                {result.carc.official_description}
                <span className="mt-1 block">— X12 Claim Adjustment Reason Codes</span>
              </p>
              {result.carc_alternatives.length > 0 && (
                <p className="mt-4 text-[12.5px] text-ink-3">
                  Also present in this claim:{" "}
                  {result.carc_alternatives.map((c) => `CARC ${c.code} (${c.plain_english.replace(/\.$/, "").toLowerCase()})`).join("; ")}
                </p>
              )}
            </>
          ) : result.fix_first ? (
            <p className="mt-3 text-[14px] leading-relaxed text-ink-2">
              None: the claim is low risk. The one problem found —{" "}
              {result.fix_first.plain_english.replace(/\.$/, "").toLowerCase()} (CARC{" "}
              {result.fix_first.code}) — is worth fixing anyway, and is shown above.
            </p>
          ) : (
            <p className="mt-3 text-[14px] leading-relaxed text-ink-2">
              {problem
                ? `None: no reason code applies. The problem shown above comes from the “${problem.label}” check, not the model.`
                : result.risk_band === "low"
                ? "None shown. No known rejection trigger was found, and naming a reason for a claim that looks clean would be inventing a problem."
                : "None named. No specific defect was found in what was answered, and the tool names a reason only when the claim actually shows one."}
            </p>
          )}
        </section>
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[1fr_1.25fr]">
        <section className="rise rise-2 card p-6 sm:p-7">
          <span className="eyebrow">Insurer rules</span>
          <div className="mt-4"><Checks checks={result.checks} /></div>
        </section>
        <section className="rise rise-3 card p-6 sm:p-7">
          <Ledger
            claim={result.claim}
            answered={result.answered}
            unsure={result.unsure}
            questions={questions}
            busy={busy}
            onEdit={onEdit}
          />
          <p className="mt-4 text-[12px] text-ink-3">
            {questionCount(result)}{" "}
            {result.stop_reason ? STOP_REASON_TEXT[result.stop_reason] ?? "" : ""}
          </p>
        </section>
      </div>

      <section className="no-print rise rise-4 flex flex-wrap items-start justify-between gap-4 rounded-[18px] border border-line px-6 py-5">
        <button
          type="button"
          onClick={onRestart}
          className="inline-flex h-11 items-center gap-2 rounded-full bg-brand px-6 text-[14px] font-medium text-brand-ink transition hover:bg-brand-hover"
        >
          <IconRestart size={16} /> Check another claim
        </button>
        <button
          type="button"
          onClick={printSummary}
          className="inline-flex h-11 items-center gap-2 rounded-full border border-line-strong px-5 text-[13.5px] text-ink-2 transition hover:border-ink-3 hover:text-ink"
        >
          <IconFile size={15} /> Download summary (PDF)
        </button>
        <Feedback key={result.session_id} sessionId={result.session_id} />
      </section>
    </div>
    </>
  );
}
