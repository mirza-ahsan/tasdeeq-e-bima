import { describe, expect, it } from "vitest";
import { pct, pkr, points, relative } from "@/lib/format";

describe("format", () => {
  it("rounds probabilities to whole percentages", () => {
    expect(pct(0.6717)).toBe("67%");
    expect(pct(0.005)).toBe("1%");
  });

  it("writes rupees with grouping", () => {
    expect(pkr(85000)).toBe("PKR 85,000");
  });

  it("signs points with a real minus and one decimal", () => {
    expect(points(40.66)).toBe("+40.7");
    expect(points(-4.31)).toBe("−4.3");
    expect(points(0.04)).toBe("0.0");
  });

  it("describes risk relative to the average claim without overstating it", () => {
    expect(relative(0.67, 0.237)).toBe("2.8× the average claim");
    expect(relative(0.18, 0.237)).toBe("24% below the average claim");
    expect(relative(0.25, 0.237)).toBe("about the average claim");
  });
});
