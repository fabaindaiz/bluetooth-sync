// The Cadena screen (spec 2026-10-02 §7.1): generated entirely from the `chain` operation's
// reply. A stage added in Python appears here without touching this code: nothing below names
// a stage, an algorithm or a knob.
import { useEffect, useMemo, useRef, useState } from "preact/hooks";
import type { ChainDescription, ChainStageValue, ParamValue } from "../contract.gen.ts";
import { current, metrics as liveMetrics, quality as liveQuality, send, subscribe } from "../bridge.ts";
import { nf } from "../format.ts";
import { QualityStrip } from "./QualityStrip.tsx";
import { StageCard, type StageActions } from "./StageCard.tsx";

const REFRESH_MS = 300;
const REDUCED_EVERY_MS = 200;

/** Whether the screen is on view: its tab is shown and the page is visible. */
function onView(root: HTMLElement): boolean {
  if (document.hidden) return false;
  const view = root.closest<HTMLElement>("[data-view]");
  return !(view && view.hidden) && root.isConnected;
}

const reducedMotion = (): boolean =>
  typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;

/** Re-render on the bridge's updates, once per frame at most, and only while on view
 * (≤ 5 per second with reduced motion, as app.js's meters). */
function useLiveTick(root: HTMLElement): number {
  const [tick, setTick] = useState(0);
  useEffect(() => {
    let frame = 0;
    let last = 0;
    const unsubscribe = subscribe(() => {
      if (frame || !onView(root)) return;
      frame = requestAnimationFrame((now) => {
        frame = 0;
        if (!onView(root) || (reducedMotion() && now - last < REDUCED_EVERY_MS)) return;
        last = now;
        setTick((t) => t + 1);
      });
    });
    return () => {
      unsubscribe();
      if (frame) cancelAnimationFrame(frame);
    };
  }, [root]);
  return tick;
}

function jump(stageId: string): void {
  const card = document.getElementById(`stage-${stageId}`);
  if (!card) return;
  card.scrollIntoView({ block: "start", behavior: reducedMotion() ? "auto" : "smooth" });
  card.querySelector<HTMLElement>(".stage-title")?.focus({ preventScroll: true });
}

export function ChainScreen({ root }: { root: HTMLElement }) {
  useLiveTick(root);
  const [chain, setChain] = useState<ChainDescription | null>(null);
  const [failed, setFailed] = useState(false);
  const fetched = useRef<{ sequence: number | null; at: number; busy: boolean; again: boolean }>({
    sequence: null, at: Number.NEGATIVE_INFINITY, busy: false, again: false,
  });
  const timer = useRef<number | null>(null);
  const state = current().state;
  const sequence = state?.sequence ?? null;

  const refresh = useMemo(() => {
    const run = async (): Promise<void> => {
      const f = fetched.current;
      if (f.busy) {
        f.again = true;
        return;
      }
      f.busy = true;
      const seen = current().state?.sequence ?? null;
      const reply = await send("chain", {});
      f.busy = false;
      f.at = performance.now();
      if (reply && reply.ok) {
        f.sequence = seen;
        setChain(reply.result);
        setFailed(false);
      } else {
        setFailed(true);
      }
      if (f.again) {
        f.again = false;
        schedule();
      }
    };
    const schedule = (): void => {
      if (timer.current !== null) return;
      const wait = Math.max(0, REFRESH_MS - (performance.now() - fetched.current.at));
      timer.current = window.setTimeout(() => {
        timer.current = null;
        void run();
      }, wait);
    };
    return schedule;
  }, []);

  // Once at the start, so the screen exists before it is shown (a link to a knob, the tests'
  // `aurasyncShow`); then a change anywhere (`sequence`) is fetched again only while on view.
  // Also each time the screen comes into view: what it shows may have changed while hidden.
  const wasOnView = useRef(false);
  useEffect(() => {
    const visible = onView(root);
    const cameIntoView = visible && !wasOnView.current;
    wasOnView.current = visible;
    if ((chain === null && !document.hidden) || cameIntoView || (visible && sequence !== fetched.current.sequence)) refresh();
  });

  const patch = (stage: string, value: ChainStageValue): void => {
    setChain((c) => (c ? { ...c, stages: c.stages.map((s) => (s.id === stage ? { ...s, value } : s)) } : c));
  };

  const actions: StageActions = {
    chooseAlgorithm: (stage, algorithm) => {
      void send("chain_set", { stage, algorithm }).then((reply) => {
        if (reply?.ok) patch(stage, reply.result.value);
        refresh();
      });
    },
    setParam: (stage: string, param: string, value: ParamValue, speaker?: string) => {
      const args = speaker ? { stage, params: { [param]: value }, speaker } : { stage, params: { [param]: value } };
      void send("chain_set", args).then((reply) => {
        if (reply?.ok) patch(stage, reply.result.value);
        else refresh();
      });
    },
    resetParam: (stage: string, param: string, speaker?: string) => {
      const args = speaker ? { stage, param, speaker } : { stage, param };
      void send("chain_reset", args).then((reply) => {
        if (reply?.ok) patch(stage, reply.result.value);
        refresh();
      });
    },
  };

  const playing = state?.session.status === "playing";
  const metrics = liveMetrics();
  if (!chain) {
    return (
      <p class="muted small" role="status">
        {failed ? "No se pudo leer la cadena del servicio: se reintenta." : "Leyendo la cadena…"}
      </p>
    );
  }
  return (
    <div class="chain" data-latency={chain.latency_ms}>
      <QualityStrip quality={playing ? liveQuality() : null} playing={playing} />
      {/* The order of the stages, and a way to jump to one: on a phone the screen is long. */}
      <nav class="stage-flow" aria-label="Etapas, en el orden de proceso">
        <span class="flow-end">Entrada</span>
        {chain.stages.map((s) => (
          <span key={s.id} class="flow-step">
            <span aria-hidden="true">→</span>
            <button type="button" class="flow-stage" data-jump-to={s.id} onClick={() => jump(s.id)}>
              {s.title}
            </button>
          </span>
        ))}
        <span class="flow-step">
          <span aria-hidden="true">→</span>
          <span class="flow-end">Parlantes</span>
        </span>
        <span class="flow-note muted small">la cadena agrega {nf(chain.latency_ms, 1)} ms, igual en todos</span>
      </nav>
      <ol class="stage-list">
        {chain.stages.map((s, i) => (
          <li key={s.id} class="stage-item">
            {i > 0 && (
              <span class="stage-arrow" aria-hidden="true">
                ↓
              </span>
            )}
            <StageCard stage={s} index={i} metrics={metrics?.[s.id] ?? null} live={playing} actions={actions} />
          </li>
        ))}
      </ol>
    </div>
  );
}
