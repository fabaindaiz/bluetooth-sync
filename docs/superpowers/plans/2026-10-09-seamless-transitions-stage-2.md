# Seamless transitions, stage 2 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** In `crossfade` mode, every chain change that today cuts because a stateful stage changes
(diffuse, bass, limiter of equal latency, EQ, decorrelator bank and on/off, ambience extractor knobs)
passes without a hole: the new instance of only that stage warms up in the shadow on the same input,
then the two are mixed with the fade weights, then only the new one remains.

**Architecture:** The `Transition` clock gains a WARM phase before FADE and gives the motor the
per-block weights. A small generic `Crossfaded(old, new, weights)` in `dsp/transition.py` stands in for a
stage object: it forwards each call to both instances and returns `old·w_old + new·w_new` (the new one
always runs, so it warms). The motor installs one where it used to swap the object at a cut's bottom, and
replaces it by `new` when the fade ends or a cut lands. Ramps and delay crossfades start when FADE
starts, so everything a change touches moves together.

**Tech Stack:** Python 3.12, numpy, hatch; the Rust extension follows engine switches for every new
instance by itself (`backend.register` is lazy and weak, experiment report 2026-10-09).

**Spec:** `docs/superpowers/specs/2026-10-08-seamless-transitions-design.md` §3 (WARM, `need_warm`, max
1 s), §4 (stage 2 rows, the `Crossfaded` seam), §5 stage 2, §7. Stage 1 plan and its status:
`docs/superpowers/plans/2026-10-08-seamless-transitions-stage-1.md`.

## Global Constraints

- New code and tests in English; inside `motor.py` (Spanish) keep its language; logic that can live in
  `dsp/transition.py` goes there.
- No commits (repository rule); the controller snapshots for review. All test runs with `nice -n 19`
  (a live session plays on this machine; CLAUDE.md).
- The cut mode (`transition=cut`) stays byte-identical: `golden_motor.run` plays in cut mode and must
  keep matching.
- Weights: `fade_weights(shape, position, n, length)` as built in stage 1; stage 2 crossfades use the
  chain's `shape` (default `equal_gain`: two versions of the same music, correlated). Delay crossfades
  stay `equal_power`.
- WARM length = the longest memory of the changed stages, capped at 1 s (spec §3): EQ `TAPS-1` = 2047;
  decorrelator `len(filter)-1`; extractor `N_FFT + 10·SALTO` (≈ 7168); limiter its `_keep`; diffuse
  `predelay + rt60` samples; bass the longest FIR of the new stage. With only ramps/delays, WARM is 0
  (stage 1 unchanged).
- A limiter change that changes `latencia_limitador` (peak ↔ true_peak, another `lookahead_ms`) and a
  render change (stage 3) still cut, in either mode: `pide_corte` stays true for exactly those.
- During WARM and FADE `en_corte` is true (render_match, the monitor, the A/B and the recalibration
  loop hold, as in stage 1). Metrics during a transition report the new instance.
- A cut that lands during WARM or FADE resolves every `Crossfaded` to its `new` at the bottom.
- An engine switch during a transition changes nothing audible (both instances move their state).

## Review Focus

1. **A second change of the same stage while it is still crossfading** (dragging a knob): the
   collapse rule queues one pending batch; the pending batch must crossfade from the *new* instance,
   never stack a `Crossfaded` inside another. Pinned in Task 2.
2. **The bass stage's three call sites** (`feed` once per block, `before_delay` and `process` per speaker):
   all three must go through the same `Crossfaded`, or the feed and the speakers disagree. Pinned in Task 2.
3. **The EQ flag `ecualizacion_activa` and `direct`** (flat taps): turning the EQ on/off crossfades between
   flat and curve taps; the flag flips when the fade ends. Pinned in Task 3.
4. **The decorrelator bypass** (`decorrelacion_activa`) is a flag, not an object: the motor already computes
   both signals, so its weights mix them. Pinned in Task 4.
5. **The extractor feeds every speaker and the latency compensation**: a second extractor must have the
   same `latencia` (same `n_fft`), so the direct path stays aligned. Pinned in Task 5.

---

### Task 1: WARM in the clock, per-block weights, and `Crossfaded`

**Files:** Modify `host/src/aurasync/dsp/transition.py`; test `host/tests/test_transition.py`.

**Interfaces (produces):**
- `Transition.need_warm(samples: int) -> None`: during the starting actions (before the first block of
  the transition), raise the warm length to `min(samples, MAX_WARM)`; `MAX_WARM = 48000` (1 s at 48 kHz;
  take `sr` from the constructor, default 48000).
- States `IDLE`, `WARM`, `FADE`. `begin(length, actions=None)` starts in WARM when, after the starting
  actions ran, a warm length was asked; `advance(n)` moves WARM → FADE (returning the marker
  `Transition.FADE_STARTS` once, so the motor starts the ramps then) and FADE → IDLE as today.
- `block_weights(n: int, shape: str) -> tuple[float | np.ndarray, float | np.ndarray] | None`: for the
  block about to be processed: `(1.0, 0.0)` in WARM, `fade_weights(shape, pos, n, length)` in FADE, None
  in IDLE. Pure (does not advance).
- `class Crossfaded`: `Crossfaded(old, new, weights: Callable[[], tuple | None])`. Attribute access for a
  method returns a callable that calls the method on both and mixes array results `old·w_old + new·w_new`
  (both outputs must be the same length; `None` results stay `None`; non-array results come from `new`).
  Non-callable attributes read from `new`. `resolve() -> new`. It never nests: `Crossfaded(Crossfaded(a,b),
  c)` is refused (`ValueError`).

- [ ] **Step 1: Failing tests:** `test_warm_then_fade_then_idle` (need_warm(1000), length 3840: advance
  through WARM returns FADE_STARTS once, then the fade, then the pending batch); `test_need_warm_is_capped_at_one_second`;
  `test_block_weights_in_each_phase`; `test_crossfaded_mixes_arrays_and_reads_attributes_from_new`;
  `test_crossfaded_runs_new_during_warm` (a stateful fake counts calls on both); `test_crossfaded_refuses_nesting`;
  `test_no_warm_is_stage_1` (begin without need_warm goes straight to FADE).
- [ ] **Step 2: Run** `cd host && nice -n 19 hatch test tests/test_transition.py` → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** it and `hatch fmt --check` → PASS.

### Task 2: The motor crossfades whole stage objects: diffuse, bass, limiter

**Files:** Modify `host/src/aurasync/motor.py` (`aplicar_cadena`, `_clases_de_corte`, `pide_corte`,
`_empezar_transicion`, `_avanzar_transicion`, `_saltar`, `procesar`, `metricas_cadena`,
`actualizar_tipos`); test `host/tests/test_motor_transitions.py`.

**Interfaces:**
- Consumes Task 1.
- In crossfade mode, the classes `difusion`, `graves` and `limitador` (when `chain_stages.limiter_latency`
  does not change) no longer cut: `aplicar_cadena` builds the new object as today (outside the block) and
  asks `cambiar` for an action that installs `Crossfaded(old, new, self._pesos)` in place of
  `self._difusion` / `self._graves` / each `self._limitadores[name]`, and calls `need_warm` with that
  stage's memory (Global Constraints). `self._pesos` returns the block's weights computed once per block
  at its start (`Transition.block_weights(n, shape)`).
- `_empezar_transicion` splits: the starting actions run at the first block (they may ask for warm); the
  glides and delay crossfades start when FADE starts (immediately when there is no warm).
- When the transition goes IDLE, and at a cut's bottom, every `Crossfaded` is replaced by its `new`
  (`_resolver_cruces()`). A pending batch that changes the same stage starts from the resolved object.
- `pide_corte` / `_clases_de_corte`: in crossfade mode these classes stop asking for a cut (a limiter
  change with a different latency still does).
- `actualizar_tipos` (a speaker's kind changes the bass stage) goes through `cambiar`.
- Metrics during a transition read the new instance (`Crossfaded` forwards attributes to `new`); the
  limiter accounting (`isinstance(lim, TruePeakLimiter)`, `limitado is not x`) works through the wrapper.

- [ ] **Step 1: Failing tests** (default chain plus each stage on, noise ×0.05, limiter idle unless the
  test is about it): for each of diffuse (`rt60_s` change), bass (`cutoff_hz` change and `actualizar_tipos`),
  limiter (`ceiling`-independent cut-class change of equal latency):
  `test_<stage>_change_has_no_hole` (10 ms RMS never 1 dB under the smaller steady level),
  `test_<stage>_change_lands_like_a_cut` (after warm + fade + the stage's memory, ≤ 1e-9 against a motor that
  changed by a cut), `test_<stage>_change_in_cut_mode_still_cuts`. Plus
  `test_dragging_a_stage_knob_never_nests_crossfades` (20 changes of `rt60_s` during one transition),
  `test_a_cut_during_a_stage_warm_resolves_to_new`, `test_engine_switch_during_a_stage_crossfade_changes_nothing`
  (importorskip), `test_a_limiter_change_of_latency_still_cuts`, `test_ramps_start_with_the_fade_not_the_warm`.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4:** focused files, then the full suite
  (`cd host && nice -n 19 hatch test tests --deselect tests/test_radio_service.py::test_a_signal_restores_it`,
  in the background, output to the plan workspace) and `hatch fmt --check` → PASS.

### Task 3: The EQ

**Files:** `host/src/aurasync/motor.py` (`_cambiar_taps` callers: the `eq` class, `actualizar_ecualizacion`);
tests in `host/tests/test_motor_transitions.py`.

**Interfaces:** in crossfade mode an `eq`-class change or `actualizar_ecualizacion` builds a new
`eq.StreamingFIR(self._taps_de(p))` per speaker (with the new flag/curve) and installs
`Crossfaded(old_fir, new_fir, self._pesos)` in `self._ecualizador[name]`, warm `eq.TAPS - 1`;
`ecualizacion_activa` takes its new value at the start (so `_taps_de` builds the new taps) and the old
FIRs keep their old taps until resolved. `actualizar_ecualizacion` goes through `cambiar`. Cut mode as today.

- [ ] Tests: `test_eq_on_off_has_no_hole`, `test_eq_curve_change_lands_like_a_cut`,
  `test_calibration_eq_update_crossfades`, `test_eq_in_direct_stays_flat` (render `direct`: flat both sides).
- [ ] Implement; focused tests and fmt → PASS.

### Task 4: The decorrelator

**Files:** `host/src/aurasync/motor.py` (`banco` and `decorrelacion` classes, `_cambiar_banco`,
`_convolucionar`, `metricas_cadena`); tests in `host/tests/test_motor_transitions.py`.

**Interfaces:** a bank change builds the new filters and a new `StreamingFIR` per speaker; each
`self._decorreladores[name]` becomes `Crossfaded(old, new, self._pesos)`, warm `len(filter) - 1`; `_filtros`,
`_orden`, `_banco_actual`, `_aviso` take the new values at the start (metrics show the new). The bypass
flag: during a transition that flips `decorrelacion_activa`, the motor mixes `decorrelado` and the
undecorrelated signal with the block's weights (both are computed today), and the flag takes its value
when the fade ends; the bass feed's `atraso` follows the flag at the end too.

- [ ] Tests: `test_decorrelator_bank_change_has_no_hole`, `test_decorrelator_on_off_has_no_hole`,
  `test_decorrelator_change_lands_like_a_cut`, `test_metrics_show_the_new_bank_during_the_fade`.
- [ ] Implement; focused tests and fmt → PASS.

### Task 5: The ambience extractor

**Files:** `host/src/aurasync/motor.py` (`ambiente` class); tests in `host/tests/test_motor_transitions.py`.

**Interfaces:** an `ambiente`-class change builds `ambience.Extractor(new_params)` (same `n_fft`, so the same
`latencia`) and installs `Crossfaded(old, new, self._pesos)` in `self._extractor`, warm
`N_FFT + 10 * SALTO`. `procesar` (stereo in, mono ambience out) is mixed by the wrapper. `tiene_extractor`,
`latencia` and metrics read through it. The extractor's `reiniciar` on the wrapper resets both.

- [ ] Tests: `test_extractor_knob_change_has_no_hole`, `test_extractor_change_lands_like_a_cut` (after warm,
  fade and the forgetting time), `test_latency_is_unchanged_through_the_crossfade`.
- [ ] Implement; focused tests and fmt → PASS.

### Task 6: Service, presets and records

**Files:** `host/src/aurasync/service.py` (preset_load's stateful branch now crossfades when `pide_corte` is
false; A/B pair rule unchanged), `host/docs/control-api.md`, `docs/roadmap.md` (stage 2 → Hecho),
`docs/research/experimentos/23-…md` (what changed for the A/B final), the spec's §5 status line.

- [ ] Tests: `test_a_preset_with_an_eq_change_crossfades` (was "cuts once" in stage 1: update its docstring with
  why), `test_a_preset_with_a_latency_changing_limiter_still_cuts`, `test_ab_between_eq_presets_is_blind_and_crossfades`.
- [ ] Implement; full suite (background, `nice -n 19`) and `PY=python3 scripts/check.sh` in pieces → PASS.
