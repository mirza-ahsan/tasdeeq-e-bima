"use client";

import { useEffect, useState } from "react";
import {
  IconAlert, IconArrowRight, IconChart, IconCheckCircle, IconClock, IconFile, IconLayers,
  IconPlay, IconShield, IconSparkle,
} from "@/components/icons";
import { GLOSSARY, Term } from "@/components/Term";
import { pct } from "@/lib/format";
import type { DemoKind, Meta, RiskBand } from "@/lib/types";

interface Preview {
  claim_label: string;
  patient_label: string;
  path: { label: string; value_label: string; probability: number }[];
  risk_band: RiskBand;
  probability: number;
  top_driver: { label: string; value_label: string; contribution_points: number } | null;
  carc: { code: string; plain_english: string } | null;
}

const BAND_TONE: Record<RiskBand, { mark: string; ink: string; wash: string; word: string }> = {
  low: { mark: "var(--good)", ink: "var(--good-ink)", wash: "var(--good-wash)", word: "Low risk" },
  medium: { mark: "var(--warn)", ink: "var(--warn-ink)", wash: "var(--warn-wash)", word: "Not fully cleared" },
  high: { mark: "var(--crit)", ink: "var(--crit-ink)", wash: "var(--crit-wash)", word: "High risk" },
};

/**
 * The landing page's specimen: the real risky demo claim, replayed from the API's own
 * computation, so every number on it is one the model actually produced.
 */
function Specimen() {
  const [preview, setPreview] = useState<Preview | null>(null);
  const [shown, setShown] = useState(0);

  useEffect(() => {
    fetch("/api/demo/risky/preview", { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : null))
      .then(setPreview)
      .catch(() => setPreview(null));
  }, []);

  // Reveal one step at a time, as the loop did.
  useEffect(() => {
    if (!preview) return;
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduce) {
      // The whole path at once, without the replay.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setShown(preview.path.length);
      return;
    }
    const timers = preview.path.map((_, i) => setTimeout(() => setShown(i + 1), 500 + i * 900));
    return () => timers.forEach(clearTimeout);
  }, [preview]);

  const visible = preview?.path.slice(0, Math.max(shown, 1)) ?? [];
  const current = visible.at(-1)?.probability ?? 0;
  const finished = preview !== null && shown >= preview.path.length;
  // Neutral until the path is complete: the band is the API's verdict, never re-derived here.
  const band: RiskBand | null = finished ? preview!.risk_band : null;
  const tone = band
    ? BAND_TONE[band]
    : { mark: "var(--ink-3)", ink: "var(--ink-2)", wash: "var(--surface-2)", word: "Provisional" };

  return (
    <div className="relative">
      <div className="dot-grid pointer-events-none absolute -inset-10 opacity-60" aria-hidden />
      <div className="card relative overflow-hidden p-0 shadow-float">
        <div className="flex items-center justify-between border-b border-line px-5 py-3.5">
          <div className="flex items-center gap-2 text-[12px] text-ink-3">
            <span className="h-2 w-2 rounded-full bg-brand live-dot" />
            Live check · example claim
          </div>
          <span className="rounded-full bg-surface-2 px-2.5 py-1 text-[11px] text-ink-2">
            {preview?.patient_label ?? "—"}
          </span>
        </div>

        <div className="px-5 pb-5 pt-4">
          <p className="text-[13px] text-ink-2">{preview?.claim_label ?? "Loading the example…"}</p>

          <div className="mt-4 flex items-end justify-between gap-4">
            <div>
              <div className="eyebrow">Rejection risk</div>
              <div className="mt-1 flex items-baseline gap-1">
                <span className="text-[56px] font-semibold leading-none tracking-[-0.04em] transition-all duration-500">
                  {preview ? Math.round(current * 100) : "–"}
                </span>
                <span className="text-[22px] font-medium text-ink-3">%</span>
              </div>
            </div>
            <span
              className="mb-1 inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[12px] font-medium transition-colors"
              style={{ background: tone.wash, color: tone.ink }}
            >
              {band === "high" ? <IconAlert size={14} /> : band === "low" ? <IconCheckCircle size={14} /> : <IconClock size={14} />}
              {tone.word}
            </span>
          </div>

          {/* meter */}
          <div className="relative mt-4 h-2 overflow-hidden rounded-full bg-surface-2">
            <div
              className="h-full rounded-full transition-[width,background-color] duration-700 ease-out"
              style={{ width: `${Math.max(current * 100, 2)}%`, background: tone.mark }}
            />
          </div>

          <ol className="mt-5 space-y-2">
            {visible.slice(1).map((p, i) => {
              const before = visible[i].probability;
              const delta = Math.round((p.probability - before) * 100);
              return (
                <li key={p.label} className="rise flex items-center gap-3 rounded-xl bg-surface-2 px-3.5 py-2.5 text-[13px]">
                  <span className="grid h-5 w-5 shrink-0 place-items-center rounded-full bg-surface text-[10px] font-semibold text-ink-2">
                    {i + 1}
                  </span>
                  <span className="min-w-0 flex-1 truncate">
                    <span className="text-ink-3">{p.label}: </span>
                    <span className="text-ink">{p.value_label}</span>
                  </span>
                  <span className="tnum shrink-0 font-mono text-[12px] text-ink-2">
                    {delta === 0 ? "no change" : `${delta > 0 ? "+" : "−"}${Math.abs(delta)} pts`}
                  </span>
                </li>
              );
            })}
          </ol>

          {finished && preview?.carc && (
            <div className="rise mt-4 flex items-start gap-3 rounded-xl border border-line px-3.5 py-3">
              <span className="shrink-0 whitespace-nowrap rounded-md bg-ink px-1.5 py-0.5 font-mono text-[11px] text-bg">
                CARC {preview.carc.code}
              </span>
              <span className="text-[12.5px] leading-snug text-ink-2">{preview.carc.plain_english}</span>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function Stat({ value, label, detail }: { value: string; label: string; detail: string }) {
  return (
    <div className="card p-5">
      <div className="text-[30px] font-semibold leading-none tracking-[-0.03em]">{value}</div>
      <div className="mt-2.5 text-[13px] font-medium text-ink">{label}</div>
      <div className="mt-1 text-[12px] leading-relaxed text-ink-3">{detail}</div>
    </div>
  );
}

const CHECKS = [
  { icon: IconShield, title: "Pre-authorisation", body: "Knows which procedures and amounts need insurer approval, and asks about it first when they do." },
  { icon: IconClock, title: "Filing deadline", body: "Each insurer's window, measured against how long ago the treatment was." },
  { icon: IconChart, title: "Amount vs typical", body: "Flags a bill several times what that procedure usually costs." },
  { icon: IconLayers, title: "Plan coverage", body: "Procedures a plan tier excludes, or refuses as not medically necessary." },
  { icon: IconFile, title: "Diagnosis fits procedure", body: "Whether the recorded diagnosis justifies what was billed." },
  { icon: IconCheckCircle, title: "Procedure fits patient", body: "Catches procedures that cannot apply to the patient's age or sex." },
];

export function Landing({
  meta,
  busy,
  error,
  onStart,
  onDemo,
}: {
  meta: Meta | null;
  busy: boolean;
  error: string | null;
  onStart: () => void;
  onDemo: (which: DemoKind) => void;
}) {
  const v = meta?.verdicts;
  return (
    <div className="relative overflow-x-clip">
      <div className="hero-glow pointer-events-none absolute inset-x-0 top-0 h-[640px]" aria-hidden />

      <section className="relative mx-auto grid max-w-[1200px] items-center gap-14 px-4 pb-16 pt-14 sm:px-8 lg:grid-cols-[1.05fr_0.95fr] lg:pt-20">
        <div>
          <span className="rise inline-flex items-center gap-2 rounded-full border border-line bg-surface px-3 py-1.5 text-[12px] text-ink-2 shadow-[var(--shadow-sm)]">
            <IconSparkle size={14} className="text-brand" />
            A pre-submission check for Pakistani private clinics
          </span>

          <h1 className="rise rise-1 mt-6 text-[44px] font-semibold leading-[1.04] tracking-[-0.035em] sm:text-[60px]">
            Catch the rejection{" "}
            <span className="font-display text-[1.08em] font-normal italic tracking-[-0.01em] text-brand">
              before
            </span>{" "}
            you submit the claim.
          </h1>

          <p className="rise rise-2 mt-6 max-w-[540px] text-[16.5px] leading-[1.65] text-ink-2">
            Most claim rejections aren&apos;t clinical. They&apos;re paperwork: a missing
            pre-authorisation, a filing deadline passed, documents left out. Enter the bill,
            answer the few questions that matter for it, and see how likely it is to bounce,
            which field is the problem, and what to fix.
          </p>

          <div className="rise rise-3 mt-9 flex flex-wrap items-center gap-3">
            <button
              type="button"
              onClick={onStart}
              disabled={busy}
              className="group inline-flex h-12 items-center gap-2 rounded-full bg-brand px-6 text-[14.5px] font-medium
                         text-brand-ink shadow-[var(--shadow-md)] transition hover:bg-brand-hover disabled:opacity-50"
            >
              Check a claim
              <IconArrowRight size={16} className="transition group-hover:translate-x-0.5" />
            </button>
            <button
              type="button"
              onClick={() => onDemo("risky")}
              disabled={busy}
              className="inline-flex h-12 items-center gap-2 rounded-full border border-line-strong bg-surface px-5 text-[14px]
                         font-medium text-ink transition hover:border-ink-3 disabled:opacity-50"
            >
              <IconPlay size={14} className="text-crit" />
              Risky example
            </button>
            <button
              type="button"
              onClick={() => onDemo("clean")}
              disabled={busy}
              className="inline-flex h-12 items-center gap-2 rounded-full border border-line-strong bg-surface px-5 text-[14px]
                         font-medium text-ink transition hover:border-ink-3 disabled:opacity-50"
            >
              <IconPlay size={14} className="text-good" />
              Clean example
            </button>
          </div>

          {error && (
            <div role="alert" className="rise mt-6 flex max-w-[540px] items-start gap-3 rounded-2xl border border-crit/30 bg-crit-wash px-4 py-3 text-[13.5px] text-crit-ink">
              <IconAlert size={18} className="mt-0.5 shrink-0" />
              <span>
                {error}
                <span className="mt-1 block text-[12.5px] opacity-80">
                  Locally: <code className="font-mono">uv run uvicorn backend.main:app --port 8000</code>
                </span>
              </span>
            </div>
          )}

          <p className="rise rise-4 mt-8 flex items-center gap-2 text-[12.5px] text-ink-3">
            <IconShield size={15} />
            The model decides; the language model only words it, and cannot change a number.
          </p>
        </div>

        <div className="rise rise-2">
          <Specimen />
        </div>
      </section>

      {/* What the numbers are, from the held-out test split */}
      <section className="mx-auto max-w-[1200px] px-4 sm:px-8">
        <div className="flex items-baseline justify-between gap-4">
          <h2 className="text-[13px] font-medium text-ink-2">On claims the model never saw</h2>
          <span className="text-[12px] text-ink-3">{v ? `${v.claims} held-out test claims` : ""}</span>
        </div>
        <div className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <Stat
            value={meta ? meta.test_auc.toFixed(2) : "—"}
            label="ROC AUC"
            detail={`Ranks rejected claims above accepted ones. 0.5 is guessing.`}
          />
          <Stat
            value={v ? pct(1 - (v.bands.low.rejection_rate ?? 0)) : "—"}
            label="of cleared claims were accepted"
            detail={v ? `${pct(v.bands.low.share)} of claims were cleared as low risk.` : ""}
          />
          <Stat
            value={v ? pct(v.rejections_flagged) : "—"}
            label="of rejections flagged in advance"
            detail="Marked high risk or not cleared before submission."
          />
          <Stat
            value={v ? ((v.mean_questions_approved + v.mean_questions_rejected) / 2).toFixed(1) : "—"}
            label="questions on average"
            detail={v ? `Never fewer than ${meta?.min_questions}; ${v.distinct_question_orders} different question orders.` : ""}
          />
        </div>
      </section>

      {/* How it works */}
      <section className="mx-auto max-w-[1200px] px-4 pt-20 sm:px-8">
        <div className="max-w-[620px]">
          <span className="eyebrow">How it works</span>
          <h2 className="mt-3 text-[32px] font-semibold leading-[1.15] tracking-[-0.025em]">
            Three steps, about a minute.
          </h2>
        </div>
        <div className="mt-8 grid gap-4 md:grid-cols-3">
          {[
            ["01", "Enter the bill", "Patient age and sex, the procedure and the amount — everything printed on the claim."],
            ["02", "Answer what matters", <>Each question is the one with the most{" "}<Term definition={GLOSSARY.infoGain}>information gain</Term>{" "}for this claim. It stops as soon as the answer is settled.</>],
            ["03", "Fix it before it bounces", <>The risk, the fields driving it, the likely{" "}<Term definition={GLOSSARY.carc}>CARC</Term>{" "}reason code and the one thing to do about it.</>],
          ].map(([n, title, body]) => (
            <div key={n as string} className="card p-6">
              <span className="font-mono text-[12px] text-brand">{n}</span>
              <h3 className="mt-3 text-[17px] font-semibold tracking-[-0.01em]">{title}</h3>
              <p className="mt-2 text-[14px] leading-relaxed text-ink-2">{body}</p>
            </div>
          ))}
        </div>
      </section>

      {/* The rules it knows */}
      <section className="mx-auto max-w-[1200px] px-4 pt-20 sm:px-8">
        <div className="max-w-[640px]">
          <span className="eyebrow">What it knows that the clerk may not</span>
          <h2 className="mt-3 text-[32px] font-semibold leading-[1.15] tracking-[-0.025em]">
            Six insurer rules, checked on every claim.
          </h2>
          <p className="mt-3 text-[15px] leading-relaxed text-ink-2">
            Being told a missing pre-authorisation is a problem tells a clerk nothing new.
            Knowing that this procedure needed one, or that this insurer&apos;s window closes at
            day 30, is the part worth checking.
          </p>
        </div>
        <div className="mt-8 grid gap-px overflow-hidden rounded-[18px] border border-line bg-[var(--border)] sm:grid-cols-2 lg:grid-cols-3">
          {CHECKS.map(({ icon: Ic, title, body }) => (
            <div key={title} className="bg-surface p-6">
              <span className="grid h-9 w-9 place-items-center rounded-xl bg-brand-wash text-brand">
                <Ic size={18} />
              </span>
              <h3 className="mt-4 text-[15px] font-semibold">{title}</h3>
              <p className="mt-1.5 text-[13.5px] leading-relaxed text-ink-2">{body}</p>
            </div>
          ))}
        </div>
      </section>

      {/* Honesty */}
      <section className="mx-auto max-w-[1200px] px-4 pb-24 pt-20 sm:px-8">
        <div className="card grid gap-8 p-8 md:grid-cols-[1fr_1.2fr] md:p-10">
          <div>
            <span className="eyebrow">About the data</span>
            <h2 className="mt-3 font-display text-[34px] leading-[1.1]">
              The records are simulated. <span className="italic text-ink-3">The rules are real.</span>
            </h2>
          </div>
          <div className="space-y-3 text-[14.5px] leading-relaxed text-ink-2">
            <p>
              No public dataset of real clinic claims and their outcomes exists — insurers
              don&apos;t publish one. So the claims come from <strong className="font-medium text-ink">Synthea</strong>, MITRE&apos;s open clinical
              simulator, re-priced for Pakistani clinics, and the reasons from the real{" "}
              <strong className="font-medium text-ink">X12 CARC</strong> standard.
            </p>
            <p>
              Only the labelling rules are ours, and they&apos;re written down. That is also why the
              model isn&apos;t near-perfect: on data we labelled, a perfect score would only prove it
              had memorised our rules.
            </p>
            <p className="text-[13px] text-ink-3">
              {meta ? `${meta.n_training_claims.toLocaleString()} synthetic claims · ` : ""}LightGBM, calibrated · Shapley attribution · Qwen on Alibaba Cloud DashScope
            </p>
          </div>
        </div>
      </section>
    </div>
  );
}
