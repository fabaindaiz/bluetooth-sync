# Idiomatic Rust engine, then the decorrelator and the limiter in Rust: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring `engine/` in line with the Rust, PyO3 0.29 and rust-numpy 0.29 practices of research/15, API and structure included, without changing a single output number. Then continue the port with the 2026-10-05 plan's Tasks 8 (decorrelation), 9 (true-peak limiter) and 12 (diffuse tail).

**Architecture:** The two-crate split stays: `aurasync-dsp` is pure Rust and `aurasync-engine` is a thin PyO3 binding. Tasks 1–3 tidy the core (lints, shared test helpers, error types and shared internals). Tasks 4–5 rebuild the binding: a declarative module, an error enum with `From` conversions, exceptions that subclass `RuntimeError`, a `Reader` class instead of a global `Mutex`, keyword-only `set_params`, and a hand-written `.pyi`. Tasks 6–7 port the next stages in the 2026-10-05 mould: the numpy stage owns the Rust object, the golden against numpy is ≤ 1e-9, a planted fault is seen to fail, and the cost is measured.

**Tech Stack:** Rust 1.99.0 (edition 2024), pyo3 =0.29.3 (abi3-py312), numpy =0.29.0, realfft =3.5.0, rustfft =6.4.1, maturin 1.15.0. On the host: hatch, pytest, numpy.

**Spec:**
- `docs/research/15-rust-idiomatico-y-buenas-practicas.md`: §B is the list of departures and §D what this plan applies.
- `docs/superpowers/specs/2026-10-05-rust-engine-scaffold-and-sinc-design.md` §5: the stage-by-stage mould.
- `docs/superpowers/plans/2026-10-05-rust-engine-scaffold-and-sinc.md`, "Stages after the scaffold": the per-stage steps.

## Global Constraints

- **No output number changes.** The golden tolerances stay as they are:
  - `TOLERANCE = 1e-9` in `host/tests/test_chain_golden.py`, and every `test_*_rust.py`;
  - the Rust tests in `engine/crates/*/tests/`.
  If a refactor moves a result by more than the test's tolerance, it is reverted, not re-recorded.
- **Do not apply clippy's `suboptimal_flops` / `mul_add` or `manual_midpoint` rewrites.** They change the rounding against the numpy oracle (research/15 §A.5). Keep the arithmetic order of numpy everywhere.
- **No new third-party crate,** not even a dev-dependency (user rule: nothing third-party without asking). Use std only.
- **`unsafe`:** `#![forbid(unsafe_code)]` stays in both `src/lib.rs`. The test allocator's `unsafe impl GlobalAlloc` lives under `tests/` only.
- **Panics:** `panic = "unwind"` stays (the binding's `guard` and PyO3 rely on `catch_unwind`). Every exported Python-callable body still runs inside `guard`.
- **The per-block path allocates nothing in the core after warm-up.** Every new or touched stage keeps a counting-allocator test proving it.
- **Code and docs inside `engine/` and `host/` are in English** (d-7c8794-7b3093). Existing Spanish files keep their language.
- **Heavy work runs under `nice -n 19`** (CLAUDE.md): the suite, cargo builds and the cost measurements.
- **Gate:**
  - On the Mac, `cargo` needs `PYO3_PYTHON=/opt/homebrew/bin/python3.14`.
  - From `engine/`, all three must be green: `cargo fmt --check`, `cargo clippy --all-targets -- -D warnings` and `cargo test`.
  - From `host/`, all three must be green: `hatch run engine-build`, `hatch fmt --check` and `nice -n 19 hatch test tests`. On the Mac, 4 known macOS-only failures are allowed: `test_sonido.py::test_room_*` and `test_spatial_rust.py::test_numpy_itself_changes_0_349_*` (i-7c8794-437907).
- **No commits.** The user commits; the repository rule is "commits only when asked". Reviews diff against the working tree.
- **Branch:** `seamless-transitions` (user's choice, 2026-10-09).

## Review Focus

1. **A stale extension against a new host,** or the reverse. The host must refuse a build whose binding API differs, with its hint, instead of failing on the first block. Pinned by Task 4's `capabilities()["api"] == {"version": 2}` test, both ways.
2. **A Rust failure after the refactor still becomes silence until the cut, then numpy.** The host catches `RuntimeError`, so every new exception must subclass it. Pinned by Task 4's hierarchy test and by Task 5's planted panic in `Reader.read`.
3. **`set_params` called positionally by an old caller** must raise `TypeError` instead of silently taking the values. Pinned in Task 5.
4. **A decorrelator change in the middle of a stream.** That covers a new seed or length at a cut's bottom, a switch to and from `direct` that empties the tails, and an engine switch between blocks. Each must give the same samples as numpy. Pinned in Task 6.
5. **Limiter edge cases.** These are a block shorter than the look-ahead, a live `configure()` between blocks, digital silence after a peak (release), and alternating block sizes 4096/1024. Each must match numpy ≤ 1e-9. Pinned in Task 7.


## Status on 2026-10-09 (paused for a hand-off to another machine)

Session s-7c8794-816f05, on the Mac. Executed subagent-driven: a fresh implementer per task and a task review before the next. Nothing is committed (repository rule); everything is in the working tree of `seamless-transitions`. The local ledger (`.superpowers/`, git-ignored) is summarised here so the work can continue from any machine.

- **Tasks 1–7: done, each reviewed and approved.**
  - The gate was green at each one. The full host suite at Task 7 gave 1976 passed and the 4 known macOS failures (i-7c8794-437907).
  - Task 3 was checked bit-identical against the pre-task core (12.4M samples, `to_bits`).
  - The limiter in Rust differs from numpy by ≤ 1.55e-15, and its metrics by ≤ 1.78e-15.
- **The final whole-branch review** asked for 5 Important fixes and found no defect that changes audio:
  - experiment 20 claims without a source or repetition;
  - the browser stand-in, broken by Tasks 5–6;
  - two broken probes;
  - `engine/README.md`;
  - the limiter constants missing from `capabilities()`.

  The controller added eight minors. All 13 went to **one fix wave**, whose brief is reproduced below.
- **Paused by the user** while that wave ran: let it finish, start nothing after it. Its outcome is recorded in the session's entry in `.claude/logs/agent-changelog.md`.
- **Closed on 2026-10-09 (user asked for a full close):**
  - the scoped re-review of the fix wave found all 13 items ADDRESSED, with no new Critical or Important breakage;
  - its two new minors and the open design calls are in the roadmap (i-7c8794-a75d67);
  - the plan is complete; what follows is in the roadmap.

**To resume (from any machine):**
1. ~~Run the scoped re-review of the fix wave~~ (done at the close: all 13 ADDRESSED). Run the gate on Linux with `scripts/check.sh`.
2. **The browser test** `hatch run browser:test tests_browser/test_panel_engine.py` was never run (no Chromium on the Mac). Run it on a machine that has it.
3. **Task 8 (records):** done by this session except what depends on the re-review (see the changelog entry).
4. Then the user's **listening on `HP-O16`** (i-7c8794-93f50c), and the port's next stages (Tasks 13 ramps and 14 loudness meters of the 2026-10-05 plan; Task 15, `RustMotor`, gets its own plan).

**Rulings taken during execution** (each with its cost if wrong):
- Integration tests may silence pedantic numeric lints file-wide, with a reason. Cost: a few lints silenced in tests.
- Task 2's unit tests of existing private functions are characterization tests, with no RED step. `sum32`/`dot32` are tested at length 32 only, because they take `&[f64; 32]`. Cost: none.
- Task 3 added `OutOfRange { field, got, max }` for index-range checks. Cost: one more variant.
- Task 4:
  - added `EngineError::Python(PyErr)`;
  - **kept `module = "aurasync_engine"` on every `#[pyclass]`**, because without it `__module__` is `builtins` (research/15 §A.3 corrected);
  - added binding files `interpolation.rs`/`reader.rs` and `capabilities.rs`.

  Cost: none.
- The "both ways" api check means the new host refuses a stale extension. An old host loading the new extension is handled by bumping `fir` to version 2 in the fix wave. Cost: a forced rebuild on any host.
- `_panic` was removed; `--check-production` detects test-panic builds by `_panic_outside_the_read`. Cost: none.
- The limiter's `configure()` returns `Result`; its shared constants go into `capabilities()` (fix wave). Cost: none.

**Deferred minors** are copied to the roadmap (i-7c8794-a75d67).

### Final review fix wave (one dispatch, 2026-10-09)

1. Experiment 20:
   - the §8.2 p95 drift with 1 speaker repeats in both runs;
   - the §8 intro wrongly lists the panic test as a Rust-vs-numpy comparison;
   - ~L50: the metrics reach ≤ 1.8e-15;
   - the §9 claims with no saved source (the 1.7M bit check, 0.086–0.090 ms, the "bajo" baseline at one run and load 10, "prueba aislada no guardada") are saved and re-run, or marked one run/INFERIDO.
2. Browser stand-in (`host/tests_browser/test_panel_engine.py`): add a `Reader`, take keyword `set_params`, and add pass-throughs for `StreamingFIR`/`TruePeakLimiter`. Prove it at backend level.
3. Probes `costo.py` (→ `Reader().read`) and `costo_espacial.py` (→ keyword `set_params`) must run again.
4. `engine/README.md`:
   - remove `read(...)`;
   - describe `Reader`, the exceptions, the `.pyi`, the limiter and the `"api"` key.
5. `capabilities()["limiter"]` and `_expected()` gain `margin_db` and `near_ceiling`.
6. `backend._rust_read` builds the `Reader` through `built`.
7. `capabilities()["fir"]` goes to `{"version": 2}`, so pre-api hosts refuse the new build.
8. The api comment in `backend.py`.
9. `OutputMismatch` becomes `EngineError`.
10. Remove the duplicated `#[pymodule_export]`.
11. Update the stale crate and pyproject descriptions.
12. New tests:
    - a planted panic in the limiter's `configure`/`set_state`;
    - a planted panic on the real `backend._reader`, then a read after the cut.
13. The stub test compares signatures, not only names.

---

### Task 1: Workspace lints, release profile and the docs they ask for

**Files:**
- Modify:
  - `engine/Cargo.toml` (`[workspace.lints]`, `[profile.release]`);
  - both crates' `Cargo.toml` (`[lints] workspace = true`);
  - every `engine/crates/aurasync-dsp/src/*.rs` (docs, `Debug`, `#[must_use]`);
  - `engine/crates/aurasync-engine/src/lib.rs` (`#[allow]` → `#[expect]`, and the intra-doc link to private `guard`);
  - `engine/crates/aurasync-engine/pyproject.toml` (stale description).

- [ ] **Step 1: Set the lint table** in `engine/Cargo.toml`.
  - `[workspace.lints.rust]`: `missing_docs = "warn"`, `missing_debug_implementations = "warn"`, `unused_qualifications = "warn"`.
  - `[workspace.lints.clippy]`:
    - `pedantic = { level = "warn", priority = -1 }` and `allow_attributes_without_reason = "warn"`;
    - `allow` for `cast_precision_loss`, `cast_possible_truncation`, `cast_sign_loss`, `float_cmp`, `many_single_char_names`, `manual_midpoint`, `similar_names` and `module_name_repetitions`;
    - each `allow` gets a one-line TOML comment with its reason, e.g. "sample counts become f64 by design", or "numpy order: no midpoint/mul_add rewrites".
  - Add `[lints] workspace = true` to both crates.
- [ ] **Step 2: Set the release profile.** Keep `codegen-units = 1`. Add `lto = "fat"`, and `panic = "unwind"` with a comment: "the binding's guard and PyO3 catch panics with catch_unwind; abort would kill the service".
- [ ] **Step 3: Run clippy** (`cargo clippy --all-targets -- -D warnings`) and see it fail with the pedantic and doc lints research/15 §A.7 counted.
- [ ] **Step 4: Fix every warning without touching arithmetic:**
  - `Debug` by hand for `Reader`, `StreamingFir`, `PartitionedFir`, `Extractor`, `SpatialUpmix` and `VirtualBass`, printing sizes and parameters only (`f.debug_struct(..).field("taps", &self.taps.len())..finish_non_exhaustive()`);
  - `#[must_use]` where clippy asks;
  - `# Errors` / `# Panics` sections;
  - a crate doc for `aurasync-dsp` with one runnable doctest: a `StreamingFir` with taps `[0.5, 0.25]` fed `[1.0, 0.0, 0.0]` gives `[0.5, 0.25, 0.0]`;
  - `#[allow(clippy::too_many_arguments)]` → `#[expect(clippy::too_many_arguments, reason = "...")]`; Task 5 removes it;
  - the engine crate doc no longer links `[`guard`]`, it names it in backticks.
  - A lint that only a numeric rewrite would satisfy gets a targeted `#[expect(lint, reason = "numpy order")]` on the item, never a crate-wide allow.
- [ ] **Step 5: Verify.** `cargo fmt --check && cargo clippy --all-targets -- -D warnings && cargo test` are green. `cargo doc --no-deps` gives no warning. `RUSTDOCFLAGS="-D warnings" cargo doc --no-deps --workspace` passes.
- [ ] **Step 6: Fix the stale description** in `pyproject.toml`: "The aurasync DSP in Rust (PyO3): the delay read, spatial upmix, ambience extractor, FIR filters and virtual bass."

### Task 2: Shared test helpers and unit tests of the private numerics

**Files:**
- Create: `engine/crates/aurasync-dsp/tests/common/mod.rs`.
- Modify: the five `engine/crates/aurasync-dsp/tests/*.rs`, `src/interpolation.rs`, `src/spatial.rs`, `src/ambience.rs` and `src/fir.rs` (the `#[cfg(test)] mod tests`).

**Interfaces:**
- Produces, in `tests/common/mod.rs`:
  - `pub struct Counting;` with `unsafe impl GlobalAlloc` and the `#[global_allocator] static`;
  - `pub fn allocations_in(f: impl FnOnce()) -> usize`;
  - `pub struct Rng` with the exact generator the five files use today: same algorithm, same seeds and same `next_f64`/`normal` names, so no test input changes.
  - Each test file starts with `mod common;` and `use common::{...};`.
  - The file carries `#![allow(dead_code, reason = "each test binary uses a subset")]`.

- [ ] **Step 1: Move the helpers.** Diff the five copies first. If they differ, the union goes to `common` and each file keeps calling the same names. No test body changes.
- [ ] **Step 2:** `cargo test` gives the same test count as before, all green.
- [ ] **Step 3: Write in-module unit tests**, failing first where the function is not yet reachable:
  - `sum32` / `dot32`: equal to a reference pairwise sum in numpy's 8-lane order on lengths 0, 1, 7, 8, 9, 31, 33 and 1000, with `==`, not approximate;
  - `bessel_i0`: `I0(0) == 1.0`, and `I0(1)` within 1e-15 of `1.2660658777520082`;
  - `fft_size`: `fft_size(1) == 1`, `fft_size(4096 + 256 - 1) == 8192`;
  - `root_hann(n)` for n = 1024 and hop n/2: overlap-added squares are constant within 1e-12 away from the edges.
- [ ] **Step 4:** `cargo test` is green.

### Task 3: The core's API: errors, constructors, shared internals

**Files:**
- Create:
  - `engine/crates/aurasync-dsp/src/complex.rs` (`pub(crate)`: `scaled`, `times_conj` and the squared norm the two STFT stages share);
  - `src/stft.rs` (`pub(crate)`: `root_hann`, the ambience curve, and the streaming-STFT buffer machinery common to `ambience.rs` and `spatial.rs`, as far as it is identical);
  - `src/fft.rs` (`pub(crate)`: the one thread-local `RealFftPlanner<f64>` and `plans(size) -> (Forward, Inverse)`, moved from `fir.rs`).
- Modify: `src/lib.rs`, `fir.rs`, `ambience.rs`, `spatial.rs`, `interpolation.rs`, `virtual_bass.rs`, their tests, and `engine/crates/aurasync-engine/src/lib.rs` (callers only).

**Interfaces:**
- Produces:
  - `aurasync_dsp::Complex`, re-exported once at the crate root. The three per-module re-exports go.
  - `ambience::Extractor::new(n_fft: usize, hop: usize, max_block: usize) -> Result<Self, AmbienceError>`, with a new variant `AmbienceError::InvalidConfig { n_fft: usize, hop: usize }`.
  - `spatial::SpatialUpmix::new(speakers: usize, sr: u32, n_fft: usize, hop: usize, max_block: usize) -> Result<Self, SpatialError>`, with a new variant `SpatialError::InvalidConfig { n_fft: usize, hop: usize, sr: u32 }`.
  - The conditions are the binding's of today: `n_fft >= 2`, `0 < hop <= n_fft`, `sr > 0`. The binding drops its own copies and uses `?`.
  - `BadShape(String)` becomes `BadShape { field: &'static str, got: usize, expected: usize }` in `FirError`, `AmbienceError` and `SpatialError`. The `Display` reads `"{field}: {got} values where {expected} are needed"`. The Python `ValueError` texts change accordingly: update any host test that matches the old text.
  - `virtual_bass::VirtualBassError` replaces `FirError` in `VirtualBass`'s public API: `Filter(FirError)` with `source()`, plus `From<FirError>`. Add `VirtualBass::block(&self) -> usize`.
  - `state(&self)` becomes `to_state(&self)` on every core type (C-CONV: it allocates a copy). The Python method names stay `state`.
  - `Reader::weights_from_formula` becomes an associated function (no `&self`).
- Named constants replace the literals in `spatial.rs`:
  - `1e-20` is `TINY`, shared with ambience;
  - `1e-40`, `1e-30` and `1e-9` each get a name and a doc line saying which numpy expression they mirror;
  - `0.01` is `MIN_ENERGY_SHARE`, the `et > 0.01 * energy` threshold.

- [ ] **Step 1: Write the failing constructor tests:**
  - `Extractor::new(1, 1, 8)` is `Err(AmbienceError::InvalidConfig { n_fft: 1, hop: 1 })`;
  - `SpatialUpmix::new(2, 48000, 1024, 0, 8192)` is `Err(SpatialError::InvalidConfig { .. })`;
  - a `set_state` with a wrong-length buffer gives `BadShape { field: "<name>", got, expected }` with the right numbers.
- [ ] **Step 2:** Run them and see them fail. Then implement the interface above.
- [ ] **Step 3: Move the shared internals** into `complex`, `stft` and `fft`. **Move only what is byte-identical in both stages.** Where the two differ in order or in a constant, keep both and say why in a comment. Ambience and spatial then share the one planner.
- [ ] **Step 4: Verify.**
  - `cargo test` and clippy are green.
  - `hatch run engine-build`, then the host's goldens, all green at their unchanged tolerances: `nice -n 19 hatch test tests/test_spatial_rust.py tests/test_ambience_rust.py tests/test_eq_rust.py tests/test_crossover_rust.py tests/test_virtual_bass_rust.py tests/test_engine_rust.py tests/test_engine_backend.py`.
  - The only allowed diffs are error-message tests.

### Task 4: The binding's structure: modules, errors, exceptions, declarative module, stub

**Files:**
- Split `engine/crates/aurasync-engine/src/lib.rs` into:
  - `lib.rs`: crate doc and the `#[pymodule] mod aurasync_engine`;
  - `error.rs`: `EngineError` and the exceptions;
  - `convert.rs`: `float64_vector`, `samples`, rows, matrix and dict helpers;
  - `planted.rs`: the test-panic switch;
  - one file per class: `spatial.rs`, `ambience.rs`, `fir.rs` (both FIRs) and `virtual_bass.rs`.
- Create:
  - `engine/crates/aurasync-engine/aurasync_engine.pyi`, which maturin ships with `py.typed`;
  - `host/tests/test_engine_stub.py`.
- Modify:
  - `host/src/aurasync/dsp/backend.py` (`_expected()` gets `"api": {"version": 2}`);
  - `host/tests/test_engine_rust.py` and `host/tests/test_engine_backend.py`.

**Interfaces:**
- Produces, in Python:
  - `aurasync_engine.EngineError(RuntimeError)`, the base;
  - `aurasync_engine.EnginePanic(EngineError)`, a caught Rust panic.
  - A broken internal invariant (numpy `BorrowError` / `AsSliceError` on our own outputs, `ReadError::BlockTooLarge`) is `EngineError`.
  - Core configuration and state errors stay `ValueError`; wrong argument types stay `TypeError`.
  - `capabilities()["api"] == {"version": 2}`.
- Rust:
  - `enum EngineError { Panic(String), Internal(String), Invalid(String), Type(String) }`;
  - `From<FirError | AmbienceError | SpatialError | VirtualBassError | ReadError>` and `From<numpy::BorrowError | numpy::AsSliceError>`;
  - one `impl From<EngineError> for PyErr`;
  - `guard<T>(body: impl FnOnce() -> Result<T, EngineError>) -> PyResult<T>`;
  - every `map_err(|e| PyRuntimeError::new_err(..))` becomes `?`.
- `planted.rs`: `pub struct Planted { armed: bool }` with `fn arm(&mut self)` and `fn check(&mut self, class: &str, call: &str)`. It panics when armed and is a no-op without the `test-panic` feature. It replaces the four copies and the inline one.
- The module is declarative: `#[pymodule] mod aurasync_engine { #[pymodule_export] use super::...; }`. Remove `module = "aurasync_engine"` from each `#[pyclass]`. The `test-panic` members stay `#[cfg]`-gated.

- [ ] **Step 1: Write the failing host tests:**
  - `issubclass(aurasync_engine.EnginePanic, aurasync_engine.EngineError)` and `issubclass(aurasync_engine.EngineError, RuntimeError)`;
  - a planted panic in `StreamingFIR.process` raises `EnginePanic`;
  - `backend._expected()["api"] == {"version": 2}`;
  - a fake module whose `capabilities()` lacks `"api"` is refused with the build hint, both ways;
  - `test_engine_stub.py`: every public name of the module is in the `.pyi` and every name in the `.pyi` exists, by `dir()` against an `ast` parse of the stub; `test-panic` names (leading underscore) are excluded.
- [ ] **Step 2:** `hatch run engine-build`, then run the tests and see them fail.
- [ ] **Step 3: Implement** the split, the error enum, the exceptions, `Planted`, the declarative module, `"api"` in `capabilities()` (Rust) and `_expected()` (host), and the `.pyi`. The `.pyi` gives signatures with `numpy.typing.NDArray[numpy.float64]` and docstrings from the Rust docs.
- [ ] **Step 4: Verify.** The cargo gate and `nice -n 19 hatch test tests/test_engine_*.py tests/test_*_rust.py` are green, then the full host suite (Global Constraints).

### Task 5: `Reader` as a class, and keyword-only `set_params`

**Files:**
- Create: `engine/crates/aurasync-engine/src/reader.rs`.
- Modify:
  - `engine/crates/aurasync-engine/src/{lib.rs,spatial.rs,ambience.rs}` and `aurasync_engine.pyi`;
  - `host/src/aurasync/dsp/{backend.py,spatial.py,ambience.py}`;
  - `host/tests/{test_engine_rust.py,test_spatial_rust.py,test_ambience_rust.py,test_engine_backend.py}`.

**Interfaces:**
- Produces:
  - `aurasync_engine.Reader(max_block: int = 8192)`, with `.read(data, position) -> NDArray[float64]`.
    - A block larger than `max_block` rebuilds its table for the next power of two (at least 8192) once, as `with_reader` does today.
    - `Reader._panic_next()` exists under `test-panic`.
    - The module-level `read`, `static READER` and `lock_reader` are removed, and so is `_panic` (its test moves to `Reader._panic_next`). `_panic_outside_the_read` stays.
  - `SpatialUpmix.set_params(self, *, arc_deg, ambience, ambient_level_db, haas_ms, threshold, lam, front_intact)` and `AmbienceExtractor.set_params(self, *, lam, threshold, mu0, mu1, sigma, min_energy)`. A positional call is `TypeError`. The `#[expect(clippy::too_many_arguments)]` goes if clippy allows; else it keeps its reason.
  - Host: `backend.read` builds `_reader = _module.Reader()` lazily. On a guarded failure it drops `_reader`, and a new one is built after the cut. `spatial._rust_params()` and `ambience._rust_params()` return a `dict` and the calls become `rust.set_params(**self._rust_params())`.
  - `capabilities()["api"]` stays `{"version": 2}`, because Tasks 4 and 5 land together before any build ships.

- [ ] **Step 1: Write the failing tests:**
  - `Reader().read(...)` equals `interpolation.read_numpy` ≤ 1e-9 on the existing read-golden inputs, including a 20 000-position block that forces the rebuild;
  - `Reader()._panic_next()`, then `read`, raises `EnginePanic`, and a fresh `backend.read` after `use()` works;
  - `SpatialUpmix(...).set_params(0.0, ...)` positional raises `TypeError`, and the same for `AmbienceExtractor`;
  - `aurasync_engine` has no attribute `read`.
- [ ] **Step 2: Run them; see them fail. Implement. Run them; see them pass.**
- [ ] **Step 3: Verify.** The cargo gate, then `nice -n 19 hatch test tests` (Global Constraints).

### Task 6: Task 8 of the port, the decorrelator's convolution in Rust

**Approach:**
- The motor's `_convolucionar` (`host/src/aurasync/motor.py`) is an exact overlap-add of `np.convolve` with a per-speaker tail. That is the semantics of `eq.StreamingFIR`, whose Rust port exists.
- The decorrelator becomes one `eq.StreamingFIR` per speaker (`None` without a filter). It follows the engine like every other FIR.
- Its design (RNG, bank and assignment) stays in numpy: it runs at a cut, not per block.

**Files:**
- Modify: `host/src/aurasync/motor.py` (`__init__`, `_cambiar_filtros`, `reiniciar`, the switch to and from `direct` that empties the tails, `_convolucionar`).
- Tests:
  - `host/tests/test_decorrelate.py` or a new `host/tests/test_decorrelate_rust.py`;
  - `host/tests/test_chain_golden.py` (unchanged, must stay green);
  - any test that reads `_cola_filtro`.

**Interfaces:**
- Produces:
  - `Motor._decorreladores: dict[str, eq.StreamingFIR | None]` replaces `_filtros`' convolution use and `_cola_filtro`. `_filtros` (the taps) stays for the metrics.
  - `_cambiar_filtros(filtros)` builds fresh `StreamingFIR`s; a length change starts from silence, as `_cola_filtro` did.
  - Emptying the tails (`reiniciar`, the `direct` switch) builds fresh ones too.
  - `eq.StreamingFIR(taps, block=self.bloque)`: if the numpy class has no `block` argument, keep its signature and let the Rust side prepare on its first block.

- [ ] **Step 1: Write the failing tests:**
  - with `AURASYNC_ENGINE=rust`, a motor with 3 speakers has every `_decorreladores[n]._rust is not None` after one block;
  - `procesar_completo` under `engine=rust` equals the numpy engine ≤ 1e-9 over the golden scenario's input with the decorrelator on;
  - a stateful preset changing `decorrelate.seed` at a cut's bottom, then 3 more blocks, equals numpy ≤ 1e-9;
  - a `classic → direct → classic` switch empties the tails in both engines alike;
  - switching engine between blocks mid-stream (`backend.use`) gives the same samples as no switch, ≤ 1e-9;
  - a planted panic (`_panic_next` on one speaker's FIR) gives one silent block, then numpy from the cut.
- [ ] **Step 2: Run; fail. Implement. Run; pass.**
  - `tests/test_chain_golden.py` stays ≤ 1e-9 on the numpy engine. That proves FFT overlap-add equals `np.convolve` here.
  - If it does not, stop and report the measured max diff; do not re-record.
- [ ] **Step 3: Measure the cost** (`nice -n 19`): the decorrelator per block with 1/3/8 speakers, numpy direct convolution before, numpy FFT after, and Rust. Append §8 to `docs/research/experimentos/20-costo-de-la-lectura-sinc-en-rust.md` with the machine, versions and MEDIDO numbers, following §5–§7's layout.
- [ ] **Step 4: Verify.** Full gate (Global Constraints).

### Task 7: Task 9 of the port, the true-peak limiter in Rust; Task 12 checked

**Files:**
- Create:
  - `engine/crates/aurasync-dsp/src/limiter.rs` and `engine/crates/aurasync-dsp/tests/limiter.rs`;
  - `engine/crates/aurasync-engine/src/limiter.rs`;
  - `host/tests/test_limiter_rust.py`.
- Modify:
  - `engine/crates/aurasync-dsp/src/lib.rs` and `engine/crates/aurasync-engine/src/lib.rs` (export);
  - `aurasync_engine.pyi`;
  - `host/src/aurasync/dsp/limiter.py` (`TruePeakLimiter` owns the Rust object);
  - `host/src/aurasync/dsp/backend.py` (`_expected()["limiter"] = {"version": 1}`).

**Interfaces:**
- Rust core: `limiter::TruePeakLimiter`.
  - `new(kernels: &[f64], half_width: usize, ceiling_db: f64, latency: usize, attack: usize, hold: usize, release_ms: f64, sr: u32) -> Result<Self, LimiterError>`. The numpy class computes `latency`, `attack`, `hold` and the interpolation kernels (`loudness.interpolation_kernels(w)`) and passes them, so no constant is duplicated.
  - `configure(&mut self, ceiling_db: Option<f64>, release_ms: Option<f64>)`.
  - `process(&mut self, x: &[f64], out: &mut [f64]) -> Result<(), LimiterError>`.
  - `gain()`, `max_reduction_db()` and `active_fraction()`.
  - `to_state() -> LimiterState { x: Vec<f64>, gain: f64 }` and `set_state(&LimiterState)`.
- Python: `aurasync_engine.TruePeakLimiter(kernels, half_width, ceiling_db, latency, attack, hold, release_ms, sr)`.
  - `.configure(*, ceiling_db=None, release_ms=None)`;
  - `.process(x) -> (out, gain, max_reduction_db, active_fraction)`;
  - `.state()` / `.set_state(dict)` with keys `x` and `gain`;
  - `._panic_next()` under `test-panic`.
- Host:
  - `TruePeakLimiter` uses the `_RustOwned` pattern of `dsp/eq.py` (import it, do not copy it). It keeps `gain`, `max_reduction_db` and `active_fraction` updated from Rust's return.
  - `PeakLimiter` (the chain's `peak`) stays numpy: it is a closed form costing ~nothing (state it in the docstring).
- **Order.** Follow numpy's order in each step:
  - `_running_min` (van Herk) and `_hann_average` (running sums in sequence, the `theta` cos/sin tables);
  - `_release` (log domain, running maximum);
  - the 8-tap kernel product per window.
  The kernel product is numpy `@` (BLAS) on the Python side: in Rust, a plain left-to-right dot; the golden tolerance covers BLAS's own order.

- [ ] **Step 1: Write the failing tests:**
  - Rust golden-shape unit tests in `tests/limiter.rs`:
    - block-size invariance (a stream cut into 4096, 1024, 7 and 1 gives the same output ≤ 1e-12);
    - no allocation in `process` after the first block;
    - `set_state(to_state())` round trip.
  - Host `test_limiter_rust.py`, ≤ 1e-9 against numpy on:
    - the `test_limiter.py` inputs (60 Hz sine 6 dB over, the inter-sample-peak corpus up to +11 dBTP);
    - random blocks of odd sizes;
    - a sweep of `lookahead_ms` 1.0/3.0/5.0, `release_ms` 50/250/1000 and `hold_ms` 0/15;
    - a live `configure()` between blocks;
    - silence after a peak;
    - an engine switch mid-stream;
    - a planted panic giving a silent block, then numpy;
    - the metrics (`max_reduction_db`, `active_fraction`, `gain`) equal numpy's ≤ 1e-9.
- [ ] **Step 2: Run; fail. Implement. Run; pass.** `tests/test_chain_golden.py` and `tests/test_limiter.py` stay green.
- [ ] **Step 3: Measure the cost** (1/3/8 speakers, numpy against Rust, `nice -n 19`). Append §9 to experiment 20.
- [ ] **Step 4: Check Task 12, the diffuse tail.** Its per-block work is `eq.PartitionedFIR`, already in Rust. Measure what `DiffuseStage.process` spends outside the FIR, as the crossover's Task 10 did (experiment 20 §6). If it is under ~0.1 ms per block with 3 speakers, record "nothing more to port" in experiment 20 §10 and tick Task 12. Otherwise report the number and stop; do not port without asking.
- [ ] **Step 5: Verify.** Full gate.

### Task 8: Records (controller, not a subagent)

- [ ] Tick Tasks 8, 9 and 12 in `docs/superpowers/plans/2026-10-05-rust-engine-scaffold-and-sinc.md`.
- [ ] Update the roadmap's i-7c8794-fd9732 state, and the `**Los menores diferidos del motor Rust**` list (strike what Tasks 1–5 fixed).
- [ ] Mark research/15 §D as applied, with what was left.
- [ ] Write the session entry in `.claude/logs/agent-changelog.md`.
- [ ] Run `scripts/check.sh` in parts on the Mac (i-7c8794-437907).
