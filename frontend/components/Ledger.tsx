"use client";

import { useEffect, useRef, useState } from "react";
import { IconCheck, IconPencil } from "@/components/icons";
import { GLOSSARY, Term } from "@/components/Term";
import { points as fmtPoints } from "@/lib/format";
import { parseWhole } from "@/lib/numbers";
import type { Field, Question } from "@/lib/types";

/** Below this, a movement is noise and pointing at it would mislead. */
const NOTABLE = 1;

function Points({ value }: { value: number }) {
  const notable = Math.abs(value) >= NOTABLE;
  return (
    <span className="tnum inline-flex shrink-0 items-center gap-1.5 font-mono text-[12px] text-ink-2">
      {notable && (
        <span
          className="h-1.5 w-1.5 rounded-full"
          style={{ background: value > 0 ? "var(--up)" : "var(--down)" }}
          aria-hidden
        />
      )}
      {notable ? `${fmtPoints(value)} pts` : <span className="text-ink-3">little effect</span>}
    </span>
  );
}

/** Inline editor for one answer, using the question exactly as it was asked. */
function Editor({
  question,
  current,
  busy,
  onSave,
  onCancel,
}: {
  question: Question;
  current: unknown;
  busy: boolean;
  onSave: (value: unknown) => void;
  onCancel: () => void;
}) {
  const [text, setText] = useState(String(current ?? ""));
  const first = useRef<HTMLButtonElement>(null);
  // Opening the editor moves focus into it; the pencil that opened it is gone.
  useEffect(() => first.current?.focus(), []);

  if (question.kind === "number") {
    const n = parseWhole(text);
    const ok =
      n !== null &&
      (question.min_value === null || n >= question.min_value) &&
      (question.max_value === null || n <= question.max_value);
    return (
      <form
        className="mt-2 flex flex-wrap items-center gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          if (ok) onSave(n);
        }}
      >
        {text !== "" && !ok && (
          <span role="alert" className="order-last basis-full text-[12px] text-crit-ink">
            A whole number between {question.min_value?.toLocaleString()} and{" "}
            {question.max_value?.toLocaleString()}.
          </span>
        )}
        <input
          autoFocus
          inputMode="numeric"
          value={text}
          onChange={(e) => setText(e.target.value.slice(0, 12))}
          aria-invalid={text !== "" && !ok}
          className="tnum h-9 w-28 rounded-lg border border-line-strong bg-surface px-3 text-[14px] outline-none focus:border-brand"
          aria-label={question.text}
        />
        <button type="submit" disabled={!ok || busy}
                className="h-9 rounded-lg bg-brand px-3 text-[13px] font-medium text-brand-ink disabled:opacity-40">
          Save
        </button>
        <button type="button" onClick={onCancel} className="h-9 rounded-lg px-2 text-[13px] text-ink-3 hover:text-ink">
          Cancel
        </button>
      </form>
    );
  }
  return (
    <div className="mt-2 flex flex-wrap gap-1.5">
      {question.options.map((o, i) => {
        const on = o.value === current;
        return (
          <button
            key={String(o.value)}
            ref={i === 0 ? first : undefined}
            type="button"
            disabled={busy}
            onClick={() => (on ? onCancel() : onSave(o.value))}
            className={`inline-flex h-8 items-center gap-1.5 rounded-full border px-3 text-[12.5px] transition disabled:opacity-40 ${
              on ? "border-brand bg-brand-wash text-ink" : "border-line bg-surface text-ink-2 hover:border-line-strong hover:text-ink"
            }`}
          >
            {on && <IconCheck size={13} />}
            {o.label}
          </button>
        );
      })}
      <button type="button" onClick={onCancel} className="h-8 px-2 text-[12.5px] text-ink-3 hover:text-ink">
        Cancel
      </button>
    </div>
  );
}

/**
 * Everything known about the claim, kept on screen rather than scrolled away as a chat
 * transcript. Each row carries what it is doing to the estimate right now (exact
 * Shapley points, measured from a typical claim), and any answer can be corrected.
 */
export function Ledger({
  claim,
  answered,
  questions,
  busy,
  onEdit,
}: {
  claim: Field[];
  answered: Field[];
  questions: Record<string, Question>;
  busy: boolean;
  onEdit: (feature: string, value: unknown) => void;
}) {
  const [editing, setEditing] = useState<string | null>(null);

  return (
    <div>
      <div className="flex items-baseline justify-between gap-3">
        <span className="eyebrow">The claim so far</span>
        <span className="text-[11.5px] text-ink-3">
          <Term definition={GLOSSARY.shapley} align="right">Effect on the estimate</Term>
        </span>
      </div>

      <dl className="mt-3 divide-y divide-[var(--border)] rounded-2xl border border-line">
        {claim.map((f) => (
          <div key={f.feature} className="flex items-center justify-between gap-4 px-4 py-3">
            <div className="min-w-0">
              <dt className="text-[11.5px] text-ink-3">
                {f.label} · {f.source === "document" ? "read from the documents" : "from the bill"}
              </dt>
              <dd className="truncate text-[14px]">{f.value_label}</dd>
              {f.evidence && <q className="block truncate text-[11.5px] italic text-ink-3">{f.evidence}</q>}
            </div>
            <Points value={f.contribution_points} />
          </div>
        ))}
        {answered.map((f) => {
          const q = questions[f.feature];
          const isEditing = editing === f.feature;
          return (
            <div key={f.feature} className="px-4 py-3">
              <div className="flex items-center justify-between gap-4">
                <div className="min-w-0">
                  <dt className="text-[11.5px] text-ink-3">
                    {f.label}
                    {f.source === "document" && " · read from the documents"}
                  </dt>
                  <dd className="truncate text-[14px]">{f.value_label}</dd>
                  {f.evidence && <q className="block truncate text-[11.5px] italic text-ink-3">{f.evidence}</q>}
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  <Points value={f.contribution_points} />
                  {q && !isEditing && (
                    <button
                      type="button"
                      onClick={() => setEditing(f.feature)}
                      disabled={busy}
                      className="grid h-7 w-7 place-items-center rounded-full text-ink-3 transition hover:bg-surface-2 hover:text-ink disabled:opacity-40"
                      aria-label={`Change ${f.label}`}
                    >
                      <IconPencil size={14} />
                    </button>
                  )}
                </div>
              </div>
              {q && isEditing && (
                <Editor
                  question={q}
                  current={f.value}
                  busy={busy}
                  onCancel={() => setEditing(null)}
                  onSave={(v) => {
                    setEditing(null);
                    onEdit(f.feature, v);
                  }}
                />
              )}
            </div>
          );
        })}
      </dl>
      <p className="mt-2 flex items-center gap-3 text-[11px] text-ink-3">
        <span className="inline-flex items-center gap-1.5"><span className="h-1.5 w-1.5 rounded-full" style={{ background: "var(--up)" }} />raises risk</span>
        <span className="inline-flex items-center gap-1.5"><span className="h-1.5 w-1.5 rounded-full" style={{ background: "var(--down)" }} />lowers risk</span>
      </p>
    </div>
  );
}
