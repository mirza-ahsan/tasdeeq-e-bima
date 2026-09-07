"use client";

import { useState } from "react";
import { GLOSSARY, Term } from "@/components/Term";
import type { Question } from "@/lib/types";

/**
 * One question at a time.
 *
 * The information-gain figure is shown deliberately: it is the evidence that
 * the question was chosen rather than scripted. It is jargon, so it carries its
 * definition rather than assuming one.
 *
 * `alert` reports what the previous answer did to the estimate. It appears here
 * — attached to the next question, while the claim is still being assembled —
 * rather than in the final explanation, because a problem the user can still
 * fix should be raised while they can still fix it.
 */
export function QuestionCard({
  question,
  index,
  suggested,
  busy,
  alert,
  onAnswer,
}: {
  question: Question;
  index: number;
  suggested?: unknown;
  busy: boolean;
  alert: { label: string; valueLabel: string; deltaPoints: number } | null;
  onAnswer: (value: unknown) => void;
}) {
  // The parent remounts this on every question, so the prefilled demo value is
  // initial state rather than an effect that re-syncs it after the first paint.
  const [numberValue, setNumberValue] = useState<string>(() =>
    suggested !== undefined && suggested !== null ? String(suggested) : ""
  );

  const selectedIndex =
    suggested === undefined
      ? -1
      : question.options.findIndex((o) => o.value === suggested);

  return (
    <section className="rise">
      {alert && (
        <div
          role="status"
          className="mb-8 border-l-2 pl-4"
          style={{ borderColor: "var(--risk-high)" }}
        >
          <p className="text-[13.5px] leading-relaxed text-ink">
            <span className="text-ink-muted">{alert.label}:</span> {alert.valueLabel} —
            this raised the estimate by{" "}
            <span className="tnum font-medium">{alert.deltaPoints} points</span>.
          </p>
          <p className="mt-1 text-[12px] leading-relaxed text-ink-muted">
            Worth confirming before this claim goes out. If it was entered wrongly, start
            again rather than submitting on it.
          </p>
        </div>
      )}

      <div className="flex items-baseline gap-3">
        <span className="eyebrow tnum">Question {index}</span>
        <span className="h-px flex-1 bg-rule" />
        <span className="eyebrow tnum !normal-case !tracking-normal">
          <Term definition={GLOSSARY.infoGain}>
            info gain {question.expected_info_gain.toFixed(4)}
          </Term>
        </span>
      </div>

      <h2 className="font-display mt-4 text-[1.75rem] leading-[1.25] tracking-[-0.01em]">
        {question.text}
      </h2>
      <p className="mt-2 max-w-prose text-[13.5px] leading-relaxed text-ink-soft">
        {question.help_text}
      </p>

      {question.kind === "choice" ? (
        <ul className="mt-6 space-y-px">
          {question.options.map((option, i) => (
            <li key={String(option.value)}>
              <button
                type="button"
                disabled={busy}
                onClick={() => onAnswer(option.value)}
                className="group flex w-full items-center gap-4 border border-transparent border-b-rule
                           bg-transparent px-3 py-3 text-left transition-colors
                           hover:border-rule-strong hover:bg-surface
                           disabled:opacity-40"
              >
                <span
                  className="tnum w-5 shrink-0 text-[11px] text-ink-muted
                             group-hover:text-accent"
                >
                  {String.fromCharCode(65 + i)}
                </span>
                <span className="flex-1 text-[15px]">{option.label}</span>
                {i === selectedIndex && (
                  <span className="eyebrow shrink-0 !text-[10px] text-accent">
                    from demo claim
                  </span>
                )}
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <form
          className="mt-6 flex items-end gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            if (numberValue !== "") onAnswer(Number(numberValue));
          }}
        >
          <label className="block">
            <span className="eyebrow">{question.unit ?? "value"}</span>
            <input
              type="number"
              autoFocus
              value={numberValue}
              onChange={(e) => setNumberValue(e.target.value)}
              className="tnum mt-1.5 block w-52 border-b border-rule-strong bg-transparent
                         px-1 py-2 font-mono text-2xl outline-none
                         focus:border-accent"
              placeholder="0"
            />
          </label>
          <button
            type="submit"
            disabled={busy || numberValue === ""}
            className="border border-accent bg-accent px-5 py-2.5 text-[13px] tracking-wide text-paper transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-30"
          >
            Continue
          </button>
        </form>
      )}
    </section>
  );
}
