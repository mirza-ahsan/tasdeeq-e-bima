"use client";

import { useState } from "react";
import { GLOSSARY, Term } from "@/components/Term";
import { points as fmtPoints } from "@/lib/format";
import type { Driver } from "@/lib/types";

interface Row {
  key: string;
  label: string;
  detail: string;
  from: number; // percentage points, running total before this row
  to: number;
  kind: "total" | "up" | "down";
}

/**
 * From a typical claim to this one. Exact Shapley values add up, so the bars chain
 * without a gap: the typical claim's estimate, plus every contribution, is this claim's
 * estimate. Polarity uses the diverging pair (raises / lowers) with a legend; every
 * value is printed beside its bar, and the rows themselves are the table view.
 */
export function Waterfall({
  typical,
  probability,
  drivers,
  other,
  patientLabel,
}: {
  typical: number;
  probability: number;
  drivers: Driver[];
  other: number;
  patientLabel: string;
}) {
  const [active, setActive] = useState<string | null>(null);

  const rows: Row[] = [];
  let run = typical * 100;
  rows.push({ key: "typical", label: "Typical claim", detail: `for a ${patientLabel}`, from: 0, to: run, kind: "total" });
  for (const d of drivers) {
    const next = run + d.contribution_points;
    rows.push({ key: d.feature, label: d.label, detail: d.value_label, from: run, to: next, kind: d.contribution_points > 0 ? "up" : "down" });
    run = next;
  }
  if (Math.abs(other) >= 0.05) {
    const next = run + other;
    rows.push({ key: "other", label: "Everything else", detail: "smaller effects combined", from: run, to: next, kind: other > 0 ? "up" : "down" });
    run = next;
  }
  const end = probability * 100;
  rows.push({ key: "claim", label: "This claim", detail: "the estimate shown above", from: 0, to: end, kind: "total" });

  // Bars use the track minus a fixed gutter, so the value printed after the longest bar
  // always fits, at any width.
  const max = Math.max(...rows.map((r) => Math.max(r.from, r.to)), 10);
  const at = (v: number) => `calc((100% - 3.25rem) * ${(v / max).toFixed(4)})`;
  const color = { total: "var(--ink-3)", up: "var(--up)", down: "var(--down)" };
  const focused = rows.find((r) => r.key === active);

  return (
    <div>
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <div>
          <span className="eyebrow">What moved the estimate</span>
          <p className="mt-1 text-[13px] text-ink-2">
            Percentage points, by{" "}
            <Term definition={GLOSSARY.shapley}>exact Shapley attribution</Term> of this claim.
          </p>
        </div>
        <div className="flex items-center gap-3 text-[11.5px] text-ink-3">
          <span className="inline-flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-[3px]" style={{ background: "var(--up)" }} />raises risk</span>
          <span className="inline-flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-[3px]" style={{ background: "var(--down)" }} />lowers risk</span>
        </div>
      </div>

      <ul className="mt-5 space-y-1">
        {rows.map((r) => {
          const lo = Math.min(r.from, r.to);
          const width = Math.abs(r.to - r.from);
          const value = r.kind === "total" ? `${Math.round(r.to)}%` : `${fmtPoints(r.to - r.from)}`;
          return (
            <li
              key={r.key}
              tabIndex={0}
              onMouseEnter={() => setActive(r.key)}
              onMouseLeave={() => setActive(null)}
              onFocus={() => setActive(r.key)}
              onBlur={() => setActive(null)}
              className={`grid grid-cols-[minmax(0,42%)_minmax(0,1fr)] items-center gap-4 rounded-xl px-2 py-1.5 outline-none transition
                          ${active === r.key ? "bg-surface-2" : ""} ${r.kind === "total" ? "font-medium" : ""}`}
            >
              <div className="min-w-0">
                <div className="truncate text-[13px] text-ink">{r.label}</div>
                <div className="truncate text-[11.5px] font-normal text-ink-3">{r.detail}</div>
              </div>
              <div className="relative h-6">
                <div className="absolute inset-y-0 left-0 w-px bg-[var(--axis)]" />
                <div
                  className="grow-x absolute top-1/2 h-3.5 -translate-y-1/2 rounded-[4px]"
                  style={{
                    left: at(lo),
                    width: `max(${at(width)}, 3px)`,
                    background: color[r.kind],
                  }}
                />
                <span
                  className="tnum absolute top-1/2 -translate-y-1/2 whitespace-nowrap pl-2 font-mono text-[11.5px] text-ink-2"
                  style={{ left: at(Math.max(r.from, r.to)) }}
                >
                  {value}
                </span>
              </div>
            </li>
          );
        })}
      </ul>

      <p className="mt-3 min-h-[1.25rem] text-[12px] text-ink-3" aria-live="polite">
        {focused && focused.kind !== "total"
          ? `${focused.label} (${focused.detail}): ${fmtPoints(focused.to - focused.from)} points, taking the estimate from ${Math.round(focused.from)}% to ${Math.round(focused.to)}%.`
          : "Hover or tab through a row for its running total."}
      </p>
    </div>
  );
}
