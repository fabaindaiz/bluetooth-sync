// Pure functions of the Cadena screen: numbers in Spanish, knobs' values, the quality strip's
// readings and its warnings. No DOM here: test/format.test.ts runs them in Node.
import type { ApplyKind, ChainParam, MetricValue, ParamValue, QualityEvent } from "./contract.gen.ts";

const FORMATS = new Map<number, Intl.NumberFormat>();

/** A number with a decimal comma, as the documents write it (Intl, no dependencies). */
export function nf(value: number | null | undefined, digits = 1): string {
  if (value == null || Number.isNaN(value)) return "—";
  let f = FORMATS.get(digits);
  if (!f) {
    f = new Intl.NumberFormat("es", { minimumFractionDigits: digits, maximumFractionDigits: digits, useGrouping: false });
    FORMATS.set(digits, f);
  }
  // A value that rounds to zero is "0", never "-0,0" (a limiter at rest reads -0,00 dB).
  return f.format(Math.abs(value) < 0.5 * 10 ** -digits ? 0 : value);
}

/** How many decimals a knob's step shows: 0,05 → 2; 0,5 → 1; 1 → 0. */
export function digitsOf(step: number | null): number {
  if (step == null || step <= 0) return 2;
  const text = String(step);
  const dot = text.indexOf(".");
  return dot < 0 ? 0 : Math.min(3, text.length - dot - 1);
}

export function withUnit(text: string, unit: string): string {
  if (!unit) return text;
  return unit.startsWith("/") ? `${text}${unit}` : `${text} ${unit}`;
}

/** A knob's value as the listener reads it. */
export function formatParam(param: Pick<ChainParam, "kind" | "step" | "unit">, value: ParamValue | undefined): string {
  if (value === undefined) return "—";
  if (param.kind === "bool") return value ? "sí" : "no";
  if (param.kind === "choice") return choiceLabel(String(value));
  if (typeof value !== "number") return String(value);
  return withUnit(nf(value, param.kind === "int" ? 0 : digitsOf(param.step)), param.unit);
}

export function choiceLabel(choice: string): string {
  return choice === "auto" ? "automático" : choice;
}

export const APPLY_LABELS: Record<ApplyKind, string> = {
  live: "en vivo",
  cut: "con un corte breve",
  restart: "al reiniciar",
};

export const APPLY_HELP: Record<ApplyKind, string> = {
  live: "Se aplica mientras suena, con una rampa: no se oye un salto.",
  cut: "Se aplica bajando y subiendo el audio en 80 + 80 ms: un corte breve, a propósito.",
  restart: "Se aplica la próxima vez que arranque la sesión.",
};

/** Equal values, the way the service compares them (numbers within a hair). */
export function sameValue(a: ParamValue | undefined, b: ParamValue | undefined): boolean {
  if (typeof a === "number" && typeof b === "number") return Math.abs(a - b) < 1e-9;
  return a === b;
}

/** A slider's value as the contract takes it: on the step, inside the range, an int for `int`. */
export function snap(param: Pick<ChainParam, "kind" | "low" | "high" | "step">, value: number): number {
  let v = value;
  if (param.low != null) v = Math.max(param.low, v);
  if (param.high != null) v = Math.min(param.high, v);
  if (param.step && param.low != null) {
    v = param.low + Math.round((v - param.low) / param.step) * param.step;
    v = Number(v.toFixed(digitsOf(param.step) + 2));
  }
  return param.kind === "int" ? Math.round(v) : v;
}

// -- the quality strip (spec 2026-10-02 §7.1) -----------------------------------------------

export const FLATTENING_DB = 1;
export const GAIN_LOST_LU = -3;

export interface QualityWarning {
  kind: "flattening" | "gain_lost";
  text: string;
}

export interface QualityReadout {
  inLufs: number | null;
  outLufs: number | null;
  gainLu: number | null;
  netGainLu: number | null;
  psrIn: number | null;
  psrOut: number | null;
  psrOutWho: string | null;
  tpMax: number | null;
  limiterPct: number | null;
  warnings: QualityWarning[];
}

const short = (name: string): string => name.replace(/^JBL /, "");

/**
 * The strip's numbers. "Ganancia" is the chain's (`chain_gain_lu`): without the digital
 * volume, which is the listener's choice (at -20 dB the net gain is -20 LU on purpose). PSR out
 * is the lowest of the outputs: the one the chain squashed most.
 */
export function qualityReadout(q: QualityEvent | null): QualityReadout {
  const empty: QualityReadout = {
    inLufs: null, outLufs: null, gainLu: null, netGainLu: null, psrIn: null, psrOut: null,
    psrOutWho: null, tpMax: null, limiterPct: null, warnings: [],
  };
  if (!q) return empty;
  let psrOut: number | null = null;
  let psrOutWho: string | null = null;
  for (const [name, out] of Object.entries(q.outputs)) {
    if (out.psr != null && (psrOut == null || out.psr < psrOut)) {
      psrOut = out.psr;
      psrOutWho = name;
    }
  }
  const warnings: QualityWarning[] = [];
  const flat = q.flattening_outputs.length ? q.flattening_outputs : q.flattening && psrOutWho ? [psrOutWho] : [];
  if (flat.length) {
    warnings.push({
      kind: "flattening",
      text: `La cadena aplana: el PSR de ${flat.map(short).join(", ")} quedó más de ${nf(FLATTENING_DB, 0)} dB debajo del de la entrada.`,
    });
  }
  if (q.chain_gain_lu != null && q.chain_gain_lu < GAIN_LOST_LU) {
    warnings.push({
      kind: "gain_lost",
      text: `Ganancia perdida: la cadena suena ${nf(-q.chain_gain_lu, 1)} LU más baja que la entrada (sin contar el volumen).`,
    });
  }
  return {
    inLufs: q.input.s,
    outLufs: q.sum.s,
    gainLu: q.chain_gain_lu,
    netGainLu: q.net_gain_lu,
    psrIn: q.input.psr,
    psrOut,
    psrOutWho,
    tpMax: q.tp_max,
    limiterPct: q.limiter_pct_max,
    warnings,
  };
}

// -- a stage's live metrics ------------------------------------------------------------------

/** Labels for the metrics the stages report today. An unknown key shows as it comes. */
export const METRIC_LABELS: Record<string, [string, string, number]> = {
  mix_now: ["mezcla de ambiente ahora", "", 2],
  share: ["ambiente en la salida", "%", 0],
  active: ["activa", "", 0],
  length: ["largo del filtro", "muestras", 0],
  mean_ms: ["retardo medio", "ms", 1],
  tail_db: ["cola respecto de lo que suena", "dB", 1],
  delay_now_ms: ["retardo ahora", "ms", 2],
  moving: ["moviéndose", "", 0],
  max_boost_db: ["realce máximo", "dB", 1],
  boost_energy_db: ["realce total", "dB", 1],
  to: ["graves a", "", 0],
  reason: ["motivo", "", 0],
  removed_db: ["grave quitado", "dB", 1],
  harmonics_db: ["armónicos", "dB", 1],
  feed_dbfs: ["alimentación de graves", "dBFS", 1],
  volume_db_now: ["volumen ahora", "dB", 1],
  mode: ["modo", "", 0],
  kind: ["tipo", "", 0],
  latency_ms: ["latencia", "ms", 1],
  reduction_db: ["reducción", "dB", 1],
  active_pct: ["tiempo activo", "%", 0],
};

export function metricLabel(key: string): string {
  return METRIC_LABELS[key]?.[0] ?? key.replace(/_/g, " ");
}

/** One scalar of a metric, with its unit. */
export function formatMetricScalar(key: string, value: number | boolean | string | null): string {
  if (value == null) return "—";
  if (typeof value === "boolean") return value ? "sí" : "no";
  if (typeof value === "string") return choiceLabel(value);
  const [, unit, digits] = METRIC_LABELS[key] ?? ["", "", 2];
  // `share` comes as a fraction; the rest as they come.
  const shown = key === "share" ? value * 100 : value;
  return withUnit(nf(shown, digits), unit);
}

/** A metric as text: a scalar, or one value per speaker ("Red 1,2 dB · Blue …"). */
export function formatMetric(key: string, value: MetricValue): string {
  if (value !== null && typeof value === "object") {
    const parts = Object.entries(value).map(([name, v]) => `${short(name)} ${formatMetricScalar(key, v)}`);
    return parts.length ? parts.join(" · ") : "—";
  }
  return formatMetricScalar(key, value);
}
