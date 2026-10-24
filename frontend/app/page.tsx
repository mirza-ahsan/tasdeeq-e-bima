"use client";

import { useCallback, useEffect, useState } from "react";
import { Checks } from "@/components/Checks";
import { IconAlert, IconFile, IconRestart } from "@/components/icons";
import { Intake } from "@/components/Intake";
import { Landing } from "@/components/Landing";
import { Ledger } from "@/components/Ledger";
import { QuestionCard } from "@/components/QuestionCard";
import { ResultView } from "@/components/ResultView";
import { RiskPanel } from "@/components/RiskPanel";
import { TopBar } from "@/components/TopBar";
import { Trajectory, type TrajectoryPoint } from "@/components/Trajectory";
import { ApiError, api } from "@/lib/api";
import { pkr } from "@/lib/format";
import { SAMPLE_SETS, loadSample, sampleUrl } from "@/lib/samples";
import type { DemoKind, Intake as IntakeValues, Meta, Question, Result, Step } from "@/lib/types";

type View = "landing" | "intake" | "check";

const SAMPLES = SAMPLE_SETS.map((set) => ({
  label: set.label,
  previews: set.files.map(sampleUrl),
  load: () => loadSample(set),
}));

/** Pause between scripted answers, long enough to narrate over. */
const AUTOPLAY_MS = 1600;
const AUTOPLAY_AFTER_ALERT_MS = 2800;

function billSummary(step: Step) {
  return step.claim.map((f) => f.value_label).join(" · ");
}

/** Answers read from the documents, rather than asked. */
const fromDocuments = (step: Step) => step.answered.filter((a) => a.source === "document").length;
/** Questions actually put to the clerk, including those left as "not sure" (from the API:
 * an edited document fact becomes the clerk's answer, but was never asked). */
const asked = (step: Step) => step.n_asked;
/** Answers the clerk gave: the minimum a verdict needs counts only these. */
const clerkAnswers = (step: Step) => step.n_asked - (step.unsure?.length ?? 0);

function ClaimStrip({ step, demo, onRestart }: { step: Step; demo: DemoKind | null; onRestart: () => void }) {
  const proc = step.claim.find((f) => f.feature === "procedure_category");
  const amount = step.claim.find((f) => f.feature === "claim_amount_pkr");
  return (
    <div className="flex flex-wrap items-center gap-2">
      {demo && (
        <span className="rounded-full bg-ink px-2.5 py-1 text-[11.5px] font-medium text-bg">
          {demo === "risky" ? "Demo · risky claim" : "Demo · clean claim"}
        </span>
      )}
      <span className="rounded-full border border-line bg-surface px-3 py-1 text-[12.5px] text-ink-2">{step.patient.label}</span>
      {proc && <span className="rounded-full border border-line bg-surface px-3 py-1 text-[12.5px] text-ink-2">{proc.value_label}</span>}
      {amount && (
        <span className="tnum rounded-full border border-line bg-surface px-3 py-1 text-[12.5px] text-ink-2">
          {typeof amount.value === "number" ? pkr(amount.value) : amount.value_label}
        </span>
      )}
      {step.documents.length > 0 && (
        <span className="inline-flex items-center gap-1.5 rounded-full border border-line bg-surface px-3 py-1 text-[12.5px] text-ink-2">
          <IconFile size={13} /> Read from {step.documents.length === 1 ? "1 document" : `${step.documents.length} documents`}
        </span>
      )}
      <button
        type="button"
        onClick={onRestart}
        className="no-print ml-auto inline-flex h-8 items-center gap-1.5 rounded-full px-3 text-[12.5px] text-ink-3 transition hover:bg-surface hover:text-ink"
      >
        <IconRestart size={14} /> New check
      </button>
    </div>
  );
}

function Progress({ step, settled }: { step: Step; settled: boolean }) {
  const { min_questions: min, max_questions: max } = step;
  const read = fromDocuments(step);
  const unsure = step.unsure?.length ?? 0;
  const n = clerkAnswers(step);
  const used = step.n_asked;   // the cap counts every question put, answered or not
  return (
    <div>
      <div className="flex items-baseline justify-between">
        <span className="eyebrow">Answers</span>
        <span className="tnum text-[11.5px] text-ink-3">{used} of at most {max}</span>
      </div>
      <div className="relative mt-2.5 flex gap-[3px]" aria-hidden>
        {Array.from({ length: max }, (_, i) => (
          <span
            key={i}
            className="h-1.5 flex-1 rounded-full transition-colors duration-300"
            style={{ background: i < n ? "var(--brand)" : i < used ? "var(--ink-3)" : "var(--surface-3)" }}
          />
        ))}
        {/* the minimum before any verdict, marked on the track itself */}
        <span
          className="absolute -top-1 h-3.5 w-[2px] rounded-full bg-ink-3"
          style={{ left: `calc(${(min / max) * 100}% - 2px)` }}
        />
      </div>
      <p className="mt-2.5 text-[12px] leading-relaxed text-ink-3">
        {read > 0 && `${read} read from the documents. `}
        {unsure > 0 && `${unsure} marked not sure. `}
        {settled
          ? "Finished."
          : n < min
            ? `${min - n} more before a verdict — no claim is judged on fewer than ${min}.`
            : "It stops as soon as another answer would not change the verdict."}
      </p>
    </div>
  );
}

export default function Page() {
  const [view, setView] = useState<View>("landing");
  const [meta, setMeta] = useState<Meta | null>(null);
  const [step, setStep] = useState<Step | null>(null);
  const [result, setResult] = useState<Result | null>(null);
  const [demo, setDemo] = useState<DemoKind | null>(null);
  const [script, setScript] = useState<Record<string, unknown> | null>(null);
  const [autoplay, setAutoplay] = useState(false);
  const [questions, setQuestions] = useState<Record<string, Question>>({});
  const [history, setHistory] = useState<TrajectoryPoint[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<{ message: string; lost: boolean } | null>(null);
  // The check finished but its verdict could not be fetched: offer a retry, never strand it.
  const [verdictError, setVerdictError] = useState<string | null>(null);
  // Bumped on every new verdict, so per-verdict UI (the correction form) starts fresh.
  const [verdictVersion, setVerdictVersion] = useState(0);
  // One persistent live region, so screen readers hear each new question and the verdict.
  const [announcement, setAnnouncement] = useState("");

  // The intake form and the landing figures come from the trained artefacts.
  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    const load = () =>
      api.meta().then(
        (m) => !cancelled && setMeta(m),
        () => { if (!cancelled) timer = setTimeout(load, 5000); },
      );
    load();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, []);

  const fail = (e: unknown) => {
    const lost = e instanceof ApiError && e.sessionLost;
    const message = e instanceof Error ? e.message : "Something went wrong.";
    setError({ message, lost });
    setAnnouncement(message);
    setAutoplay(false);
  };

  const remember = (s: Step) => {
    const q = s.question;
    // Answers read from the documents were never asked; their questions arrive with the
    // step so the ledger can still offer to change them.
    const editors = Object.fromEntries((s.document_questions ?? []).map((d) => [d.feature, d]));
    setQuestions((prev) => ({ ...prev, ...editors, ...(q ? { [q.feature]: q } : {}) }));
    if (q) setAnnouncement(`Question ${asked(s) + 1}: ${q.text}`);
  };

  const finish = useCallback(async (sessionId: string) => {
    setVerdictError(null);
    try {
      const verdict = await api.result(sessionId);
      setResult(verdict);
      setVerdictVersion((v) => v + 1);
      setAutoplay(false);
      setAnnouncement(`Verdict: ${verdict.probability_percent}% rejection risk. ${verdict.explanation.text}`);
    } catch (e) {
      if (e instanceof ApiError && e.sessionLost) fail(e);
      else setVerdictError(e instanceof Error ? e.message : "The verdict could not be loaded.");
    }
  }, []);

  async function begin(
    load: () => Promise<[Step, Record<string, unknown> | null]>,
    kind: DemoKind | null,
  ) {
    setBusy(true);
    setError(null);
    try {
      const [first, answers] = await load();
      setDemo(kind);
      setScript(answers);
      setAutoplay(kind !== null);
      setResult(null);
      setQuestions({});
      remember(first);
      setVerdictError(null);
      setHistory([
        first.documents.length > 0
          ? { tick: "Docs", label: "Documents read", detail: billSummary(first), probability: first.probability }
          : { tick: "Bill", label: "Bill entered", detail: billSummary(first), probability: first.probability },
      ]);
      setStep(first);
      setView("check");
      window.scrollTo({ top: 0 });
      if (first.done) await finish(first.session_id);
    } catch (e) {
      fail(e);
    } finally {
      setBusy(false);
    }
  }

  const startIntake = (values: IntakeValues) =>
    begin(async () => [await api.start(values), null], null);

  const startDemo = (which: DemoKind) =>
    begin(() => Promise.all([api.startDemo(which), api.demoAnswers(which)]), which);

  async function submit(feature: string, value: unknown, edit: boolean, notSure = false) {
    if (!step) return;
    if (edit || notSure) setAutoplay(false);   // the user taking over from the script
    setBusy(true);
    setError(null);
    try {
      const next = await api.answer(step.session_id, feature, value, notSure);
      // The claim has changed on the server, so any verdict on screen is now stale.
      setResult(null);
      remember(next);
      const field = next.answered.find((a) => a.feature === feature);
      const label = field?.label ?? next.unsure.find((u) => u.feature === feature)?.label ?? feature;
      setHistory((h) => [
        ...h,
        {
          tick: edit ? "Edit" : `Q${asked(next)}`,
          label: edit ? `Changed ${label}` : label,
          detail: notSure ? "Not sure" : field?.value_label ?? String(value),
          probability: next.probability,
        },
      ]);
      setStep(next);
      if (next.done) await finish(next.session_id);
    } catch (e) {
      fail(e);
    } finally {
      setBusy(false);
    }
  }

  // Demo autoplay: answer each question from the script after a pause.
  const question = step?.question ?? null;
  useEffect(() => {
    if (!autoplay || !script || !question || busy || result || error) return;
    const value = script[question.feature];
    if (value === undefined) return;
    const big = step?.last_change && Math.abs(step.last_change.delta_points) >= 8;
    const t = setTimeout(() => submit(question.feature, value, false), big ? AUTOPLAY_AFTER_ALERT_MS : AUTOPLAY_MS);
    return () => clearTimeout(t);
    // submit is recreated each render; the step it acts on is captured via `question`.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoplay, script, question, busy, result, error]);

  function toggleAutoplay() {
    setAutoplay((on) => {
      if (!on) setError(null);   // pressing Play is the user's go-ahead to carry on
      return !on;
    });
  }

  function home() {
    setVerdictError(null);
    setView("landing");
    setStep(null);
    setResult(null);
    setDemo(null);
    setScript(null);
    setAutoplay(false);
    setHistory([]);
    setError(null);
  }

  // ---------------------------------------------------------------- views

  // Once the health poll sees the service again, a "not reachable" message is stale.
  const recover = () => setError((e) => (e && !e.lost ? null : e));
  const live = (
    <div className="sr-only" role="status" aria-live="polite">
      {announcement}
    </div>
  );

  if (view === "landing" || (view === "check" && !step)) {
    return (
      <div className="flex min-h-full flex-col">
        {live}
        <TopBar onHome={home} onRecover={recover} />
        <main className="flex-1">
          <Landing
            meta={meta}
            busy={busy}
            error={error?.message ?? null}
            onStart={() => {
              setError(null);
              if (meta) setView("intake");
              else setError({ message: "The risk service is not reachable right now. Check that the API is running.", lost: false });
            }}
            onDemo={startDemo}
          />
        </main>
        <Footer />
      </div>
    );
  }

  if (view === "intake" && meta) {
    return (
      <div className="flex min-h-full flex-col">
        {live}
        <TopBar onHome={home} onRecover={recover} />
        <main className="flex-1">
          <Intake meta={meta} busy={busy} error={error?.message ?? null} onBack={home} onSubmit={startIntake}
                  onAnnounce={setAnnouncement} samples={SAMPLES} />
        </main>
        <Footer />
      </div>
    );
  }

  const s = step!;
  const settled = Boolean(result);
  const delta = history.length > 1 ? (history.at(-1)!.probability - history.at(-2)!.probability) * 100 : null;
  const probability = result?.probability ?? s.probability;
  const band = result?.risk_band ?? s.risk_band;
  // A band word is a verdict: not before the clerk has given the minimum answers, and not
  // when the check had to end short of it.
  const early = (!settled && clerkAnswers(s) < s.min_questions) || result?.stop_reason === "too_few_answers";

  const errorBanner = error && (
    <div role="alert" className="rise flex flex-wrap items-center gap-3 rounded-2xl border border-crit/30 bg-crit-wash px-4 py-3 text-[13.5px] text-crit-ink">
      <IconAlert size={18} className="shrink-0" />
      <span className="flex-1">{error.message}</span>
      {error.lost && (
        <button type="button" onClick={home} className="rounded-full bg-crit-ink px-3.5 py-1.5 text-[12.5px] font-medium text-bg">
          Start again
        </button>
      )}
    </div>
  );

  const scriptGap =
    script && s.question && script[s.question.feature] === undefined
      ? "The demo script has no answer for this question — pick one yourself."
      : null;

  return (
    <div className="flex min-h-full flex-col">
      {live}
      <TopBar onHome={home} onRecover={recover} />

      {/* On a narrow screen the rail sits below the fold, so the reading is repeated
          as a sticky strip: the model's current opinion is never out of view. */}
      <div className="no-print sticky top-16 z-30 border-b border-line bg-[color-mix(in_oklab,var(--bg)_90%,transparent)] backdrop-blur-md lg:hidden">
        <div className="mx-auto max-w-[1200px] px-4 py-3 sm:px-8">
          <RiskPanel compact probability={probability} band={band} bands={s.bands} early={early}
                     typical={s.typical_probability} base={s.base_rate} settled={settled} delta={delta} />
        </div>
      </div>

      <main className="mx-auto w-full max-w-[1200px] flex-1 px-4 pb-16 pt-6 sm:px-8 sm:pt-8">
        <div className="no-print"><ClaimStrip step={s} demo={demo} onRestart={home} /></div>

        {result ? (
          <div className="mt-6 space-y-4">
            {errorBanner}
            <ResultView
              key={verdictVersion}
              result={result}
              questions={questions}
              busy={busy}
              onEdit={(f, v) => submit(f, v, true)}
              onRestart={home}
            />
          </div>
        ) : (
          <div className="mt-6 grid gap-6 lg:grid-cols-[minmax(0,1fr)_360px]">
            <div className="min-w-0 space-y-6">
              {errorBanner}
              <div className="card p-6 sm:p-9">
                {s.question ? (
                  <QuestionCard
                    key={`${s.question.feature}-${s.n_answered}`}
                    question={s.question}
                    index={asked(s) + 1}
                    suggested={script ? script[s.question.feature] : undefined}
                    suggestedBy={script ? "demo" : "documents"}
                    busy={busy}
                    change={s.last_change}
                    autoplay={script ? autoplay : null}
                    notice={scriptGap}
                    onToggleAutoplay={toggleAutoplay}
                    onAnswer={(v) => submit(s.question!.feature, v, false)}
                    onNotSure={() => submit(s.question!.feature, null, false, true)}
                  />
                ) : verdictError ? (
                  <div className="flex flex-wrap items-center gap-4 py-8">
                    <p className="flex-1 text-[14px] text-ink-2">
                      Every question is answered, but the verdict could not be loaded. {verdictError}
                    </p>
                    <button
                      type="button"
                      onClick={() => finish(s.session_id)}
                      className="inline-flex h-11 items-center gap-2 rounded-full bg-brand px-5 text-[14px] font-medium text-brand-ink transition hover:bg-brand-hover"
                    >
                      Try again
                    </button>
                  </div>
                ) : (
                  <div className="flex items-center gap-3 py-10 text-[14px] text-ink-2" role="status">
                    <span className="pulse-dot h-2.5 w-2.5 rounded-full bg-brand" />
                    Preparing the verdict…
                  </div>
                )}
              </div>
              <div className="card p-6">
                <Ledger
                  claim={s.claim}
                  answered={s.answered}
                  unsure={s.unsure}
                  questions={questions}
                  busy={busy || autoplay}
                  onEdit={(f, v) => submit(f, v, true)}
                />
              </div>
            </div>

            <aside className="space-y-6 lg:sticky lg:top-24 lg:self-start">
              <div className="card hidden p-6 lg:block">
                <RiskPanel probability={probability} band={band} bands={s.bands} early={early}
                           typical={s.typical_probability} base={s.base_rate} settled={settled} delta={delta} />
              </div>
              <div className="card p-6">
                <Trajectory points={history} bands={s.bands} />
              </div>
              <div className="card p-6">
                <Progress step={s} settled={settled} />
              </div>
              <div className="card p-6">
                <span className="eyebrow">Insurer rules</span>
                <div className="mt-3"><Checks checks={s.checks} /></div>
              </div>
            </aside>
          </div>
        )}
      </main>
      <Footer />
    </div>
  );
}

function Footer() {
  return (
    <footer className="no-print border-t border-line">
      <div className="mx-auto flex max-w-[1200px] flex-col gap-2 px-4 py-6 text-[12px] leading-relaxed text-ink-3 sm:flex-row sm:items-center sm:justify-between sm:px-8">
        <span>
          A second check for clinic staff, not a replacement for their judgement. Synthetic
          data; real X12 reason codes.
        </span>
        <span>Bano Qabil AI hackathon · Mirza Ahsan Baig &amp; Safee Akmal</span>
      </div>
    </footer>
  );
}
