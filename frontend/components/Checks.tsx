import { IconAlert, IconCheckCircle, IconDash, IconXCircle } from "@/components/icons";
import type { Check, CheckStatus } from "@/lib/types";

const STATUS: Record<CheckStatus, { icon: typeof IconCheckCircle; color: string; word: string }> = {
  ok: { icon: IconCheckCircle, color: "var(--good-ink)", word: "Passed" },
  warn: { icon: IconAlert, color: "var(--warn-ink)", word: "Worth a look" },
  issue: { icon: IconXCircle, color: "var(--crit-ink)", word: "Problem" },
  pending: { icon: IconDash, color: "var(--ink-3)", word: "Not checked" },
};

/**
 * The insurer rules, as deterministic checks rather than model output. "Not checked"
 * means the loop never needed that answer to reach its verdict, not that it was skipped
 * by mistake.
 */
export function Checks({ checks, layout = "list" }: { checks: Check[]; layout?: "list" | "grid" }) {
  // Checks on the uploaded documents get their own group under the insurer rules.
  const rules = checks.filter((c) => !c.id.startsWith("doc_"));
  const documents = checks.filter((c) => c.id.startsWith("doc_"));
  if (documents.length === 0) return <CheckList checks={rules} layout={layout} />;
  return (
    <div className="space-y-5">
      <CheckList checks={rules} layout={layout} />
      <div>
        <span className="eyebrow">The documents</span>
        <div className="mt-3"><CheckList checks={documents} layout={layout} /></div>
      </div>
    </div>
  );
}

function CheckList({ checks, layout }: { checks: Check[]; layout: "list" | "grid" }) {
  const order: CheckStatus[] = ["issue", "warn", "ok", "pending"];
  const sorted = [...checks].sort((a, b) => order.indexOf(a.status) - order.indexOf(b.status));
  return (
    <ul className={layout === "grid" ? "grid gap-2 sm:grid-cols-2" : "space-y-1.5"}>
      {sorted.map((c) => {
        const s = STATUS[c.status];
        const Icon = s.icon;
        return (
          <li
            key={c.id}
            className={`flex items-start gap-3 rounded-xl px-3 py-2.5 ${c.status === "pending" ? "" : "bg-surface-2"}`}
          >
            <Icon size={17} className="mt-px shrink-0" style={{ color: s.color }} aria-hidden />
            <div className="min-w-0">
              <div className="flex flex-wrap items-baseline gap-x-2">
                <span className={`text-[13px] font-medium ${c.status === "pending" ? "text-ink-3" : "text-ink"}`}>
                  {c.label}
                </span>
                <span className="text-[11px]" style={{ color: s.color }}>{s.word}</span>
              </div>
              <p className={`mt-0.5 text-[12.5px] leading-snug ${c.status === "pending" ? "text-ink-3" : "text-ink-2"}`}>
                {c.detail}
              </p>
            </div>
          </li>
        );
      })}
    </ul>
  );
}
