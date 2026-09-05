"use client";

export function StartScreen({
  busy,
  error,
  onStart,
  onDemo,
}: {
  busy: boolean;
  error: string | null;
  onStart: () => void;
  onDemo: (which: "risky" | "clean") => void;
}) {
  return (
    <div className="rise mx-auto max-w-[640px] py-16 sm:py-24">
      <h1 className="font-display text-[2.6rem] leading-[1.15] tracking-[-0.02em] sm:text-[3.1rem]">
        A spell-checker for insurance claims.
      </h1>
      <p className="mt-6 max-w-prose text-[15.5px] leading-[1.65] text-ink-soft">
        Most claim rejections are not clinical. They are paperwork — a missing
        pre-authorisation, a filing deadline passed, documents left out. This checks a claim
        before it goes to the insurer and tells you which part is the problem.
      </p>
      <p className="mt-4 max-w-prose text-[15.5px] leading-[1.65] text-ink-soft">
        It asks one question at a time, choosing each one based on what it has already
        learned about the claim. Straightforward claims are done in three questions.
      </p>

      <div className="mt-10 flex flex-wrap gap-3">
        <button
          onClick={onStart}
          disabled={busy}
          className="border border-ink px-6 py-3 text-[13.5px] tracking-wide transition-colors
                     hover:bg-ink hover:text-paper disabled:opacity-40"
        >
          Check a claim
        </button>
        <button
          onClick={() => onDemo("risky")}
          disabled={busy}
          className="border border-rule-strong px-6 py-3 text-[13.5px] tracking-wide text-ink-soft
                     transition-colors hover:border-ink hover:text-ink disabled:opacity-40"
        >
          Example: problem claim
        </button>
        <button
          onClick={() => onDemo("clean")}
          disabled={busy}
          className="border border-rule-strong px-6 py-3 text-[13.5px] tracking-wide text-ink-soft
                     transition-colors hover:border-ink hover:text-ink disabled:opacity-40"
        >
          Example: clean claim
        </button>
      </div>

      {error && (
        <p className="mt-6 border-l-2 border-risk-high pl-4 text-[13px] leading-relaxed text-ink-soft">
          {error}
          <br />
          <span className="text-ink-muted">
            Start the API with <code className="font-mono">uvicorn backend.main:app --reload</code>.
          </span>
        </p>
      )}

      <p className="mt-14 rule-t pt-5 text-[12px] leading-relaxed text-ink-muted">
        Built on Synthea-generated clinical records and the real X12 CARC reason-code
        standard. The rejection outcomes are simulated — no public dataset of real claims and
        their outcomes exists, because insurers do not publish one.
      </p>
    </div>
  );
}
