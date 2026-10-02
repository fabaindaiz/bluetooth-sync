// One knob, drawn from its descriptor alone: a slider with its number and unit for `float` and
// `int`, a checkbox for `bool`, buttons or a list for `choice`. A ↺ when it is not at its
// default (it calls `chain_reset`), (?) with the help, and how a change is applied.
import { useRef, useState } from "preact/hooks";
import type { ChainParam, ParamValue } from "../contract.gen.ts";
import { markPending } from "../bridge.ts";
import { APPLY_HELP, APPLY_LABELS, choiceLabel, digitsOf, formatParam, sameValue, snap } from "../format.ts";
import { throttle, type Throttled } from "../sender.ts";

export interface ParamProps {
  stageId: string;
  param: ChainParam;
  value: ParamValue | undefined;
  /** The speaker of a per-speaker knob: the cell of the table. */
  speaker?: string | undefined;
  /** In the speakers' table: no title, no help, no number field. */
  compact?: boolean;
  set: (value: ParamValue) => void;
  reset: () => void;
}

const EDIT_GRACE_MS = 1500;

export function ApplyBadge({ apply }: { apply: ChainParam["apply"] }) {
  return (
    <span class="apply-badge" data-apply={apply} title={APPLY_HELP[apply]}>
      {APPLY_LABELS[apply]}
    </span>
  );
}

export function Help({ id, title, text }: { id: string; title: string; text: string }) {
  if (!text) return null;
  return (
    <span class="help">
      <button type="button" class="help-btn" aria-label={`Ayuda: ${title}`} aria-describedby={id}>
        ?
      </button>
      <span id={id} role="tooltip" class="help-pop">
        {text}
      </span>
    </span>
  );
}

export function ParamControl(props: ParamProps) {
  const { param, compact, speaker, stageId } = props;
  const label = speaker ? `${param.title} de ${speaker}` : param.title;
  const key = `${stageId}-${param.id}${speaker ? `-${speaker}` : ""}`.replace(/[^\w-]/g, "_");
  const control =
    param.kind === "bool" ? (
      <BoolParam {...props} label={label} />
    ) : param.kind === "choice" ? (
      <ChoiceParam {...props} label={label} />
    ) : (
      <NumberParam {...props} label={label} />
    );
  return (
    <div class={compact ? "param param-compact" : "param"} data-param={param.id} data-speaker={speaker}>
      {!compact && (
        <div class="param-head">
          <span class="param-title">
            {param.title} <ApplyBadge apply={param.apply} />
          </span>
          <Help id={`help-${key}`} title={param.title} text={param.help} />
        </div>
      )}
      {control}
      {!compact && param.summary && <p class="setting-desc">{param.summary}</p>}
    </div>
  );
}

function ResetButton({ param, label, visible, onReset }: { param: ChainParam; label: string; visible: boolean; onReset: () => void }) {
  const back = formatParam(param, param.default);
  return (
    <button
      type="button"
      class={`reset-btn${visible ? "" : " invisible"}`}
      aria-label={`${label}: volver a ${back}`}
      title={`Volver a ${back} (el valor por defecto)`}
      tabIndex={visible ? 0 : -1}
      onClick={onReset}
    >
      ↺
    </button>
  );
}

/** While the person moves a control, it shows their value, not the last one the service sent. */
function useDraft<T>(server: T | undefined): [T | undefined, (value: T, ms?: number) => void] {
  const [draft, setDraft] = useState<{ value: T; until: number } | null>(null);
  const shown = draft && Date.now() < draft.until ? draft.value : server;
  const hold = (value: T, ms = EDIT_GRACE_MS): void => setDraft({ value, until: Date.now() + ms });
  return [shown, hold];
}

function NumberParam(props: ParamProps & { label: string }) {
  const { param, value, label, compact } = props;
  const out = useRef<HTMLOutputElement>(null);
  const [shown, hold] = useDraft(typeof value === "number" ? value : undefined);
  const latest = useRef(props);
  latest.current = props;
  const sender = useRef<Throttled<number> | null>(null);
  if (!sender.current) {
    sender.current = throttle<number>((v) => {
      markPending(out.current);
      latest.current.set(v);
    });
  }
  const digits = param.kind === "int" ? 0 : digitsOf(param.step);
  const current = shown ?? (typeof param.default === "number" ? param.default : 0);
  const fromEvent = (e: Event): number => snap(param, Number((e.currentTarget as HTMLInputElement).value));
  const atDefault = sameValue(current, param.default);
  return (
    <div class="param-row">
      <input
        type="range"
        class="param-range"
        min={param.low ?? undefined}
        max={param.high ?? undefined}
        step={param.step ?? "any"}
        value={current}
        aria-label={label}
        aria-valuetext={formatParam(param, current)}
        onInput={(e) => {
          const v = fromEvent(e);
          hold(v);
          sender.current?.input(v);
        }}
        onChange={(e) => {
          const v = fromEvent(e);
          hold(v);
          sender.current?.commit(v);
        }}
      />
      {!compact && (
        <input
          type="number"
          class="num-input param-number"
          min={param.low ?? undefined}
          max={param.high ?? undefined}
          step={param.step ?? "any"}
          value={current.toFixed(digits)}
          aria-label={`${label}, valor`}
          onFocus={() => hold(current, 600_000)}
          onBlur={() => hold(current)}
          onChange={(e) => {
            const raw = Number((e.currentTarget as HTMLInputElement).value);
            if (!Number.isFinite(raw)) return;
            const v = snap(param, raw);
            hold(v);
            sender.current?.commit(v);
          }}
        />
      )}
      <output ref={out} class="param-out num" data-pending-style="dots">
        {compact ? formatParam(param, current) : param.unit}
      </output>
      <ResetButton param={param} label={label} visible={!atDefault} onReset={() => {
        hold(param.default as number);
        markPending(out.current);
        props.reset();
      }} />
    </div>
  );
}

function BoolParam(props: ParamProps & { label: string }) {
  const { param, value, label, compact } = props;
  const box = useRef<HTMLLabelElement>(null);
  const [shown, hold] = useDraft(typeof value === "boolean" ? value : undefined);
  const checked = shown ?? Boolean(param.default);
  return (
    <div class="param-row">
      <label ref={box} class="check toggle-check" data-pending-style="dots">
        <input
          type="checkbox"
          checked={checked}
          aria-label={compact ? label : undefined}
          onChange={(e) => {
            const v = (e.currentTarget as HTMLInputElement).checked;
            hold(v);
            markPending(box.current);
            props.set(v);
          }}
        />
        {compact ? (checked ? "sí" : "no") : param.title}
      </label>
      <ResetButton param={param} label={label} visible={!sameValue(checked, param.default)} onReset={() => {
        hold(Boolean(param.default));
        props.reset();
      }} />
    </div>
  );
}

function ChoiceParam(props: ParamProps & { label: string }) {
  const { param, value, label } = props;
  const group = useRef<HTMLDivElement>(null);
  const [shown, hold] = useDraft(typeof value === "string" ? value : undefined);
  const current = shown ?? String(param.default);
  const choose = (v: string): void => {
    hold(v);
    markPending(group.current);
    props.set(v);
  };
  const reset = (
    <ResetButton param={param} label={label} visible={current !== param.default} onReset={() => {
      hold(String(param.default));
      props.reset();
    }} />
  );
  if (param.choices.length > 4) {
    return (
      <div class="param-row" ref={group}>
        <select class="input" aria-label={label} value={current} onChange={(e) => choose((e.currentTarget as HTMLSelectElement).value)}>
          {param.choices.map((c) => (
            <option value={c} key={c}>
              {choiceLabel(c)}
            </option>
          ))}
        </select>
        {reset}
      </div>
    );
  }
  return (
    <div class="param-row">
      <div class="segmented" role="group" aria-label={label} ref={group}>
        {param.choices.map((c) => (
          <button type="button" class="seg" key={c} aria-pressed={c === current} onClick={() => choose(c)}>
            {choiceLabel(c)}
          </button>
        ))}
      </div>
      {reset}
    </div>
  );
}
