# Sync estimator, steps 1-2 (server side, explained settings) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A server-side `SyncEstimator` that takes probe measurements (today from the server's
microphone), fits per-speaker latency with a robust model, and suggests absolute delays that the
listener applies from the panel, with every setting explained in words and a simulated figure.

**Architecture:** `Measurement` (one shape for every source) → `SyncEstimator` (own thread,
O(1) `submit`) → `sync_methods.robust_ls` (IRLS Huber least squares over a window) →
`Suggestion` read by the snapshot and the panel; `sync_apply` writes the delays through the
motor's cut. Explanations (`knob_docs`) and figures (`sync_sim`) are computed on the
estimator's thread and cached.

**Tech Stack:** Python 3.12, numpy, the existing `aurasync` contract (`control.py`, `rest.py`),
vanilla JS panel (`panel/app.js`, Tailwind classes), pytest via `hatch test`, Playwright
browser tests (`tests_browser/`, outside `check.sh`).

**Spec:** `docs/superpowers/specs/2026-10-03-sync-estimator-design.md` (§2-§5, §6.1 rows
`sync_state`, `sync_set`, `sync_apply`, `sync_explain`, §7, §8 steps 1-2).

## Global Constraints

- New host code, identifiers, docstrings, REST routes and contract fields in English
  (d-7c8794-7b3093); panel copy in Spanish, as the rest of the panel.
- The engine thread only does fixed-length work (d-7c8794-589dec): `submit` is a queue put;
  fits, explanations and figures run on the estimator's thread.
- The estimator never writes delays by itself (d-7c8794-2c6f91); only `sync_apply` does,
  through `motor.cortar`, with **absolute** delays.
- Levels are statistics only (d-7c8794-e61118): nothing reads them back into gains or EQ.
- Settings defaults (spec §4.4): `method="robust_ls"`, `window_min=10`, `huber_ms=0.3`,
  `jump_repeats=2`, `point_role_default="target"`, `point_vote_weight=3`,
  `moved_threshold_ms=1.0`, `min_speakers=2`, `accept_processed_audio=False`,
  `continuous_every_s=20`.
- Simulation results are labelled SIMULADO; every acceptance criterion holds in two seeds and
  survives `window_min` 10 → 20 (CLAUDE.md).
- No git commits (CLAUDE.md: only when the user asks). Each task ends with
  `cd host && hatch test <its tests>` green and `hatch fmt --check` clean instead.

## Review Focus

1. A measurement where only one speaker passed → ignored with reason, no suggestion change
   (Task 1 validation + Task 3 test `test_one_speaker_is_not_a_measurement`).
2. A speaker never heard by an anchor → no suggestion for it, its delay untouched on apply
   (Task 3 `test_a_speaker_without_anchor_gets_no_suggestion`, Task 6 `test_apply_leaves_unsuggested_speakers`).
3. `sync_apply` with no suggestion, or a stale one (older than the last applied) → `conflict`
   error, nothing changes (Task 6 `test_apply_without_suggestion_is_a_conflict`).
4. A burst of measurements faster than the fit → the queue keeps the newest `QUEUE_MAX` and
   the engine thread never waits (Task 4 `test_submit_never_blocks`).
5. Corrupt `sync.json` on disk → defaults with a warning, the service starts (Task 6
   `test_corrupt_sync_json_falls_back_to_defaults`).

---

### Task 1: `Measurement` and its validation

**Files:**
- Create: `host/src/aurasync/sync_measurement.py`
- Modify: `host/src/aurasync/probe_measure.py` (add `levels_db` to `ProbeMeasurement`)
- Test: `host/tests/test_sync_measurement.py`, `host/tests/test_probe_measure.py`

**Interfaces:**
- Produces:
  - `MAX_AGE_S = 600.0`
  - `@dataclass(frozen=True) class Measurement: source_id: str; position_id: str; kind: Literal["continuous","point"]; role: Literal["target","vote"] | None; weight: float; t: float; arrivals_ms: dict[str, float]; halves_ms: dict[str, tuple[float, float]]; levels_db: dict[str, float]; quality: dict[str, Any]; origin: Literal["server","browser"]`
  - `Measurement.from_dict(data: dict, speakers: set[str], now: float) -> Measurement` raising `MeasurementError(code, message)` (`code` in `"bad_request"`, `"out_of_range"`, `"unknown_field"`).
  - `Measurement.to_dict() -> dict`
  - `Measurement.heard -> frozenset[str]` (keys of `arrivals_ms`)
  - `ProbeMeasurement.levels_db: dict[str, float]` — per measured speaker, `20*log10(g)` with `g` the least-squares gain of the reference at the found lag: `g = <mic[lag:lag+len(ref)], ref> / <ref, ref>`.

- [ ] **Step 1: Write failing tests** in `test_sync_measurement.py`:
  `test_round_trip` (to_dict/from_dict equal); `test_unknown_speaker_is_rejected` (code `out_of_range`);
  `test_point_needs_a_role` (`kind="point"`, `role=None` → `bad_request`);
  `test_continuous_has_no_role` (`role="target"` with continuous → `bad_request`);
  `test_too_old_is_rejected` (`t = now - MAX_AGE_S - 1` → `out_of_range`);
  `test_one_speaker_is_kept_but_flagged` (`len(heard) == 1` parses; `heard` has 1 — the estimator decides);
  `test_non_finite_arrival_is_rejected`.
  In `test_probe_measure.py`: `test_levels_follow_the_room_gains` — in `_engine_run(3, 0, decorrelate=True)` with room gains known, `levels_db` differences between speakers within 0.5 dB of `20*log10(gain_i/gain_j)`.
- [ ] **Step 2: Run** `cd host && hatch test tests/test_sync_measurement.py tests/test_probe_measure.py` — expect import failure / missing attribute.
- [ ] **Step 3: Implement** both. `weight` defaults: target 1.0, vote = settings value passed by caller, continuous 1.0. Arrivals in 0..`probe_measure.MAX_LAG_MS`+1000.
- [ ] **Step 4: Run** the same command — PASS; `hatch fmt --check` clean.

### Task 2: `sync_sim` — the simulator of measurements with known truth

**Files:**
- Create: `host/src/aurasync/sync_sim.py`
- Test: `host/tests/test_sync_sim.py`

**Interfaces:**
- Produces:
  - `@dataclass class Scenario: speakers: list[str]; latency_ms: dict[str, float]; drift_ppm: dict[str, float]; jumps: list[tuple[float, str, float]]  # (t_s, speaker, ms); positions: dict[str, dict[str, float]]  # position -> speaker -> acoustic ms; hears: dict[str, set[str]]; noise_ms: float = 0.01; outlier_rate: float = 0.0; outlier_ms: float = 5.0; every_s: float = 20.0; duration_s: float = 3600.0; anchor_position: str | None = None; bias_ms: dict[str, float] = field(default_factory=dict)  # per position`
  - `def true_latency(sc: Scenario, speaker: str, t: float) -> float`
  - `def measurements(sc: Scenario, seed: int) -> list[Measurement]` — one per position per `every_s`; the anchor position's are `kind="point", role="target"` for the first one and continuous afterwards; others continuous; `common` random per recording in ±200 ms; outliers per speaker at `outlier_rate`.
  - `def standard(name: str, n: int = 3) -> Scenario` with names `"drift"`, `"jump"`, `"partial"`, `"two_positions"`, `"moved"`, `"biased"` (spec §7 cases; `"jump"` = 6.52 ms on speaker 1 at t=1200 s; drift 22 ppm on speaker 0).
- [ ] **Step 1: Failing tests:** `test_true_latency_follows_drift_and_jumps`; `test_partial_hears_only_its_subset` (no arrival for unheard speakers); `test_common_offset_cancels_in_differences` (within-recording differences equal truth ± 4·noise); `test_seeds_differ_and_repeat` (same seed → identical; other seed → different).
- [ ] **Step 2: Run** `hatch test tests/test_sync_sim.py` — FAIL.
- [ ] **Step 3: Implement.** Pure numpy, no threads.
- [ ] **Step 4: Run** — PASS.

### Task 3: `sync_methods.robust_ls`

**Files:**
- Create: `host/src/aurasync/sync_methods.py`
- Test: `host/tests/test_sync_methods.py`

**Interfaces:**
- Consumes: `Measurement` (Task 1); `sync_sim` (Task 2) in tests.
- Produces:
  - `@dataclass(frozen=True) class SyncSettings` with the Global Constraints defaults, `SyncSettings.from_dict(d, warn) -> SyncSettings`, `.to_dict()`, `.replace(**changes)` raising `ValueError` on out-of-range (ranges: spec §4.4).
  - `@dataclass(frozen=True) class Fit: latency_ms: dict[str, float]  # at `now`, only placed speakers; sigma_ms: dict[str, float]; drift_ppm: dict[str, float]; jumps: list[tuple[float, str, float]]; used: int; rejected: dict[int, str]  # index -> reason; anchor: str | None; reason: str | None  # why nothing is placed`
  - `def fit(measurements: list[Measurement], settings: SyncSettings, now: float, server_position: str) -> Fit`
  - `def suggested_delays(fit: Fit) -> dict[str, float]` — `max(L) - L_s` over placed speakers (smallest 0).

Algorithm (the part the tests do not fully determine):
- Keep measurements with `t >= now - window_min*60`, `len(heard) >= min_speakers`, and
  (unless `accept_processed_audio`) no truthy `quality` processing flag. Rejected ones go to
  `rejected` with a reason.
- Anchor: the latest `role="target"` measurement's `position_id`; if none, `server_position`.
- Unknowns: per speaker `e_s`, `d_s` (only if the speaker's measurements span ≥
  `arrival_loop.MIN_SPAN_S`, else 0), per measurement `c_k`, per (position, speaker) `b_{p,s}`
  for positions that are not the anchor; step unknowns for confirmed jumps. Time as
  `(t - now)` seconds.
- Rows: `y_{k,s} = e_s + d_s·(t_k-now) + Σ steps + b_{p(k),s} + c_k`, weight `w_k` (the
  measurement's `weight`). Votes add pseudo-rows `b_{p,s} = 0` with weight
  `point_vote_weight`. Anchor positions have no `b` columns.
- Solve weighted least squares with `numpy.linalg.lstsq` (min-norm handles the gauge), then
  IRLS with Huber weights (`huber_ms`), 5 iterations.
- Jumps: after the fit, for each speaker, if its last `jump_repeats` residuals all exceed
  `3*huber_ms` with the same sign and agree within `huber_ms`, add a step at the first of them
  and refit once.
- `sigma_ms`: sqrt of the diagonal of `σ² (AᵀWA)⁺` for `e_s + d_s·0`; `σ²` the weighted
  residual variance.
- Placed speakers: those with at least one anchor or vote row.

- [ ] **Step 1: Failing tests** (each over `seed in (0, 1)` and `window_min in (10, 20)`; truth from `sync_sim.true_latency`; error = suggested-vs-true alignment spread):
  `test_drift_is_followed` (`"drift"`, error < 0.25 ms at every fit after 10 min);
  `test_a_real_jump_is_believed_after_two` (`"jump"`: no step after 1 shifted measurement, step within 0.3 ms of 6.52 after 2);
  `test_a_lone_outlier_is_not_a_jump` (`outlier_rate=0.05`: no jumps, error < 0.3 ms);
  `test_partial_microphones_are_combined` (`"partial"`, two continuous positions hearing {0,1} and {1,2} plus anchor hearing all at start: drift of all three followed);
  `test_a_speaker_without_anchor_gets_no_suggestion` (anchor hears {0,1} only → speaker 2 absent from `latency_ms`, `reason` mentions it);
  `test_one_speaker_is_not_a_measurement` (all measurements with one speaker → `reason` set, nothing placed);
  `test_a_vote_moves_towards_its_spot` (vote position with acoustic offset +1 ms on speaker 0: weight 1 → shift < weight 10 shift);
  `test_biased_continuous_microphone_does_not_move_offsets` (`"biased"`: constant bias on a continuous position changes nothing at the anchor).
- [ ] **Step 2: Run** `hatch test tests/test_sync_methods.py` — FAIL.
- [ ] **Step 3: Implement** `SyncSettings`, `Fit`, `fit`, `suggested_delays`.
- [ ] **Step 4: Run** — PASS. Also time one `fit` over 30 measurements × 8 speakers: < 20 ms (print in test, assert < 100 ms).

### Task 4: `SyncEstimator` (thread, queue, suggestion) and `sync_levels`

**Files:**
- Create: `host/src/aurasync/sync_estimator.py`, `host/src/aurasync/sync_levels.py`
- Test: `host/tests/test_sync_estimator.py`

**Interfaces:**
- Consumes: Tasks 1, 3.
- Produces:
  - `QUEUE_MAX = 64`; `HISTORY_MAX = 2000` (measurements kept, a deque).
  - `@dataclass(frozen=True) class Suggestion: delays_ms: dict[str, float]; current_ms: dict[str, float]; sigma_ms: dict[str, float]; spread_after_ms: float | None; spread_now_ms: float | None; anchor: str | None; based_on: int; at: float; reason: str | None; id: int` (`id` increments per new suggestion).
  - `class SyncEstimator(settings: SyncSettings, current_delays: Callable[[], dict[str, float]], server_position: str = "server", clock=time.monotonic)`: `submit(m: Measurement) -> None` (O(1), drops the oldest queued when full and counts `dropped`); `suggestion -> Suggestion | None`; `settings` / `set_settings(s)` (re-fits); `levels() -> dict` (from `sync_levels`); `sources() -> list[dict]` (per source/position: last seen, kind, count, rejected count); `request_explain() -> None`, `explain -> dict | None` (Task 5 fills it); `wait_idle(timeout) -> bool`; `close()`. Thread name `"aurasync-sync-estimator"`.
  - `sync_levels.summarise(measurements: Iterable[Measurement]) -> dict` → `{"by_position": {pos: {speaker: {"median_db","spread_db","n"}}}, "by_speaker": {speaker: {"spread_across_positions_db"}}}`.
- [ ] **Step 1: Failing tests:** `test_submit_never_blocks` (a fit that sleeps 0.5 s; 200 submits take < 50 ms total; `dropped > 0`); `test_suggestion_appears_off_the_engine_thread` (feed `sync_sim` `"drift"` measurements; suggestion built on thread `"aurasync-sync-estimator"`, `delays_ms` within 0.25 ms of truth-derived delays); `test_suggestion_ids_increase`; `test_levels_are_statistics_only` (`levels()` shape; `Suggestion` has no gain field); `test_close_stops_the_thread`.
- [ ] **Step 2: Run** `hatch test tests/test_sync_estimator.py` — FAIL.
- [ ] **Step 3: Implement.** The worker drains the queue, appends to history, calls `sync_methods.fit` once per drained batch, builds `Suggestion` with `current_delays()`.
- [ ] **Step 4: Run** — PASS.

### Task 5: `knob_docs` — setting descriptors with recommendation, sound and figure; the "together" explanation

**Files:**
- Create: `host/src/aurasync/knob_docs.py`, `host/src/aurasync/sync_docs.py`
- Modify: `host/src/aurasync/sync_estimator.py` (compute `explain` on its thread, cached by settings)
- Test: `host/tests/test_sync_docs.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class Figure: kind: Literal["timeline","bars","histogram","room"]; x: str; y: str; series: list[dict]  # {"label","points":[[x,y],...],"style":"recommended"|"current"|"other"}; marks: list[dict]; caption: str; evidence: Literal["SIMULADO","MEDIDO"]`
  - `@dataclass(frozen=True) class Doc: id: str; title: str; summary: str; help: str; recommended: Any; why_recommended: str; sounds_low: str; sounds_high: str; sounds_choices: dict[str, str]; figure_kind: str` plus `to_dict()`.
  - `sync_docs.DOCS: dict[str, Doc]` — one per `SyncSettings` field (Spanish copy; content from the spec §4.4 table and the in-chat explanations: e.g. `window_min` low: "sigue rápido una deriva, pero sugiere cambios chicos que no hacían falta", high: "sugerencias firmes, pero tarda en ver una deriva: se oye como un eco leve que crece hasta que la aplicás").
  - `sync_docs.figure(setting_id: str, current: SyncSettings, seed: int = 0) -> Figure` — runs `sync_sim.standard(...)` relevant to the setting (window → `"drift"`, jump_repeats → `"jump"`, huber → outliers histogram, method → `"biased"`, point role/weight → `"room"` bars per spot, min_speakers → `"partial"` bars) for the recommended value and the current one; y = alignment error (ms) at the anchor.
  - `sync_docs.together(current: SyncSettings, seed: int = 0) -> dict` → `{"text": str, "figure": Figure}`: text assembled from the settings (2-3 sentences); figure = one simulated hour combining drift + jump + partial + moved, current vs recommended.
  - `SyncEstimator.explain -> {"settings": hash, "docs": {id: Doc.to_dict()|{"figure": Figure}}, "together": {...}} | None`, computed after `request_explain()` or a settings change.
- [ ] **Step 1: Failing tests:** `test_every_setting_has_a_doc` (keys of `DOCS` == fields of `SyncSettings`; every doc has non-empty `recommended`, `why_recommended`, and sounds text); `test_recommended_matches_defaults`; `test_figures_are_simulated_and_compare_current_with_recommended` (two series styled `recommended`/`current`, `evidence == "SIMULADO"`); `test_a_worse_window_shows_more_error` (window_min=2 figure's mean error > recommended's, seeds 0 and 1); `test_together_text_names_the_choices` (with `jump_repeats=1` the text mentions it); `test_explain_is_built_off_the_engine_thread`.
- [ ] **Step 2: Run** `hatch test tests/test_sync_docs.py` — FAIL.
- [ ] **Step 3: Implement.** Figures must cost < 2 s each on PC-Ryzen5 (shorter simulated durations for figures: 20 min).
- [ ] **Step 4: Run** — PASS.

### Task 6: the service: settings file, session hook, ops, REST, snapshot, apply

**Files:**
- Modify: `host/src/aurasync/session.py` (hook after `self._arrivals(result)`: `self.on_measurement(arrivals, valid, t_mid, levels)` if set; and after a finished noise calibration, its delays as a point `target`)
- Modify: `host/src/aurasync/service.py` (own `SyncEstimator`; `sync.json` next to `chain.json`; methods `sync_state`, `sync_set`, `sync_apply`, `sync_explain`)
- Modify: `host/src/aurasync/control.py` (`OPS`: `"sync_state": Op()`, `"sync_set": Op(required={"changes": Field(dict)})`, `"sync_apply": Op(optional={"id": Field(int)})`, `"sync_explain": Op()`)
- Modify: `host/src/aurasync/clients.py` (`READ_OPS` += `sync_state`, `sync_explain`; `CONTROL_OPS` += `sync_set`, `sync_apply`)
- Modify: `host/src/aurasync/rest.py` (`GET /v1/sync`, `PATCH /v1/sync`, `POST /v1/sync/apply`, `GET /v1/sync/explain`)
- Modify: `host/src/aurasync/snapshot.py` (`"sync": svc.sync_brief()` — suggestion id, delays, spread now/after, anchor, reason; no figures)
- Modify: `host/docs/control-api.md` (the four ops and routes)
- Test: `host/tests/test_sync_service.py`, `host/tests/test_rest.py` (route parity)

**Interfaces:**
- Consumes: Tasks 1, 3, 4, 5.
- Produces:
  - `Service.sync_state() -> dict` `{"settings", "suggestion", "levels", "sources", "dropped"}`
  - `Service.sync_set(changes: dict) -> dict` (validates via `SyncSettings.replace`, writes `sync.json` atomically with `write_atomic`, `{"v": 1, "sync": {...}}`)
  - `Service.sync_apply(id: int | None = None) -> dict` — `conflict` if no suggestion, or `id` given and not the current; sets `installation.por_nombre(n).retardo_ms = delays_ms[n]` for suggested speakers only, inside `motor.cortar(...)`, then `motor.actualizar()`, marks installation dirty, logs `sync: applied suggestion <id>: ...`.
  - `Service.sync_explain() -> dict` `{"pending": True}` or the cached `explain`.
  - `Service.sync_brief() -> dict | None`.
- [ ] **Step 1: Failing tests** (simulated service fixture as `tests/test_many_speakers.py::service8`, 3 speakers): `test_the_loop_feeds_the_estimator` (start with recalibration on in simulation; within the test's fake clock a suggestion appears with `anchor == "server"`); `test_apply_sets_absolute_delays` (apply twice → same delays, not doubled); `test_apply_leaves_unsuggested_speakers`; `test_apply_without_suggestion_is_a_conflict`; `test_stale_id_is_a_conflict`; `test_settings_persist` (set, restart service, read back); `test_corrupt_sync_json_falls_back_to_defaults`; `test_scopes` (read client can `sync_state`, cannot `sync_apply` → `forbidden`); `test_snapshot_carries_a_brief_without_figures`; REST parity in `test_rest.py`.
- [ ] **Step 2: Run** `hatch test tests/test_sync_service.py tests/test_rest.py` — FAIL.
- [ ] **Step 3: Implement.** The service creates the estimator with `current_delays=lambda: {p.nombre: p.retardo_ms for p in installation.parlantes}`; closes it in `close()`.
- [ ] **Step 4: Run** — PASS, then the full `PY=python3 scripts/check.sh` (expect only the known `test_interpolation` failure).

### Task 7: the panel card "Sincronía" with suggestion, apply, explained settings and figures

**Files:**
- Modify: `host/src/aurasync/panel/index.html` (a `<section data-card="sync">` in the Calibrar tab, after `calibration`)
- Modify: `host/src/aurasync/panel/app.js` (`renderSync(s)` from `s.sync`; on open, `sync_state` and `sync_explain` via `/v1/command`; "Aplicar" sends `sync_apply` with the suggestion `id`; settings as controls with the doc's recommendation badge, the two sounds texts, and an inline SVG figure drawn by `drawFigure(fig)` for `timeline|bars|histogram|room`; the "En conjunto" block with `together.text` and figure; SIMULADO badge)
- Modify: `host/src/aurasync/panel/tailwind.css` (rebuild if new classes: `cd host && hatch run web:css`)
- Test: `host/tests_browser/test_panel_sync.py`; update the card lists in `tests_browser/test_panel.py`

- [ ] **Step 1: Failing browser test** (`cd host && hatch run browser:test tests_browser/test_panel_sync.py`): simulated service with a suggestion → card shows current vs suggested per speaker; "Aplicar" disabled without suggestion; clicking applies (state shows new delays); every setting shows "Recomendado: …" and its figure has a SIMULADO badge; changing `window_min` updates the together text.
- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement.** Copy in Spanish; figures accessible (each SVG has `<title>` with the caption; series distinguished by dash, not only colour, WCAG 1.4.1).
- [ ] **Step 4: Run** browser test — PASS; run `hatch run browser:test tests_browser/test_panel.py` — PASS.

### Task 8: records

**Files:**
- Modify: `docs/roadmap.md` (new entry `### Estimador base de sincronía alimentado por mediciones continuas y puntuales de varios micrófonos · i-7c8794-737d4e`, state "A medias: pasos 1-2 hechos en simulación"; cross-link from i-7c8794-4745b4)
- Modify: `docs/decisions.md` (rows d-7c8794-589dec, d-7c8794-2c6f91, d-7c8794-ee5d47, d-7c8794-e61118, d-7c8794-0a4586, with "Se hace cumplir en" pointing at the tests above)
- Modify: `docs/research/13-…` §5.3 (link the spec), `.claude/logs/agent-changelog.md`
- [ ] **Step 1:** write them; **Step 2:** `PY=python3 scripts/check.sh` — ids recognised, only the known failure.
