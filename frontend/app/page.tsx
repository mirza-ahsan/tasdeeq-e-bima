"use client";

import { useCallback, useState } from "react";
import { AnswerLedger } from "@/components/AnswerLedger";
import { FlaggedFields } from "@/components/FlaggedFields";
import { Masthead } from "@/components/Masthead";
import { QuestionCard } from "@/components/QuestionCard";
import { ResultPanel } from "@/components/ResultPanel";
import { RiskMeter } from "@/components/RiskMeter";
import { StartScreen } from "@/components/StartScreen";
import { api } from "@/lib/api";
import type { Result, Step } from "@/lib/types";

type DemoKind = "risky" | "clean";

export default function Page() {
  const [step, setStep] = useState<Step | null>(null);
  const [result, setResult] = useState<Result | null>(null);
  const [scripted, setScripted] = useState<Record<string, unknown> | null>(null);
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

  async function answer(value: unknown) {
    if (!step?.question) return;
    setBusy(true);
    try {
      const next = await api.answer(step.session_id, step.question.feature, value);
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
    setError(null);
  }

  const showWorkspace = step !== null;

  return (
    <div className="flex min-h-full flex-col">
      <Masthead />

      <main className="mx-auto w-full max-w-[1180px] flex-1 px-6 sm:px-10">
        {!showWorkspace ? (
          <StartScreen
            busy={busy}
            error={error}
            onStart={startBlank}
            onDemo={startDemo}
          />
        ) : (
          <div className="grid gap-x-14 gap-y-10 py-12 lg:grid-cols-[minmax(0,1fr)_320px]">
            {/* conversation */}
            <div className="min-w-0">
              <AnswerLedger answered={step.answered} />

              {result ? (
                <ResultPanel result={result} onRestart={restart} />
              ) : step.question ? (
                <QuestionCard
                  question={step.question}
                  index={step.n_answered + 1}
                  suggested={scripted?.[step.question.feature]}
                  busy={busy}
                  onAnswer={answer}
                />
              ) : (
                <p className="text-[13px] text-ink-muted">Working…</p>
              )}

              {error && (
                <p className="mt-8 border-l-2 border-risk-high pl-4 text-[13px] text-ink-soft">
                  {error}
                </p>
              )}
            </div>

            {/* instrument rail */}
            <aside className="lg:sticky lg:top-10 lg:self-start">
              <div className="border border-rule bg-surface p-6">
                <RiskMeter
                  probability={result?.probability ?? step.probability}
                  band={result?.risk_band ?? step.risk_band}
                  sequence={step.n_answered}
                  settled={Boolean(result)}
                />
              </div>

              {result && result.flagged_fields.length > 0 && (
                <div className="mt-5 border border-rule bg-surface p-6">
                  <FlaggedFields fields={result.flagged_fields} />
                </div>
              )}

              <div className="mt-5 px-1">
                <div className="flex items-baseline justify-between text-[11px] text-ink-muted">
                  <span className="eyebrow">Progress</span>
                  <span className="tnum">
                    {step.n_answered} of up to {step.max_questions}
                  </span>
                </div>
                <div className="mt-2 flex gap-1">
                  {Array.from({ length: step.max_questions }, (_, i) => (
                    <span
                      key={i}
                      className="h-[3px] flex-1"
                      style={{
                        background:
                          i < step.n_answered ? "var(--accent)" : "var(--surface-sunk)",
                      }}
                    />
                  ))}
                </div>
                {result?.stop_reason && (
                  <p className="mt-3 text-[11.5px] leading-relaxed text-ink-muted">
                    Stopped early: {result.stop_reason.replaceAll("_", " ")}.
                  </p>
                )}
              </div>
            </aside>
          </div>
        )}
      </main>

      <footer className="rule-t mt-12">
        <div className="mx-auto max-w-[1180px] px-6 py-6 text-[11.5px] leading-relaxed text-ink-muted sm:px-10">
          A second check for clinic staff, not a replacement for their judgement. Risk and
          reason code come from gradient-boosted trees; the plain-language reading is written
          by Qwen from those figures and cannot alter them.
        </div>
      </footer>
    </div>
  );
}
