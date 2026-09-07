"use client";

import { useId, useState } from "react";

/**
 * A domain term with its definition attached.
 *
 * The audience is clinic admin staff, not developers: CARC, SHAP and
 * information gain are all load-bearing here and none of them can be assumed.
 * Hiding the jargon would cost accuracy, so it stays — with the definition one
 * hover, tap or Tab away.
 *
 * Opens on hover, on focus and on click, so it is reachable by mouse, keyboard
 * and touch alike. `aria-describedby` ties the definition to the term for
 * screen readers rather than leaving it as unannounced decoration.
 */
export function Term({
  children,
  definition,
}: {
  children: React.ReactNode;
  definition: string;
}) {
  const id = useId();
  const [open, setOpen] = useState(false);

  return (
    <span className="relative inline-block">
      <button
        type="button"
        className="term bg-transparent p-0 text-left font-[inherit] text-[inherit]"
        aria-describedby={open ? id : undefined}
        aria-expanded={open}
        onMouseEnter={() => setOpen(true)}
        onMouseLeave={() => setOpen(false)}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        onClick={() => setOpen((v) => !v)}
      >
        {children}
      </button>
      {open && (
        <span
          id={id}
          role="tooltip"
          className="absolute bottom-[calc(100%+8px)] left-0 z-20 block w-[264px] border
                     border-rule-strong bg-surface p-3 text-[12px] font-normal normal-case
                     leading-relaxed tracking-normal text-ink-soft shadow-[0_2px_10px_rgba(0,0,0,0.06)]"
        >
          {definition}
        </span>
      )}
    </span>
  );
}

/** Definitions live in one place so the same term never gets two explanations. */
export const GLOSSARY = {
  carc: "Claim Adjustment Reason Code — the standard X12 code an insurer puts on a rejected claim to say why. Quoting the right one in an appeal is what gets it re-opened.",
  infoGain:
    "How much uncertainty this question is expected to remove, measured in bits. The field with the highest value is asked next, which is why the order changes from claim to claim.",
  shap: "SHAP attribution — how much each individual field pushed this particular claim's estimate up or down, in percentage points. It is computed per claim, not an average over the dataset.",
  auc: "Area under the ROC curve on held-out claims. 0.5 is guessing, 1.0 is perfect. It measures ranking: how reliably a rejected claim scores above a clean one.",
} as const;
