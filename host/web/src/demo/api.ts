// The demo: the panel with no device. An `Api` (transport.ts) that answers like the service from a
// state captured from the simulated service (fixture.json, written by host/scripts/demo_fixture.py),
// keeps in memory what is changed (settings, the mode, the source, presets, the monitor) and answers
// "unavailable" to what needs real speakers or a microphone. Nothing leaves the browser.
import type { Api } from "../transport.ts";
import fixture from "./fixture.json";

type Json = Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any
type Reply = { v: 1; ok: true; result: unknown } | { v: 1; ok: false; error: { code: string; message: string } };

/** What needs a device: the demo says so instead of pretending. */
const NEEDS_HARDWARE = new Set([
  "calibrate",
  "calibrate_cancel",
  "calibration_apply",
  "measurement_save",
  "calibration_dump",
  "eq_apply",
  "tone",
  "scan",
  "connect",
  "disconnect",
  "forget",
  "speaker_add",
  "speaker_remove",
  "microphone_set",
  "recalibrate",
  "service_start",
  "service_stop",
  "service_restart",
  "radio_log",
  "ab_start",
  "ab_play",
  "ab_answer",
  "ab_stop",
  "sync_measure",
  "probe_reference",
  "sync_apply",
  "pair_start",
  "pair_approve",
  "pair_deny",
  "client_revoke",
  "client_rename",
]);

const NO_DEVICE = "En la demo no hay equipo: esto necesita parlantes o un micrófono de verdad.";

const ok = (result: unknown): Reply => ({ v: 1, ok: true, result });
const refuse = (message: string, code = "unavailable"): Reply => ({ v: 1, ok: false, error: { code, message } });

export function createDemoApi(): Api {
  const data: Json = structuredClone(fixture) as Json;
  const s: Json = data["state"];
  s["service"] = { ...s["service"], demo: true };
  const chainStage = (id: string): Json | undefined => (data["chain"]["stages"] as Json[]).find((st) => st["id"] === id);

  function apply(message: Json): Reply {
    const { op, ...args } = message as { op: string } & Json;
    if (NEEDS_HARDWARE.has(op)) return refuse(NO_DEVICE);
    switch (op) {
      case "state":
        return ok(s);
      case "chain":
      case "presets":
      case "sync_state":
      case "sync_explain":
      case "spatial_explain":
        return ok(op === "presets" ? { ...data["presets"], presets: s["presets"] } : data[op]);
      case "logs":
        return ok(data["logs"]);
      case "start":
        s["session"] = { ...s["session"], status: "playing" };
        return ok({});
      case "stop":
        s["session"] = { ...s["session"], status: "stopped" };
        return ok({});
      case "set": {
        const changes = (args["changes"] ?? {}) as Json;
        if (typeof args["speaker"] === "string") {
          const speaker = (s["speakers"] as Json[]).find((sp) => sp["name"] === args["speaker"]);
          if (!speaker) return refuse(`no hay un parlante ${args["speaker"]}`, "not_found");
          Object.assign(speaker, changes);
        } else {
          Object.assign(s["global"], changes);
        }
        return ok({});
      }
      case "chain_set": {
        const stage = chainStage(String(args["stage"]));
        if (!stage) return refuse(`no hay una etapa ${args["stage"]}`, "not_found");
        stage["chosen"] = { ...(stage["chosen"] ?? {}), ...(args["algorithm"] ? { algorithm: args["algorithm"] } : {}) };
        if (args["params"]) stage["chosen"]["params"] = { ...(stage["chosen"]["params"] ?? {}), ...args["params"] };
        if (args["algorithm"]) s["chain_summary"] = { ...s["chain_summary"], [String(args["stage"])]: args["algorithm"] };
        return ok({});
      }
      case "chain_reset": {
        const stage = chainStage(String(args["stage"]));
        if (stage) stage["chosen"] = {};
        return ok({});
      }
      case "source":
        s["source"] = { ...s["source"], kind: args["kind"], name: args["name"] ?? null };
        return ok({});
      case "preset_save":
        if (!(s["presets"] as string[]).includes(String(args["name"]))) (s["presets"] as string[]).push(String(args["name"]));
        s["preset"] = args["name"];
        return ok({});
      case "preset_load":
        s["preset"] = args["name"];
        return ok({});
      case "preset_delete":
        s["presets"] = (s["presets"] as string[]).filter((n) => n !== args["name"]);
        if (s["preset"] === args["name"]) s["preset"] = null;
        return ok({});
      case "monitor_set": {
        const on = args["mode"] !== "off";
        s["monitor"] = {
          ...s["monitor"],
          mode: args["mode"],
          target: on ? (args["target"] ?? s["monitor"]["target"]) : null,
          gain_db: args["gain_db"] ?? s["monitor"]["gain_db"],
          state: on ? "on" : "off",
          routed_to: on ? (args["target"] ?? s["monitor"]["target"]) : null,
          reached: on,
        };
        return ok(s["monitor"]);
      }
      case "sync_time":
        return ok({ t: performance.now() / 1000 });
      default:
        return ok({});
    }
  }

  const reply = async (message: Json): Promise<Reply> => {
    const answer = apply(message);
    if (answer.ok) s["sequence"] = Number(s["sequence"] ?? 0) + 1;
    return structuredClone(answer);
  };

  return {
    base: "demo",
    remote: true,
    command: (op, args) => reply({ op, ...(args as object) }) as never,
    raw: (message) => reply(message) as never,
    state: () => reply({ op: "state" }) as never,
    stream: async () => null,
    imageUrl: async () => null,
    url: () => "#",
  };
}
