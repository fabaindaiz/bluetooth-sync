# Rust engine: scaffold and the sinc delay read Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Rust implementation of `dsp/interpolation.read`, built as a PyO3 extension with maturin, selectable live (`numpy` | `rust`) and falling back to numpy by itself, proven equal to numpy within 1e-9 and with its cost measured.

**Architecture:** `engine/` is a Cargo workspace: `aurasync-dsp` (pure Rust, `forbid(unsafe_code)`) holds the read with both numpy strategies; `aurasync-engine` (PyO3, maturin, `abi3-py312`) exposes `read` and `capabilities`. In Python, `dsp/backend.py` resolves the engine and `interpolation.read` dispatches through it; the service switches engines at the bottom of `motor.cortar` and falls back on a Rust failure.

**Tech Stack:** Rust 1.99 (rustup via pacman on HP-O16), PyO3 + rust-numpy (pinned exactly), maturin; Python 3.12, numpy; hatch; vanilla JS panel.

**Spec:** `docs/superpowers/specs/2026-10-05-rust-engine-scaffold-and-sinc-design.md`

## Global Constraints

- f64 throughout; golden **≤ 1e-9** absolute against numpy on full-scale signals (d-7c8794-36dde5).
- Constants verbatim from `dsp/interpolation.py`: `HALF = 16`, `BETA = 8.0`, `_FEW = 64`, `_STEPS = 2048`, kernel grid start `-HALF - 1.0`, `(2*HALF+2)*_STEPS + 1` table points, 4-point Lagrange, weights normalised to sum 1.
- `engine` setting: `"numpy"` default; `AURASYNC_ENGINE` overrides for tests/CLI; missing extension → warning + numpy (d-7c8794-196e0c).
- Live switch only at the bottom of `motor.cortar` (80 + 80 ms); op `engine_set {engine}` scope `control`.
- A Rust failure: that block is silence, Rust disabled, numpy from the next cut; music never stops.
- `aurasync-dsp` has `#![forbid(unsafe_code)]`; versions of `pyo3`, `numpy` (crate) and `maturin` pinned exactly.
- New code in English; `engine/README.md` and `docs/` in Spanish.
- No commits unless the user asks (CLAUDE.md). Work in the worktree `../bluetooth-sync-rust` (detached at 63c6445); each task ends with `cargo test`, `cargo clippy --all-targets -- -D warnings`, `cargo fmt --check` (Rust tasks) and `cd host && hatch test` + `hatch fmt --check` (Python tasks) green. Known flaky, pre-existing: `test_interpolation::test_it_costs_far_less_than_the_formula`, `test_service::test_a_speaker_missing_at_start_is_unavailable_and_nothing_plays`.

## Review Focus

1. Non-contiguous or non-float64 numpy arrays passed to the Rust `read` (a slice with stride, a float32 view) → converted or refused with `TypeError`, never wrong numbers — Task 2 `test_rust_read_accepts_strided_and_rejects_float32`.
2. Positions at the exact boundaries (`HALF - 1` and `len - 1 - HALF`) and a position out of range → identical to numpy in range; out of range → `ValueError`, not a panic or garbage — Task 2 `test_rust_read_boundaries_and_out_of_range`.
3. `engine_set rust` while the extension is missing, during a session → stays numpy, `reason` says why, no cut wasted — Task 3 `test_engine_set_rust_without_extension_keeps_numpy`.
4. A block with exactly 64 and exactly 65 distinct fractions (the strategy boundary) → both within 1e-9 of numpy — Task 1 `strategy_boundary_64_65` and Task 2 golden case.
5. Empty input (`position` of length 0) → empty output, no panic — Task 1 `empty_read`.

---

### Task 1: `engine/` workspace and `aurasync-dsp::interpolation`

**Files:**
- Create: `engine/Cargo.toml` (workspace), `engine/crates/aurasync-dsp/{Cargo.toml,src/lib.rs,src/interpolation.rs}`, `engine/.gitignore` (`target/`), `engine/rust-toolchain.toml` (channel `1.99.0`)
- Test: `engine/crates/aurasync-dsp/tests/interpolation.rs`

**Interfaces:**
- Produces: `pub struct Reader { .. }` with `Reader::new(max_block: usize) -> Reader` and `fn read(&mut self, data: &[f64], position: &[f64], out: &mut [f64]) -> Result<(), ReadError>`; `pub const HALF: usize = 16; pub const BETA: f64 = 8.0;` `ReadError::{OutOfRange{index}, LengthMismatch, BlockTooLarge}`. `read` allocates nothing when `position.len() <= max_block`.

- [ ] **Step 1: Write the failing tests** — `integer_positions_copy_exactly` (single 1 at integer positions: `out[i] == data[p]` bit-exact), `weights_sum_to_one` (for 1000 random fractions), `table_matches_formula_within_1e_10` (sweep of 10 000 fractions in [0,1)), `strategy_boundary_64_65` (64 and 65 distinct fractions both equal the formula within 1e-10), `empty_read`, `out_of_range_is_an_error`, `no_allocation_in_read` (counting global allocator in the test binary, with a control `Vec::with_capacity(1)` that the counter sees).
- [ ] **Step 2:** `cd engine && cargo test -p aurasync-dsp` — FAIL (not compiled / not implemented).
- [ ] **Step 3: Implement.** Port `_kernel` (Bessel I0 via its power series to 1e-16), the table built once in `Reader::new`, the distinct-fraction grouping (sort + dedup of fractions in scratch buffers, ≤ 64 → formula per distinct fraction, else table + Lagrange), normalisation, dot product over `_OFFSETS = -HALF+1 ..= HALF`.
- [ ] **Step 4:** `cargo test`, `cargo clippy --all-targets -- -D warnings`, `cargo fmt --check` — PASS.

### Task 2: `aurasync-engine` (PyO3/maturin) in the hatch environment, and the read golden

**Files:**
- Create: `engine/crates/aurasync-engine/{Cargo.toml,pyproject.toml,src/lib.rs}`
- Modify: `host/pyproject.toml` (hatch workspace member, or the fallback script — see Step 3)
- Test: `host/tests/test_engine_rust.py` (new)

**Interfaces:**
- Consumes: `aurasync_dsp::interpolation::Reader`.
- Produces: Python module `aurasync_engine` with `read(data: np.ndarray, position: np.ndarray) -> np.ndarray` (float64, C-contiguous output; accepts strided float64 by copying; float32 → `TypeError`; out of range → `ValueError`; length mismatch → `ValueError`; a Rust panic → `RuntimeError`, never `PanicException`), `capabilities() -> dict` = `{"interpolation": {"half": 16, "beta": 8.0, "steps": 2048}}`, and, only with cargo feature `test-panic`, `_panic()`.

- [ ] **Step 1: Write the failing tests** (`pytest.importorskip("aurasync_engine")` is NOT allowed in this file: the file must fail when the extension is missing in the test env — the gate requires it built): golden vs `interpolation.read` within 1e-9 for still (≤64 fractions), moving (ramp over a block), integer, boundary, odd block sizes (1, 63, 64, 65, 4095, 4096, 4097), 64/65 distinct fractions; `test_rust_read_accepts_strided_and_rejects_float32`; `test_rust_read_boundaries_and_out_of_range`; `test_capabilities_match_python_constants` (against `interpolation.HALF`, `BETA`, `_STEPS`); `test_a_planted_fault_is_seen` is not a test — the planted fault is done by hand in Step 5.
- [ ] **Step 2:** `cd host && hatch test tests/test_engine_rust.py` — FAIL (module missing).
- [ ] **Step 3: Integration.** First try a hatch workspace member (`[tool.hatch.envs.hatch-test.workspace] members = [...]`, hatch 1.16.2 here). If the extension does not build or import, use the fallback: a script in the `hatch-test` env (`[tool.hatch.envs.hatch-test.scripts] engine-build = "maturin develop -m ../engine/crates/aurasync-engine/pyproject.toml --release"`) and make `hatch test` depend on it via the env's `post-install-commands`. Record which one worked, with the commands and output, in the report.
- [ ] **Step 4:** tests PASS.
- [ ] **Step 5: Seen to fail.** Plant a fault (flip the sign of one Lagrange coefficient, then `HALF - 1` → `HALF - 2` in the range check), rebuild, show the golden failing in the report, remove it, rebuild, green.

### Task 3: The backend, the setting, the live switch and the fallback

**Files:**
- Create: `host/src/aurasync/dsp/backend.py`
- Modify: `host/src/aurasync/dsp/interpolation.py` (dispatch), `host/src/aurasync/service.py` (`CONFIG_KEYS` += `engine`; `engine_set`; fallback hook; snapshot), `host/src/aurasync/control.py` (`OPS["engine_set"]`), `host/src/aurasync/clients.py` (`CONTROL_OPS`), `host/src/aurasync/snapshot.py`, `host/src/aurasync/contract_types.py` + regenerate `host/web/src/contract.gen.ts`, `host/src/aurasync/panel/app.js` + `index.html` (Ajustes selector, Diagnóstico line), `host/docs/control-api.md`
- Test: `host/tests/test_engine_backend.py` (new), `host/tests/test_chain_golden.py` (parametrised by engine when available), browser `host/tests_browser/test_panel_engine.py` (new)

**Interfaces:**
- Produces: `backend.resolve(wanted: str) -> Resolved(active: str, available: bool, reason: str | None)`; `backend.use(name: str) -> None` (only called at a cut's bottom); `backend.active() -> str`; `backend.read(data, position) -> np.ndarray` (numpy or Rust; on `RuntimeError` from Rust: returns zeros of the right length, marks Rust disabled with the reason, and calls the registered `on_failure(reason)` once); `backend.on_failure: Callable[[str], None] | None`. Snapshot `engine: {wanted, active, available, reason}`. Op `engine_set {engine: "numpy"|"rust"}` (control).

- [ ] **Step 1: Write the failing tests** — `test_default_is_numpy`; `test_env_overrides_setting`; `test_rust_without_extension_falls_back_with_reason` (monkeypatch the import to fail); `test_engine_set_switches_only_at_the_cut` (fake motor `cortar`: active changes only when the action runs); `test_engine_set_rust_without_extension_keeps_numpy`; `test_rust_failure_gives_one_silent_block_then_numpy` (fake Rust read raising `RuntimeError`: one zero block, `on_failure` called once, next cut → numpy, reason in snapshot); `test_engine_set_without_session_only_saves_the_setting`; chain goldens run for both engines when the extension is present; browser: the selector switches and Diagnóstico shows the active engine.
- [ ] **Step 2:** run — FAIL. **Step 3:** implement. **Step 4:** `cd host && hatch test` and the browser test — PASS; `python3 host/scripts/web_stamp.py` ok after `npm run build` if `host/web/src` changed.

### Task 4: `check.sh`, the cost experiment, and the documents

**Files:**
- Modify: `scripts/check.sh` (`cargo` required with a clear message; `cargo fmt --check`, `cargo clippy --all-targets -- -D warnings`, `cargo test` in `engine/`; the extension built before `hatch test`)
- Create: `engine/README.md` (Spanish), `docs/research/experimentos/19-costo-de-la-lectura-sinc-en-rust.md` (MEDIDO on HP-O16: per-block cost of `read`, 1/3/8 speakers, 4096-sample blocks, still and moving, numpy vs Rust, median of 500 blocks, `uptime` load before and after, versions), a throwaway script under `probes/20-costo-sinc-rust/` that produces it
- Modify: `docs/decisions.md` (d-7c8794-dc712e, d-7c8794-196e0c), `docs/roadmap.md` (i-7c8794-fd9732: steps 1–2 started; `PC-Ryzen5` needs rustup), `docs/research/experimentos/00-inventario-hp-o16.md` (rustup via pacman 2026-10-05, rustc/cargo 1.99.0; revert `sudo pacman -Rns rustup; rm -rf ~/.rustup ~/.cargo`), `.claude/logs/agent-changelog.md`

- [ ] **Step 1:** Run the cost script twice (independent runs, CLAUDE.md asks for repetition); write both in the experiment.
- [ ] **Step 2:** Write the documents. Note: the phase-2 plan names experiment 19 for `PC-Ryzen5`; this one takes the next free number at the time of writing — check `ls docs/research/experimentos/` and adjust.
- [ ] **Step 3:** `PY=python3 scripts/check.sh` in the worktree — `check: ok` (rerun alone any known flaky test that fails, and say so).

## Stages after the scaffold (spec §5): one task per stage, same mould

Each of Tasks 5 to 15 ports one stage, in the spec §5 order, with the same steps: read the numpy module completely; port to `aurasync-dsp` (state in a struct; no allocation in the per-block call; `forbid(unsafe_code)`); expose in `aurasync-engine` (panic boundary around the whole body; float64 in/out; strided copied); dispatch in `dsp/backend.py` (the numpy stage instance owns the Rust object when `engine=rust`; a Rust failure → silence for that block, numpy from the next cut); golden ≤ 1e-9 against the numpy stage on its existing tests' inputs plus random blocks of odd sizes and parameter sweeps; a planted fault seen to fail; cost per block measured (numpy vs Rust, 1/3/8 speakers) and appended to the cost experiment; `cargo test/clippy/fmt`, `hatch test`, `hatch fmt --check` green.

- [ ] Task 5: spatial / front upmix (`dsp/spatial.py`)
- [ ] Task 6: ambience extractor (`dsp/ambience.py`)
- [ ] Task 7: EQ, FFT convolution (`dsp/eq.py` `StreamingFIR`)
- [ ] Task 8: decorrelation (`dsp/decorrelate.py`, `dsp/decorrelation_bank.py`)
- [ ] Task 9: limiter / true peak (`dsp/limiter.py`)
- [ ] Task 10: crossover (`dsp/crossover.py`)
- [ ] Task 11: virtual bass (`dsp/virtual_bass.py`)
- [ ] Task 12: diffuse tail (`dsp/diffuse.py`)
- [ ] Task 13: ramps and the cut (`dsp/ramps.py`)
- [ ] Task 14: loudness meters (`dsp/loudness.py`)
- [ ] Task 15: the whole engine, `RustMotor` (spec §5.11) — gets its own detailed plan before it starts, since it changes how the motor is built.
