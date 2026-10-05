# Rust engine: scaffold and first stage (the sinc delay read) — design

**Date:** 2026-10-05 · **Status:** reviewed interactively with the user on 2026-10-05 (both strategies
replicated; numpy by default; fallback to numpy; `check.sh` requires `cargo`; live switch through the
cut; a Rust failure falls back to numpy by itself). **Decisions:** d-7c8794-36dde5 (2026-10-02: PyO3 now, `engine/` at the root, f64 golden
≤1e-9, numpy as oracle), d-7c8794-dc712e (the gate of research/12 §4 is skipped: scaffold and first
stage before measuring experimentos/12), d-7c8794-196e0c (`engine` selector with numpy fallback).
Research: `docs/research/12-motor-de-audio-en-rust.md` (§2.3–2.5, §4 steps 1–2).

## 1. What and why

Steps 1 and 2 of research/12 §4, started with one stage: a Rust implementation of the band-limited
fractional read of the delay line, `dsp/interpolation.read(data, position)`, injected into the numpy
`Motor` (a hybrid engine) and compared against it. The user asked for it on 2026-10-05, in parallel
with phase 2 of the virtual speakers, choosing this stage because it is the engine's hot path: it
took the engine to ~5× real time before its numpy fix (experimentos/12 §1.1), and its timing test is
the one that flakes under load today (`tests/test_interpolation.py::test_it_costs_far_less_than_the_formula`).

Not in scope: native I/O (`aurasync-io-pw`, step 4; `probes/17` keeps exploring it), any other stage,
f32, the Raspberry Pi.

## 2. Structure and integration (approved)

- `engine/` at the repository root, a Cargo workspace with two crates:
  - `aurasync-dsp`: pure Rust, no Python. `interpolation::read(data: &[f64], position: &[f64],
    out: &mut [f64])` with **both** strategies of the numpy module, so the results agree far below
    1e-9: the exact Kaiser-windowed sinc (`HALF = 16`, `BETA = 8.0`, normalised weights) when a
    block has at most 64 distinct fractions, and the table (`_STEPS = 2048` points per sample, the
    kernel on [−HALF−1, HALF+1]) read with 4-point Lagrange otherwise. No allocation inside `read`
    for blocks up to a size fixed at construction (a `Reader` struct holding scratch buffers).
  - `aurasync-engine`: the PyO3 extension built with maturin (`abi3-py312`), module
    `aurasync_engine`, exposing `read(data, position) -> numpy.ndarray[float64]` and
    `capabilities() -> dict` (`{"interpolation": {"half": 16, "beta": 8.0, "steps": 2048}}`).
- The numpy `interpolation.read` stays and is the oracle. The Python side chooses through one seam:
  `aurasync/dsp/backend.py` (new, English) resolves the engine once and `interpolation.read`
  dispatches to it. `LineaDeRetardo` does not change.
- **Selector** (d-7c8794-196e0c): `"engine": "numpy" | "rust"` in `service.json` (default `numpy`),
  `AURASYNC_ENGINE` for tests and the CLI. `rust` without the extension installed → one warning in
  the log and numpy; nothing fails.
- **Live switch** (chosen by the user): op `engine_set {engine}` (scope `control`) changes the engine
  while playing, at the bottom of the next cut (`motor.cortar`, 80 + 80 ms); without a session it only
  changes the setting. The sinc read keeps no state, so the switch leaves nothing half-done. The
  panel (Ajustes) has a numpy/rust selector and Diagnóstico shows the active engine and, if it fell
  back, why. The snapshot carries `engine: {wanted, active, available, reason}`.
- **A Rust failure falls back to numpy by itself** (chosen by the user): a panic is caught at the
  extension's boundary and raised as `RuntimeError`; the dispatcher returns silence for that block,
  marks Rust disabled, and the engine switches to numpy at the next cut. The log and the panel say
  why; Rust stays disabled until the user chooses it again or the service restarts. The music does
  not stop.
- **hatch integration (the main risk, unproven per research/12 §2.5):** first try `engine/` as a
  member of a hatch workspace (hatch ≥1.16; this machine has 1.16.2). If that does not build or does
  not stay importable, fall back to a hatch script in the test environment that runs
  `maturin develop` into it. Whichever works is written down with its evidence.

## 3. The stage, tests and checks (section 2, for review)

**Switch and fallback.** Tests: `engine_set` while playing changes the engine only at the cut's
bottom and the output stays continuous; a planted panic in a test build (a feature flag of
`aurasync-engine`) produces silence on every speaker from the failing block until the cut's bottom, then numpy, with the reason in the
snapshot.

**Golden.** With `engine=rust`, `interpolation.read` must agree with numpy within **1e-9** (absolute,
full-scale signals) on: still delays (≤64 fractions), moving delays (the table path), integer
positions (exact copy), the boundaries (`HALF − 1` before, `HALF` after), and odd block sizes. The
existing chain goldens (`tests/test_chain_golden.py`, `tests/test_interpolation.py`) run once per
engine when the extension is present.

**Seen to fail.** A planted fault (for example the Lagrange coefficient sign, or `HALF − 1` off by
one) must make the golden fail; it is shown in the report, then removed.

**Cost, measured.** The per-block cost of `read` for 1, 3 and 8 speakers, 4096-sample blocks, still
and moving, numpy vs Rust, median of 500 blocks, written in `docs/research/experimentos/` (new file,
MEDIDO, HP-O16, with load noted — `uptime` — because of the timing flake). No target speed-up is
promised; the number is the result.

**Rust-side tests.** `cargo test` in `engine/`: the kernel at integer positions is a single 1; weights
sum to 1; table vs formula within 1e-10 over a sweep of fractions; no allocation in `read` (an
allocation-counting allocator in the test, with a control that shows it sees one).

**Checks.** `scripts/check.sh` adds `cargo fmt --check`, `cargo clippy --all-targets -- -D warnings`
and `cargo test` in `engine/` (research/12 §2.5; d-7c8794-36dde5 says `check.sh` requires `cargo`),
and runs the host suite with the extension built. A machine without `cargo` fails the check with a
clear message (like the missing-hatch case today), as d-7c8794-36dde5 decided; `PC-Ryzen5` needs
`rustup` installed before its next check (noted in the roadmap).

**Documentation.** `engine/README.md` (Spanish: what it is, how to build, how to choose the engine);
the two decisions in `docs/decisions.md`; roadmap i-7c8794-fd9732 updated (steps 1–2 started);
`docs/research/experimentos/00-inventario-hp-o16.md` notes the system change: `rustup` installed with
pacman on 2026-10-05 (rustc/cargo 1.99.0), reverted with `sudo pacman -Rns rustup; rm -rf ~/.rustup
~/.cargo`.

## 4. Risks

- The hatch workspace with a maturin member may not work (§2 fallback).
- PyO3 + numpy crate versions must match the numpy in the hatch env; pin them exactly, as the repo
  pins `bumble` and `lc3py`.
- A panic in Rust: the extension catches it at the boundary and raises a Python `RuntimeError`
  (not PyO3's `PanicException`, which derives from `BaseException` and that `service._step` would
  not catch — VERIFICADO in research/12 §2.4); the dispatcher falls back to numpy (§2).
- A segfault (unsafe code, a bad numpy view) would still kill the service: the crate uses no
  `unsafe` of its own (`#![forbid(unsafe_code)]` in `aurasync-dsp`).

## 5. The whole engine, stage by stage (added 2026-10-05, d-7c8794-0d2a1e)

The user asked to keep going until the Rust engine does everything the numpy one does, **in order of
cost** (chosen over simple-to-complex and over a single big port). Every stage follows the sinc read's
mould: ported into `aurasync-dsp` (pure Rust, `forbid(unsafe_code)`), exposed by `aurasync-engine`,
dispatched by `dsp/backend.py` when `engine=rust` (a hybrid engine: Rust for every stage that has
one, numpy for the rest), golden ≤ 1e-9 against the numpy stage, a planted fault seen to fail, and its
cost measured. Stateful stages keep their state in a Rust object owned by the Python stage instance,
and the live switch (§2) moves state only at the bottom of a cut (or restarts the stage there).

Order (costs from `chain.py`, to be re-measured as each stage lands):

1. spatial / front upmix (`dsp/spatial.py`, STFT, ~3 ms per block with 3 speakers);
2. ambience extractor (`dsp/ambience.py`, STFT, ~1 ms);
3. EQ by FFT convolution (`dsp/eq.py` `StreamingFIR`);
4. decorrelation (`dsp/decorrelate.py`, `dsp/decorrelation_bank.py`);
5. limiter / true peak (`dsp/limiter.py`);
6. crossover (`dsp/crossover.py`);
7. virtual bass (`dsp/virtual_bass.py`);
8. diffuse tail (`dsp/diffuse.py`);
9. ramps and the cut (`dsp/ramps.py`);
10. loudness meters (`dsp/loudness.py`, `quality.py`'s meters);
11. **the whole engine** (`RustMotor`, research/12 §4 step 3): `Motor.procesar` in Rust with the same
    public API, the chain goldens (`tests/test_chain_golden.py`, `test_chain*`) with `engine=rust`,
    `--simular` and the browser tests green, and the speed-up measured on `PC-Ryzen5`.
