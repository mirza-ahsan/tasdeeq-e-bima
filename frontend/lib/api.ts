import type { DemoKind, Health, Intake, Meta, Result, Step } from "./types";

/**
 * Typed client for the API, reached through this app's own `/api` proxy.
 *
 * Errors carry a message written for a clinic clerk, never the raw HTTP status line or
 * a JSON body: "404 Not Found — {"detail": ...}" tells a receptionist nothing.
 */

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }

  /** The check is gone (expired, or the service restarted) and has to be started again. */
  get sessionLost() {
    return this.status === 404;
  }
}

const FRIENDLY: Record<number, string> = {
  404: "This check has expired, or the service restarted. Start a new one.",
  429: "Too many checks started in the last minute. Wait a moment and try again.",
  502: "The risk service is not reachable right now. Check that the API is running.",
  503: "The risk model is not available on this server.",
};

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`/api${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...init?.headers },
      cache: "no-store",
    });
  } catch {
    throw new ApiError(FRIENDLY[502], 502);
  }
  if (!res.ok) {
    let detail = "";
    try {
      const body = await res.json();
      if (typeof body?.detail === "string") detail = body.detail;
    } catch {
      /* not JSON */
    }
    // Validation and state messages from the API are already written for people;
    // anything else gets a fixed sentence.
    const message =
      res.status === 409 || res.status === 422 || res.status === 400
        ? detail || "That answer could not be accepted."
        : (FRIENDLY[res.status] ?? "Something went wrong. Try again in a moment.");
    throw new ApiError(message, res.status);
  }
  return (await res.json()) as T;
}

export const api = {
  health: () => call<Health>("/health"),
  meta: () => call<Meta>("/meta"),

  start: (intake: Intake) =>
    call<Step>("/session/start", { method: "POST", body: JSON.stringify(intake) }),

  startDemo: (which: DemoKind) => call<Step>(`/demo/${which}`, { method: "POST" }),

  demoAnswers: (which: DemoKind) => call<Record<string, unknown>>(`/demo/${which}/answers`),

  answer: (sessionId: string, feature: string, value: unknown) =>
    call<Step>(`/session/${sessionId}/answer`, {
      method: "POST",
      body: JSON.stringify({ feature, value }),
    }),

  result: (sessionId: string) => call<Result>(`/session/${sessionId}/result`),

  feedback: (sessionId: string, note?: string) =>
    call<{ ok: boolean; logged_id: number }>("/feedback", {
      method: "POST",
      body: JSON.stringify({ session_id: sessionId, note: note?.trim() || null }),
    }),
};
