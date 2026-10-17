import { describe, expect, it } from "vitest";
import { parseWhole, regroupAmount } from "@/lib/numbers";

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

describe("regroupAmount", () => {
  it("keeps a number typed digit by digit grouped and valid", () => {
    let v = "";
    for (const key of "240000") v = regroupAmount(v, v + key);
    expect(v).toBe("240,000");
    expect(parseWhole(v)).toBe(240000);
  });

  it("regroups after a digit is deleted", () => {
    expect(regroupAmount("240,000", "24,000")).toBe("24,000");
    expect(regroupAmount("1,000", "1,00")).toBe("100");
  });

  it("never strips what the clerk typed", () => {
    expect(regroupAmount("185", "1,85")).toBe("1,85");
    expect(regroupAmount("1,500", "1,500.")).toBe("1,500.");
    expect(regroupAmount("1,2", "1,23")).toBe("1,23");
  });
});
