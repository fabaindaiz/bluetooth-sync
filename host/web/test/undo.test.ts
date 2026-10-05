import { describe, expect, it } from "vitest";
import { type Snapshot, capture, restorePlan, runPlan } from "../src/undo.ts";

const speaker = (name: string, fields: Record<string, unknown>) => ({
  name,
  address: `AA:${name}`,
  pan: 0,
  ambience: 0.15,
  gain_db: 0,
  delay_ms: 0,
  muted: false,
  kind: "go4",
  eq_db: null,
  ...fields,
});

const state = (speakers: ReturnType<typeof speaker>[], global: Record<string, unknown> = {}, loop = false) => ({
  speakers,
  global: { rear_delay_ms: 12, extract_ambience: true, decorrelate: true, eq_active: true, volume_db: -20, ...global },
  recalibration: { active: loop },
});

describe("the restore plan", () => {
  it("writes an EQ curve back, and clears one that was not there (research/10 §7.1)", () => {
    const curve = Array.from({ length: 27 }, (_, i) => (i === 5 ? 3.5 : 0));
    const withCurve = capture(state([speaker("Red", { eq_db: curve }), speaker("Blue", {})]), null);
    const cleared = capture(state([speaker("Red", {}), speaker("Blue", {})]), null);
    expect(restorePlan(withCurve, cleared)).toEqual([{ op: "set", speaker: "Red", changes: { eq_db: curve } }]);
    expect(restorePlan(cleared, withCurve)).toEqual([{ op: "set", speaker: "Red", changes: { eq_db: null } }]);
  });

  it("gives a speaker added back its EQ curve", () => {
    const curve = Array.from({ length: 27 }, () => 1);
    const before = capture(state([speaker("Red", { eq_db: curve })]), null);
    const now = capture(state([]), null);
    expect(restorePlan(before, now)).toEqual([
      { op: "speaker_add", address: "AA:Red" },
      expect.objectContaining({ op: "set", speaker: "Red", changes: expect.objectContaining({ eq_db: curve }) }),
    ]);
  });

  it("is empty when nothing changed", () => {
    const s = capture(state([speaker("Red", {})]), { stages: [{ id: "eq", chosen: {} }] });
    expect(restorePlan(s, s)).toEqual([]);
  });

  it("puts back what a preset changed, only what differs, and never the volume", () => {
    const before = capture(state([speaker("Red", { pan: -0.7 }), speaker("Blue", { ambience: 0.55 })]), null);
    const now = capture(
      state([speaker("Red", { pan: -0.7 }), speaker("Blue", { ambience: 0.9 })], { decorrelate: false, volume_db: -6 }),
      null,
    );
    expect(restorePlan(before, now)).toEqual([
      { op: "set", changes: { decorrelate: true } },
      { op: "set", speaker: "Blue", changes: { ambience: 0.55 } },
    ]);
  });

  it("adds a removed speaker back first, with everything it had", () => {
    const red = speaker("Red", { pan: -0.7, gain_db: -3, muted: true });
    const before = capture(state([red, speaker("Blue", {})]), null);
    const now = capture(state([speaker("Blue", {})]), null);
    const plan = restorePlan(before, now);
    expect(plan[0]).toEqual({ op: "speaker_add", address: "AA:Red" });
    expect(plan.at(-1)).toEqual({
      op: "set",
      speaker: "Red",
      changes: { pan: -0.7, ambience: 0.15, gain_db: -3, delay_ms: 0, muted: true, kind: "go4", eq_db: null },
    });
  });

  it("switches the loop off around the delays it owns, and on again", () => {
    const before = capture(state([speaker("Red", { delay_ms: 0 })], {}, true), null);
    const now = capture(state([speaker("Red", { delay_ms: 9 })], {}, true), null);
    expect(restorePlan(before, now)).toEqual([
      { op: "set", changes: { recalibrate: false } },
      { op: "set", speaker: "Red", changes: { delay_ms: 0 } },
      { op: "set", changes: { recalibrate: true } },
    ]);
  });

  it("resets a changed stage and writes back its own choices, per speaker too", () => {
    const chosen = { algorithm: "true_peak", params: { release_ms: 80 }, speakers: { Red: { trim_db: -2 } } };
    const before = capture(state([speaker("Red", {})]), {
      stages: [
        { id: "limiter", chosen },
        { id: "volume", chosen: { algorithm: "digital" } },
      ],
    });
    const now = capture(state([speaker("Red", {})]), {
      stages: [
        { id: "limiter", chosen: {} },
        { id: "volume", chosen: { algorithm: "avrcp" } },
      ],
    });
    expect(restorePlan(before, now)).toEqual([
      { op: "chain_reset", stage: "limiter" },
      { op: "chain_set", stage: "limiter", algorithm: "true_peak", params: { release_ms: 80 } },
      { op: "chain_set", stage: "limiter", speaker: "Red", params: { trim_db: -2 } },
    ]);
  });

  it("a stage that had no choices goes back to its defaults", () => {
    const before: Snapshot = capture(state([]), { stages: [{ id: "eq", chosen: {} }] });
    const now: Snapshot = capture(state([]), { stages: [{ id: "eq", chosen: { algorithm: "off" } }] });
    expect(restorePlan(before, now)).toEqual([{ op: "chain_reset", stage: "eq" }]);
  });
});

describe("running a plan", () => {
  it("keeps going after a refusal and says which ones failed", async () => {
    const sent: string[] = [];
    const failed = await runPlan(
      [
        { op: "set", changes: {} },
        { op: "chain_set", stage: "x" },
        { op: "set", speaker: "Red", changes: {} },
      ],
      async (m) => {
        sent.push(m.op);
        return m.op === "chain_set" ? { ok: false, error: { message: "unknown stage" } } : { ok: true };
      },
    );
    expect(sent).toEqual(["set", "chain_set", "set"]);
    expect(failed).toEqual(["chain_set: unknown stage"]);
  });
});
