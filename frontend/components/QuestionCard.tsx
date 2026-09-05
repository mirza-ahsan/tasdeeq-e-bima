"use client";

import { useEffect, useState } from "react";
import type { Question } from "@/lib/types";

/**
 * One question at a time. The information-gain figure is shown deliberately: it is the
 * evidence that the question was chosen rather than scripted, and it is the detail worth
 * pointing at during the walkthrough.
 */
export function QuestionCard({
  question,
  index,
  suggested,
  busy,
  onAnswer,
}: {
  question: Question;
  index: number;
  suggested?: unknown;
  busy: boolean;
  onAnswer: (value: unknown) => void;
}) {
  const [numberValue, setNumberValue] = useState<string>("");

  useEffect(() => {
    setNumberValue(
      suggested !== undefined && suggested !== null ? String(suggested) : ""
    );
  }, [question.feature, suggested]);

  const selectedIndex =
    suggested === undefined
      ? -1
      : question.options.findIndex((o) => o.value === suggested);

  return (
    <section key={question.feature} className="rise">
      <div className="flex items-baseline gap-3">
        <span className="eyebrow tnum">Question {index}</span>
        <span className="h-px flex-1 bg-rule" />
        <span
          className="tnum text-[11px] text-ink-muted"
          title="Expected reduction in uncertainty from asking this, in bits. The field with the highest value is chosen."
        >
          info gain {question.expected_info_gain.toFixed(4)}
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
                           focus-visible:border-accent focus-visible:outline-none
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
            className="border border-ink px-5 py-2.5 text-[13px] tracking-wide
                       transition-colors hover:bg-ink hover:text-paper
                       disabled:cursor-not-allowed disabled:opacity-30"
          >
            Continue
          </button>
        </form>
      )}
    </section>
  );
}
