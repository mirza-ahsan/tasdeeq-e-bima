"use client";

import { useState } from "react";
import { GLOSSARY, Term } from "@/components/Term";
import { api } from "@/lib/api";
import type { Result } from "@/lib/types";

const BAND_COLOR = {
  low: "var(--risk-low)",
  medium: "var(--risk-med)",
  high: "var(--risk-high)",
} as const;

const HEADLINE = {
  low: "This claim looks ready to submit.",
  medium: "This claim needs a second look.",
  high: "This claim is likely to be rejected.",
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
        {HEADLINE[result.risk_band]}
      </h2>

      {/*
        The plain-language reading is the thing the user actually acts on, so it
        is set in display type at reading size and given the width to breathe —
        not compressed into a card alongside the raw figures. Typography is what
        separates it from model output; it needs no box to do that.
      */}
      <figure className="mt-7">
        <blockquote
          className="font-display border-l-2 pl-6 text-[1.24rem] leading-[1.62] text-ink
                     [text-wrap:pretty]"
          style={{ borderColor: BAND_COLOR[result.risk_band] }}
        >
          {result.explanation.text}
        </blockquote>
        <figcaption className="mt-3 pl-6 text-[11px] leading-relaxed text-ink-muted">
          Written by{" "}
          {result.explanation.source === "qwen"
            ? `Qwen (${result.explanation.model})`
            : "a fallback template — Qwen was unreachable"}
          , restating the figures on this page. It cannot change the probability, the
          reason code, or which fields were flagged.
        </figcaption>
      </figure>

      {/* The reason code, presented as a formal citation. */}
      {result.carc && (
        <div className="rule-t mt-10 pt-5">
          <span className="eyebrow">Most likely reason</span>
          <div className="mt-3 flex items-baseline gap-3">
            <span className="font-mono text-[13px] tracking-wide">
              <Term definition={GLOSSARY.carc}>CARC {result.carc.code}</Term>
            </span>
            <span className="tnum text-[11px] text-ink-muted">
              {Math.round(result.carc.confidence * 100)}% confidence
            </span>
          </div>
          <p className="mt-2 max-w-prose text-[15px] leading-relaxed">
            {result.carc.plain_english}
          </p>
          <p className="mt-3 border-l border-rule pl-3 font-mono text-[11.5px] leading-relaxed text-ink-muted">
            {result.carc.official_description}
            <br />
            <span className="opacity-70">— X12 Claim Adjustment Reason Codes</span>
          </p>

          <div className="mt-6 border border-rule-strong bg-surface p-5">
            <span className="eyebrow">Do this before submitting</span>
            <p className="mt-2 max-w-prose text-[15px] leading-relaxed">
              {result.carc.staff_action}
            </p>
          </div>

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
        <p className="rule-t mt-10 max-w-prose pt-5 text-[13.5px] leading-relaxed text-ink-soft">
          No rejection reason is shown, because the reason model is trained only on
          rejected claims. Naming one for a claim that looks clean would be inventing a
          problem.
        </p>
      )}

      <div className="rule-t mt-10 flex flex-wrap items-center gap-3 pt-5">
        <button
          onClick={onRestart}
          className="border border-accent bg-accent px-5 py-2.5 text-[13px] tracking-wide text-paper transition-opacity hover:opacity-90"
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
          <span className="max-w-prose text-[12px] leading-relaxed text-ink-muted">
            Logged as a training example. Nothing is retrained live — real systems close
            this loop from insurer remittance files over weeks.
          </span>
        )}
      </div>
    </div>
  );
}
