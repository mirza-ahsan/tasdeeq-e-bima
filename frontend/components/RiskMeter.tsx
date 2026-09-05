"use client";

import { useEffect, useRef, useState } from "react";
import type { RiskBand } from "@/lib/types";

/** Band boundaries mirror models/metadata.json risk_bands. */
const LOW_MAX = 0.2;
const HIGH_MIN = 0.4;

const BAND_COLOR: Record<RiskBand, string> = {
  low: "var(--risk-low)",
  medium: "var(--risk-med)",
  high: "var(--risk-high)",
};

const BAND_WORD: Record<RiskBand, string> = {
  low: "Low risk",
  medium: "Needs a look",
  high: "High risk",
};

/**
 * A measuring scale rather than a dashboard gauge. Ticks every 10%, the two band
 * boundaries marked as labelled regions, and a single marker on the reading.
 *
 * The change note is keyed on `sequence` (the answer count), not on the percentage.
 * Keying it on the percentage meant an answer that left the estimate untouched showed
 * the previous answer's delta forever, and React's development double-invoke made a
 * "no change" note appear before any question had been answered. Isotonic calibration
 * is a step function, so an answer genuinely can leave the reading where it was —
 * saying "no change" is honest, and better than a gauge that looks broken.
 */
export function RiskMeter({
  probability,
  band,
  settled,
  sequence,
}: {
  probability: number;
  band: RiskBand;
  settled: boolean;
  sequence: number;
}) {
  const pct = Math.round(probability * 100);
  const previous = useRef<{ seq: number; pct: number } | null>(null);
  const [delta, setDelta] = useState<number | null>(null);

  useEffect(() => {
    const prev = previous.current;
    if (prev && prev.seq !== sequence) setDelta(pct - prev.pct);
    if (!prev || prev.seq !== sequence) previous.current = { seq: sequence, pct };
  }, [sequence, pct]);

  return (
    <div>
      <div className="flex items-baseline justify-between">
        <span className="eyebrow">Rejection risk</span>
        {delta !== null && (
          <span className="tnum text-[11px] text-ink-muted">
            {delta === 0 ? (
              "no change"
            ) : (
              <>
                {delta > 0 ? "▲" : "▼"} {Math.abs(delta)} pts
              </>
            )}
          </span>
        )}
      </div>

      <div className="mt-3 flex items-baseline gap-2.5">
        <span
          className="font-display tnum leading-none"
          style={{ fontSize: "3.25rem", color: BAND_COLOR[band] }}
        >
          {pct}
        </span>
        <span className="font-display text-2xl leading-none text-ink-muted">%</span>
        <span
          className="ml-auto text-[11px] font-medium tracking-wide"
          style={{ color: BAND_COLOR[band] }}
        >
          {BAND_WORD[band]}
        </span>
      </div>

      <div className="mt-4">
        <div className="relative h-9">
          <div className="absolute inset-x-0 top-3 h-[3px] bg-surface-sunk" />
          <div
            className="absolute top-3 h-[3px] opacity-30"
            style={{ left: 0, width: `${LOW_MAX * 100}%`, background: "var(--risk-low)" }}
          />
          <div
            className="absolute top-3 h-[3px] opacity-30"
            style={{ left: `${HIGH_MIN * 100}%`, right: 0, background: "var(--risk-high)" }}
          />

          {Array.from({ length: 11 }, (_, i) => (
            <div
              key={i}
              className="absolute top-[22px] w-px bg-rule"
              style={{ left: `${i * 10}%`, height: i % 5 === 0 ? 7 : 4 }}
            />
          ))}

          <div
            className="absolute top-0 transition-[left] duration-300 ease-out"
            style={{ left: `${Math.min(Math.max(pct, 0), 100)}%` }}
          >
            <div
              className="-ml-[5px] h-[18px] w-[10px] rounded-[1px]"
              style={{ background: BAND_COLOR[band] }}
            />
          </div>
        </div>

        <div className="tnum mt-0.5 flex justify-between text-[10px] text-ink-muted">
          <span>0</span>
          <span>50</span>
          <span>100</span>
        </div>
      </div>

      {!settled && (
        <p className="mt-3 text-[12px] leading-relaxed text-ink-muted">
          Provisional — still gathering the details that matter most.
        </p>
      )}
    </div>
  );
}
