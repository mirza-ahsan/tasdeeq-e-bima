"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import type { Result } from "@/lib/types";

const BAND_COLOR = {
  low: "var(--risk-low)",
  medium: "var(--risk-med)",
  high: "var(--risk-high)",
} as const;

export function ResultPanel({ result, onRestart }: { result: Result; onRestart: () => void }) {
  const [marked, setMarked] = useState(false);
  const [sending, setSending] = useState(false);

  async function markWrong() {
    setSending(true);
    try {
      await api.feedback(result.session_id, "wrong");
      setMarked(true);
    } finally {
      setSending(false);
    }
  }

  return (
    <div className="rise">
      <div className="flex items-baseline gap-3">
        <span className="eyebrow">Assessment</span>
        <span className="h-px flex-1 bg-rule" />
        <span className="tnum text-[11px] text-ink-muted">
          {result.n_answered} questions asked
        </span>
      </div>

      <h2 className="font-display mt-5 text-[2rem] leading-[1.2] tracking-[-0.01em]">
        {result.risk_band === "low"
          ? "This claim looks ready to submit."
          : result.risk_band === "high"
            ? "This claim is likely to be rejected."
            : "This claim needs a second look."}
      </h2>

      {/* Qwen's plain-language reading, explicitly framed as a restatement. */}
      <figure className="mt-6 border-l-2 pl-5" style={{ borderColor: BAND_COLOR[result.risk_band] }}>
        <blockquote className="text-[16px] leading-[1.6] text-ink">
          {result.explanation.text}
        </blockquote>
        <figcaption className="mt-2.5 text-[11px] text-ink-muted">
          Written by{" "}
          {result.explanation.source === "qwen"
            ? `Qwen (${result.explanation.model})`
            : "a fallback template — Qwen was unreachable"}
          , restating the figures above. It cannot change the probability, the reason code,
          or which fields were flagged.
        </figcaption>
      </figure>

      {/* The reason code, presented as a formal citation. */}
      {result.carc && (
        <div className="mt-8 rule-t pt-5">
          <span className="eyebrow">Most likely reason</span>
          <div className="mt-3 flex items-baseline gap-3">
            <span className="font-mono text-[13px] tracking-wide text-accent">
              CARC {result.carc.code}
            </span>
            <span className="tnum text-[11px] text-ink-muted">
              {Math.round(result.carc.confidence * 100)}% confidence
            </span>
          </div>
          <p className="mt-2 text-[15px] leading-relaxed">{result.carc.plain_english}</p>
          <p className="mt-3 border-l border-rule pl-3 font-mono text-[11.5px] leading-relaxed text-ink-muted">
            {result.carc.official_description}
            <br />
            <span className="opacity-70">— X12 Claim Adjustment Reason Codes</span>
          </p>
          <p className="mt-4 text-[14px] leading-relaxed">
            <span className="eyebrow">Do this</span>
            <br />
            <span className="mt-1 inline-block">{result.carc.staff_action}</span>
          </p>

          {result.carc_alternatives.length > 0 && (
            <p className="mt-4 text-[12px] text-ink-muted">
              Also possible:{" "}
              {result.carc_alternatives
                .map((c) => `CARC ${c.code} (${Math.round(c.confidence * 100)}%)`)
                .join(", ")}
            </p>
          )}
        </div>
      )}

      {!result.carc && (
        <p className="mt-8 rule-t pt-5 text-[13.5px] leading-relaxed text-ink-soft">
          No rejection reason is shown, because the reason model is trained only on rejected
          claims. Naming one for a claim that looks clean would be inventing a problem.
        </p>
      )}

      <div className="mt-10 flex flex-wrap items-center gap-3 rule-t pt-5">
        <button
          onClick={onRestart}
          className="border border-ink px-5 py-2.5 text-[13px] tracking-wide transition-colors hover:bg-ink hover:text-paper"
        >
          Check another claim
        </button>
        <button
          onClick={markWrong}
          disabled={marked || sending}
          className="border border-rule-strong px-5 py-2.5 text-[13px] tracking-wide text-ink-soft
                     transition-colors hover:border-ink hover:text-ink disabled:opacity-40"
        >
          {marked ? "Correction logged" : "This prediction is wrong"}
        </button>
        {marked && (
          <span className="text-[12px] text-ink-muted">
            Logged as a training example. Nothing is retrained live — real systems close this
            loop from insurer remittance files over weeks.
          </span>
        )}
      </div>
    </div>
  );
}
