// One stage of the chain, drawn from its descriptor: what it does, its algorithms (each with
// its summary, cost and latency; the unavailable ones disabled with the reason), the knobs of
// the chosen one, the per-speaker knobs as a table, and its live numbers.
import { useState } from "preact/hooks";
import type { ChainAlgorithm, ChainParam, ChainStage, ParamValue, StageMetrics } from "../contract.gen.ts";
import { APPLY_HELP, APPLY_LABELS, formatMetric, formatMetricScalar, metricLabel, nf, sameValue } from "../format.ts";
import { Help, ParamControl } from "./ParamControl.tsx";

export interface StageActions {
  chooseAlgorithm: (stage: string, algorithm: string) => void;
  setParam: (stage: string, param: string, value: ParamValue, speaker?: string) => void;
  resetParam: (stage: string, param: string, speaker?: string) => void;
}

interface Props {
  stage: ChainStage;
  index: number;
  metrics: StageMetrics | null;
  live: boolean;
  actions: StageActions;
}

const short = (name: string): string => name.replace(/^JBL /, "");

function algorithmMeta(a: ChainAlgorithm, isDefault: boolean): string {
  const parts = [];
  if (a.cost) parts.push(`costo ${a.cost}`);
  parts.push(a.latency_ms > 0 ? `latencia ${nf(a.latency_ms, 1)} ms` : "sin latencia");
  if (isDefault) parts.push("por defecto");
  if (!a.implemented) parts.push("todavía no corre");
  return parts.join(" · ");
}

export function StageCard({ stage, index, metrics, live, actions }: Props) {
  const current = stage.algorithms.find((a) => a.id === stage.value.algorithm) ?? stage.algorithms[0];
  if (!current) return null;
  const globals = current.params.filter((p) => p.scope === "global");
  const perSpeaker = current.params.filter((p) => p.scope === "speaker");
  const titleId = `stage-${stage.id}-title`;
  return (
    <section id={`stage-${stage.id}`} class="stage-card" data-stage={stage.id} aria-labelledby={titleId}>
      <div class="stage-head">
        <h3 id={titleId} class="stage-title" tabIndex={-1}>
          <span class="stage-n" aria-hidden="true">
            {index + 1}
          </span>
          {stage.title}
        </h3>
        <span class="chip" title="Latencia que agrega esta etapa, igual en todos los parlantes">
          {current.latency_ms > 0 ? `+${nf(current.latency_ms, 1)} ms` : "0 ms"}
        </span>
        {stage.pending && (
          <span class="chip chip-warn" title="El motor todavía no corre lo elegido: suena como el valor por defecto">
            elegido, todavía no corre
          </span>
        )}
        <Help id={`help-stage-${stage.id}`} title={stage.title} text={stage.help} />
      </div>
      <p class="setting-desc">{stage.summary}</p>

      {stage.algorithms.length > 1 ? (
        <fieldset class="algo-list">
          <legend class="algo-legend">
            Algoritmo{" "}
            <span class="apply-badge" data-apply={stage.algorithm_apply} title={APPLY_HELP[stage.algorithm_apply]}>
              {APPLY_LABELS[stage.algorithm_apply]}
            </span>
          </legend>
          {stage.algorithms.map((a) => (
            <label
              key={a.id}
              class="algo-option"
              data-algorithm={a.id}
              data-current={a.id === current.id ? "1" : undefined}
              aria-disabled={a.available ? undefined : "true"}
            >
              <input
                type="radio"
                name={`algo-${stage.id}`}
                value={a.id}
                checked={a.id === current.id}
                disabled={!a.available}
                onChange={() => actions.chooseAlgorithm(stage.id, a.id)}
              />
              <span class="algo-text">
                <b>{a.title}</b>
                <small>{a.summary}</small>
                <small class="algo-meta">{algorithmMeta(a, a.id === stage.default_algorithm)}</small>
                {!a.available && <small class="algo-why">No disponible: {a.unavailable_reason}</small>}
              </span>
            </label>
          ))}
        </fieldset>
      ) : (
        <p class="algo-single">
          <b>{current.title}</b> · <span class="muted">{algorithmMeta(current, true)}</span>
        </p>
      )}

      {globals.length > 0 ? (
        <Knobs key={current.id} stage={stage} algorithm={current} params={globals} actions={actions} />
      ) : (
        current.help && (
          <div class="param-group-head">
            <span class="param-group-title">Sobre «{current.title}»</span>
            <Help id={`help-algo-${stage.id}`} title={current.title} text={current.help} />
          </div>
        )
      )}
      {perSpeaker.length > 0 && <SpeakerTable stage={stage} params={perSpeaker} actions={actions} />}
      <Metrics stage={stage} metrics={metrics} live={live} />
    </section>
  );
}

/**
 * The chosen algorithm's own knobs, behind a disclosure (research/11 §4.3: two levels at most):
 * eight stages with every knob open made a phone page of ~7 800 px. Open from the start when one
 * of them is off its default, so a change is never hidden; the summary says how many.
 */
function Knobs({ stage, algorithm, params, actions }: { stage: ChainStage; algorithm: ChainAlgorithm; params: ChainParam[]; actions: StageActions }) {
  const changed = params.filter((p) => !sameValue(stage.value.params[p.id], p.default)).length;
  // Until the person opens or closes it, it follows whether something is changed.
  const [chosen, setChosen] = useState<boolean | null>(null);
  const open = chosen ?? changed > 0;
  return (
    <details
      class="knobs"
      open={open}
      onToggle={(e) => {
        const now = (e.currentTarget as HTMLDetailsElement).open;
        if (now !== open) setChosen(now);
      }}
    >
      <summary class="knobs-summary">
        Perillas de «{algorithm.title}»{" "}
        <span class="knobs-count">
          {params.length}
          {changed > 0 ? ` · ${changed} fuera de su valor por defecto` : ""}
        </span>
      </summary>
      {algorithm.help && <p class="setting-desc mt-2">{algorithm.help}</p>}
      <div class="param-grid">
        {params.map((p) => (
          <ParamControl
            key={p.id}
            stageId={stage.id}
            param={p}
            value={stage.value.params[p.id]}
            set={(v) => actions.setParam(stage.id, p.id, v)}
            reset={() => actions.resetParam(stage.id, p.id)}
          />
        ))}
      </div>
    </details>
  );
}

/** From this many speakers each speaker's block starts folded on a phone (experimentos/16 §7). */
export const FOLD_FROM = 4;

/** Columns = speakers on a PC; one block per speaker on a phone (the CSS turns it). With
 * FOLD_FROM speakers or more, each block on a phone shows its name and whether something
 * differs from the default, and opens with a tap; on a PC the columns are always open. */
function SpeakerTable({ stage, params, actions }: { stage: ChainStage; params: ChainParam[]; actions: StageActions }) {
  const speakers = Object.keys(stage.value.speakers);
  const [open, setOpen] = useState<ReadonlySet<string>>(new Set());
  if (!speakers.length) return <p class="muted small mt-2">Sin parlantes en la instalación.</p>;
  const many = speakers.length >= FOLD_FROM;
  const toggle = (name: string): void => {
    const next = new Set(open);
    if (next.has(name)) next.delete(name);
    else next.add(name);
    setOpen(next);
  };
  return (
    <div
      class="spk-grid"
      role="group"
      aria-label={`Por parlante: ${params.map((p) => p.title).join(", ")}`}
      style={{
        gridTemplateRows: `repeat(${params.length + 1}, auto)`,
        gridTemplateColumns: `auto repeat(${speakers.length}, minmax(0, 1fr))`,
      }}
    >
      <div class="spk-col spk-labels" aria-hidden="true">
        <div class="spk-head">Por parlante</div>
        {params.map((p) => (
          <div class="spk-label" key={p.id}>
            {p.title}{" "}
            <span class="apply-badge" data-apply={p.apply}>
              {APPLY_LABELS[p.apply]}
            </span>
          </div>
        ))}
      </div>
      {speakers.map((name) => {
        const folded = many && !open.has(name);
        // Only the chain's own knobs: pan or ambience live in the installation, each speaker its own.
        const changed = params.some(
          (p) => p.store === "chain" && !sameValue(stage.value.speakers[name]?.[p.id], p.default),
        );
        return (
        <div class="spk-col" key={name} data-speaker-col={name} data-folded={folded ? "" : undefined}>
          {many ? (
            <button
              type="button"
              class="spk-head spk-toggle"
              title={name}
              aria-expanded={!folded}
              onClick={() => toggle(name)}
            >
              <span class="spk-toggle-mark" aria-hidden="true">{folded ? "▸" : "▾"}</span>
              <span class="truncate">{short(name)}</span>
              {changed && <span class="spk-changed">cambiado</span>}
            </button>
          ) : (
            <div class="spk-head" title={name}>
              {short(name)}
            </div>
          )}
          {params.map((p) => (
            <div class="spk-cell" key={p.id}>
              <span class="spk-cell-label">{p.title}</span>
              <ParamControl
                stageId={stage.id}
                param={p}
                speaker={name}
                compact
                value={stage.value.speakers[name]?.[p.id]}
                set={(v) => actions.setParam(stage.id, p.id, v, name)}
                reset={() => actions.resetParam(stage.id, p.id, name)}
              />
            </div>
          ))}
        </div>
        );
      })}
    </div>
  );
}

/** Metrics drawn as bars per speaker, with their full scale. The number goes beside it. */
const BAR_KEYS: Record<string, number> = { reduction_db: 12, active_pct: 100 };

function Metrics({ stage, metrics, live }: { stage: ChainStage; metrics: StageMetrics | null; live: boolean }) {
  if (!live) {
    return <p class="stage-metrics muted small">En vivo: sin datos (se miden mientras suena una sesión).</p>;
  }
  if (!metrics) return <p class="stage-metrics muted small">En vivo: esperando la primera medición…</p>;
  const entries = Object.entries(metrics).filter(([k]) => k !== "pending");
  return (
    <div class="stage-metrics" aria-label={`${stage.title}: en vivo`} role="group">
      <span class="metrics-title">En vivo</span>
      {metrics.pending && <span class="small muted"> · suena como el valor por defecto</span>}
      <dl class="metric-list">
        {entries.map(([key, value]) => {
          const full = BAR_KEYS[key];
          if (full !== undefined && value !== null && typeof value === "object") {
            return (
              <div class="metric metric-bars" key={key} data-metric={key}>
                <dt>{metricLabel(key)}</dt>
                <dd>
                  {Object.entries(value).map(([name, v]) => {
                    const pct = Math.max(0, Math.min(1, (v ?? 0) / full));
                    return (
                      <span class="metric-bar-row" key={name}>
                        <span class="metric-bar-name">{short(name)}</span>
                        <span class="metric-bar" aria-hidden="true">
                          <span class="metric-bar-fill" style={{ transform: `scaleX(${pct.toFixed(3)})` }} />
                        </span>
                        <span class="metric-bar-value num">{formatMetricScalar(key, v)}</span>
                      </span>
                    );
                  })}
                </dd>
              </div>
            );
          }
          return (
            <div class="metric" key={key} data-metric={key}>
              <dt>{metricLabel(key)}</dt>
              <dd class="num">{formatMetric(key, value)}</dd>
            </div>
          );
        })}
      </dl>
    </div>
  );
}
