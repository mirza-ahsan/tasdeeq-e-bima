import type { AnsweredField } from "@/lib/types";

/** Answers so far, kept visible as a running record rather than a chat transcript. */
export function AnswerLedger({ answered }: { answered: AnsweredField[] }) {
  if (answered.length === 0) return null;
  return (
    <div className="mb-10">
      <span className="eyebrow">Recorded</span>
      <dl className="mt-3">
        {answered.map((a) => (
          <div
            key={a.feature}
            className="rule-b flex items-baseline gap-4 py-2 text-[13px]"
          >
            <dt className="w-56 shrink-0 text-ink-muted">{a.label}</dt>
            <dd className="text-ink-soft">{a.value_label}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}
