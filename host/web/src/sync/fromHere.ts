// "Medir desde este teléfono": a point measurement of the sync with this device's microphone
// (spec 2026-10-03 §6.3). The audio never leaves the phone: only the arrivals do.
//
// 1. the server clock: 8 round trips to `sync_time`, keeping the shortest;
// 2. record `seconds` with every voice processing off, at 48 kHz;
// 3. the probe each speaker sent in that span (`probe_reference`);
// 4. measure here (measure.ts, the same as the server's);
// 5. send the arrivals (`sync_measure`): the estimator suggests, nothing is applied.
import type { Api } from "../transport.ts";
import { MAX_LAG_MS, SR, measure } from "./measure.ts";

export const ROUND_TRIPS = 8;
export const SECONDS = 8;
/** The reference is the recording minus this: a probe that arrives up to MAX_LAG_MS late still fits. */
export const TAIL_S = MAX_LAG_MS / 1000;

export interface Recording {
  samples: Float32Array;
  sampleRate: number;
  /** `performance.now()` (ms) of the first sample. */
  startedAt: number;
  /** What the browser applied (MediaTrackSettings), sent with the measurement. */
  settings: Record<string, unknown>;
}

export interface Recorder {
  record(seconds: number): Promise<Recording>;
}

export interface Result {
  ok: boolean;
  /** In words, for the panel. */
  message: string;
  heard: string[];
  reasons: Record<string, string>;
  arrivalsMs: Record<string, number>;
}

type Raw = (message: Record<string, unknown>) => Promise<{ ok: boolean; result?: unknown; error?: { message: string } } | null>;

/** Server time minus `performance.now()/1000`, from the round trip with the shortest time. */
export async function clockOffset(raw: Raw, rounds = ROUND_TRIPS): Promise<{ offset: number; rttMs: number }> {
  let best = { offset: 0, rttMs: Number.POSITIVE_INFINITY };
  for (let i = 0; i < rounds; i++) {
    const t0 = performance.now();
    const reply = await raw({ op: "sync_time" });
    const t1 = performance.now();
    if (!reply?.ok) continue;
    const server = (reply.result as { t: number }).t;
    const rtt = t1 - t0;
    if (rtt < best.rttMs) best = { offset: server - (t0 + t1) / 2000, rttMs: rtt };
  }
  if (!Number.isFinite(best.rttMs)) throw new Error("el equipo no respondió la hora");
  return best;
}

function decode(b64: string, scale: number): Float64Array {
  const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
  const view = new Int16Array(bytes.buffer);
  return Float64Array.from(view, (v) => v * scale);
}

/** The microphone, with every voice processing off, through an AudioWorklet at 48 kHz. */
export const microphone: Recorder = {
  async record(seconds) {
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false, channelCount: 1 },
    });
    const track = stream.getAudioTracks()[0];
    const settings = (track?.getSettings() ?? {}) as Record<string, unknown>;
    const ctx = new AudioContext({ sampleRate: SR });
    const code =
      "class Tap extends AudioWorkletProcessor { process(inputs) { const c = inputs[0] && inputs[0][0]; if (c) this.port.postMessage(c.slice(0)); return true; } }" +
      "registerProcessor('aurasync-tap', Tap);";
    const url = URL.createObjectURL(new Blob([code], { type: "text/javascript" }));
    try {
      await ctx.audioWorklet.addModule(url);
      const source = ctx.createMediaStreamSource(stream);
      const tap = new AudioWorkletNode(ctx, "aurasync-tap");
      const wanted = Math.round(seconds * ctx.sampleRate);
      const samples = new Float32Array(wanted);
      let filled = 0;
      let startedAt = 0;
      await new Promise<void>((resolve) => {
        tap.port.onmessage = (e: MessageEvent<Float32Array>) => {
          if (filled === 0) startedAt = performance.now() - (e.data.length / ctx.sampleRate) * 1000;
          samples.set(e.data.subarray(0, wanted - filled), filled);
          filled += e.data.length;
          if (filled >= wanted) resolve();
        };
        source.connect(tap);
      });
      return {
        samples,
        sampleRate: ctx.sampleRate,
        startedAt,
        settings: {
          echo_cancellation: settings["echoCancellation"] ?? null,
          noise_suppression: settings["noiseSuppression"] ?? null,
          auto_gain_control: settings["autoGainControl"] ?? null,
          sample_rate: ctx.sampleRate,
        },
      };
    } finally {
      for (const t of stream.getTracks()) t.stop();
      void ctx.close();
      URL.revokeObjectURL(url);
    }
  },
};

export interface Options {
  role?: "target" | "vote";
  sourceId: string;
  positionId: string;
  recorder?: Recorder;
  seconds?: number;
}

export async function measureFromHere(api: Api, options: Options): Promise<Result> {
  const raw = api.raw as unknown as Raw;
  const seconds = options.seconds ?? SECONDS;
  const fail = (message: string, reasons: Record<string, string> = {}): Result => ({ ok: false, message, heard: [], reasons, arrivalsMs: {} });
  const clock = await clockOffset(raw);
  const recording = await (options.recorder ?? microphone).record(seconds);
  if (recording.sampleRate !== SR) return fail(`el micrófono grabó a ${recording.sampleRate} Hz; hace falta ${SR}`);
  const processed = ["echo_cancellation", "noise_suppression", "auto_gain_control"].filter((k) => recording.settings[k] === true);
  const from = recording.startedAt / 1000 + clock.offset;
  const refSeconds = Math.max(1, seconds - TAIL_S);
  const reply = await raw({ op: "probe_reference", from, seconds: refSeconds });
  if (!reply?.ok) return fail(`no se pudo leer la sonda del equipo: ${reply?.error?.message ?? "sin conexión"}`);
  const ref = reply.result as { scale: number; speakers: Record<string, string> };
  const probes = Object.fromEntries(Object.entries(ref.speakers).map(([n, b]) => [n, decode(b, ref.scale)]));
  const m = measure(recording.samples, probes, SR);
  const heard = [...m.valid].sort();
  if (heard.length < 2) {
    const why = heard.length ? `solo se oyó ${heard[0]}` : "no se oyó ningún parlante";
    return fail(`${why}: acercate a los parlantes o subí el volumen, y que la sonda esté encendida.`, m.reasons);
  }
  const measurement = {
    source_id: options.sourceId,
    position_id: options.positionId,
    kind: "point",
    role: options.role ?? "target",
    t: from + refSeconds / 2,
    arrivals_ms: Object.fromEntries(heard.map((n) => [n, m.arrivalsMs[n]])),
    halves_ms: Object.fromEntries(heard.map((n) => [n, m.halvesMs[n]])),
    quality: recording.settings,
    origin: "browser",
  };
  const sent = await raw({ op: "sync_measure", measurement });
  if (!sent?.ok) return fail(`el equipo no aceptó la medición: ${sent?.error?.message ?? "sin conexión"}`, m.reasons);
  const note = processed.length ? ` Ojo: el navegador dejó encendido ${processed.join(", ")}.` : "";
  return {
    ok: true,
    message: `Medido: se oyeron ${heard.length} parlantes (${heard.join(", ")}); la sugerencia se actualiza.${note}`,
    heard,
    reasons: m.reasons,
    arrivalsMs: measurement.arrivals_ms as Record<string, number>,
  };
}
