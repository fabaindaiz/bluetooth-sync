// host/src/aurasync/probe_measure.py's `measure`, in the browser (spec 2026-10-03 §6.3 step 5): each
// speaker's arrival in the phone's recording, by a PHAT-weighted cross-correlation with the probe it
// sent, inside the probe's band, the peak interpolated; believed only if the two halves agree and it
// is near the others. Same constants; test/measure.test.ts checks it against Python's results.
import { type Spectrum, irfft, rfft } from "./fft.ts";

export const SR = 48000;
export const MAX_LAG_MS = 1500;
export const AGREEMENT_MS = 0.25;
export const CONSENSUS_MS = 100;
export const SILENT_RMS = 1e-7;
export const BAND_HZ: [number, number] = [300, 8000];

export interface ProbeMeasurement {
  arrivalsMs: Record<string, number>;
  halvesMs: Record<string, [number, number]>;
  valid: Set<string>;
  reasons: Record<string, string>;
}

function peak(corr: Float64Array, limit: number): number {
  let best = 0;
  for (let i = 1; i <= limit; i++) if ((corr[i] as number) > (corr[best] as number)) best = i;
  if (best > 0 && best < limit) {
    const a = corr[best - 1] as number;
    const b = corr[best] as number;
    const c = corr[best + 1] as number;
    const d = a - 2 * b + c;
    if (d !== 0) return best + (0.5 * (a - c)) / d;
  }
  return best;
}

const rms = (x: ArrayLike<number>, from: number, to: number): number => {
  let s = 0;
  for (let i = from; i < to; i++) s += (x[i] as number) ** 2;
  return Math.sqrt(s / Math.max(1, to - from));
};

function median(values: number[]): number {
  const v = [...values].sort((a, b) => a - b);
  const mid = v.length >> 1;
  return v.length % 2 ? (v[mid] as number) : ((v[mid - 1] as number) + (v[mid] as number)) / 2;
}

export function measure(
  mic: ArrayLike<number>,
  probes: Record<string, ArrayLike<number>>,
  sr: number = SR,
  { maxLagMs = MAX_LAG_MS, agreementMs = AGREEMENT_MS, bandHz = BAND_HZ } = {},
): ProbeMeasurement {
  const names = Object.keys(probes);
  if (!names.length) return { arrivalsMs: {}, halvesMs: {}, valid: new Set(), reasons: {} };
  const length = Math.max(...names.map((n) => (probes[n] as ArrayLike<number>).length));
  const n = 2 ** Math.ceil(Math.log2(mic.length + length));
  const bins = n / 2 + 1;
  const inBand = new Uint8Array(bins);
  for (let k = 0; k < bins; k++) {
    const f = (k * sr) / n;
    inBand[k] = f >= bandHz[0] && f <= bandHz[1] ? 1 : 0;
  }
  const spectrum = rfft(mic, n);
  const limit = Math.min(Math.trunc((sr * maxLagMs) / 1000), n - 1);

  const arrival = (ref: Spectrum): number => {
    const re = new Float64Array(bins);
    const im = new Float64Array(bins);
    for (let k = 0; k < bins; k++) {
      if (!inBand[k]) continue;
      // spectrum × conj(ref), normalised to unit magnitude (PHAT)
      const xr = (spectrum.re[k] as number) * (ref.re[k] as number) + (spectrum.im[k] as number) * (ref.im[k] as number);
      const xi = (spectrum.im[k] as number) * (ref.re[k] as number) - (spectrum.re[k] as number) * (ref.im[k] as number);
      const mag = Math.hypot(xr, xi);
      if (mag > 1e-20) {
        re[k] = xr / mag;
        im[k] = xi / mag;
      }
    }
    return (peak(irfft({ re, im }, n), limit) / sr) * 1000;
  };

  const arrivalsMs: Record<string, number> = {};
  const halvesMs: Record<string, [number, number]> = {};
  const reasons: Record<string, string> = {};
  for (const name of names) {
    const ref = probes[name] as ArrayLike<number>;
    const half = ref.length >> 1;
    if (Math.min(rms(ref, 0, half), rms(ref, half, ref.length)) < SILENT_RMS) {
      reasons[name] = "no probe in the window (silence)";
      continue;
    }
    const first = new Float64Array(ref.length);
    const second = new Float64Array(ref.length);
    for (let i = 0; i < half; i++) first[i] = ref[i] as number;
    for (let i = half; i < ref.length; i++) second[i] = ref[i] as number;
    const r1 = rfft(first, n);
    const r2 = rfft(second, n);
    const both: Spectrum = { re: r1.re.map((v, k) => v + (r2.re[k] as number)), im: r1.im.map((v, k) => v + (r2.im[k] as number)) };
    const whole = arrival(both);
    const a1 = arrival(r1);
    const a2 = arrival(r2);
    arrivalsMs[name] = whole;
    halvesMs[name] = [a1, a2];
    if (Math.abs(a1 - a2) > agreementMs || Math.abs(whole - 0.5 * (a1 + a2)) > agreementMs) {
      reasons[name] = `the two halves disagree: ${a1.toFixed(2)} and ${a2.toFixed(2)} ms`;
    }
  }
  const agreed = Object.entries(arrivalsMs).filter(([name]) => !(name in reasons)).map(([, a]) => a);
  if (agreed.length) {
    const mid = median(agreed);
    for (const [name, a] of Object.entries(arrivalsMs)) {
      if (!(name in reasons) && Math.abs(a - mid) > CONSENSUS_MS) reasons[name] = `${(a - mid).toFixed(0)} ms away from the others`;
    }
  }
  const valid = new Set(Object.keys(arrivalsMs).filter((name) => !(name in reasons)));
  return { arrivalsMs, halvesMs, valid, reasons };
}
