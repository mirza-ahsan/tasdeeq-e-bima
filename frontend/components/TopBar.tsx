"use client";

import { useEffect, useState } from "react";
import { IconMoon, IconSun } from "@/components/icons";
import { api } from "@/lib/api";
import type { Health } from "@/lib/types";

/** The brand mark: a claim form with a tick, inside a rounded square. */
export function BrandMark({ size = 30 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden>
      <rect width="32" height="32" rx="9" fill="var(--brand)" />
      <path
        d="M10 8h8.5l3.5 3.5V23a1.5 1.5 0 0 1-1.5 1.5H10A1.5 1.5 0 0 1 8.5 23V9.5A1.5 1.5 0 0 1 10 8z"
        fill="none"
        stroke="var(--brand-ink)"
        strokeWidth="1.7"
        strokeLinejoin="round"
      />
      <path
        d="M12 17.2l2.5 2.5 5-5.4"
        fill="none"
        stroke="var(--brand-ink)"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

/** Whether the risk service is up. Retries quietly, so it never shows "connecting…" forever. */
function useHealth() {
  const [health, setHealth] = useState<Health | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const h = await api.health();
        if (!cancelled) {
          setHealth(h);
          setFailed(false);
        }
      } catch {
        if (!cancelled) setFailed(true);
      }
      if (!cancelled) timer = setTimeout(poll, 15_000);
    };
    poll();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, []);
  return { health, failed };
}

function ThemeToggle() {
  const [dark, setDark] = useState<boolean | null>(null);

  useEffect(() => {
    const explicit = document.documentElement.dataset.theme;
    // Reading the theme the pre-paint script applied; this runs once on mount.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setDark(explicit ? explicit === "dark" : window.matchMedia("(prefers-color-scheme: dark)").matches);
  }, []);

  function toggle() {
    const next = !dark;
    setDark(next);
    document.documentElement.dataset.theme = next ? "dark" : "light";
    try {
      localStorage.setItem("theme", next ? "dark" : "light");
    } catch {
      /* private mode: the choice lasts for this page only */
    }
  }

  return (
    <button
      type="button"
      onClick={toggle}
      aria-label={dark ? "Switch to light theme" : "Switch to dark theme"}
      className="grid h-9 w-9 place-items-center rounded-full border border-line text-ink-2 transition
                 hover:border-line-strong hover:bg-surface hover:text-ink"
    >
      {dark === null ? null : dark ? <IconSun size={16} /> : <IconMoon size={16} />}
    </button>
  );
}

export function TopBar({ onHome }: { onHome?: () => void }) {
  const { health, failed } = useHealth();
  const online = health?.models_loaded && !failed;

  return (
    <header className="no-print sticky top-0 z-40 border-b border-line bg-[color-mix(in_oklab,var(--bg)_82%,transparent)] backdrop-blur-md">
      <div className="mx-auto flex h-16 max-w-[1200px] items-center gap-4 px-4 sm:px-8">
        <button
          type="button"
          onClick={onHome}
          className="flex items-center gap-2.5 rounded-lg"
          aria-label="Claim Check — home"
        >
          <BrandMark />
          <span className="flex flex-col items-start leading-none">
            <span className="text-[15px] font-semibold tracking-[-0.01em]">Claim Check</span>
            <span className="mt-1 text-[11px] text-ink-3">Tasdeeq-e-Bima</span>
          </span>
        </button>

        <div className="ml-auto flex items-center gap-2.5">
          <span
            className="hidden items-center gap-2 rounded-full border border-line bg-surface px-3 py-1.5 text-[12px] text-ink-2 sm:flex"
            role="status"
          >
            <span
              className={`h-2 w-2 rounded-full ${online ? "live-dot bg-brand" : failed ? "bg-crit" : "bg-surface-3"}`}
            />
            {online
              ? health?.qwen_configured
                ? "Model online · Qwen on"
                : "Model online · template explanations"
              : failed
                ? "Risk service offline — retrying"
                : "Checking the service…"}
          </span>
          <ThemeToggle />
        </div>
      </div>
    </header>
  );
}
