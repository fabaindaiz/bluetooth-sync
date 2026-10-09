# Seamless transitions, stage 4 (the cushion by stretching) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A cushion that runs low (the speakers' shared one, and the monitor's) is refilled by playing
slightly slower for a while (resampling by `1 + ε`), not by inserting silence; a cushion that is too full
by playing slightly faster. Silence stays only as the last resort when a pipe is about to run dry.

**Architecture:** A new `dsp/stretch.py` with `OutputStretcher`: one per output group (one shared by every
real speaker, so their alignment does not move; one for the monitor). It reads each block at a fractional
position that advances by `1/(1+ε)` per output sample with the band-limited read of the delay line
(`interpolation.read`, numpy and Rust), keeping its history and fractional position across blocks. With
`ε = 0` it passes the block through bit for bit. A small adaptive controller moves `ε` by ramps between 0,
`start` and `max`. The cushions (`cushion.py`) ask the stretcher for frames instead of returning silence.

**Tech Stack:** Python 3.12, numpy, the existing `interpolation.read` (Rust-backed).

**Spec:** `docs/superpowers/specs/2026-10-08-seamless-transitions-design.md` §4b, §5 stage 4, §7 (stretcher tests).

## Global Constraints

- New module and tests in English; Spanish files keep their language. No commits; `nice -n 19` for every test run.
- `start_stretch_ppm` 1000 (0.1 %, 1.7 cents) and `max_stretch_ppm` 5000 (0.5 %, 8.6 cents), as params of the
  chain's `transition` stage (live knobs; Spanish panel copy). The adaptive rule: start at `start`; while the
  pipe keeps falling, step up toward `max`; step down as it recovers; back to 0 once the missing frames are in.
  Every change of `ε` is a ramp (no step in the read speed). Negative `ε` for a pipe that is too full.
- **The speakers' streams get the same frames**: one stretcher, one ratio, one fractional position for all of
  them, so their relative alignment never moves (the existing alignment tests must pass through it).
- **Last resort:** below one driver quantum (`cushion.DRIVER_QUANTUM_FRAMES`) the stretcher yields: the
  speakers' cushion keeps today's padded refill (with its cut), the monitor its silence refill.
- Engine cost: the stretcher runs only while `ε ≠ 0`; with `ε = 0` it must not allocate or copy.
- The live session on HP-O16 must not be touched; tests use fakes.

## Review Focus

1. A stretcher whose ratio changes mid-block never clicks (continuous position, ramped ε).
2. The speakers stay aligned to the sample: same output length and same position for every speaker.
3. `separado` vs `combinado` outputs (outputs.py) and a speaker that joins/leaves during a stretch.
4. Interaction with transitions and cuts: a cut while stretching (the stretch continues or resets cleanly).
5. The monitor's cushion: its `trims` path today drops a block; with the stretcher, trims become `ε < 0`.

---

### Task 1: `OutputStretcher` (dsp/stretch.py)

**Interfaces (produces):** `OutputStretcher(channels: int | list[str], sr=48000, start_ppm=1000, max_ppm=5000)`;
`process(blocks: dict[str, np.ndarray] | np.ndarray) -> same shape with n·(1+ε) frames (±1)`;
`want(frames: int)` (positive: frames to add; negative: to drop) and `cancel()`; properties `epsilon_ppm`,
`pending_frames`, `active`; `set_limits(start_ppm, max_ppm)`.

- [ ] Tests (spec §7): `ε = 0` passes bit for bit and allocates nothing new; a constant ε gives `n·(1+ε)` frames
  (±1) per block over 1000 blocks with no drift of the position; a sine keeps its frequency within 1e-6 of
  `f/(1+ε)` and THD+N below −90 dB; ε ramps (no step) when it changes; `want(+N)` adds exactly N frames
  in total then returns to 0; `want(−N)` drops N; several channels get identical lengths and positions.
- [ ] Implement with `interpolation.read` on a history long enough for its kernel (`interpolation.HALF`).

### Task 2: the speakers' shared cushion stretches

**Files:** `cushion.py` (`SharedCushion`), `session.py` (where the speakers' blocks are written and where the
cushion asks for a cut and `_refill` pads), `outputs.py` if needed; tests in the cushion/session test files.

- [ ] `SharedCushion` asks the stretcher for the missing frames (`target − level`) instead of a cut, unless the
  level is under one quantum (then today's cut + pad). The speakers' blocks go through the one stretcher before
  `outputs.write`. Too full → `want(−N)`.
- [ ] Tests: a pipe that drains slowly (a fake clock 100 ppm fast) is kept at its target with no cut and no
  pad; a pipe under one quantum still gets the cut + pad; every speaker gets the same frames; `refills` and a
  new `stretched_frames` counter are reported in `state.health.output_cushion`.

### Task 3: the monitor's cushion stretches

**Files:** `cushion.py` (`Cushion.plan`), `monitor.py`/`monitor_control.py` (where the monitor writes); tests.

- [ ] `Cushion.plan` asks its own stretcher instead of writing silence (refill) or dropping a block (trim),
  except under one quantum. Tests as in Task 2, for one stream; the monitor's `refills`/`trims` counters keep
  their meaning for the last-resort paths and a `stretched_frames` counter is added.

### Task 4: knobs, docs and records

- [ ] `start_stretch_ppm` and `max_stretch_ppm` in the `transition` stage (chain.py), live, Spanish copy, not
  carried by presets (the stage is excluded already); control-api.md; experiment note: the listening check on
  HP-O16 (a refill by stretching vs by silence, from the spec §7) as pending in experimentos/23; roadmap stage 4
  entry → A medias (built, to listen).
