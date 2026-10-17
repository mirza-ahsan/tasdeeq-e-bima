"use client";

import { useMemo, useState } from "react";
import { IconArrowLeft, IconArrowRight, IconShield } from "@/components/icons";
import { pkr } from "@/lib/format";
import { parseWhole, regroupAmount } from "@/lib/numbers";
import type { Intake as IntakeValues, Meta } from "@/lib/types";

/**
 * The bill, entered once. Everything here is printed on the claim, so it is a form
 * rather than a conversation; the adaptive questions start from it.
 *
 * As the clerk types it already shows two things the tool knows: the typical price
 * for the procedure, and whether this bill needs pre-authorisation.
 */
/**
 * Arrow-key movement for a radiogroup: one tab stop (the selected option, else the
 * first), arrows move and select, as the WAI-ARIA radio group pattern expects.
 */
function radioKeys<T>(values: T[], current: T | null, select: (v: T) => void) {
  return (e: React.KeyboardEvent<HTMLElement>) => {
    const step = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[e.key];
    if (!step) return;
    e.preventDefault();
    const i = current === null ? 0 : values.indexOf(current);
    const next = (i + step + values.length) % values.length;
    select(values[next]);
    const group = e.currentTarget;
    requestAnimationFrame(() =>
      (group.querySelectorAll<HTMLElement>('[role="radio"]')[next])?.focus());
  };
}

const tabStop = <T,>(value: T, current: T | null, index: number) =>
  value === current || (current === null && index === 0) ? 0 : -1;

export function Intake({
  meta,
  busy,
  error,
  onBack,
  onSubmit,
}: {
  meta: Meta;
  busy: boolean;
  error: string | null;
  onBack: () => void;
  onSubmit: (values: IntakeValues) => void;
}) {
  const [age, setAge] = useState("");
  const [gender, setGender] = useState<"F" | "M" | null>(null);
  const [procedure, setProcedure] = useState<string | null>(null);
  const [amount, setAmount] = useState("");

  const [ageMin, ageMax] = meta.age_limits;
  const [amtMin, amtMax] = meta.amount_limits;
  const proc = meta.procedures.find((p) => p.value === procedure) ?? null;

  // Whole numbers only. Anything else is refused with a message, never silently stripped:
  // "4.5" must not become 45, nor "1500.00" PKR 150,000.
  const ageNum = /^\d+$/.test(age) ? Number(age) : null;
  const amountNum = parseWhole(amount);
  const ageOk = ageNum !== null && ageNum >= ageMin && ageNum <= ageMax;
  const amountOk = amountNum !== null && amountNum >= amtMin && amountNum <= amtMax;
  const ready = ageOk && gender && proc && amountOk;

  const hint = useMemo(() => {
    if (!proc) return null;
    const needs = proc.needs_pre_auth || (amountOk && amountNum! > meta.pre_auth_amount_threshold_pkr);
    const ratio = amountOk && proc.typical_amount_pkr ? amountNum! / proc.typical_amount_pkr : null;
    return { needs, ratio };
  }, [proc, amountOk, amountNum, meta.pre_auth_amount_threshold_pkr]);

  function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!ready) return;
    onSubmit({
      patient_age: ageNum!,
      patient_gender: gender!,
      procedure_category: proc!.value,
      claim_amount_pkr: amountNum!,
    });
  }

  return (
    <div className="mx-auto max-w-[880px] px-4 py-10 sm:px-8 sm:py-14">
      <button
        type="button"
        onClick={onBack}
        className="inline-flex items-center gap-1.5 rounded-full px-1 text-[13px] text-ink-3 transition hover:text-ink"
      >
        <IconArrowLeft size={15} /> Back
      </button>

      <div className="rise mt-5">
        <span className="eyebrow">New check · step 1 of 2</span>
        <h1 className="mt-2 text-[34px] font-semibold leading-tight tracking-[-0.03em]">
          What&apos;s on the bill?
        </h1>
        <p className="mt-2 max-w-[560px] text-[15px] leading-relaxed text-ink-2">
          The patient and what was billed. After this the check asks only the paperwork
          questions that matter for this particular claim.
        </p>
      </div>

      <form onSubmit={submit} className="rise rise-1 card mt-8 overflow-hidden">
        {/* Patient */}
        <fieldset className="grid gap-6 border-b border-line p-6 sm:grid-cols-2 sm:p-8">
          <legend className="sr-only">Patient</legend>
          <label className="block">
            <span className="text-[13px] font-medium">Patient age</span>
            <div className="mt-2 flex items-center rounded-xl border border-line-strong bg-surface focus-within:border-brand">
              <input
                inputMode="numeric"
                value={age}
                onChange={(e) => setAge(e.target.value.trim().slice(0, 6))}
                placeholder="e.g. 44"
                aria-invalid={age !== "" && !ageOk}
                className="tnum h-12 w-full rounded-xl bg-transparent px-4 text-[16px] outline-none"
              />
              <span className="pr-4 text-[13px] text-ink-3">years</span>
            </div>
            {age !== "" && !ageOk && (
              <span className="mt-1.5 block text-[12px] text-crit-ink">
                A whole number of years, between {ageMin} and {ageMax}.
              </span>
            )}
          </label>

          <div>
            <span className="text-[13px] font-medium" id="sex-label">Patient sex</span>
            <div
              role="radiogroup"
              aria-labelledby="sex-label"
              onKeyDown={radioKeys<"F" | "M">(["F", "M"], gender, setGender)}
              className="mt-2 grid h-12 grid-cols-2 gap-1 rounded-xl bg-surface-2 p-1"
            >
              {(["F", "M"] as const).map((g, i) => (
                <button
                  key={g}
                  type="button"
                  role="radio"
                  aria-checked={gender === g}
                  tabIndex={tabStop(g, gender, i)}
                  onClick={() => setGender(g)}
                  className={`rounded-[10px] text-[14px] transition ${
                    gender === g ? "bg-surface font-medium text-ink shadow-[var(--shadow-sm)]" : "text-ink-2 hover:text-ink"
                  }`}
                >
                  {g === "F" ? "Female" : "Male"}
                </button>
              ))}
            </div>
          </div>
        </fieldset>

        {/* Procedure */}
        <div className="border-b border-line p-6 sm:p-8">
          <span id="proc-label" className="text-[13px] font-medium">Procedure billed</span>
          <div
            role="radiogroup"
            aria-labelledby="proc-label"
            onKeyDown={radioKeys(meta.procedures.map((p) => p.value), procedure, setProcedure)}
            className="mt-3 grid gap-2 sm:grid-cols-2"
          >
            {meta.procedures.map((p, i) => {
              const on = procedure === p.value;
              return (
                <button
                  key={p.value}
                  type="button"
                  role="radio"
                  aria-checked={on}
                  tabIndex={tabStop(p.value, procedure, i)}
                  onClick={() => setProcedure(p.value)}
                  className={`flex items-center justify-between gap-3 rounded-xl border px-4 py-3 text-left transition ${
                    on
                      ? "border-brand bg-brand-wash"
                      : "border-line bg-surface hover:border-line-strong hover:bg-surface-2"
                  }`}
                >
                  <span className="min-w-0">
                    <span className="block truncate text-[14px] font-medium">{p.label}</span>
                    <span className="tnum mt-0.5 block text-[12px] text-ink-3">
                      Typically {pkr(p.typical_amount_pkr)}
                    </span>
                  </span>
                  {p.needs_pre_auth && (
                    <span className="shrink-0 rounded-full border border-line px-2 py-0.5 text-[10.5px] text-ink-2">
                      Pre-auth
                    </span>
                  )}
                </button>
              );
            })}
          </div>
        </div>

        {/* Amount */}
        <div className="p-6 sm:p-8">
          <label className="block max-w-[420px]">
            <span className="text-[13px] font-medium">Claim amount</span>
            <div className="mt-2 flex items-center rounded-xl border border-line-strong bg-surface focus-within:border-brand">
              <span className="pl-4 text-[14px] text-ink-3">PKR</span>
              <input
                inputMode="numeric"
                value={amount}
                onChange={(e) => {
                  const input = e.target;
                  const raw = input.value.slice(0, 12);
                  const next = regroupAmount(amount, raw);
                  setAmount(next);
                  if (next === raw) return;
                  // Regrouping rewrites the value, which sends the caret to the end; put it
                  // back after the same number of digits so a mid-number edit stays put.
                  const digits = raw.slice(0, input.selectionStart ?? raw.length).replace(/\D/g, "").length;
                  let at = 0;
                  for (let seen = 0; at < next.length && seen < digits; at++) if (/\d/.test(next[at])) seen++;
                  requestAnimationFrame(() => input.setSelectionRange(at, at));
                }}
                placeholder={proc ? proc.typical_amount_pkr.toLocaleString("en-US") : "0"}
                aria-invalid={amount !== "" && !amountOk}
                className="tnum h-12 w-full rounded-xl bg-transparent px-3 text-[16px] outline-none"
              />
            </div>
            {amount !== "" && !amountOk && (
              <span className="mt-1.5 block text-[12px] text-crit-ink">
                Whole rupees, between {pkr(amtMin)} and {pkr(amtMax)}.
              </span>
            )}
          </label>

          {hint && (
            <div className="fade mt-4 flex flex-wrap gap-2 text-[12.5px]">
              <span className="inline-flex items-center gap-1.5 rounded-full bg-surface-2 px-3 py-1.5 text-ink-2">
                <IconShield size={14} className={hint.needs ? "text-warn-ink" : "text-good-ink"} />
                {hint.needs
                  ? proc?.needs_pre_auth
                    ? "Needs pre-authorisation at any price"
                    : `Needs pre-authorisation above ${pkr(meta.pre_auth_amount_threshold_pkr)}`
                  : "No pre-authorisation needed"}
              </span>
              {hint.ratio !== null && (
                <span className="tnum inline-flex items-center rounded-full bg-surface-2 px-3 py-1.5 text-ink-2">
                  {hint.ratio >= 1.8
                    ? `${hint.ratio.toFixed(1)}× the typical amount`
                    : hint.ratio <= 0.5
                      ? "Well below the typical amount"
                      : "In the usual range"}
                </span>
              )}
            </div>
          )}

          {error && (
            <p role="alert" className="mt-5 rounded-xl bg-crit-wash px-4 py-3 text-[13.5px] text-crit-ink">
              {error}
            </p>
          )}

          <div className="mt-8 flex items-center justify-between gap-4">
            <span className="text-[12.5px] text-ink-3">
              Then {meta.min_questions}–{meta.max_questions} quick questions.
            </span>
            <button
              type="submit"
              disabled={!ready || busy}
              className="group inline-flex h-12 items-center gap-2 rounded-full bg-brand px-6 text-[14.5px] font-medium
                         text-brand-ink transition hover:bg-brand-hover disabled:cursor-not-allowed disabled:opacity-40"
            >
              {busy ? "Starting…" : "Start the check"}
              <IconArrowRight size={16} className="transition group-hover:translate-x-0.5" />
            </button>
          </div>
        </div>
      </form>
    </div>
  );
}
