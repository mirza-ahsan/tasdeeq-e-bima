"use client";

import { useId, useState } from "react";
import type { Bands } from "@/lib/types";

export interface TrajectoryPoint {
  tick: string;      // axis label: "Bill", "Q1", "Q2" … or "Edit" for a corrected answer
  label: string;     // what happened: "Bill entered", "Pre-authorisation", "Changed Plan tier"
  detail: string;    // the value: "No, not obtained"
  probability: number;
}

const W = 300;
const H = 126;
const PAD = { top: 24, right: 34, bottom: 22, left: 6 };
const LABEL_GAP = 11;   // minimum vertical distance between the two band labels

/**
 * How the estimate moved, answer by answer. One series, so no legend: the title names
 * it. The two band boundaries are solid hairlines labelled at the right; every point has
 * a hover/focus readout, and the answer ledger beside it is the table view. A corrected
 * answer is plotted as "Edit", not as another question.
 */
export function Trajectory({ points, bands }: { points: TrajectoryPoint[]; bands: Bands }) {
  const id = useId();
  const [active, setActive] = useState<number | null>(null);
  if (points.length === 0) return null;

  const top = Math.max(0.5, Math.max(...points.map((p) => p.probability)) * 1.08, bands.high_min + 0.08);
  const innerW = W - PAD.left - PAD.right;
  const innerH = H - PAD.top - PAD.bottom;
  const x = (i: number) =>
    PAD.left + (points.length === 1 ? innerW / 2 : (i / (points.length - 1)) * innerW);
  const y = (p: number) => PAD.top + innerH - (p / top) * innerH;

  // Keep the two boundary labels apart when the scale squeezes them together.
  const yLow = y(bands.low_max);
  const yHigh = y(bands.high_min);
  const squeeze = Math.max(0, LABEL_GAP - (yLow - yHigh)) / 2;
  const labelY = { low: yLow + squeeze, high: yHigh - squeeze };

  const path = points.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p.probability).toFixed(1)}`).join(" ");
  const area = `${path} L${x(points.length - 1).toFixed(1)},${PAD.top + innerH} L${x(0).toFixed(1)},${PAD.top + innerH} Z`;
  const last = points.at(-1)!;
  const shown = active ?? points.length - 1;
  const pct = (p: number) => `${Math.round(p * 100)}%`;

  return (
    <div>
      <div className="flex items-baseline justify-between gap-2">
        <span className="eyebrow">How the estimate moved</span>
        <span className="tnum truncate text-[11.5px] text-ink-3">
          {points[shown].label}: <span className="font-medium text-ink">{pct(points[shown].probability)}</span>
        </span>
      </div>

      <svg viewBox={`0 0 ${W} ${H}`} className="mt-2 block h-auto w-full overflow-visible" role="group"
           aria-labelledby={`${id}-t`}>
        <title id={`${id}-t`}>Rejection risk after each step</title>

        {/* band boundaries: solid recessive hairlines, labelled at the right */}
        <line x1={PAD.left} x2={W - PAD.right} y1={yLow} y2={yLow} stroke="var(--grid)" strokeWidth={1} />
        <line x1={PAD.left} x2={W - PAD.right} y1={yHigh} y2={yHigh} stroke="var(--grid)" strokeWidth={1} />
        <text x={W - PAD.right + 6} y={labelY.low + 3.5} fontSize={10} fill="var(--ink-3)" className="tnum">
          {pct(bands.low_max)}
        </text>
        <text x={W - PAD.right + 6} y={labelY.high + 3.5} fontSize={10} fill="var(--ink-3)" className="tnum">
          {pct(bands.high_min)}
        </text>
        <line x1={PAD.left} x2={W - PAD.right} y1={PAD.top + innerH} y2={PAD.top + innerH} stroke="var(--axis)" strokeWidth={1} />

        <path d={area} fill="var(--brand)" opacity={0.08} />
        <path d={path} fill="none" stroke="var(--brand)" strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />

        {points.map((p, i) => (
          <g key={i}>
            {i === active && (
              <circle cx={x(i)} cy={y(p.probability)} r={8.5} fill="none" stroke="var(--brand)" strokeWidth={1.5} />
            )}
            <circle
              cx={x(i)}
              cy={y(p.probability)}
              r={i === shown ? 5 : 4}
              fill={i === points.length - 1 ? "var(--brand)" : "var(--surface)"}
              stroke={i === points.length - 1 ? "var(--surface)" : "var(--brand)"}
              strokeWidth={2}
            />
            {/* a hit area much larger than the mark; focus shows the ring above */}
            <rect
              x={x(i) - 14}
              y={PAD.top - 6}
              width={28}
              height={innerH + 12}
              fill="transparent"
              tabIndex={0}
              role="img"
              aria-label={`${p.label}${p.detail ? `: ${p.detail}` : ""} — ${pct(p.probability)}`}
              onMouseEnter={() => setActive(i)}
              onMouseLeave={() => setActive(null)}
              onFocus={() => setActive(i)}
              onBlur={() => setActive(null)}
              style={{ outline: "none" }}
            />
            <text x={x(i)} y={H - 6} fontSize={10} textAnchor="middle" fill="var(--ink-3)" className="tnum">
              {p.tick}
            </text>
          </g>
        ))}

        <text x={x(points.length - 1)} y={y(last.probability) - 10} fontSize={11} fontWeight={600}
              textAnchor="middle" fill="var(--ink)" className="tnum">
          {pct(last.probability)}
        </text>
      </svg>

      <p className="mt-1.5 min-h-[1.25rem] truncate text-[12px] text-ink-3">{points[shown].detail}</p>
    </div>
  );
}
