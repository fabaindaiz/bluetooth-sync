// The demo: the panel with no device (web/src/demo/api.ts). It answers like the service from a
// captured state, keeps what is changed in memory, and says so for what needs real hardware.
import { describe, expect, it } from "vitest";
import { createDemoApi } from "../src/demo/api.ts";

type Any = Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any

async function state(api: ReturnType<typeof createDemoApi>): Promise<Any> {
  const reply = await api.state();
  expect(reply?.ok).toBe(true);
  return (reply as { result: Any }).result;
}

describe("the demo", () => {
  it("answers the state of a made-up installation, marked as a demo", async () => {
    const api = createDemoApi();
    const s = await state(api);
    expect(s.speakers).toHaveLength(3);
    expect(s.service.demo).toBe(true);
    expect(api.remote).toBe(true);
    expect(await api.stream(0)).toBeNull();
  });

  it("keeps what is changed, as the service would show it", async () => {
    const api = createDemoApi();
    const name = (await state(api)).speakers[0].name;
    await api.raw({ op: "set", changes: { volume_db: -33 } });
    await api.raw({ op: "set", speaker: name, changes: { ambience: 0.4, muted: true } });
    await api.raw({ op: "chain_set", stage: "spatial", algorithm: "front" });
    await api.raw({ op: "source", kind: "system" });
    const s = await state(api);
    expect(s.global.volume_db).toBe(-33);
    expect(s.speakers[0].ambience).toBe(0.4);
    expect(s.speakers[0].muted).toBe(true);
    expect(s.chain_summary.spatial).toBe("front");
    expect(s.source.kind).toBe("system");
    const chain = (await api.raw({ op: "chain" })) as { result: Any };
    expect(chain.result.stages.find((st: Any) => st.id === "spatial").chosen.algorithm).toBe("front");
  });

  it("starts and stops, saves and loads presets, and plays the monitor", async () => {
    const api = createDemoApi();
    await api.raw({ op: "stop" });
    expect((await state(api)).session.status).toBe("stopped");
    await api.raw({ op: "start" });
    expect((await state(api)).session.status).toBe("playing");
    await api.raw({ op: "preset_save", name: "mío" });
    expect((await state(api)).presets).toContain("mío");
    await api.raw({ op: "preset_load", name: "mío" });
    expect((await state(api)).preset).toBe("mío");
    await api.raw({ op: "preset_delete", name: "mío" });
    expect((await state(api)).presets).not.toContain("mío");
    const target = (await state(api)).monitor.candidates[0].node;
    await api.raw({ op: "monitor_set", mode: "binaural", target });
    const m = (await state(api)).monitor;
    expect(m.state).toBe("on");
    expect(m.reached).toBe(true);
  });

  it("says that what needs real hardware cannot be done in the demo", async () => {
    const api = createDemoApi();
    for (const op of ["calibrate", "scan", "tone", "sync_measure", "speaker_add", "ab_start"]) {
      const reply = (await api.raw({ op })) as { ok: boolean; error: { code: string; message: string } };
      expect(reply.ok).toBe(false);
      expect(reply.error.code).toBe("unavailable");
      expect(reply.error.message).toContain("demo");
    }
  });
});
