import type { AnsweredField } from "@/lib/types";

/** Below this, a movement is calibration noise and pointing at it would mislead. */
const NOTABLE_POINTS = 4;

/**
 * Everything answered so far, kept on screen rather than scrolled away as a
 * chat transcript — the user should never have to remember what they told it.
 *
 * Each row also carries what that answer did to the estimate, so a field that
 * hurt the claim is flagged the moment it is entered rather than saved for the
 * final explanation. That is the whole point of checking before submission:
 * a warning that arrives with the verdict has arrived too late to act on.
 */
export function AnswerLedger({
  answered,
  effects,
}: {
  answered: AnsweredField[];
  effects: Record<string, number>;
}) {
  if (answered.length === 0) return null;

  return (
    <section className="mt-12">
      <div className="flex items-baseline gap-3">
        <span className="eyebrow">Recorded so far</span>
        <span className="h-px flex-1 bg-rule" />
        <span className="tnum text-[11px] text-ink-muted">{answered.length} answered</span>
      </div>

      <dl className="mt-3">
        {answered.map((a) => {
          const delta = effects[a.feature];
          const notable = delta !== undefined && Math.abs(delta) >= NOTABLE_POINTS;
          const up = (delta ?? 0) > 0;
          return (
            <div
              key={a.feature}
              className="rule-b grid grid-cols-[minmax(0,13rem)_minmax(0,1fr)_auto] items-baseline
                         gap-x-4 py-2.5 text-[13px]"
            >
              <dt className="truncate text-ink-muted">{a.label}</dt>
              <dd className="min-w-0 text-ink">{a.value_label}</dd>
              <dd
                className="tnum shrink-0 text-right font-mono text-[11.5px]"
                style={{
                  color: notable
                    ? up
                      ? "var(--risk-high)"
                      : "var(--risk-low)"
                    : "var(--ink-muted)",
                }}
              >
                {delta === undefined
                  ? "—"
                  : notable
                    ? `${up ? "raised" : "lowered"} ${Math.abs(delta)} pts`
                    : "little effect"}
              </dd>
            </div>
          );
        })}
      </dl>

      <p className="mt-3 text-[11px] leading-relaxed text-ink-muted">
        Movement is measured against the estimate immediately before that answer.
      </p>
    </section>
  );
}
