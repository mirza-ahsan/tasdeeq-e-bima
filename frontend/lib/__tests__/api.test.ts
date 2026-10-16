import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, api } from "@/lib/api";

function respond(status: number, body: unknown) {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(body), { status })));
}

afterEach(() => vi.unstubAllGlobals());

describe("api errors", () => {
  it("never shows a clerk the raw HTTP status line or JSON", async () => {
    respond(500, { detail: "Traceback (most recent call last)" });
    const err = await api.health().catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.message).not.toMatch(/500|Traceback|detail/);
  });

  it("passes the API's own wording through for validation and state errors", async () => {
    respond(422, { detail: "Days since treatment must be between 0 and 365 days." });
    const err = await api.answer("s", "days_since_treatment", -5).catch((e) => e);
    expect(err.message).toBe("Days since treatment must be between 0 and 365 days.");
    expect(err.sessionLost).toBe(false);
  });

  it("recognises a lost session, so the UI can offer to start again", async () => {
    respond(404, { detail: "Session not found." });
    const err = await api.result("gone").catch((e) => e);
    expect(err.sessionLost).toBe(true);
    expect(err.message).toMatch(/expired/);
  });

  it("names the problem when the service cannot be reached at all", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("Failed to fetch"); }));
    const err = await api.meta().catch((e) => e);
    expect(err.status).toBe(502);
    expect(err.message).not.toMatch(/Failed to fetch/);
  });

  it("calls the same-origin proxy, never an absolute API address", async () => {
    const fetchMock = vi.fn(async () => new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    await api.startDemo("risky");
    expect(fetchMock).toHaveBeenCalledWith("/api/demo/risky", expect.objectContaining({ method: "POST" }));
  });
});
