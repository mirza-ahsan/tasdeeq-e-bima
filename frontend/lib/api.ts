import type { Health, Result, Step } from "./types";

const BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!res.ok) {
    const detail = await res.text().catch(() => "");
    throw new Error(`${res.status} ${res.statusText}${detail ? ` — ${detail}` : ""}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  health: () => call<Health>("/api/health"),

  start: (patient_age = 44, patient_gender: "M" | "F" = "F") =>
    call<Step>("/api/session/start", {
      method: "POST",
      body: JSON.stringify({ patient_age, patient_gender }),
    }),

  answer: (sessionId: string, feature: string, value: unknown) =>
    call<Step>(`/api/session/${sessionId}/answer`, {
      method: "POST",
      body: JSON.stringify({ feature, value }),
    }),

  result: (sessionId: string) => call<Result>(`/api/session/${sessionId}/result`),

  startDemo: (which: "risky" | "clean") => call<Step>(`/api/demo/${which}`),

  demoAnswers: (which: "risky" | "clean") =>
    call<Record<string, unknown>>(`/api/demo/${which}/answers`),

  feedback: (sessionId: string, verdict: "wrong" | "right", note?: string) =>
    call<{ ok: boolean; logged_id: number }>("/api/feedback", {
      method: "POST",
      body: JSON.stringify({ session_id: sessionId, verdict, note: note ?? null }),
    }),
};
