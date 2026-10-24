"use client";

import { useEffect, useRef, useState } from "react";
import { IconAlert, IconArrowRight, IconFile, IconPause, IconPlay, IconSparkle } from "@/components/icons";
import { GLOSSARY, Term } from "@/components/Term";
import { parseWhole } from "@/lib/numbers";
import type { Change, Question } from "@/lib/types";

/** A jump this large is worth interrupting the flow for; smaller ones sit in the ledger. */
const ALERT_POINTS = 8;

/**
 * One question at a time.
 *
 * The information-gain figure is shown deliberately: it is the evidence that the
 * question was chosen rather than scripted. Options can be picked with their letter
 * keys while the card has focus — and focus moves to each new question, so keyboard
 * and screen-reader users follow the flow. The keys are inert anywhere else (the
 * answer editor, the demo controls), so a letter typed there cannot answer by accident.
 * A typed answer must be a whole number within the limits the API enforces; nothing is
 * silently stripped ("1500.00" is refused, not read as 150,000).
 *
 * `change` reports what the previous answer did to the estimate. A big jump is raised
 * here, attached to the next question, because a problem the user can still fix should
 * be raised while they can still fix it — not saved for the verdict.
 */
export function QuestionCard({
  question,
  index,
  suggested,
  suggestedBy = "demo",
  busy,
  change,
  autoplay,
  notice,
  onToggleAutoplay,
  onAnswer,
}: {
  question: Question;
  index: number;
  suggested?: unknown;
  /** Whose suggestion it is: the demo script's, or what the documents point to. */
  suggestedBy?: "demo" | "documents";
  busy: boolean;
  change: Change | null;
  autoplay: boolean | null;
  notice?: string | null;
  onToggleAutoplay: () => void;
  onAnswer: (value: unknown) => void;
}) {
  // The parent remounts this on every question, so the scripted value is initial state.
  const [text, setText] = useState<string>(() =>
    question.kind === "number" && suggested !== undefined && suggested !== null ? String(suggested) : "",
  );
  const parsed = parseWhole(text);
  const wellFormed = parsed !== null;
  const number = parsed ?? NaN;
  const numberOk =
    wellFormed &&
    (question.min_value === null || number >= question.min_value) &&
    (question.max_value === null || number <= question.max_value);

  const card = useRef<HTMLElement>(null);
  const heading = useRef<HTMLHeadingElement>(null);

  // A new question takes focus (the number field focuses itself).
  useEffect(() => {
    if (question.kind === "choice") heading.current?.focus({ preventScroll: true });
  }, [question]);

  // Letter keys pick an option, but only while focus is inside this card.
  useEffect(() => {
    if (question.kind !== "choice") return;
    const onKey = (e: KeyboardEvent) => {
      if (busy || e.metaKey || e.ctrlKey || e.altKey) return;
      const focused = document.activeElement;
      if (!card.current?.contains(focused) || focused?.closest("[data-no-shortcuts]")) return;
      const i = e.key.toUpperCase().charCodeAt(0) - 65;
      if (e.key.length === 1 && i >= 0 && i < question.options.length) {
        e.preventDefault();
        onAnswer(question.options[i].value);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [question, busy, onAnswer]);

  const big = change && Math.abs(change.delta_points) >= ALERT_POINTS ? change : null;

  return (
    <section ref={card} className="rise">
      {big && (
        <div
          role="status"
          className="mb-6 flex items-start gap-3 rounded-2xl border px-4 py-3.5"
          style={{
            borderColor: big.delta_points > 0 ? "color-mix(in oklab, var(--crit) 30%, transparent)" : "var(--border)",
            background: big.delta_points > 0 ? "var(--crit-wash)" : "var(--surface-2)",
          }}
        >
          <IconAlert size={18} className="mt-0.5 shrink-0" style={{ color: big.delta_points > 0 ? "var(--crit-ink)" : "var(--ink-3)" }} />
          <div className="text-[13.5px] leading-relaxed">
            <p className="text-ink">
              <span className="text-ink-2">{big.label}:</span> {big.value_label} —{" "}
              {big.delta_points > 0 ? "raised" : "lowered"} the estimate by{" "}
              <span className="tnum font-semibold">{Math.abs(Math.round(big.delta_points))} points</span>.
            </p>
            {big.delta_points > 0 && (
              <p className="mt-0.5 text-[12.5px] text-ink-2">
                Worth confirming before this claim goes out. If it was entered wrongly, change it
                in the list below.
              </p>
            )}
          </div>
        </div>
      )}

      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <span className="rounded-full bg-ink px-2.5 py-1 text-[11.5px] font-medium text-bg tnum">
          Question {index}
        </span>
        <span className="inline-flex items-center gap-1.5 text-[12px] text-ink-3">
          <IconSparkle size={13} className="text-brand" />
          Chosen for{" "}
          <Term definition={GLOSSARY.infoGain}>
            <span className="tnum">{question.expected_info_gain.toFixed(3)} bits</span>
          </Term>{" "}
          of information gain
        </span>
        {autoplay !== null && (
          <button
            type="button"
            data-no-shortcuts
            onClick={onToggleAutoplay}
            className="ml-auto inline-flex h-8 items-center gap-1.5 rounded-full border border-line-strong bg-surface px-3 text-[12px] text-ink-2 transition hover:text-ink"
          >
            {autoplay ? <IconPause size={13} /> : <IconPlay size={13} />}
            {autoplay ? "Pause demo" : "Play demo"}
          </button>
        )}
      </div>
      {notice && <p className="mt-3 text-[12.5px] text-ink-2">{notice}</p>}

      <h2
        ref={heading}
        tabIndex={-1}
        className="mt-5 text-[28px] font-semibold leading-[1.2] tracking-[-0.025em] outline-none sm:text-[32px]"
      >
        {question.text}
      </h2>
      <p className="mt-2.5 max-w-[620px] text-[14.5px] leading-relaxed text-ink-2">{question.help_text}</p>
      {question.evidence && (
        <p className="mt-4 flex max-w-[620px] items-start gap-2.5 rounded-xl border border-line bg-surface-2 px-3.5 py-2.5 text-[13px] leading-relaxed text-ink-2">
          <IconFile size={15} className="mt-0.5 shrink-0 text-ink-3" />
          <span>
            <span className="font-medium text-ink">From the documents: </span>
            {question.evidence}
            {suggestedBy === "documents" && " You answer it — a document can suggest, not confirm."}
          </span>
        </p>
      )}

      {question.kind === "choice" ? (
        <ul className="mt-7 grid gap-2.5 sm:grid-cols-2">
          {question.options.map((option, i) => {
            const isSuggested = suggested !== undefined && option.value === suggested;
            return (
              <li key={String(option.value)}>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => onAnswer(option.value)}
                  className={`group flex w-full items-center gap-3.5 rounded-2xl border px-4 py-3.5 text-left transition
                              disabled:cursor-wait disabled:opacity-50 ${
                                isSuggested
                                  ? "border-brand bg-brand-wash"
                                  : "border-line bg-surface hover:-translate-y-px hover:border-line-strong hover:shadow-[var(--shadow-md)]"
                              }`}
                >
                  <kbd
                    className={`grid h-7 w-7 shrink-0 place-items-center rounded-lg border font-mono text-[12px] transition ${
                      isSuggested
                        ? "border-brand bg-brand text-brand-ink"
                        : "border-line bg-surface-2 text-ink-2 group-hover:border-line-strong group-hover:text-ink"
                    }`}
                  >
                    {String.fromCharCode(65 + i)}
                  </kbd>
                  <span className="flex-1 text-[15px] leading-snug">{option.label}</span>
                  {isSuggested && (
                    <span className="shrink-0 text-[11px] font-medium text-brand">
                      {suggestedBy === "demo" ? "Demo answer" : "Documents suggest"}
                    </span>
                  )}
                </button>
              </li>
            );
          })}
        </ul>
      ) : (
        <form
          className="mt-7 flex flex-wrap items-end gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            if (numberOk) onAnswer(number);
          }}
        >
          <label className="block">
            <span className="text-[12px] text-ink-3">
              {question.unit ?? "Value"}
              {question.min_value !== null && question.max_value !== null &&
                ` · ${question.min_value.toLocaleString()}–${question.max_value.toLocaleString()}`}
            </span>
            <div className="mt-1.5 flex items-center rounded-2xl border border-line-strong bg-surface focus-within:border-brand">
              <input
                autoFocus
                inputMode="numeric"
                value={text}
                onChange={(e) => setText(e.target.value.slice(0, 12))}
                placeholder="0"
                aria-invalid={text !== "" && !numberOk}
                className="tnum h-14 w-44 rounded-2xl bg-transparent px-4 text-[24px] font-medium outline-none"
              />
              {question.unit && <span className="pr-4 text-[14px] text-ink-3">{question.unit}</span>}
            </div>
          </label>
          <button
            type="submit"
            disabled={busy || !numberOk}
            className="group inline-flex h-14 items-center gap-2 rounded-2xl bg-brand px-6 text-[14.5px] font-medium text-brand-ink transition
                       hover:bg-brand-hover disabled:cursor-not-allowed disabled:opacity-40"
          >
            Continue <IconArrowRight size={16} className="transition group-hover:translate-x-0.5" />
          </button>
          {text !== "" && !numberOk && (
            <span role="alert" className="w-full text-[12.5px] text-crit-ink">
              {wellFormed ? "Enter" : "Digits only, please — enter"} a whole number between{" "}
              {question.min_value?.toLocaleString()} and {question.max_value?.toLocaleString()}.
            </span>
          )}
        </form>
      )}

      {question.kind === "choice" && (
        <p className="mt-4 hidden text-[12px] text-ink-3 sm:block">
          Tip: while this question has focus, press <kbd className="rounded border border-line bg-surface-2 px-1 font-mono">A</kbd>–
          <kbd className="rounded border border-line bg-surface-2 px-1 font-mono">
            {String.fromCharCode(64 + question.options.length)}
          </kbd>{" "}
          to answer.
        </p>
      )}
    </section>
  );
}
