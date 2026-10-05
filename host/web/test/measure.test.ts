// The browser's port of probe_measure.measure against Python's on the same recordings (spec
// 2026-10-03 §7: "the same arrivals within 0.01 ms"). The fixture is written by
// host/tests/test_probe_measure_fixture.py.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { measure } from "../src/sync/measure.ts";

interface Case {
  sr: number;
  scale: number;
  mic: string;
  probes: Record<string, string>;
  python: { arrivals_ms: Record<string, number>; halves_ms: Record<string, [number, number]>; valid: string[] };
}

const fixture = JSON.parse(readFileSync(new URL("./fixtures/probe_measure.json", import.meta.url), "utf8")) as { cases: Case[] };

function decode(b64: string, scale: number): Float64Array {
  const bytes = Buffer.from(b64, "base64");
  const view = new Int16Array(bytes.buffer, bytes.byteOffset, bytes.byteLength / 2);
  return Float64Array.from(view, (v) => v * scale);
}

describe("the probe measurement in the browser", () => {
  it.each(fixture.cases.map((c, i) => [i, c] as const))("gives Python's arrivals on case %i", (_i, c) => {
    const mic = decode(c.mic, c.scale);
    const probes = Object.fromEntries(Object.entries(c.probes).map(([n, b]) => [n, decode(b, c.scale)]));
    const m = measure(mic, probes, c.sr);
    expect([...m.valid].sort()).toEqual(c.python.valid);
    for (const [name, a] of Object.entries(c.python.arrivals_ms)) {
      expect(Math.abs((m.arrivalsMs[name] ?? Number.NaN) - a)).toBeLessThan(0.01);
      const [h1, h2] = c.python.halves_ms[name] ?? [Number.NaN, Number.NaN];
      expect(Math.abs((m.halvesMs[name]?.[0] ?? Number.NaN) - h1)).toBeLessThan(0.01);
      expect(Math.abs((m.halvesMs[name]?.[1] ?? Number.NaN) - h2)).toBeLessThan(0.01);
    }
  });

  it("says why a speaker without probe is not believed", () => {
    const m = measure(new Float64Array(2000), { a: new Float64Array(1000) }, 16000);
    expect(m.valid.size).toBe(0);
    expect(m.reasons["a"]).toContain("silence");
  });
});
