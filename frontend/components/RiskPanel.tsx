"use client";

import { IconAlert, IconCheckCircle, IconClock } from "@/components/icons";
import { GLOSSARY, Term } from "@/components/Term";
import { BAND_LABEL, pct, relative } from "@/lib/format";
import type { Bands, RiskBand } from "@/lib/types";

export const BAND_TONE: Record<RiskBand, { mark: string; ink: string; wash: string }> = {
  low: { mark: "var(--good)", ink: "var(--good-ink)", wash: "var(--good-wash)" },
  medium: { mark: "var(--warn)", ink: "var(--warn-ink)", wash: "var(--warn-wash)" },
  high: { mark: "var(--crit)", ink: "var(--crit-ink)", wash: "var(--crit-wash)" },
};

export function BandBadge({
  band,
  settled = true,
  early = false,
}: {
  band: RiskBand;
  settled?: boolean;
  early?: boolean;
}) {
  if (early) {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-full bg-surface-2 px-2.5 py-1 text-[12px] font-medium text-ink-2">
        <IconClock size={14} /> Early estimate
      </span>
    );
  }
  const tone = BAND_TONE[band];
  const Icon = band === "high" ? IconAlert : band === "low" ? IconCheckCircle : IconClock;
  return (
    <span
      className="inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[12px] font-medium"
      style={{ background: tone.wash, color: tone.ink }}
    >
      <Icon size={14} />
      {BAND_LABEL[band]}
      {!settled && <span className="font-normal opacity-75">· so far</span>}
    </span>
  );
}

/**
 * The model's current reading. A hero figure and a meter: the track is split at the
 * band boundaries the API reports, so the zones on screen are the ones the verdict uses.
 * Low ends at low_max inclusive, high starts at high_min inclusive.
 *
 * Before the minimum number of answers the band is not shown at all. A band word is a
 * verdict, and no claim gets one on fewer answers: a minor surgery whose pre-authorisation
 * has not been asked about yet must not sit under a green "Low risk", however typical
 * its bill.
 */
export function RiskPanel({
  probability,
  band,
  bands,
  typical,
  base,
  settled,
  delta,
  early = false,
  compact = false,
}: {
  probability: number;
  band: RiskBand;
  bands: Bands;
  typical: number;
  base: number;
  settled: boolean;
  delta: number | null;
  early?: boolean;
  compact?: boolean;
}) {
  const p = Math.min(Math.max(probability, 0), 1);
  const tone = early ? { mark: "var(--ink-3)", ink: "var(--ink-2)", wash: "var(--surface-2)" } : BAND_TONE[band];
  const lo = bands.low_max * 100;
  const hi = bands.high_min * 100;
  const change =
    delta === null ? null : Math.round(delta) === 0 ? "no change" : `${delta > 0 ? "▲" : "▼"} ${Math.abs(Math.round(delta))} pts`;

  const meter = (
    <div className="relative" aria-hidden>
      <div className="relative flex h-2.5 gap-[2px] overflow-hidden rounded-full">
        <span className="h-full rounded-l-full" style={{ width: `${lo}%`, background: "var(--good-wash)" }} />
        <span className="h-full" style={{ width: `${hi - lo}%`, background: "var(--warn-wash)" }} />
        <span className="h-full flex-1 rounded-r-full" style={{ background: "var(--crit-wash)" }} />
        <span
          className="absolute inset-y-0 left-0 rounded-full transition-[width,background-color] duration-500 ease-out"
          style={{ width: `${Math.max(p * 100, 1.5)}%`, background: tone.mark }}
        />
      </div>
      {/* the typical claim for this patient, for reference */}
      <span
        className="absolute -top-1 h-[18px] w-[2px] rounded-full bg-ink-3"
        style={{ left: `calc(${Math.min(typical * 100, 99)}% - 1px)` }}
      />
    </div>
  );

  if (compact) {
    return (
      <div className="flex items-center gap-4">
        <div className="flex items-baseline gap-0.5">
          <span className="text-[30px] font-semibold leading-none tracking-[-0.03em]">{Math.round(p * 100)}</span>
          <span className="text-[15px] text-ink-3">%</span>
        </div>
        <div className="min-w-0 flex-1">
          <div className="mb-2 flex items-center justify-between gap-2">
            <BandBadge band={band} settled={settled} early={early} />
            <span className="tnum text-[11.5px] text-ink-3">{change ?? (settled ? "final" : "provisional")}</span>
          </div>
          {meter}
        </div>
      </div>
    );
  }

  return (
    <div>
      <div className="flex items-center justify-between">
        <span className="eyebrow">Rejection risk</span>
        {change && (
          <span key={change} className="fade tnum rounded-full bg-surface-2 px-2 py-0.5 text-[11.5px] text-ink-2">
            {change}
          </span>
        )}
      </div>

      <div className="mt-3 flex items-end justify-between gap-3">
        <div className="flex items-baseline gap-1">
          <span className="text-[64px] font-semibold leading-[0.9] tracking-[-0.045em] transition-all">
            {Math.round(p * 100)}
          </span>
          <span className="text-[24px] font-medium text-ink-3">%</span>
        </div>
        <BandBadge band={band} settled={settled} early={early} />
      </div>

      <p className="mt-2 text-[12.5px] text-ink-3">
        {relative(p, base)} ({pct(base)} on average)
      </p>

      <div className="mt-5">{meter}</div>

      <div className="tnum relative mt-2 h-4 text-[10.5px] text-ink-3">
        <span className="absolute left-0">0</span>
        <span className="absolute -translate-x-1/2" style={{ left: `${lo}%` }}>{Math.round(lo)}</span>
        <span className="absolute -translate-x-1/2" style={{ left: `${hi}%` }}>{Math.round(hi)}</span>
        <span className="absolute right-0">100</span>
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1.5 text-[11.5px] text-ink-3">
        <span className="inline-flex items-center gap-1.5">
          <span className="h-2 w-2 rounded-full" style={{ background: "var(--good)" }} /> Low up to {Math.round(lo)}%
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span className="h-2 w-2 rounded-full" style={{ background: "var(--crit)" }} /> High from {Math.round(hi)}%
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span className="h-3 w-[2px] rounded-full bg-ink-3" />
          <Term definition={GLOSSARY.typical} align="right">Typical claim</Term> {pct(typical)}
        </span>
      </div>
    </div>
  );
}
