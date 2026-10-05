# Spatial mode, "auto" layout, principal/ambient speakers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A layout `auto` for any N, a principal/ambient role per speaker, and a `spatial` render mode (time-frequency upmix by panning index) beside today's `classic`.

**Architecture:** `control.layout_roles(layout, n)` gives the roles of every layout (fixed ones from `ROLES`, `auto` computed); `control.angle_of(pan, ambience)` inverts `role_from_angle` so any speaker has an angle. `dsp/spatial.py` (`SpatialUpmix`, streaming STFT like `ambience.Extractor`) returns per speaker a direct and an ambient block; the motor, in `spatial` mode only, mixes `direct + g·decorrelate(ambient)`. A chain stage `spatial` holds the knobs (`character` macro, `manual` switch, `arc_deg`, `ambience`, `ambient_level_db`, `haas_ms`).

**Tech Stack:** Python 3.12, numpy; vanilla JS panel; pytest via `hatch test`; Playwright (`hatch -e browser run pytest`).

**Spec:** `docs/superpowers/specs/2026-10-04-spatial-mode-and-auto-layout-design.md`

## Global Constraints

- New code in English (d-7c8794-7b3093); panel copy in Spanish.
- Fixed-length work per block on the engine thread (d-7c8794-589dec).
- `classic` is the default and bit-exact with today (`tests/test_chain_golden.py`).
- Every new knob documented with recommendation, sound and figure (d-7c8794-0a4586).
- Speaker field `role_kind`: `principal` (default) | `ambient`.
- No commits (CLAUDE.md); each task ends with its tests green and `hatch fmt --check` clean.

## Review Focus

1. No principal speaker at all (all ambient) in spatial mode → everything goes to the ambients as in all-principal (no silence, no crash) — Task 2 `test_all_ambient_still_plays`.
2. A layout change with fewer principals than roles, or `assign` of P5 with 3 principals → `out_of_range` — Task 1 `test_auto_roles_follow_the_principals`.
3. Switching `render` while playing → through the cut, no click — Task 3 `test_render_switch_goes_through_the_cut`.
4. Old installation files without `role_kind` → load as principal — Task 1 `test_old_installation_loads_as_principal`.
5. Loudness jump spatial vs classic > 1 LU → test — Task 2 `test_spatial_keeps_the_loudness`.

---

### Task 1: layouts, angles and roles

**Files:** Modify `host/src/aurasync/control.py`, `host/src/aurasync/config.py` (`role_kind`), `host/src/aurasync/service.py` (assign/add use `layout_roles`), `host/src/aurasync/snapshot.py` (`roles`, `role_places`, `role` per speaker via `layout_roles`). Test: `host/tests/test_auto_layout.py`.

**Interfaces (produces):**
- `control.auto_angles(n: int) -> list[float]`: the front pair at ±180/n, then every 360/n; odd n ends at 180. Ordered clockwise from front left. `auto_angles(3) == [-60, 60, 180]`, `auto_angles(4) == [-45, 45, 135, -135]`.
- `control.layout_roles(layout: str, n_principal: int) -> dict[str, tuple[float, float]]` (role → (pan, ambience)); for `auto`, roles `P1..Pn` from `auto_angles(n)` through `role_from_angle`.
- `control.angle_of(pan: float, ambience: float) -> float` (degrees), exact inverse of `role_from_angle` on its range.
- `control.LAYOUTS` (fixed ones + `"auto"`), `GLOBAL_FIELDS["layout"]` choices from it; `ALL_ROLES` includes `P1..P8`.
- `control.role_of(pan, ambience, layout, n_principal=None)`.
- `Parlante.role_kind: str = "principal"`; `SPEAKER_FIELDS["role_kind"] = Field(str, attr="role_kind", choices=("principal", "ambient"))`.
- [ ] Tests: `test_auto_angles_for_two_to_eight`, `test_angle_of_inverts_role_from_angle` (every fixed layout's roles round-trip within 0.5°), `test_auto_roles_follow_the_principals` (assign P4 with 3 principals → out_of_range; with 4 → ok), `test_old_installation_loads_as_principal`, `test_role_kind_is_a_speaker_field` (set via contract; persisted).
- [ ] Run → fail; implement; run → pass.

### Task 2: `dsp/spatial.py`

**Files:** Create `host/src/aurasync/dsp/spatial.py`. Test: `host/tests/test_spatial.py`.

**Interfaces:**
- `@dataclass(frozen=True) class SpatialParams: arc_deg: float = 105.0; ambience: float = 0.5; ambient_level_db: float = 3.0; haas_ms: float = 14.0; lam: float = 0.9`.
- `def from_character(c: float) -> SpatialParams`: arc 150→60, ambience 0.2→0.8, level 0→+6 dB, haas 8→20 ms, linear in c.
- `class SpatialUpmix(names: list[str], angles: dict[str, float], ambient: set[str], sr=48000, params=SpatialParams())`: `latency = 2048`; `process(left, right) -> dict[str, tuple[np.ndarray, np.ndarray]]` (direct, ambient-already-Haas-delayed-and-level-set, each `len(left)` samples); `set_params(p)` live; `set_layout(angles, ambient)` (at a cut).
- [ ] Tests (2 seeds where random): `test_a_source_comes_out_at_its_angle` (left/centre/right tones panned with sin/cos law, 3 principals at −60/60/180: the nearest principal ≥ 10 dB over the farthest); `test_wider_arc_moves_a_hard_left_source_back`; `test_ambient_feeds_are_decorrelated_enough` (two ambients, diffuse input: |corr| < 0.3 before the motor's decorrelator); `test_more_character_more_ambience`; `test_mono_silence_one_channel`; `test_all_ambient_still_plays`; `test_spatial_keeps_the_loudness` (sum of output power within 1 dB of input power at character 0.5, Haas aside); `test_cost_per_block_is_flat` (3 and 8 speakers, < 3 / < 8 ms median on 4096).

### Task 3: the chain stage and the motor

**Files:** Modify `host/src/aurasync/chain.py` (`_SPATIAL_STAGE` after ambience: algorithms `classic` (default) / `spatial` with params `character`, `manual`, `arc_deg`, `ambience`, `ambient_level_db`, `haas_ms`), `host/src/aurasync/motor.py` (build `SpatialUpmix` when `spatial`; per speaker `x = direct + decorrelate(ambient)` instead of the classic mix; live param changes; `render` through the cut). Test: `host/tests/test_motor_spatial.py`; golden must still pass.
- [ ] Tests: `test_classic_is_bit_exact` (golden unchanged), `test_spatial_output_differs_and_is_finite`, `test_render_switch_goes_through_the_cut`, `test_manual_off_uses_character`, `test_ambient_speaker_gets_no_direct`.

### Task 4: explanations

**Files:** Create `host/src/aurasync/spatial_docs.py` (a `Doc` per knob, with sounds texts; figures: `localisation` bars — level per speaker for a left/centre/right source at the current vs recommended settings; `room` top view — speakers by angle with role), op `spatial_explain` (read) computed on a one-worker executor in the service. Test: `host/tests/test_spatial_docs.py`.

### Task 5: panel

**Files:** `panel/index.html`, `panel/app.js`: in the speakers list a principal/ambient switch per speaker and "Todos principales"; layout `auto` in the selector; a "Modo espacial" block (render, character, manual knobs) with each knob's recommendation, sounds and figure (reusing `drawFigure`), and the room top view. Browser test `tests_browser/test_panel_spatial.py`.

### Task 6: records

`docs/roadmap.md` (i-7c8794-eb1f78), `docs/decisions.md` (d-7c8794-d67a23, -48ae2c, -be2477), an experiment protocol `docs/research/experimentos/17-modo-espacial-con-3-go-4.md` (the blind A/B, criterion ≥ 8/10, written before measuring), changelog; `scripts/check.sh`.
