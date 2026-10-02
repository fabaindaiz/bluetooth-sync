// What the Preact screens share with app.js while both live in the panel (d-7c8794-6da524).
//
// app.js owns the one EventSource (a browser gives a page 6 HTTP/1.1 connections, research/11
// §4.4) and the polling fallback. It re-emits what it receives as DOM events on `document`:
// `aurasync:state` (each snapshot it renders), `aurasync:quality`, `aurasync:chain` and
// `aurasync:radio` (the stream's events). This module turns them into a small store, and sends
// orders the same way app.js does: through the one transport (transport.ts, runtime.ts), which
// uses the cookie on the panel the service serves and the client's bearer in the PWA.
import type { ChainMetricsEvent, OpArgs, OpName, QualityEvent, RadioEvent, Reply, ResultOf, StateView } from "./contract.gen.ts";
import { runtime } from "./runtime.ts";

export interface Live {
  state: StateView | null;
  quality: QualityEvent | null;
  /** When the last `quality` event arrived (performance.now), to fall back to `state.quality`. */
  qualityAt: number;
  metrics: ChainMetricsEvent | null;
  metricsAt: number;
  radio: RadioEvent | null;
}

type Listener = (live: Live) => void;

const live: Live = { state: null, quality: null, qualityAt: 0, metrics: null, metricsAt: 0, radio: null };
const listeners = new Set<Listener>();

function emit(): void {
  for (const listener of listeners) listener(live);
}

export function subscribe(listener: Listener): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function current(): Live {
  return live;
}

/** A stream event older than this is not "live": the panel shows the polled state's copy. */
export const FRESH_MS = 3000;

export function quality(now: number = performance.now()): QualityEvent | null {
  if (live.quality && now - live.qualityAt < FRESH_MS) return live.quality;
  return live.state?.quality ?? null;
}

export function metrics(now: number = performance.now()): ChainMetricsEvent | null {
  return live.metrics && now - live.metricsAt < FRESH_MS ? live.metrics : null;
}

export function connect(target: EventTarget = document): void {
  target.addEventListener("aurasync:state", (e) => {
    live.state = (e as CustomEvent<StateView>).detail;
    emit();
  });
  target.addEventListener("aurasync:quality", (e) => {
    live.quality = (e as CustomEvent<QualityEvent>).detail;
    live.qualityAt = performance.now();
    emit();
  });
  target.addEventListener("aurasync:chain", (e) => {
    live.metrics = (e as CustomEvent<ChainMetricsEvent>).detail;
    live.metricsAt = performance.now();
    emit();
  });
  target.addEventListener("aurasync:radio", (e) => {
    live.radio = (e as CustomEvent<RadioEvent>).detail;
    emit();
  });
}

export function toast(message: string): void {
  document.dispatchEvent(new CustomEvent("aurasync:toast", { detail: message }));
}

/** One order. A refused one is said in the toast, as app.js does; null: it did not leave. */
export async function send<K extends OpName>(op: K, args: OpArgs[K]): Promise<Reply<ResultOf<K>> | null> {
  const api = runtime.api ?? (await runtime.ready);
  const reply = await api.command(op, args);
  if (reply === null) {
    toast("Sin conexión: la orden no se envió.");
    return null;
  }
  if (!reply.ok) toast(reply.error.message);
  return reply;
}

// -- "en camino" (research/11 §4.3) --------------------------------------------------------
// The value changes on screen at once, but the ear hears it after the measured latency. The
// same `data-pending` attribute and CSS as app.js.

const DEFAULT_PENDING_MS = 500;
const timers = new WeakMap<Element, number>();

export function pendingMs(): number {
  const l = live.state?.latency;
  return (l && (l.measured_ms ?? l.known_ms)) ?? DEFAULT_PENDING_MS;
}

export function markPending(node: Element | null | undefined, style: "text" | "dots" = "dots"): void {
  if (!node) return;
  node.setAttribute("data-pending", style);
  const timer = timers.get(node);
  if (timer !== undefined) clearTimeout(timer);
  timers.set(node, window.setTimeout(() => node.removeAttribute("data-pending"), pendingMs()));
}
