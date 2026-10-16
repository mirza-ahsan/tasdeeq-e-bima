import type { NextRequest } from "next/server";

/**
 * Same-origin proxy to the FastAPI service.
 *
 * The browser only ever calls `/api/...` on this origin, so there is no CORS to
 * configure and the API's address is read at request time from `API_URL` rather than
 * baked into the bundle at build time — moving the API is a restart, not a rebuild.
 * The API itself never needs a public port.
 */

const API_URL = (process.env.API_URL ?? "http://127.0.0.1:8000").replace(/\/$/, "");
// Above the API's own worst case (an 8 s Qwen call plus one retry) and below nginx's 60 s.
const TIMEOUT_MS = 30_000;
const ALLOWED = /^[A-Za-z0-9_\-/]+$/;

export const dynamic = "force-dynamic";

async function forward(request: NextRequest, path: string[]): Promise<Response> {
  const joined = path.join("/");
  if (!ALLOWED.test(joined)) {
    return Response.json({ detail: "Not found." }, { status: 404 });
  }

  const headers = new Headers({ "Content-Type": "application/json" });
  // The API rate-limits per client; tell it who the client is.
  const forwarded = request.headers.get("x-forwarded-for");
  const client = forwarded?.split(",")[0]?.trim() || request.headers.get("x-real-ip") || "";
  if (client) headers.set("X-Forwarded-For", client);

  try {
    const upstream = await fetch(`${API_URL}/api/${joined}`, {
      method: request.method,
      headers,
      body: request.method === "POST" ? await request.text() : undefined,
      cache: "no-store",
      signal: AbortSignal.timeout(TIMEOUT_MS),
    });
    return new Response(upstream.body, {
      status: upstream.status,
      headers: {
        "Content-Type": upstream.headers.get("Content-Type") ?? "application/json",
        "Cache-Control": "no-store",
      },
    });
  } catch {
    return Response.json(
      { detail: "The risk service is not reachable right now." },
      { status: 502 },
    );
  }
}

type Context = { params: Promise<{ path: string[] }> };

export async function GET(request: NextRequest, { params }: Context) {
  return forward(request, (await params).path);
}

export async function POST(request: NextRequest, { params }: Context) {
  return forward(request, (await params).path);
}
