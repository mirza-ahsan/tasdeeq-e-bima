import { describe, expect, it } from "vitest";
import { parseWhole } from "@/lib/numbers";

describe("parseWhole", () => {
  it("reads plain digits and correctly grouped thousands", () => {
    expect(parseWhole("185000")).toBe(185000);
    expect(parseWhole(" 185,000 ")).toBe(185000);
    expect(parseWhole("1,234,567")).toBe(1234567);
  });

  it("refuses anything it would otherwise have to guess at", () => {
    for (const bad of ["1500.00", "4.5", "1,2", "12,34", "-500", "15e3", "abc", ""]) {
      expect(parseWhole(bad)).toBeNull();
    }
  });
});
