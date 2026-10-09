# Seamless transitions, stage 1 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Engine switches without a cut, and a `crossfade` transition mode (default) that moves pan,
ambience, gain, the ambience mix, the A/B compensation, the render makeup and the delays to new values
without the 80 + 80 ms hole, chosen by a new chain stage `transition` (`crossfade` | `cut`).

**Architecture:** A new `dsp/transition.py` holds the clock (state, pending actions, fade weights).
`Motor` keeps `FadeGate` for the cut mode and gains a thin `cambiar(accion)` that picks the mode. In
crossfade mode the action runs at once, every smoother glides to its target in exactly `fade_ms`, and
each delay line reads its history at the old and the new position and mixes them. The service calls
`cambiar` where it called `cortar` for presets, the A/B, calibrations and slow delays.

**Tech Stack:** Python 3.12+, numpy, hatch (`hatch test`, `hatch fmt`), the PyO3 extension for the
engine tests (`hatch run engine-build`).

**Spec:** `docs/superpowers/specs/2026-10-08-seamless-transitions-design.md` (§2, §3, §4 rows of
stage 1, §5 stage 1, §7). Stages 2–4 get their own plans after stage 1 is listened to.

## Global Constraints

- New modules, identifiers, docstrings and tests are in **English**; inside an existing Spanish file
  (`motor.py`, `retardo.py`, `service.py` comments) new code keeps the file's language, and new logic
  goes to the new English module where it can (CLAUDE.md, d-7c8794-7b3093).
- Work on the branch `seamless-transitions`. **No commits**: this repository commits only when the user
  asks (CLAUDE.md); each task ends with its tests and `hatch fmt --check` passing.
- The gate before calling the stage done: `PY=python3 scripts/check.sh` from the repository root.
- Defaults pinned by the spec: `fade_ms` 80 (range 10–500, step 10, unit `ms`), `shape` `equal_gain`
  (choices `equal_gain`, `equal_power`), the stage's default algorithm `crossfade`.
- `equal_gain`: old weight `cos²(πt/2)`, new `sin²(πt/2)` (sum 1). `equal_power`: `cos(πt/2)`,
  `sin(πt/2)` (squares sum 1). `t` runs 0 → 1 over the fade.
- The `transition` stage is **not** carried by presets (`chain.PRESET_EXCLUDED_STAGES`).
- Requests in a row collapse: during a transition, every new request joins **one** pending transition
  that runs all their actions, in order, when the current one ends.
- A request before the transition's first processed block joins that transition (so `ab_play`'s
  compensation joins its own preset's transition).
- The cut mode and a Rust failure behave exactly as today.
- Never `pkill -f`/`pgrep -f`; never touch the user's WH-CH520 or PipeWire without asking (CLAUDE.md).

## Review Focus

1. **A cut requested while a crossfade runs** (a Rust failure, the cushion, an output swap): the bottom
   must run the pending actions, end every delay crossfade (`saltar_a`) and leave no transition busy.
   Pinned in Task 5 (`test_a_cut_during_a_transition_lands_everything_at_the_bottom`).
2. **A preset that also changes a stateful stage** (another EQ, limiter type) in crossfade mode: the
   whole preset must go through the one cut, not half crossfaded and half cut. Pinned in Task 6
   (`test_a_preset_with_a_stateful_change_cuts_once`).
3. **Zero-length changes** (a preset equal to what plays, an A/B between equal presets): still a
   transition, so the A/B stays blind. Pinned in Task 6 (`test_ab_play_between_equal_presets_requests_a_transition`).
4. **A delay change larger than the line's history** (calibration of several hundred ms): the new read
   must clamp like `saltar_a` does, never read outside the history. Pinned in Task 3
   (`test_crossfade_to_clamps_to_the_maximum`).
5. **The engine switch during a transition:** the stages move state exactly, so nothing may restart.
   Pinned in Task 5 (`test_hot_switch_during_a_crossfade_changes_nothing`).

---

### Task 1: The engine switches between blocks, without a cut

**Files:**
- Modify: `host/src/aurasync/service.py:2070-2090` (`engine_set`), docstring of `_apply_engine`
- Modify: `host/src/aurasync/dsp/backend.py` (module docstring, "**Switching**" paragraph; `use` docstring)
- Modify: `host/docs/control-api.md` (the `engine_set` description: "at the next block, no cut")
- Test: `host/tests/test_engine_backend.py` (replace `test_engine_set_switches_only_at_the_cut`)
- Test: `host/tests/test_motor_transitions.py` (new)

**Interfaces:**
- Produces: `Service.engine_set(engine) -> dict` switches at once while a session plays;
  `_engine_failed` keeps `motor.cortar(self._apply_engine)`.

- [ ] **Step 1: Write the failing tests.** In `test_engine_backend.py`, replace
  `test_engine_set_switches_only_at_the_cut` with `test_engine_set_switches_at_once_without_a_cut`
  (same fixture, `rust`): after `svc.engine_set("rust")`, `backend.active() == "rust"`,
  `motor.actions == []`, and `svc.snapshot["engine"]["active"] == "rust"` after `svc._publish()`;
  and back with `"numpy"`, again with `motor.actions == []`. Keep
  `test_switching_at_the_cut_changes_nothing_audible` as it is. In the new
  `test_motor_transitions.py`, add `test_a_hot_switch_on_the_full_chain_changes_nothing`
  (`pytest.importorskip("aurasync_engine")`): four speakers (pans −0.8, 0.8, −0.6, 0.6; delays 3.2,
  7.5, 11.0, 0.0 ms; `ecualizacion_db` a non-flat curve), chain with `spatial=front`,
  `diffuse=noise_tail`, `bass=protect`, `limiter=true_peak`, `ecualizar=True`, `semilla=1`, blocks of
  4096 of Gaussian noise ×0.2, 16 blocks. Run once never switching, once calling `backend.use("rust")`
  before block 5 and `backend.use("numpy")` before block 10 **without** `cortar`. Assert
  `max |diff| <= 1e-9` on every speaker.
- [ ] **Step 2: Run them.** `cd host && hatch test tests/test_engine_backend.py tests/test_motor_transitions.py -k "at_once or hot_switch"`.
  Expected: the service test FAILS (`motor.actions` has the cut); the motor test PASSES already
  (it measures the premise, MEASURED 6.5e-14 on 2026-10-08).
- [ ] **Step 3: Implement.** In `engine_set`, when `resolved.active != backend.active()`, call
  `self._apply_engine()` directly whether or not a motor plays (the order runs on the engine thread
  between blocks, `backend.py` module note). Leave `_engine_failed` on the cut. Update the docstrings
  and `control-api.md` to say the switch happens at the next block, and why it is safe (each stage
  moves its state exactly; spec 2026-10-08 §2).
- [ ] **Step 4: Run** `cd host && hatch test tests/test_engine_backend.py tests/test_motor_transitions.py && hatch fmt --check`. Expected: PASS.

### Task 2: Glides for the smoothers

**Files:**
- Modify: `host/src/aurasync/dsp/ramps.py` (`Smoothed`, `DecibelRamp`)
- Test: `host/tests/test_ramps.py`

**Interfaces:**
- Produces: `Smoothed.glide(samples: int) -> None` and `DecibelRamp.glide(samples: int) -> None`:
  from the next `block`, reach the current target in exactly `samples` samples, linearly; then the
  configured `rate` applies again. `jump()` also ends a glide. A new `target` set during a glide ends
  the glide (the normal rate takes over).

- [ ] **Step 1: Write the failing tests** in `test_ramps.py`:
  `test_glide_reaches_the_target_in_exactly_the_samples` (`Smoothed(0, rate=0.1)`, target 1.0,
  `glide(3840)`: the first 3839 values are < 1, value 3840 == 1.0, the steps are equal within 1e-12,
  across blocks of 1000);
  `test_glide_then_the_rate_is_the_configured_one_again` (after the glide, a new target moves at
  `rate`);
  `test_glide_to_the_same_value_is_settled_at_once`;
  `test_decibel_ramp_glides_in_db` (`DecibelRamp(-20, 30)` to 0 dB with `glide(4800)`: `block_db` is
  linear in dB and ends at 0.0 at sample 4800).
- [ ] **Step 2: Run** `cd host && hatch test tests/test_ramps.py -k glide`. Expected: FAIL (no `glide`).
- [ ] **Step 3: Implement** `glide` with a temporary per-sample step `|target − current| / samples`,
  kept separate from `rate` and cleared when settled, on `jump()` or on a new target.
- [ ] **Step 4: Run** `cd host && hatch test tests/test_ramps.py && hatch fmt --check`. Expected: PASS.

### Task 3: The clock and the weights (`dsp/transition.py`), and the delay line's two reads

**Files:**
- Create: `host/src/aurasync/dsp/transition.py`
- Modify: `host/src/aurasync/dsp/retardo.py` (`LineaDeRetardo`: Spanish file, Spanish names)
- Test: `host/tests/test_transition.py` (new), `host/tests/test_retardo.py`

**Interfaces:**
- Produces, in `dsp/transition.py`:
  - `SHAPES = ("equal_gain", "equal_power")`
  - `fade_weights(shape: str, position: int, n: int, length: int) -> tuple[np.ndarray, np.ndarray]`:
    the old and new weights for samples `position+1 … position+n` of a fade of `length`, clamped to
    the end (old 0, new 1 after `length`). It raises `ValueError` for an unknown shape.
  - `class Transition`: `busy: bool`; `started: bool` (True once a block was processed in it);
    `request(action: Callable[[], None] | None) -> bool`, which returns True when the caller must start
    a transition now (IDLE), and otherwise queues the action: it joins the current transition's
    starting actions if `not started`, or the single pending batch if started;
    `begin(length: int) -> None`; `advance(n: int) -> list[Callable[[], None]] | None`, which ends the
    fade when `length` samples have passed and returns the pending batch (possibly `[]`) that must
    start next, or None while still fading or idle; `take_starting() -> list[Callable]`;
    `cancel() -> list[Callable[[], None]]`, which ends everything and returns every queued action, in
    order.
- Produces, in `LineaDeRetardo` (the spec's `crossfade_to`, named in Spanish because the file is):
  `fundir_a(retardo_ms: float, muestras: int, forma: str) -> None` (from
  the next block, read at the old position and at the new one, clamped to `[0, maximo_ms]`, mixed with
  `fade_weights`; at the end only the new read remains, and `actual_ms == objetivo_ms == retardo_ms`),
  `fundiendo: bool`. `saltar_a` and `reiniciar` end a crossfade. The speed-limited ramp is not used
  during a crossfade (both reads stay fixed).

- [ ] **Step 1: Write the failing tests.** `test_transition.py`:
  `test_equal_gain_weights_sum_to_one`, `test_equal_power_squares_sum_to_one` (length 3840, blocks of
  1000, every sample, within 1e-12);
  `test_weights_are_clamped_after_the_end`;
  `test_unknown_shape_is_an_error`;
  `test_request_while_idle_starts_now`;
  `test_requests_before_the_first_block_join_the_transition` (`request` → True, `begin`, two more
  `request`s → False; `take_starting()` returns all three actions in order);
  `test_fifty_requests_during_a_fade_make_one_pending_batch` (after `advance(1)`, 50 requests; at
  the end `advance` returns a list of 50 actions, once, and the next end returns None);
  `test_cancel_returns_every_queued_action_and_idles`.
  `test_retardo.py`:
  `test_fundir_a_lands_on_the_new_delay` (a 48 kHz sine at 1 kHz, delay 10 → 40 ms over 3840
  samples: after the fade the output equals a line that jumped to 40 ms, within 1e-12);
  `test_fundir_a_never_bends_the_pitch` (during the fade each read is a fixed delay: the output equals
  `w_old·line_at_10 + w_new·line_at_40`, computed with two independent lines, within 1e-12);
  `test_crossfade_to_clamps_to_the_maximum` (asking 10 000 ms on a 250 ms line ends at 250 ms);
  `test_saltar_a_ends_a_crossfade`.
- [ ] **Step 2: Run** `cd host && hatch test tests/test_transition.py tests/test_retardo.py`. Expected: FAIL.
- [ ] **Step 3: Implement** `dsp/transition.py` (module docstring: what the clock is, the collapse rule,
  and that the cut mode stays on `FadeGate`; cite the spec), then `fundir_a` in `retardo.py` with a
  second position and a sample counter, mixing the two reads by `fade_weights`. With `sinc=True` both
  reads go through `interpolation.read` (one call per read).
- [ ] **Step 4: Run** `cd host && hatch test tests/test_transition.py tests/test_retardo.py tests/test_interpolation.py && hatch fmt --check`. Expected: PASS.

### Task 4: The `transition` chain stage

**Files:**
- Modify: `host/src/aurasync/chain.py` (a `_TRANSITION_STAGE`, last in `CHAIN`; `PRESET_EXCLUDED_STAGES`)
- Modify: `host/src/aurasync/motor.py` (`metricas_cadena`: the new stage's entry)
- Modify: `host/src/aurasync/contract_types.py` (only if a field needs typing; `StageMetrics` takes
  extra items)
- Modify: `host/docs/control-api.md` (the stage, its params, and that presets leave it out)
- Test: `host/tests/test_chain.py`, `host/tests/test_presets.py`, `host/tests/test_chain_service.py`

**Interfaces:**
- Produces: stage id `"transition"`, algorithms `"crossfade"` (params `fade_ms`: float, default 80,
  10–500, step 10, `ms`, `apply="live"`; `shape`: choice, `("equal_gain", "equal_power")`, default
  `"equal_gain"`, `apply="live"`) and `"cut"` (no params); `default_algorithm="crossfade"`;
  `algorithm_apply="live"`; latency 0. Panel copy in Spanish: title "Transiciones"; summaries saying
  that `crossfade` passes between settings without a hole and `cut` fades out and in (the fast mode).
  Metrics: `{"pending": …, "mode": <algorithm>, "busy": <bool>}`.

- [ ] **Step 1: Write the failing tests.** In `test_chain.py`, extend
  `test_the_stages_of_the_specs_in_processing_order` with `"transition"` last; add
  `test_the_transition_stage_defaults` (default algorithm `crossfade`, `fade_ms` 80 in [10, 500] step
  10, `shape` choices exactly `("equal_gain", "equal_power")`, both `apply == "live"`).
  In `test_presets.py`, add `test_presets_do_not_carry_the_transition_stage` (save a preset with
  `transition=cut`, change to `crossfade`, load it: the chain still says `crossfade`, and
  `presets-chain.json` has no `transition` key).
  In `test_chain_service.py`, add `test_changing_the_transition_mode_is_live` (`chain_set` of
  `transition` to `cut` returns no cut and requests nothing of the motor).
- [ ] **Step 2: Run** `cd host && hatch test tests/test_chain.py tests/test_presets.py tests/test_chain_service.py`. Expected: FAIL.
- [ ] **Step 3: Implement** the stage, the exclusion and the metrics entry (`busy` reads
  `self._transicion.busy` once Task 5 adds it; until then `False`). Make `aplicar_cadena` ignore the
  stage (no live action, no cut).
- [ ] **Step 4: Run** `cd host && hatch test && hatch fmt --check`. Expected: PASS, including the
  contract and panel tests that enumerate the stages (fix any list they pin).

### Task 5: `Motor.cambiar`: the crossfade mode in the engine

**Files:**
- Modify: `host/src/aurasync/motor.py` (Spanish: `__init__`, `procesar`, `_saltar`, `en_corte`,
  `actualizar_desde_control`, a new `cambiar` and `_empezar_transicion`, the gain ramp)
- Test: `host/tests/test_motor_transitions.py`

**Interfaces:**
- Consumes: `Transition`, `fade_weights` (Task 3), `Smoothed.glide` / `DecibelRamp.glide` (Task 2),
  `LineaDeRetardo.fundir_a` (Task 3), the stage `transition` (Task 4).
- Produces: `Motor.cambiar(accion: Callable[[], None] | None = None) -> None`: with
  `transition=cut`, exactly `cortar(accion)`; with `crossfade`, through `Transition`. At the start
  of a transition it runs the starting actions, then sets every target from the installation and
  glides over `fade_ms`: `_pan`, `_ambiente`, `_mezcla_ambiente`, `_compensacion`, `_makeup`, each
  speaker's gain (a per-speaker glide that replaces `velocidad_ganancia_db_s` until reached), and
  `fundir_a(efectivos[nombre], muestras, forma)` on each line whose effective delay changed.
  `en_corte` is `self._corte.busy or self._transicion.busy`. `procesar` calls
  `self._transicion.advance(n)` after the block and starts the returned batch, if any. `_saltar` (the
  cut's bottom) first runs `self._transicion.cancel()`'s actions, then jumps as today (its
  `saltar_a` ends any crossfade). `actualizar_desde_control` calls `cambiar()` where it called
  `cortar()`.

- [ ] **Step 1: Write the failing tests** in `test_motor_transitions.py` (default chain, two speakers,
  `ecualizar=False`, noise ×0.05 so the limiter never engages, blocks of 4096):
  `test_a_change_in_crossfade_mode_has_no_hole` (pan −0.7→0.4, gain −3→0 dB, delay 3→25 ms via
  `cambiar`: the 10 ms RMS of each speaker never drops more than 1 dB below the smaller of its steady
  levels before and after);
  `test_the_same_change_in_cut_mode_drops_to_silence` (`transition=cut`: some 10 ms window is below
  −60 dBFS);
  `test_a_crossfade_lands_where_a_cut_lands` (after the fade plus 1 s, equal within 1e-9 to a motor that
  did the same change through `cortar` on the same input);
  `test_requests_in_a_row_make_one_pending_transition` (50 `cambiar` during one fade: exactly two
  transitions in total, and the final targets are the last request's);
  `test_a_cut_during_a_transition_lands_everything_at_the_bottom` (`cambiar`, then `cortar` in the
  next block: after the bottom no line is `fundiendo`, `_transicion.busy` is False, every target holds);
  `test_hot_switch_during_a_crossfade_changes_nothing` (importorskip `aurasync_engine`: a crossfade with
  `backend.use("rust")` in its middle equals the same crossfade without it, within 1e-9);
  `test_slow_control_delay_goes_through_the_transition` (`actualizar_desde_control` with a 300 ms jump
  in crossfade mode: `en_corte` is True, and no output window falls below −60 dBFS).
- [ ] **Step 2: Run** `cd host && hatch test tests/test_motor_transitions.py`. Expected: FAIL.
- [ ] **Step 3: Implement** as in Interfaces, keeping `motor.py`'s Spanish (comments and new private
  names) and the clock logic in `dsp/transition.py`.
- [ ] **Step 4: Run** `cd host && hatch test && hatch fmt --check`. Expected: PASS (the whole suite:
  the existing cut tests run with the default `crossfade`, and every test that relied on a cut where
  `cambiar` now runs must still pass or be updated with the reason in its docstring).

### Task 6: The service through `cambiar`

**Files:**
- Modify: `host/src/aurasync/service.py` (`preset_load`, `ab_play`, `calibration_apply`, the sync
  suggestion's apply around line 1068)
- Modify: `host/docs/control-api.md` (presets, A/B and calibration: "a transition, by the chain's
  `transition` mode")
- Test: `host/tests/test_presets.py`, `host/tests/test_ab_loudness.py`, `host/tests/test_service.py`

**Interfaces:**
- Consumes: `Motor.cambiar` (Task 5), `Motor.aplicar_cadena(valores) -> "none" | "live" | "cut"`.
- Produces: `preset_load` in crossfade mode calls `motor.aplicar_cadena(values)` first (live changes
  now, stateful ones request their own cut). It then writes the speakers' fields through
  `motor.cortar(apply_fields)` when that returned `"cut"` (the same fade) and through
  `motor.cambiar(apply_fields)` otherwise. In cut mode the code path stays as today. `ab_play`'s
  compensation goes through `motor.cambiar`. `calibration_apply` and the suggestion's apply go through
  `motor.cambiar`.

- [ ] **Step 1: Write the failing tests:**
  `test_a_preset_of_pan_gain_and_delay_loads_without_a_cut` (crossfade mode, a real `Motor`:
  after `preset_load`, `motor._corte.busy` is False and `motor._transicion.busy` is True);
  `test_a_preset_with_a_stateful_change_cuts_once` (a preset that also changes `eq.max_boost_db`: one
  cut, the fields applied at its bottom, no transition);
  `test_ab_play_between_equal_presets_requests_a_transition` (`en_corte` True right after `ab_play`);
  `test_ab_play_compensation_joins_the_preset_transition` (one transition, not two);
  `test_calibration_apply_crossfades_the_delays` (crossfade mode: no cut, the lines are `fundiendo`).
- [ ] **Step 2: Run** `cd host && hatch test tests/test_presets.py tests/test_ab_loudness.py tests/test_service.py`. Expected: FAIL.
- [ ] **Step 3: Implement** as in Interfaces, keeping the comment about blindness (a transition is
  always requested).
- [ ] **Step 4: Run** `cd host && hatch test && hatch fmt --check`. Expected: PASS.

### Task 7: Records, and the gate

**Files:**
- Modify: `docs/research/experimentos/23-ensayo-ab-motor-y-ecualizacion-con-monitor.md` (§3: the spec
  and this plan, and the listening protocol of spec §7 for stage 1 as "pending")
- Modify: `docs/roadmap.md` (stages 2–4 pending; the two cuts of spec §6 with their way out; ids with
  `.agents/tools/bundle.py id i "…"`)
- Modify: `docs/decisions.md` (one decision: crossfade by default, the cut as the fast mode, the engine
  switch without a cut; id with `bundle.py id d "…"`)
- Modify: `.claude/logs/agent-changelog.md` (the session's entry, its format at the end of that file)

- [ ] **Step 1:** Write the four records, in Spanish, with the marks (MEDIDO for the 6.5e-14 and the
  listening of experiment 23; VERIFICADO for what was read in the code).
- [ ] **Step 2: Run the gate** from the repository root: `PY=python3 scripts/check.sh`. Expected: it
  ends without errors.
- [ ] **Step 3:** Hand back to the controller for the listening of spec §7 on HP-O16. This is not a
  subagent's job: the user listens, and the assistant asks before touching the headphones.

---

## Status on 2026-10-09 (wave verified)

Session s-7c8794-816f05, on the Mac. The full suite ran: 1905 passed, 4 failed. All 4 are macOS-only and fail without this branch too: `F_GETPIPE_SZ` and numpy's FFT rounding (roadmap i-7c8794-437907). The scoped re-review found items 1–10 below FIXED. For each Important item and for items 6 and 7, it built a mutant reverting the fix and saw the covering test fail.

The re-review also found one defect outside the wave, same class as the loop's pre-block read: the render match freezes only on `en_corte` after the block, so an 80 ms crossfade inside one block went unseen. It is fixed test-first in session.py: the pre-block `moving` read now also holds the match (`test_session.py::test_the_render_match_freezes_on_a_crossfade_inside_one_block`).

Left open:
- Item 6's residual of up to ~0.2 s, because the measurement is polled at 2 Hz (i-7c8794-ee3f38).
- The `shape` help text says it applies to stage-to-stage fades, but in stage 1 it has no effect. It is panel text, so the user decides it.
- Step 3, the listening (i-7c8794-93f50c).

## Status on 2026-10-08 (hand-off)

Tasks 1–7 complete, each reviewed (ledger kept locally in `.superpowers/sdd/2026-10-08-seamless-transitions-stage-1/progress.md`). The final whole-branch review asked for the fix wave below; it was dispatched on 2026-10-08 and its result is recorded in `.claude/logs/agent-changelog.md` (entry s-7c8794-402f44). The wave was stopped by the user's request during its full-suite run (1111 passed, 0 failed when interrupted): its fixes for all three Important findings and most minors are in the working tree, with tests. Still to do: finish the suite, one scoped re-review of the wave against this list, then record the result. Nothing is committed (repository rule); branch `seamless-transitions`.

### Final review findings — fix wave (one dispatch)

Fix ALL of these, each Important with its test. Spec: docs/superpowers/specs/2026-10-08-seamless-transitions-design.md. The reviewer's scratch reproductions: /tmp/claude-1000/-home-fadiaz-Desktop-Project-bluetooth-sync/d2684a5c-0f64-4611-8397-186d8e93e4d4/scratchpad/order.py and blind.py.

#### Important
1. A/B blindness with stateful differences (service.py ~1447 preset_load, ab_play ~1919-1936). `preset_load` chooses its path with `motor.pide_corte(values)` against the chain playing NOW, so if A and B differ in a stateful stage (e.g. `decorrelate.seed`), playing X after B crossfades when X is B and cuts when X is A — the hole reveals X. Spec §4 "Blindness of the A/B". Fix: in `ab_play`, decide once for the pair: `cut = motor.pide_corte(values_of(ab.a)) or motor.pide_corte(values_of(ab.b))` (values as preset_load computes them); if true, load through the cut path (e.g. a private `force_cut` parameter of preset_load) and send the compensation through `cortar` too. Tests: presets differing in `decorrelate.seed` cut on EVERY play of a, b and x; presets differing only in ramps always crossfade; the compensation joins in both cases (covers the deferred "stateful preset + A/B compensation untested").
2. Order: a `cambiar` asked after a pending cut runs before that cut's actions (motor.py ~800-804): the transition starts at the next block, the cut's actions at its bottom 1–2 blocks later, so the EARLIER request wins. Reproduced: `cortar(A.retardo_ms=10)` then `cambiar(A.retardo_ms=20)` ends at 10.0. Real case: a stateful preset_load or a Rust-failure cut followed by calibration_apply within ~170 ms. Fix: in `cambiar`, if the FadeGate is in its fade-out (`self._corte.state == FadeGate.OUT`), call `self.cortar(accion)` and return (it joins that bottom, in order). Narrow `corte_pendiente` to the OUT state too (during the fade-in a preset should not turn the gate around for a second dip). Test: the two-line ordering scenario ends at 20.0; a preset during the fade-in of a cut crossfades without a second dip.
3. Every crossfade is logged as a cut (session.py ~701-709): the session adds a "fade" event ("corte intencional") whenever `en_corte` rises, and `en_corte` now includes crossfades, so the cut log (evidence for the stage-1 listening, experiment 23) reports a cut for every preset/A-B/calibration in the default mode. Fix: add the `fade` event only when the motor's cut (FadeGate busy — expose what is needed) rises; keep `_last_fade` on `en_corte` (it correctly discards loop measurements during a transition). Optional: a non-fault kind `transition`. Tests: a crossfade preset adds no `fade` event; cut mode adds one; a loop measurement overlapping a crossfade is still discarded.

#### Minor (do all)
4. chain.py ~495-527: the `transition` stage help/cost text says render, EQ and filter-bank changes pass "sin hueco" and that it "calcula los dos ajustes a la vez" — false in stage 1 (those still cut; the only extra cost is one more sinc read per changed delay). Reword to stage 1's truth (Spanish).
5. host/docs/control-api.md ~88-90: "a short dip to silence" for a slow delay is stale under the default crossfade.
6. service.py ~243 `AB_SETTLE_S` (3.3 s) assumes the 0.16 s cut; with fade_ms up to 500 and one pending transition the loudness window can include ~0.7 s of transition. Fix: start the A/B measurement only once `en_corte` has cleared (or add 2×fade_ms); test.
7. motor.py ~842-844 with ~1101-1104: if a fade ends in the same block as a cut's bottom, with a pending batch and the mode switched to `cut`, `cortar` runs during the fade-in at position 0 → one extra silent block and an empty second bottom. Fix: run `_avanzar_transicion` after `_saltar` in that block, or run the batch inline when `saltar` is set; test.
8. Docstring lines over 120 chars with doubled parentheses: dsp/eq.py:30, dsp/spatial.py:36, dsp/ambience.py:34, dsp/backend.py:46 → "between blocks (`on_engine_switch`; a cut's bottom only after a Rust failure)", rewrapped.
9. Spec §3: add a one-line "Stage 1 as built" note (FADE only, no WARM yet; `Transition` beside `FadeGate`; API `request/begin/take_starting/advance/cancel`).
10. Records: docs/research/experimentos/23-…md lines ~17 and ~53-57 get a pointer "(así era hasta la etapa 1; ver §3: ahora el cambio de motor es entre bloques, sin corte)" — do not delete text; docs/decisions.md: mark the noise-test numbers once as "MEDIDO (tests con ruido, sin micrófono)"; docs/superpowers/specs/2026-10-05-rust-engine-scaffold-and-sinc-design.md lines ~40 and ~59: add "superseded by 2026-10-08-seamless-transitions-design.md §2" notes; docs/roadmap.md stage-4 figures marked "(diseño)", and a new Planificado entry for `tests/test_radio_service.py::test_a_signal_restores_it` hanging when pytest/check.sh run detached (id via `python3 .agents/tools/bundle.py id i "…"`).
