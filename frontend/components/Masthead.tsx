"use client";

import { useEffect, useState } from "react";
import { GLOSSARY, Term } from "@/components/Term";
import { api } from "@/lib/api";
import type { Health } from "@/lib/types";

/** Provenance in the masthead: what the number came from, stated up front. */
export function Masthead() {
  const [health, setHealth] = useState<Health | null>(null);

  useEffect(() => {
    api.health().then(setHealth).catch(() => setHealth(null));
  }, []);

  return (
    <header className="rule-b">
      <div className="mx-auto flex max-w-[1180px] items-baseline gap-4 px-6 py-5 sm:px-10">
        <span className="font-display text-[19px] tracking-[-0.01em]">Claim Check</span>
        <span className="hidden text-[12.5px] text-ink-muted sm:inline">
          Rejection risk, before you submit
        </span>
        <span className="ml-auto hidden font-mono text-[10.5px] tracking-wide text-ink-muted md:inline">
          {health ? (
            <>
              LightGBM · {health.n_training_claims.toLocaleString()} synthetic claims ·{" "}
              <Term definition={GLOSSARY.auc}>AUC {health.test_auc.toFixed(2)}</Term>
            </>
          ) : (
            "connecting…"
          )}
        </span>
      </div>
    </header>
  );
}
