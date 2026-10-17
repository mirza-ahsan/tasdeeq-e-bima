"use client";

import { useEffect, useId, useRef, useState } from "react";

/**
 * A domain term with its definition attached.
 *
 * The audience is clinic admin staff, not developers: CARC, Shapley attribution and
 * information gain are all load-bearing here and none of them can be assumed. Hiding
 * the jargon would cost accuracy, so it stays — with the definition one hover, tap or
 * Tab away. A tap or click *opens* it (toggling meant the hover-then-click of a mouse,
 * or the focus-then-tap of a touch screen, closed it again at once); Escape or a tap
 * elsewhere closes it. `aria-describedby` ties the definition to the term.
 */
export function Term({
  children,
  definition,
  align = "left",
}: {
  children: React.ReactNode;
  definition: string;
  align?: "left" | "right";
}) {
  const id = useId();
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLSpanElement>(null);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    const onDown = (e: PointerEvent) => {
      if (root.current && !root.current.contains(e.target as Node)) setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    window.addEventListener("pointerdown", onDown);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("pointerdown", onDown);
    };
  }, [open]);

  return (
    <span ref={root} className="relative inline-block">
      <button
        type="button"
        className="term bg-transparent p-0 text-left font-[inherit] text-[inherit] [letter-spacing:inherit]"
        aria-describedby={open ? id : undefined}
        aria-expanded={open}
        onMouseEnter={() => setOpen(true)}
        onMouseLeave={() => setOpen(false)}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        onClick={() => setOpen(true)}
      >
        {children}
      </button>
      {open && (
        <span
          id={id}
          role="tooltip"
          className={`fade absolute bottom-[calc(100%+10px)] z-30 block w-[min(280px,80vw)] rounded-xl border
                      border-line-strong bg-surface p-3.5 text-left text-[12.5px] font-normal normal-case
                      leading-relaxed tracking-normal text-ink-2 shadow-float
                      ${align === "right" ? "right-0" : "left-0"}`}
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
    "How much uncertainty this question is expected to remove, in bits. The question worth most is asked next, which is why the order changes from claim to claim.",
  shapley:
    "How much each fact about this claim moved its estimate away from a typical claim, in percentage points. The points add up exactly to the difference.",
  auc: "Area under the ROC curve on claims the model never saw. 0.5 is guessing, 1.0 is perfect. It measures how reliably a rejected claim scores above an accepted one.",
  typical:
    "The estimate for this patient before anything about the bill was known. Every contribution is measured from here.",
} as const;
