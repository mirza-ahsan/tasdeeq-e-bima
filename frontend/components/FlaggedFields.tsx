import type { FlaggedField } from "@/lib/types";

/**
 * Per-claim SHAP attribution. Bars are scaled against the largest contribution in the
 * set, so the comparison is between fields on this claim rather than an absolute scale.
 */
export function FlaggedFields({ fields }: { fields: FlaggedField[] }) {
  if (fields.length === 0) return null;
  const widest = Math.max(...fields.map((f) => Math.abs(f.contribution_points)), 1);

  return (
    <div>
      <span className="eyebrow">What is driving this</span>
      <ul className="mt-3 space-y-3">
        {fields.map((f) => {
          const up = f.effect === "increases_risk";
          const width = (Math.abs(f.contribution_points) / widest) * 100;
          return (
            <li key={f.feature}>
              <div className="flex items-baseline justify-between gap-3 text-[13px]">
                <span className="text-ink-soft">{f.label}</span>
                <span
                  className="tnum shrink-0 font-mono text-[12px]"
                  style={{ color: up ? "var(--risk-high)" : "var(--risk-low)" }}
                >
                  {f.contribution_points > 0 ? "+" : ""}
                  {f.contribution_points.toFixed(1)}
                </span>
              </div>
              <div className="mt-1 flex items-center gap-2">
                <div className="h-[3px] flex-1 bg-surface-sunk">
                  <div
                    className="h-full transition-[width] duration-300"
                    style={{
                      width: `${width}%`,
                      background: up ? "var(--risk-high)" : "var(--risk-low)",
                    }}
                  />
                </div>
              </div>
              <p className="mt-1 text-[12px] text-ink-muted">{f.value_label}</p>
            </li>
          );
        })}
      </ul>
      <p className="mt-4 text-[11px] leading-relaxed text-ink-muted">
        Percentage points, from SHAP attribution on this specific claim.
      </p>
    </div>
  );
}
