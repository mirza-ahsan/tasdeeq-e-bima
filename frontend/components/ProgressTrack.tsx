import type { Step } from "@/lib/types";

const STOP_REASON_TEXT: Record<string, string> = {
  confident_low_risk: "Stopped early — the estimate settled in the low band.",
  confident_high_risk: "Stopped early — the estimate settled in the high band.",
  no_informative_questions_left: "Stopped early — no remaining question would move the estimate.",
  all_questions_answered: "Every answerable field has been filled in.",
  question_limit_reached: "Reached the eight-question ceiling.",
};

/**
 * Where the user is in the flow, and what has to happen before there is a
 * verdict. The old version showed "n of up to 8", which was true but useless:
 * the engine almost never asks eight, and it cannot stop before three, so the
 * reading was pessimistic at both ends.
 *
 * This shows the floor as a marked position on the track, and names the
 * condition the run is waiting on — so the count is never a surprise.
 */
export function ProgressTrack({ step, settled }: { step: Step; settled: boolean }) {
  const { n_answered, min_questions, max_questions, stop_reason } = step;
  const belowFloor = n_answered < min_questions;

  return (
    <div>
      <div className="flex items-baseline justify-between gap-3">
        <span className="eyebrow">Signals gathered</span>
        <span className="tnum text-[11px] text-ink-muted">
          {n_answered} of {max_questions} max
        </span>
      </div>

      <div className="mt-2.5 flex gap-1" aria-hidden>
        {Array.from({ length: max_questions }, (_, i) => (
          <span
            key={i}
            className="relative h-[3px] flex-1"
            style={{
              background: i < n_answered ? "var(--ink-soft)" : "var(--surface-sunk)",
            }}
          >
            {/* the minimum-answers floor, marked on the track itself */}
            {i === min_questions - 1 && (
              <span
                className="absolute -top-[3px] right-0 h-[9px] w-px"
                style={{ background: "var(--rule-strong)" }}
              />
            )}
          </span>
        ))}
      </div>

      <p className="mt-2.5 text-[11.5px] leading-relaxed text-ink-muted">
        {settled && stop_reason ? (
          STOP_REASON_TEXT[stop_reason] ?? stop_reason.replaceAll("_", " ")
        ) : belowFloor ? (
          <>
            {min_questions - n_answered} more{" "}
            {min_questions - n_answered === 1 ? "answer" : "answers"} before a verdict.
            No claim is judged on less.
          </>
        ) : (
          <>
            Past the {min_questions}-answer minimum. It will stop as soon as another
            question stops changing the estimate.
          </>
        )}
      </p>
    </div>
  );
}
