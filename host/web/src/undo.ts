// Undo instead of confirm() (research/11 §4.3: "never use a warning when you mean undo", Raskin).
//
// Two kinds of action get a "Deshacer" notice for UNDO_MS:
// - **done at once, undone by restoring** (load a preset, remove a speaker, apply a calibration):
//   the panel captures the artistic state before (`capture`), does it, and Deshacer sends what
//   puts it back (`restorePlan`, run by `restore`);
// - **done at the end** (forget a device, delete a preset, clear the EQ curves): nothing in the
//   contract brings them back, so the panel shows them done and sends the order when the notice
//   expires (`commit`); Deshacer just drops it. Leaving the page sends what was waiting.
//
// app.js (a classic script) reaches this through `window.aurasync.undo` (runtime.ts).

export const UNDO_MS = 10_000;

/** What a person chooses per speaker, and what the panel can write back with `set`. Not the EQ
 * curve (`eq_db`): the contract has no way to write it, so clearing it is a deferred action. */
export const SPEAKER_FIELDS = ["pan", "ambience", "gain_db", "delay_ms", "muted", "kind"] as const;
/** The same for the whole installation (`set` without a speaker). */
export const GLOBAL_FIELDS = ["rear_delay_ms", "extract_ambience", "decorrelate", "eq_active"] as const;
/** The chain's choices a preset does not hold (chain.PRESET_EXCLUDED_STAGES): never restored. */
export const CHAIN_EXCLUDED = new Set(["volume"]);

export type Command = { op: string } & Record<string, unknown>;

interface Chosen {
  algorithm?: string;
  params?: Record<string, unknown>;
  speakers?: Record<string, Record<string, unknown>>;
}

export interface SpeakerSnapshot {
  name: string;
  address: string | null;
  fields: Record<string, unknown>;
}

export interface Snapshot {
  speakers: SpeakerSnapshot[];
  global: Record<string, unknown>;
  /** Each stage's own choices (`chain` → `stages[].chosen`); null if the chain was not read. */
  chain: Record<string, Chosen> | null;
  /** The recalibration loop owns `delay_ms` while it runs (service.set_speaker refuses it). */
  loopActive: boolean;
}

/** The parts of `GET /v1/state` and of the `chain` reply this module reads. */
export interface StateLike {
  speakers: ({ name: string; address?: string | null } & Record<string, unknown>)[];
  global: Record<string, unknown>;
  recalibration?: { active?: boolean } | null;
}
export interface ChainLike {
  stages: { id: string; chosen?: Chosen | null }[];
}

// A null is "not set" (a speaker's `kind` before anyone chose it): `set` cannot write it back.
const pick = (from: Record<string, unknown>, keys: readonly string[]): Record<string, unknown> =>
  Object.fromEntries(keys.filter((k) => from[k] !== undefined && from[k] !== null).map((k) => [k, from[k]]));

const same = (a: unknown, b: unknown): boolean => JSON.stringify(a ?? null) === JSON.stringify(b ?? null);

export function capture(state: StateLike, chain: ChainLike | null): Snapshot {
  return {
    speakers: state.speakers.map((sp) => ({
      name: sp.name,
      address: sp.address ?? null,
      fields: pick(sp, SPEAKER_FIELDS),
    })),
    global: pick(state.global, GLOBAL_FIELDS),
    chain: chain ? Object.fromEntries(chain.stages.map((s) => [s.id, structuredClone(s.chosen ?? {})])) : null,
    loopActive: Boolean(state.recalibration?.active),
  };
}

/** The orders that take the installation from `now` back to `before`, in an order the service
 * accepts: a removed speaker first (the chain and `set` name it), then the chain, the globals,
 * and each speaker. Only what differs is sent. */
export function restorePlan(before: Snapshot, now: Snapshot): Command[] {
  const plan: Command[] = [];
  const present = new Set(now.speakers.map((s) => s.name));
  for (const sp of before.speakers) {
    if (!present.has(sp.name) && sp.address) plan.push({ op: "speaker_add", address: sp.address });
  }
  if (before.chain && now.chain) {
    const stages = new Set([...Object.keys(before.chain), ...Object.keys(now.chain)]);
    for (const stage of stages) {
      if (CHAIN_EXCLUDED.has(stage)) continue;
      const was = before.chain[stage] ?? {};
      if (same(was, now.chain[stage] ?? {})) continue;
      // Back to the defaults, then the choices it had: a choice that was not there must not stay.
      plan.push({ op: "chain_reset", stage });
      const global: Command = { op: "chain_set", stage };
      if (was.algorithm !== undefined) global["algorithm"] = was.algorithm;
      if (was.params && Object.keys(was.params).length) global["params"] = was.params;
      if ("algorithm" in global || "params" in global) plan.push(global);
      for (const [speaker, params] of Object.entries(was.speakers ?? {})) {
        if (Object.keys(params).length) plan.push({ op: "chain_set", stage, speaker, params });
      }
    }
  }
  const globals = Object.fromEntries(
    Object.entries(before.global).filter(([k, v]) => !same(v, now.global[k])),
  );
  if (Object.keys(globals).length) plan.push({ op: "set", changes: globals });
  const nowByName = new Map(now.speakers.map((s) => [s.name, s]));
  const perSpeaker: Command[] = [];
  let delays = false;
  for (const sp of before.speakers) {
    const current = nowByName.get(sp.name);
    // A speaker added back starts from the service's defaults: everything it had is written.
    const changes = Object.fromEntries(
      Object.entries(sp.fields).filter(([k, v]) => !current || !same(v, current.fields[k])),
    );
    if (!Object.keys(changes).length) continue;
    if ("delay_ms" in changes) delays = true;
    perSpeaker.push({ op: "set", speaker: sp.name, changes });
  }
  // The loop owns the delays while it runs: off, write them, on again (as calibration_apply does).
  const loop = delays && now.loopActive;
  if (loop) plan.push({ op: "set", changes: { recalibrate: false } });
  plan.push(...perSpeaker);
  if (loop) plan.push({ op: "set", changes: { recalibrate: true } });
  return plan;
}

// -- the notice -----------------------------------------------------------------------------

type Send = (message: Command) => Promise<{ ok: boolean; error?: { message: string } } | null>;

export interface Offer {
  /** What happened, in words: "Preset «cine» cargado". */
  message: string;
  /** Put it back (actions done at once). Resolves with what could not be undone, if anything. */
  undo?: () => Promise<string[]>;
  /** Do it now (actions done at the end): when the notice expires, or another one replaces it. */
  commit?: () => Promise<void>;
}

export interface Undo {
  offer(offer: Offer): void;
  /** Commits what is waiting and hides the notice (another action, leaving the page). */
  flush(): Promise<void>;
  /** For the tests: how long the notice stays (ms). */
  setDuration(ms: number): void;
  capture: typeof capture;
  restorePlan: typeof restorePlan;
  /** Runs `restorePlan(before, now)`; the orders the service refused, in words. */
  restore(before: Snapshot, now: Snapshot): Promise<string[]>;
}

/** Runs the orders one by one; one refused does not stop the rest. Returns the refusals. */
export async function runPlan(plan: Command[], send: Send): Promise<string[]> {
  const failed: string[] = [];
  for (const command of plan) {
    const reply = await send(command);
    if (!reply) failed.push(`${command.op}: sin conexión`);
    else if (!reply.ok) failed.push(`${command.op}: ${reply.error?.message ?? "rechazada"}`);
  }
  return failed;
}

export function createUndo(box: HTMLElement | null, send: Send): Undo {
  let duration = UNDO_MS;
  let current: { offer: Offer; timer: ReturnType<typeof setTimeout> } | null = null;
  let note: ReturnType<typeof setTimeout> | null = null;
  const text = box?.querySelector<HTMLElement>("[data-undo-text]") ?? null;
  const button = box?.querySelector<HTMLButtonElement>("[data-undo-button]") ?? null;
  const bar = box?.querySelector<HTMLElement>("[data-undo-bar]") ?? null;

  function show(message: string, withButton: boolean): void {
    if (!box || !text || !button) return;
    if (note !== null) clearTimeout(note);
    note = null;
    text.textContent = message;
    button.hidden = !withButton;
    button.disabled = false;
    box.hidden = false;
    if (bar) {
      // The time left, as a bar that empties (transform only: no layout per frame).
      bar.style.transition = "none";
      bar.style.transform = "scaleX(1)";
      void bar.offsetWidth;
      bar.style.transition = `transform ${duration}ms linear`;
      bar.style.transform = withButton ? "scaleX(0)" : "scaleX(1)";
    }
  }

  function hide(): void {
    if (box) box.hidden = true;
  }

  async function settle(): Promise<void> {
    const was = current;
    if (!was) return;
    current = null;
    clearTimeout(was.timer);
    hide();
    if (was.offer.commit) await was.offer.commit();
  }

  button?.addEventListener("click", async () => {
    const was = current;
    if (!was) return;
    current = null;
    clearTimeout(was.timer);
    button.disabled = true;
    const failed = was.offer.undo ? await was.offer.undo() : [];
    show(failed.length ? `Deshecho en parte; no se pudo: ${failed.join(" · ")}` : "Deshecho.", false);
    note = setTimeout(hide, failed.length ? 8000 : 3000);
  });
  // Leaving the page sends what was waiting: the person saw it done.
  globalThis.addEventListener?.("pagehide", () => void settle());

  const api: Undo = {
    offer(offer) {
      void settle();
      current = { offer, timer: setTimeout(() => void settle(), duration) };
      show(offer.message, true);
    },
    flush: settle,
    setDuration(ms) {
      duration = ms;
    },
    capture,
    restorePlan,
    restore: (before, now) => runPlan(restorePlan(before, now), send),
  };
  return api;
}
