"use client";

import { useCallback, useState } from "react";
import { AnswerLedger } from "@/components/AnswerLedger";
import { FlaggedFields } from "@/components/FlaggedFields";
import { Masthead } from "@/components/Masthead";
import { ProgressTrack } from "@/components/ProgressTrack";
import { QuestionCard } from "@/components/QuestionCard";
import { ResultPanel } from "@/components/ResultPanel";
import { RiskMeter } from "@/components/RiskMeter";
import { StartScreen } from "@/components/StartScreen";
import { api } from "@/lib/api";
import type { Result, Step } from "@/lib/types";

type DemoKind = "risky" | "clean";

/** A jump this large is worth interrupting the flow for; smaller ones sit in the ledger. */
const ALERT_POINTS = 8;

export default function Page() {
  const [step, setStep] = useState<Step | null>(null);
  const [result, setResult] = useState<Result | null>(null);
  const [scripted, setScripted] = useState<Record<string, unknown> | null>(null);
  const [effects, setEffects] = useState<Record<string, number>>({});
  const [alert, setAlert] = useState<
    { label: string; valueLabel: string; deltaPoints: number } | null
  >(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fail = (e: unknown) =>
    setError(e instanceof Error ? e.message : "Could not reach the API.");

  const finish = useCallback(async (sessionId: string) => {
    try {
      setResult(await api.result(sessionId));
    } catch (e) {
      fail(e);
    }
  }, []);

  const begin = useCallback(
    async (loader: () => Promise<Step>, script: Record<string, unknown> | null) => {
      setBusy(true);
      setError(null);
      setResult(null);
      setScripted(script);
      setEffects({});
      setAlert(null);
      try {
        const first = await loader();
        setStep(first);
        if (first.done) await finish(first.session_id);
      } catch (e) {
        fail(e);
        setStep(null);
      } finally {
        setBusy(false);
      }
    },
    [finish]
  );

  const startBlank = () => begin(() => api.start(), null);

  const startDemo = (which: DemoKind) =>
    begin(async () => {
      const [first, answers] = await Promise.all([
        api.startDemo(which),
        api.demoAnswers(which),
      ]);
      setScripted(answers);
      return first;
    }, null);

  /**
   * Each answer is scored by what it did to the estimate: the reading before it
   * against the reading after. The model reports its current probability at
   * every turn, so this costs no extra call, and it is what lets a harmful
   * field be flagged the moment it is entered instead of at the verdict.
   */
  async function answer(value: unknown) {
    if (!step?.question) return;
    const feature = step.question.feature;
    const before = Math.round(step.probability * 100);

    setBusy(true);
    try {
      const next = await api.answer(step.session_id, feature, value);
      const delta = Math.round(next.probability * 100) - before;
      const field = next.answered.find((a) => a.feature === feature);

      setEffects((prev) => ({ ...prev, [feature]: delta }));
      setAlert(
        delta >= ALERT_POINTS && field
          ? { label: field.label, valueLabel: field.value_label, deltaPoints: delta }
          : null
      );
      setStep(next);
      if (next.done) await finish(next.session_id);
    } catch (e) {
      fail(e);
    } finally {
      setBusy(false);
    }
  }

  function restart() {
    setStep(null);
    setResult(null);
    setScripted(null);
    setEffects({});
    setAlert(null);
    setError(null);
  }

  if (step === null) {
    return (
      <div className="flex min-h-full flex-col">
        <Masthead />
        <main className="mx-auto w-full max-w-[1180px] flex-1 px-6 sm:px-10">
          <StartScreen busy={busy} error={error} onStart={startBlank} onDemo={startDemo} />
        </main>
        <Footer />
      </div>
    );
  }

  const probability = result?.probability ?? step.probability;
  const band = result?.risk_band ?? step.risk_band;
  const settled = Boolean(result);

  return (
    <div className="flex min-h-full flex-col">
      <Masthead />

      {/*
        On a narrow screen the rail sits below the fold, so the reading is
        repeated here as a sticky strip. The model's current opinion is never
        more than a glance away, at any width — there is no reveal to wait for.
      */}
      <div className="rule-b sticky top-0 z-10 bg-paper/95 backdrop-blur-[2px] lg:hidden">
        <div className="mx-auto max-w-[1180px] px-6 py-3 sm:px-10">
          <RiskMeter
            compact
            probability={probability}
            band={band}
            sequence={step.n_answered}
            settled={settled}
          />
        </div>
      </div>

      <main className="mx-auto w-full max-w-[1180px] flex-1 px-6 sm:px-10">
        <div className="grid gap-x-14 gap-y-10 py-10 lg:grid-cols-[minmax(0,1fr)_320px] lg:py-12">
          {/* The active task. */}
          <div className="min-w-0 lg:col-start-1 lg:row-start-1">
            {result ? (
              <ResultPanel result={result} onRestart={restart} />
            ) : step.question ? (
              <QuestionCard
                key={step.question.feature}
                question={step.question}
                index={step.n_answered + 1}
                suggested={scripted?.[step.question.feature]}
                busy={busy}
                alert={alert}
                onAnswer={answer}
              />
            ) : (
              <p className="text-[13px] text-ink-muted">Working…</p>
            )}

            <AnswerLedger answered={step.answered} effects={effects} />

            {error && (
              <p className="mt-8 border-l-2 border-risk-high pl-4 text-[13px] text-ink-soft">
                {error}
              </p>
            )}
          </div>

          {/* Instrument rail. Second in source order, so on a narrow screen the
              question comes first and the supporting detail follows it; pinned to
              the right column above lg. The reading itself is not down here on
              mobile — it is in the sticky strip above, always in view. */}
          <aside className="lg:sticky lg:top-10 lg:col-start-2 lg:row-start-1 lg:self-start">
            <div className="hidden border border-rule bg-surface p-6 lg:block">
              <RiskMeter
                probability={probability}
                band={band}
                sequence={step.n_answered}
                settled={settled}
              />
            </div>

            <div className="border border-rule bg-surface p-6 lg:mt-5">
              <ProgressTrack step={step} settled={settled} />
            </div>

            {result && result.flagged_fields.length > 0 && (
              <div className="mt-5 border border-rule bg-surface p-6">
                <FlaggedFields fields={result.flagged_fields} />
              </div>
            )}
          </aside>
        </div>
      </main>

      <Footer />
    </div>
  );
}

function Footer() {
  return (
    <footer className="rule-t mt-12">
      <div className="mx-auto max-w-[1180px] px-6 py-6 text-[11.5px] leading-relaxed text-ink-muted sm:px-10">
        A second check for clinic staff, not a replacement for their judgement. Risk and
        reason code come from gradient-boosted trees; the plain-language reading is written
        by Qwen from those figures and cannot alter them.
      </div>
    </footer>
  );
}
