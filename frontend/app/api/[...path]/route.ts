import type { NextRequest } from "next/server";
import { visitor } from "@/lib/visitor";

/**
 * Same-origin proxy to the FastAPI service.
 *
 * The browser only ever calls `/api/...` on this origin, so there is no CORS to
 * configure and the API's address is read at request time from `API_URL` rather than
 * baked into the bundle at build time — moving the API is a restart, not a rebuild.
 * The API itself never needs a public port.
 */

const API_URL = (process.env.API_URL ?? "http://127.0.0.1:8000").replace(/\/$/, "");
// Above the API's own worst cases (an 8 s Qwen call plus one retry; a 25 s Qwen-VL read)
// and below nginx's 60 s.
const TIMEOUT_MS = 30_000;
// Every request this app sends is a few hundred bytes of JSON, except a document upload:
// up to three files of 4 MB, base64-encoded. The API enforces the per-file limits.
const MAX_BODY_BYTES = 16_384;
const MAX_UPLOAD_BYTES = 17_000_000;
const UPLOAD_PATH = "documents/read";
const ALLOWED = /^[A-Za-z0-9_\-/]+$/;

export const dynamic = "force-dynamic";

async function forward(request: NextRequest, path: string[], method: "GET" | "POST"): Promise<Response> {
  const joined = path.join("/");
  if (!ALLOWED.test(joined)) {
    return Response.json({ detail: "Not found." }, { status: 404 });
  }

  let body: string | undefined;
  if (method === "POST") {
    // JSON only: a cross-site <form> can post text/plain without a preflight, never JSON.
    if (!(request.headers.get("content-type") ?? "").includes("application/json")) {
      return Response.json({ detail: "Requests must be JSON." }, { status: 415 });
    }
    const limit = joined === UPLOAD_PATH ? MAX_UPLOAD_BYTES : MAX_BODY_BYTES;
    // Refuse a declared oversize body before reading it into memory.
    if (Number(request.headers.get("content-length") ?? 0) > limit) {
      return Response.json({ detail: "The request is too large." }, { status: 413 });
    }
    body = await request.text();
    if (body.length > limit) {
      return Response.json({ detail: "The request is too large." }, { status: 413 });
    }
  }

  const headers = new Headers({ "Content-Type": "application/json" });
  const client = visitor(request.headers, process.env.TRUST_FORWARDED_FOR === "true");
  if (client) headers.set("X-Forwarded-For", client);

  try {
    const upstream = await fetch(`${API_URL}/api/${joined}`, {
      method,
      headers,
      body,
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
  return forward(request, (await params).path, "GET");
}

export async function HEAD(request: NextRequest, { params }: Context) {
  const res = await forward(request, (await params).path, "GET");
  return new Response(null, { status: res.status, headers: res.headers });
}

export async function POST(request: NextRequest, { params }: Context) {
  return forward(request, (await params).path, "POST");
}
