import { describe, expect, it } from "vitest";
import { visitor } from "@/lib/visitor";

const h = (entries: Record<string, string>) => new Headers(entries);

describe("visitor", () => {
  it("trusts only the hop the reverse proxy appended", () => {
    expect(visitor(h({ "x-forwarded-for": "6.6.6.6, 203.0.113.9" }))).toBe("203.0.113.9");
  });

  it("takes a single proxy-set address as is", () => {
    expect(visitor(h({ "x-forwarded-for": "203.0.113.9" }))).toBe("203.0.113.9");
  });

  it("falls back to x-real-ip, then to nothing", () => {
    expect(visitor(h({ "x-real-ip": "198.51.100.4" }))).toBe("198.51.100.4");
    expect(visitor(h({}))).toBe("");
  });
});
