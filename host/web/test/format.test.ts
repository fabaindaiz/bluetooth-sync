import { describe, expect, it } from "vitest";
import type { QualityEvent } from "../src/contract.gen.ts";
import { digitsOf, formatMetric, formatParam, nf, qualityReadout, snap } from "../src/format.ts";

describe("numbers", () => {
  it("use a decimal comma and never show -0", () => {
    expect(nf(-1.25, 1)).toBe("-1,3");
    expect(nf(-0.004, 1)).toBe("0,0");
    expect(nf(null)).toBe("—");
  });
  it("take their decimals from the knob's step", () => {
    expect(digitsOf(0.05)).toBe(2);
    expect(digitsOf(0.5)).toBe(1);
    expect(digitsOf(1)).toBe(0);
    expect(formatParam({ kind: "float", step: 0.5, unit: "dB" }, -1)).toBe("-1,0 dB");
    expect(formatParam({ kind: "float", step: 5, unit: "dB/s" }, 30)).toBe("30 dB/s");
    expect(formatParam({ kind: "float", step: 0.5, unit: "/s" }, 2)).toBe("2,0/s");
    expect(formatParam({ kind: "choice", step: null, unit: "" }, "auto")).toBe("automático");
  });
  it("snap a slider onto the contract's grid", () => {
    expect(snap({ kind: "int", low: 4, high: 8, step: 4 }, 6.5)).toBe(8);
    expect(snap({ kind: "int", low: 0, high: 10, step: null }, 2.6)).toBe(3);
    expect(snap({ kind: "float", low: -6, high: 0, step: 0.5 }, -1.26)).toBe(-1.5);
    expect(snap({ kind: "float", low: 0, high: 1, step: 0.05 }, 0.30000000000000004)).toBe(0.3);
    expect(snap({ kind: "float", low: 0, high: 1, step: 0.05 }, 7)).toBe(1);
  });
  it("say a metric per speaker", () => {
    expect(formatMetric("reduction_db", { "JBL Go 4 Red": 1.234, "JBL Go 4 Blue": null })).toBe("Go 4 Red 1,2 dB · Go 4 Blue —");
    expect(formatMetric("share", 0.25)).toBe("25 %");
    expect(formatMetric("unknown_thing", 3)).toBe("3,00");
  });
});

const output = { m: -26, s: -26, tp: -6, psr: 14, limiter_pct: 0, flattening: false };
function quality(over: Partial<QualityEvent> = {}): QualityEvent {
  return {
    input: { m: -23, s: -23, i: -23, tp: -4, psr: 15 },
    outputs: { "JBL Go 4 Red": output, "JBL Go 4 Blue": { ...output, psr: 12.5, flattening: true } },
    sum: { m: -23, s: -23.4 },
    net_gain_lu: -20.4,
    chain_gain_lu: -0.4,
    flattening: true,
    flattening_outputs: ["JBL Go 4 Blue"],
    tp_max: -6,
    limiter_pct_max: 3,
    cost_ms: 0.3,
    ...over,
  };
}

describe("the quality strip", () => {
  it("takes the lowest PSR out and warns that the chain flattens", () => {
    const r = qualityReadout(quality());
    expect(r.psrOut).toBe(12.5);
    expect(r.psrOutWho).toBe("JBL Go 4 Blue");
    expect(r.warnings.map((w) => w.kind)).toEqual(["flattening"]);
    expect(r.warnings[0]?.text).toContain("Go 4 Blue");
  });
  it("judges lost gain by the chain's gain, not the listener's volume", () => {
    expect(qualityReadout(quality({ flattening: false, flattening_outputs: [] })).warnings).toEqual([]);
    const lost = qualityReadout(quality({ flattening: false, flattening_outputs: [], chain_gain_lu: -3.5 }));
    expect(lost.warnings.map((w) => w.kind)).toEqual(["gain_lost"]);
    expect(lost.warnings[0]?.text).toContain("3,5 LU");
  });
  it("is empty without a reading", () => {
    expect(qualityReadout(null).warnings).toEqual([]);
  });
});
