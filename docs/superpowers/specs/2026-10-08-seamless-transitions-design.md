# Seamless transitions: changes without the cut — design

**Date:** 2026-10-08 · **Status:** design approved in conversation on 2026-10-08 (the cut kept as the fast
mode, crossfade by default); reviewed interactively with the user on 2026-10-08, which added stage 4
(the cushion by stretching), collapsing requests, the knob left out of presets, and one fade length for
everything until it is measured. **Origin:**
experiment 23 (`docs/research/experimentos/23-…`): in a hidden engine-switch test on HP-O16 the user
heard every switch, "by the cuts". **Amends:** the live switch of
`2026-10-05-rust-engine-scaffold-and-sinc-design.md` §2 (switch at the cut's bottom).

## 1. What and why

Every change that is not a plain live ramp goes through `Motor.cortar`: the output fades to zero in
80 ms, the change jumps at the bottom, and it fades back in 80 ms (`dsp/ramps.FadeGate`). That keeps
any jump inaudible, but the 160 ms hole is itself audible, and the panel makes it frequent: a preset,
every A/B play, a chain knob of class `cut`, a render change, the engine switch, a calibration.

The user's requirements (2026-10-08):

- changes should sound like a smooth transition, without the hole;
- **as cheap as possible**: duplicate only the pieces that change, not the whole engine;
- **not an overly complex development**;
- both modes available: the cut stays as the fast mode, the seamless one is the default;
- the cushion refill, which today inserts silence, stretches the audio instead (review);
- the crossfade's shape and length are a knob of the chain;
- it covers presets and the A/B, the chain's knobs and the render; and the remaining cuts are
  explored (§6).

Not in scope: the output-level cuts of §6 (they keep the cut, with the reason and a way out).

## 2. Findings that shape the design (MEASURED / VERIFIED on 2026-10-08)

- **The engine switch needs no cut at all.** Every ported stage already moves its state exactly between
  numpy and Rust (`on_engine_switch`, `state()`/`set_state`; per-stage tests at ≤ 1e-9). A throwaway
  script ran a `Motor` with HP-O16's chain (front, diffuse `noise_tail`, bass `protect`, `true_peak`,
  EQ with a test curve, ambience, decorrelation; 4 speakers, blocks of 4096) and switched numpy → Rust
  → numpy **between blocks, without `cortar`**: the output differs from never switching by at most
  6.5e-14. The cut was only a precaution.
- **What a preset changes** (`Service.preset_load`, service.py:1405-1442): the chain values
  (`aplicar_cadena(…, en_corte=True)`), the speakers' fields in the shared `Instalacion` (pan,
  ambience, gain, delay, EQ curve) and the rear delay. Today those fields are written at the cut's
  bottom and `_saltar` jumps every smoother and delay line to them.
- **Most of a preset is already rampable.** Pan, ambience, gain, the ambience mix, the A/B
  compensation and the render makeup are `Smoothed` / `DecibelRamp`: the cut only makes them jump.
- **Delays are not rampable at speed** (the delay line moves at a limited speed, which bends pitch),
  but the line keeps its history: reading it at two positions costs one more sinc read.
- **Stateful stages** (EQ FIR, extractor STFT, decorrelator tails, limiter envelope, diffuse and bass
  convolutions, the spatial renderer) cannot take a new configuration without a jump; a new instance
  needs its memory filled first: EQ 2048 samples (43 ms), extractor 2048 (43 ms), decorrelator ~256,
  the others their own length.

## 3. The transition clock (next to the fade gate)

A new `dsp/transition.py` (English) with `Transition`, owned by the `Motor` **next to** `FadeGate`
(plan ruling, 2026-10-08: the cut mode keeps using `FadeGate` untouched, so its contract and tests do
not move; `en_corte` is true while either is busy). `Transition` adds the crossfade:

*Stage 1 as built:* only FADE (no WARM yet); `Transition` lives beside `FadeGate`, not in place of it,
and its API is `request/begin/take_starting/advance/cancel` (`dsp/transition.py`).

- `request(mode)`: `"cut"` behaves exactly like `FadeGate.request` today. `"crossfade"` starts
  **WARM → FADE → IDLE**.
- `block(n)` returns, per block, the output envelope (1.0 in crossfade mode), whether the cut's
  bottom falls after this block (cut mode only), and the **weight of the new** `w[n]` (0 during WARM,
  rising during FADE, 1 after).
- **Stage 2 as built (2026-10-09):** the ramps of a transition (pan, ambience, gain, mix, compensation, makeup)
  hold during WARM and glide with the FADE; volume and mute keep moving. A limiter crossfade always uses
  `equal_gain` whatever `shape` says (the two limiters see the same input; `equal_power` broke the ceiling by
  3 dB, MEASURED in review), as delay crossfades always use `equal_power`. A decorrelator crossfade (two banks,
  or dry vs decorrelated: nearly uncorrelated) always uses `equal_power` (`equal_gain` dipped 2.4–3.0 dB, MEASURED).
- **Stage 3 as built (2026-10-10):** a render switch in crossfade mode builds a whole new branch
  (`render_branch.RenderBranch`: the upmix, the decorrelators, the diffuse tail, the bass stage, the delay
  lines and the EQ), after every starting action (so it takes the curves, delays and bank they leave), warms
  it in the shadow (the sum of its stages' memories, capped at 1 s) and mixes the two branches per speaker
  before the gain; the extractor, the gain, the probe and the limiter stay shared. **Deviation from the
  plan:** the delay lines are per branch, not shared: a line has one input, and the two renders feed it
  different signals. **The shape is per pair, whatever `shape` says** (`motor.forma_render`): `equal_gain`
  between `spatial` and `front` (the same upmix: `equal_power` rose 2.0 dB mid-fade), `equal_power` for
  every other pair (`equal_gain` dipped up to 3.0 dB; `equal_power` ≤ 0.41 dB down, ≤ 0.20 dB up),
  MEASURED in `tests/test_motor_render_crossfade.py` (partly correlated noise, every effect on).
- **WARM** lasts the longest warm-up asked by the pieces that changed (`need_warm(samples)`, max 1 s;
  0 when only ramps and delays change).
- **Requests in a row collapse** (user, review 2026-10-08). A request during WARM joins the
  transition under way: its targets replace the pending ones, and the warm-up grows if it needs more.
  Requests during FADE collapse into **one** pending transition that holds the latest values and
  starts when this one ends. Dragging a slider of a stateful knob therefore never lags by more than
  one transition (~80–250 ms), however many changes arrive.
- **Shapes:** `equal_gain` (old `cos²`, new `sin²`; the two sum to 1, right for two versions of the
  same music, which are strongly correlated; default) and `equal_power` (`cos`, `sin`; right for
  uncorrelated signals, up to +3 dB in the middle with correlated ones).
- `busy` stays True from the request until IDLE, so `en_corte` keeps meaning "the output is not one
  steady configuration". The consumers that freeze during a cut (`render_match`, the monitor's
  loudness, the probe correlation) freeze during a transition too, with no change on their side.

**The knob.** A new chain stage `transition` ("Transiciones", last in `CHAIN`, no audio of its own)
with two algorithms: `crossfade` (default) and `cut`. Its params are `fade_ms` (default 80, range 0–500 since the listening of 2026-10-09 (user), was 10–500;
step 10) and `shape` (`choice`: `equal_gain` | `equal_power`). They are live params: they apply to the
next transition. The panel shows it like any stage. **Presets do not carry it** (user, review
2026-10-08): it is how one setting passes to another, not how a setting sounds, so it is left out of
`presets-chain.json` as `volume` is, and an A/B behaves the same in both directions. The default
length, 80 ms, is a cut's half today, so the two modes compare directly; the listening of stage 1
says whether it should be longer.

## 4. What each kind of change does in crossfade mode

The rule: **what can ramp, ramps over the fade; what has history, reads it twice; only what has
state is duplicated, and only for the transition.**

| Change | Mechanism | Duplicated during the transition | Stage |
|---|---|---|---|
| Engine numpy ↔ Rust | `backend.use` between blocks, no transition at all | nothing | 1 |
| Pan, ambience, gain, ambience mix, A/B compensation, render makeup | at the request, every smoother's rate (and the gain's own ramp, `_rampa_de_ganancia`) is set so it reaches the new target exactly at the fade's end (`Smoothed.glide(samples)`); no jump | nothing | 1 |
| Delay (preset, calibration apply, calibration suggestion, rear delay) | `LineaDeRetardo.crossfade_to(ms, weights)`: two reads of the same history, the old position and the new, mixed with `w`; then the old read stops. Same clock and length as the rest (user, review 2026-10-08); **a delay crossfade always uses `equal_power`, whatever `shape` says (MEASURED: `equal_gain` dips 2.5–3.2 dB on a pure delay move, `equal_power` stays within 0.8 dB); `shape` applies to the stage-2 crossfades**; mixing two delays is a comb filter while it lasts, and stage 1's listening measures it (§7) before any separate length is added | one sinc read per speaker | 1 |
| EQ curve or EQ knobs (`max_boost_db`, `budget_db`, `treble_cap_db`, on/off) | a new `StreamingFIR` per speaker, fed in the shadow during WARM, mixed with `w` in FADE | the EQ FIRs | 2 |
| Limiter type or `cut`-class knob | a new limiter per speaker, warmed and mixed the same way | the limiters | 2 |
| Diffuse, bass (algorithm or `cut`-class knob) | the new `DiffuseStage` / `BassStage`, warmed and mixed | that stage | 2 |
| Decorrelator on/off or bank | the new bank and tails, warmed and mixed | the decorrelator | 2 |
| Extractor knobs or on/off | a second `Extractor`, warmed and mixed | the extractor | 2 |
| Render (`classic` / `spatial` / `front` / `direct`) | the per-speaker path from the render on is run twice (old render, new render) and mixed; the makeup glides to the new render's | that path (most of the engine, only for this change) | 3 |
| Preset, A/B play | the sum of the rows above that the preset touches, all on the same clock | only what differs | 1–3 |

**The mixing seam (stages 2–3).** One small helper, `Crossfaded(old, new)`, holds two instances of a
stage. Through the transition it calls both with the same input, returns `old` during WARM and
`(1−w)·old + w·new` in FADE, and keeps only `new` at IDLE. The motor wraps the stage objects it
already keeps (`_ecualizador[name]`, `_limitadores[name]`, `_difusion`, `_graves`, the decorrelator,
the extractor, `espacial`) at the place where `aplicar_cadena` swaps them today (its `al_corte`
lambdas). It does not rebuild what did not change. The new instances are built where they are built
today, outside the block (`_nueva_difusion`, `_nuevos_graves`, `_nuevo_espacial`, the bank cache).
Each stage reports its warm-up length (`warm_samples`) to the clock.

**The shared installation.** In crossfade mode `preset_load` writes the speakers' fields at the
request, not at a bottom. Nothing jumps: the motor turns each into a glide (ramps), a two-read delay,
or a duplicated stage, as above. The fields are then already true for everything that reads them
(snapshot, presets, calibration).

**Blindness of the A/B.** `ab_play` always requests a transition, even between equal settings (today
it always cuts), so its timing never tells which preset X is. In stage 1 the path is decided once for the pair, not
against what plays now: if either preset needs a cut (a stateful change), every play of A, B and X cuts,
and the A/B compensation joins that cut; otherwise every play crossfades.

**Engine failure during a transition.** A Rust failure keeps today's path: the output is silent
until a cut's bottom (`backend.silent()`), numpy afterwards. A crossfade in progress is abandoned:
the clock turns into a cut, `new` instances are dropped, and the bottom applies the targets as today.

**CPU.** Only the duplicated pieces run twice, for WARM + FADE (≤ ~250 ms for an EQ or limiter change,
up to ~1 s for the diffuse tail). The worst case, a render change, runs the per-speaker path twice for
about 4 blocks (HP-O16, measured in experiment 23: numpy 10.8 ms, Rust 5.0 ms for the whole engine per
85 ms block).

## 4b. Refilling a cushion by stretching, not with silence (stage 4)

Added in the review (user, 2026-10-08). Today a cushion that runs low gets silence: the speakers'
`SharedCushion` asks for a cut and pads every speaker stream alike at its bottom (session.py:797,
`_refill`), at most every 30 s. The monitor's `Cushion` pads at any block. The main cause is a clock
mismatch: the speakers' (or the headphones') clock runs faster than the input's, so a refilled pipe
drains again (`cushion.py`, `LOW_BLOCKS`).

**The stretcher.** A new `dsp/stretch.py` (English) with `OutputStretcher`. It sits after the motor
and before the outputs, one per output group: one shared by every real speaker, so their alignment
does not move, and one for the monitor. It resamples the group's blocks by a ratio
`r = 1 + ε` with the same band-limited read as the delay line (`interpolation.read`, already in numpy
and Rust), so it gives `n·r` frames for `n` in. While `ε = 0` it passes the block through untouched
(bit for bit). Its fractional position and history carry across blocks.

**The control, adaptive** (user, review 2026-10-08). When a cushion would ask for a refill, the
stretcher is asked for the missing frames instead. It starts at `ε = +0.1 %` (1.7 cents, inaudible)
and, while the pipe keeps falling, steps up to at most `+0.5 %` (8.6 cents). It steps back down as
the pipe recovers and returns to `ε = 0` once the frames are in. The same works in reverse for a pipe
that is too full (the monitor's `trims`), with a negative `ε`. Every change of `ε` ramps (no step in
the read speed). If the pipe is about to run dry anyway (below one driver quantum), the stretcher
yields to today's refill, with the cut in `cut` mode and as now for the monitor: a real hole is
worse than a cut. The knobs are `max_stretch_ppm` (default 5000) and `start_stretch_ppm` (default
1000), in the `transition` stage.

**What it does not do yet.** It corrects the cushion when it runs low; it does not track the clocks
continuously. A loop that keeps `ε` at the measured clock ratio, as `ClockLoop` did for the SuperMini
(probes/21, `feed_pc.py`), is the natural next step on the same stretcher (research/08 §4), in its
own spec.

## 5. Stages (each one listened to on the monitor before the next)

1. **Engine without a cut, the clock and the knob, ramps and delays.** `engine_set` calls
   `_apply_engine` between blocks while a session plays (`_engine_failed` keeps the cut). Then
   `Transition` with both modes, the `transition` stage, `Smoothed.glide`, and
   `LineaDeRetardo.crossfade_to`. `preset_load`, `ab_play`, `calibration_apply`, the calibration
   suggestion and the slow delay of `actualizar_desde_control` all go through it. Until stage 2, a change that touches a stateful stage (a
   preset with another EQ, a `cut`-class knob) still goes through the cut, in either mode. Result: an A/B between
   presets that differ in pan, gain, delay or ambience has no hole.
2. **Stateful stages through `Crossfaded`:** EQ, limiter, diffuse, bass, decorrelator, extractor.
   After this, every `cut`-class chain knob is seamless.
   **Status: built 2026-10-09** (see "Stage 2 as built" in §3); a limiter change of latency still cuts
   (the render, stage 3, was built on 2026-10-10).
3. **The render switch**, including `direct` and the makeup (`on_render_switch` called at the
   request; `render_match` already holds while `en_corte`).
   **Status: built 2026-10-10** (see "Stage 3 as built" in §3); to listen to (experimento 23 §9).
4. **The cushion by stretching** (§4b), for the speakers' shared cushion and the monitor's.

## 6. Cuts that stay, and the way out of each (explored)

The cushion refill left this table in the review: it is stage 4 (§4b). Both rows go to the roadmap as
pending items.


| Cut | Why it stays now | Way out (not in this spec) |
|---|---|---|
| Rust failure (`_engine_failed`) | the output is already silent from the failing block; nothing is left to crossfade | none needed |
| Output player swap (session.py:1087, a speaker's `pw-play` replaced) | a new stream starts empty; crossfading needs two streams to the same sink at once | open the new player first and crossfade at stream level (PipeWire allows two streams per sink); its own spec |
| AVRCP volume switch (service.py:1192, 1222) | the speaker's hardware volume changes with an unknown delay; a digital ramp cannot meet it | measure the AVRCP step latency first (needs the JBL); then a ramp timed to it |

## 7. Tests

- **Engine:** the motor-level test of §2 (full chain, 4 speakers, numpy → Rust → numpy between
  blocks, ≤ 1e-9 against never switching) replaces `test_switching_at_the_cut_changes_nothing_audible`.
  `engine_set` while playing changes the engine at the next block, with no transition. A Rust failure
  still cuts.
- **Clock:** the cut mode is `FadeGate` itself, so its existing tests stay as they are. The crossfade weights sum to 1 (`equal_gain`) or their squares do (`equal_power`).
  WARM lasts the longest `need_warm`, and the merging rules of §3 hold.
- **No hole:** for each change kind (stage 1: preset of ramps and delays; stage 2: each stage; stage 3:
  each render pair), the output's short-term RMS (10 ms windows) never drops more than 1 dB below the
  smaller of the steady levels before and after. The cut mode drops to −∞ (the test proves which mode
  ran).
- **Lands where a cut lands:** once the transition is IDLE and both have run longer than the longest
  memory of a changed stage, the output equals, within 1e-9, a motor that changed by a cut and ran the
  same input. The ramps, delays and stages end exactly at their targets.
- **Delay crossfade:** a delay jump of 50 ms in crossfade mode has no sample-to-sample step above the
  signal's own, and no pitch bend (the reads never move).
- **Blind A/B:** `ab_play` between identical presets requests a transition.
- **Requests in a row:** 50 requests during one transition give exactly one pending transition, with
  the last values.
- **The knob:** `transition` round-trips through `chain_set` and `chain.json`, and a preset neither
  saves nor loads it; `cut`
  makes every path above behave as today (the existing cut tests pass with `transition=cut`).
- **Stretcher:** with `ε = 0` the output is the input bit for bit. A constant `ε` gives `n·(1+ε)`
  frames (±1) per block over 1000 blocks with no drift of the fractional position. A sine through it
  keeps its frequency within 1e-6 of `f/(1+ε)` and its THD+N below −90 dB. The adaptive control steps
  `ε` up while the pipe falls and back to 0 when the frames are in; below one quantum it yields to the
  pad. All the speakers' streams get the same frames, so their alignment holds (the existing
  alignment tests through the stretcher).
- **Listening (experiment 23 continued, HP-O16, monitor):** the hidden engine switches, counted again;
  A/B of the test presets with crossfade against cut; a monitor refill by stretching against one by
  silence; the comb question (a delay change of 10–30 ms
  crossfaded at 80 ms and at 200 ms), MEASURED by ear and with the captures of `probes/24-ab-monitor`.
