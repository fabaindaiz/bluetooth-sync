# Virtual speakers, sessions without speakers, and joining a session Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Speakers that are only computed (no `pw-play`) and reach the headphone monitor; sessions that open with real speakers absent; and, in phase 2, real speakers joining or leaving a running session, with a `lost` one returning by itself.

**Architecture:** `outputs.py` (new) owns the output side of a session: `OutputSet` routes each speaker's block to the real player or drops it, tracks each speaker's `output` state, and a `Pacer` keeps real time when no real output exists. `AudioSession` talks only to `OutputSet`. Phase 2 prepares a new real player off the engine thread and swaps it at the bottom of a `motor.cortar` fade; `RejoinPolicy` (pure, clock-injected) decides when a `lost` speaker returns.

**Tech Stack:** Python 3.12, numpy; vanilla JS panel (`panel/app.js`) + Preact/TS (`host/web/src/`, built with npm); pytest via `hatch test`; Playwright via `hatch run browser:test`.

**Spec:** `docs/superpowers/specs/2026-10-05-virtual-speakers-and-hot-join-design.md`

## Global Constraints

- New code, identifiers, REST routes, contract fields and API docs in English (d-7c8794-7b3093); panel copy in Spanish; `config.py`, `session.py`, `sonido.py` keep their Spanish names.
- A virtual speaker is `sink: null`; no separate `virtual` field (d-7c8794-0e5063). `sink` stays a required key.
- `output` ∈ `virtual | absent | playing | lost` (`null` without a session); `output_kind` ∈ `virtual | bluetooth | wired`; `bluetooth` = sink starts with `bluez_output.`.
- Rejoin brake: one attempt per speaker every **10 s**; **3 drops within 5 minutes** → stop trying (d-7c8794-618666).
- Cut used for swaps: the existing `motor.cortar(action)` (80 + 80 ms).
- Pacer resynchronises when more than **2 blocks** late; deadline = `origin + k · block / rate`.
- Alternate combine-module node name for a swap: `f"{sink_name}_salida_b"` (alternating with `_salida`).
- Contract version stays 1; additions only.
- No commits (CLAUDE.md: only when the user asks). Each task ends with its tests green and `cd host && hatch fmt --check` clean.
- Nothing touches the WH-CH520 headphones or PipeWire on `HP-O16` until the user allows it.

## Review Focus

1. An installation with only virtual speakers and `recalibrate` on → `start` succeeds, the loop stays off and logs why ("no speaker is playing") — Task 4 `test_recalibration_needs_two_playing`.
2. `calibrate` with no speaker playing → `conflict` ("no speaker is playing"), never a stimulus to nowhere — Task 5 `test_calibrate_without_playing_speakers_is_refused`.
3. `volume.avrcp` with virtual speakers in the installation → only Bluetooth sinks get `pactl`; never `pactl … None` — Task 5 `test_avrcp_skips_non_bluetooth`.
4. During a calibration the monitor in `binaural` still receives every speaker's channel (silence for non-participants), so its filter-chain inputs never go missing — Task 4 `test_monitor_gets_every_channel_during_calibration`.
5. A speaker name with spaces or `/` in the join route (`JBL%20Go%204%20Red`) → the right speaker — Task 9 `test_join_route_decodes_the_name`.

---

## Phase 1

### Task 1: The model — virtual speakers and output kind

**Files:**
- Modify: `host/src/aurasync/config.py` (`Parlante.sink`, `Parlante.virtual`, `Instalacion.__post_init__`)
- Create: `host/src/aurasync/outputs.py` (only `output_kind` for now)
- Modify: `docs/decisions.md` (three rows, section "Arquitectura de audio")
- Test: `host/tests/test_config.py`, `host/tests/test_outputs.py` (new)

**Interfaces:**
- Produces: `Parlante.sink: str | None`; `Parlante.virtual -> bool` (property, `sink is None`); `outputs.output_kind(sink: str | None) -> Literal["virtual", "bluetooth", "wired"]`.

- [ ] **Step 1: Write the failing tests**

```python
# test_config.py
def test_virtual_speaker_survives_save_and_load(tmp_path):
    inst = Instalacion(parlantes=[Parlante("Virtual 1", None, pan=-0.7), Parlante("Virtual 2", None, pan=0.7)])
    inst.guardar(tmp_path / "i.json")
    back = Instalacion.cargar(tmp_path / "i.json")
    assert [p.sink for p in back.parlantes] == [None, None]
    assert all(p.virtual for p in back.parlantes)
    assert '"sink": null' in (tmp_path / "i.json").read_text()

def test_missing_sink_key_is_still_an_error(tmp_path):
    (tmp_path / "i.json").write_text('{"parlantes": [{"nombre": "X"}]}')
    with pytest.raises(TypeError):
        Instalacion.cargar(tmp_path / "i.json")

def test_two_virtual_speakers_are_not_duplicate_sinks():
    Instalacion(parlantes=[Parlante("A", None), Parlante("B", None)])  # no ValueError

# test_outputs.py
@pytest.mark.parametrize(("sink", "kind"), [
    (None, "virtual"), ("bluez_output.90_F2_60_75_4A_83.1", "bluetooth"),
    ("alsa_output.pci-0000_00_1f.3.analog-stereo", "wired"),
])
def test_output_kind(sink, kind):
    assert output_kind(sink) == kind
```

- [ ] **Step 2: Run** `cd host && hatch test tests/test_config.py tests/test_outputs.py` — expect failures (`virtual` missing, module missing).
- [ ] **Step 3: Implement.** `sink: str | None` (still positional, no default); duplicate check over non-`None` sinks; `output_kind` in `outputs.py` with a module docstring citing the spec.
- [ ] **Step 4: Run** the same command — PASS.
- [ ] **Step 5: Decisions.** Add rows d-7c8794-0e5063, d-7c8794-05bdd6, d-7c8794-618666 to `docs/decisions.md` under "Arquitectura de audio", Spanish, with *Por qué* from the spec §1/§5 and *Se hace cumplir en* naming the tests of Tasks 1, 4 and 9. Run `PY=python3 scripts/check.sh` — the id check passes.

### Task 2: The `Pacer`

**Files:**
- Modify: `host/src/aurasync/outputs.py`
- Test: `host/tests/test_outputs.py`

**Interfaces:**
- Produces: `class Pacer(rate: int, block: int, clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep)` with `wait() -> None` (sleep until the next block's deadline, then advance `k`) and `reset() -> None` (origin = now, `k = 0`).

- [ ] **Step 1: Write the failing tests** (fake clock: `sleep` advances it)

```python
def test_n_blocks_take_n_block_durations():
    clock = FakeClock(); p = Pacer(48000, 4096, clock.now, clock.sleep)
    for _ in range(100): p.wait()
    assert clock.t == pytest.approx(100 * 4096 / 48000, abs=4096 / 48000)

def test_deadline_is_a_function_of_one_clock():
    # 50 waits with uneven work between them end at the same instant as 50 waits with none
    a, b = FakeClock(), FakeClock()
    pa, pb = Pacer(48000, 4096, a.now, a.sleep), Pacer(48000, 4096, b.now, b.sleep)
    for i in range(50):
        a.t += 0.03 if i % 2 else 0.0
        pa.wait(); pb.wait()
    assert a.t == pytest.approx(b.t)

def test_late_by_more_than_two_blocks_resyncs_without_burst():
    clock = FakeClock(); p = Pacer(48000, 4096, clock.now, clock.sleep)
    p.wait(); clock.t += 1.0          # a 1 s stall
    p.wait(); before = clock.t; p.wait()
    assert clock.t - before == pytest.approx(4096 / 48000)  # one block, not zero

def test_reset_moves_the_origin_to_now():
    clock = FakeClock(); p = Pacer(48000, 4096, clock.now, clock.sleep)
    clock.t = 10.0; p.reset(); p.wait()
    assert clock.t == pytest.approx(10.0 + 4096 / 48000)
```

- [ ] **Step 2: Run** `cd host && hatch test tests/test_outputs.py` — FAIL.
- [ ] **Step 3: Implement** `Pacer` in `outputs.py`: deadline `origin + (k + 1) · block / rate`; if `now - deadline > 2 · block / rate` → `reset()` then wait one block.
- [ ] **Step 4: Run** — PASS.

### Task 3: `OutputSet`

**Files:**
- Modify: `host/src/aurasync/outputs.py`
- Test: `host/tests/test_outputs.py`

**Interfaces:**
- Consumes: `Pacer`, `output_kind`.
- Produces: `class OutputSet(sinks: dict[str, str | None], pacer: Pacer)`:
  - `attach(player: PlayerLike | None, playing: set[str]) -> PlayerLike | None` — installs the real part (nodes of `playing` names) and returns the previous one for the caller to close; names with a sink not in `playing` become `absent`, `lost` ones that are in `playing` become `playing`.
  - `write(blocks: dict[str, np.ndarray], *, input_paced: bool) -> None` — playing blocks to `player.escribir({node: x})`; others dropped; when no real stream is alive and `input_paced` is false, `pacer.wait()`; when `input_paced`, `pacer.reset()`.
  - `refresh() -> list[str]` — names newly `lost` (their node left `player.vivos`).
  - `states() -> dict[str, str]`, `playing() -> list[str]`, and passthroughs `vivos`, `pids`, `mal_ruteados()`, `reparar_ruteo()`, `soltar(node)`, `close()`.
  - `PlayerLike` is a `Protocol` with `vivos`, `pids`, `escribir`, `mal_ruteados`, `reparar_ruteo`, `soltar`, `cerrar` (the shape `Reproductor`, `ReproductorCombinado` and `SimulatedPlayer` already have; add `cerrar` to `SimulatedPlayer` if missing).

- [ ] **Step 1: Write the failing tests** (use a `FakePlayer` like `tests/test_session.py`'s, plus `cerrar` and a `pids` property; its `escribir` raises `KeyError` for a node it was not opened with, so the double accepts no more than the real one)

```python
def test_only_playing_speakers_reach_the_player():
    out = OutputSet({"A": "sA", "V": None, "B": "sB"}, pacer)
    out.attach(FakePlayer(["sA"]), {"A"})
    out.write({"A": x, "V": x, "B": x}, input_paced=True)
    assert list(player.written[-1]) == ["sA"]
    assert out.states() == {"A": "playing", "V": "virtual", "B": "absent"}

def test_without_a_real_output_the_pacer_keeps_time():   # fake clock pacer
    out = OutputSet({"V": None}, pacer); out.attach(None, set())
    for _ in range(10): out.write({"V": x}, input_paced=False)
    assert clock.t == pytest.approx(10 * 4096 / 48000, abs=4096 / 48000)

def test_input_paced_blocks_do_not_sleep(): ...          # clock.t unchanged after writes with input_paced=True

def test_a_dead_stream_becomes_lost_and_writing_goes_on():
    player.alive.remove("sA"); assert out.refresh() == ["A"]
    assert out.states()["A"] == "lost"; out.write({...}, input_paced=False)  # no raise; pacer used

def test_attach_returns_the_previous_player(): ...
```

- [ ] **Step 2: Run** — FAIL. **Step 3: Implement.** **Step 4: Run** — PASS.

### Task 4: The session on `OutputSet`

**Files:**
- Modify: `host/src/aurasync/session.py` (`missing_speakers`, `AudioSession.__init__`, `_open_streams`, `step`, `_feed_monitor`, `enable_recalibration`, `start_calibration`, `pids`)
- Modify: `host/src/aurasync/simulated.py` (`SimulatedSession.open`: room and player only for playing sinks)
- Test: `host/tests/test_session.py`, `host/tests/test_session_monitor.py`, `host/tests/test_virtual_speakers.py` (new; the simulated-room cases)

**Interfaces:**
- Consumes: `OutputSet`, `Pacer` (Tasks 2–3).
- Produces: `missing_speakers(installation) -> list[str]` returns only real speakers whose sink is absent (never virtual); `AudioSession.outputs: OutputSet`; `AudioSession.lost` keeps its meaning (names `lost`) for existing callers; `AudioSession.output_states() -> dict[str, str]`.

- [ ] **Step 1: Write the failing tests**

```python
def test_opens_with_a_real_speaker_absent(monkeypatch):      # salidas_bluetooth() lists only sA
    s = open_session(monkeypatch, sinks={"A": "sA", "B": "sB"}, present={"sA"})
    assert s.output_states() == {"A": "playing", "B": "absent"}

def test_all_virtual_launches_no_process(monkeypatch):
    monkeypatch.setattr(session_module.subprocess, "Popen", fail_if_called_except_pw_record)
    s = open_session(monkeypatch, sinks={"V1": None, "V2": None}, present=set())
    assert s.output_states() == {"V1": "virtual", "V2": "virtual"}

def test_losing_every_real_speaker_does_not_end_the_session(monkeypatch):
    # previously raised SessionError("unavailable", "every speaker disconnected")
    s.outputs._player.alive.clear(); s.step(); s.step()
    assert s.output_states()["A"] == "lost"; assert any(k == "parlante perdido" for k, _ in events)

def test_monitor_gets_every_channel(monkeypatch): ...         # virtual + absent + playing names all pushed
def test_monitor_gets_every_channel_during_calibration(monkeypatch): ...  # non-participants as zeros
def test_calibration_uses_only_playing_speakers(monkeypatch):
    s.start_calibration(5.0, 0.05, "mic"); assert set(s.calibration.references) == {"A"}
def test_recalibration_needs_two_playing(monkeypatch):
    # one playing + one virtual: enable_recalibration logs "no speaker is playing"/"needs two" and leaves loop None
def test_simulated_room_never_hears_a_virtual_speaker(): ...  # Room names == playing names only
```

- [ ] **Step 2: Run** `cd host && hatch test tests/test_session.py tests/test_session_monitor.py` — FAIL.
- [ ] **Step 3: Implement.**
  - `open()`: drop the `missing_speakers` failure; `connected = {n for n, sink in sinks if sink and sink in present}`.
  - `_open_streams`: build the real player only if `connected` is non-empty (same ordering, silence, routing check as today, over those nodes), then `self.outputs.attach(player, connected)`. With none, open only the `SinkVirtual`.
  - `step`: `self.outputs.write(blocks, input_paced=pair is not None)` replaces `self._player.escribir`; the "every speaker disconnected" raise goes; `outputs.refresh()` feeds `self.lost`, the cut log and the "parlante perdido" event.
  - `_feed_monitor`: fill missing names with `self._silence`.
  - Calibration and `VentanaDeEmision` built over `self.outputs.playing()`; `enable_recalibration` refuses with a log line when fewer than two are playing.
  - `step()`'s "not open" guard checks `self._input` only.
- [ ] **Step 4: Run** the whole suite `cd host && hatch test` — PASS (existing session tests keep passing through `OutputSet`).

### Task 5: Service and contract, phase 1

**Files:**
- Modify: `host/src/aurasync/control.py` (`OPS["speaker_add_virtual"]`)
- Modify: `host/src/aurasync/clients.py` (`ADMIN_OPS` += `speaker_add_virtual`)
- Modify: `host/src/aurasync/service.py` (`speaker_add_virtual`, `_sinks` → Bluetooth only, `calibrate` refusal)
- Modify: `host/src/aurasync/snapshot.py` (`output`, `output_kind`, Bluetooth-only fields)
- Modify: `host/src/aurasync/monitor_control.py` (`forbidden_targets` skips `None`, adds `_salida_b`)
- Modify: `host/docs/control-api.md`
- Test: `host/tests/test_virtual_speakers.py` (service and snapshot), `host/tests/test_control.py`, `host/tests/test_clients.py`, `host/tests/test_monitor_control.py`

**Interfaces:**
- Consumes: `Parlante.virtual`, `output_kind`, `AudioSession.output_states()`.
- Produces: op `speaker_add_virtual {name?: str 1–64}` (admin, session stopped; default names "Virtual 1", "Virtual 2"…, first free); snapshot speaker fields `output`, `output_kind`; `address`, `battery_pct`, `codec`, `rssi_dbm`, `modalias` are `null` unless `output_kind == "bluetooth"`.

- [ ] **Step 1: Write the failing tests**

```python
def test_speaker_add_virtual_takes_the_next_free_role(service): ...      # pan/ambience from control.layout_roles
def test_speaker_add_virtual_default_names_and_conflict(service): ...    # "Virtual 1", "Virtual 2"; same name → conflict
def test_speaker_add_virtual_needs_admin(): ...                          # control token → forbidden
def test_snapshot_virtual_speaker_has_no_bluetooth_fields(service):
    sp = speaker(state, "Virtual 1")
    assert sp["output_kind"] == "virtual" and sp["sink"] is None and sp["address"] is None
def test_snapshot_output_is_null_without_session(service): ...
def test_avrcp_skips_non_bluetooth(service): ...                         # fake backend sees only bluez sinks
def test_calibrate_without_playing_speakers_is_refused(service): ...     # conflict "no speaker is playing"
def test_monitor_forbidden_targets_ignore_virtual(): assert None not in forbidden_targets(inst, "aurasync")
```

- [ ] **Step 2: Run** — FAIL. **Step 3: Implement.** **Step 4: Run** `cd host && hatch test` — PASS.
- [ ] **Step 5: Docs.** `host/docs/control-api.md`: the op, the two snapshot fields, `sink` nullable, the Bluetooth-only fields, and the version-mix note (old panels show a virtual speaker as "desconectado"/"perdido"; no crash).

### Task 6: Panel, phase 1

**Files:**
- Modify: `host/src/aurasync/panel/app.js` (`SPEAKER_STATUS`, `speakerState`, the devices card)
- Modify: `host/web/src/undo.ts` (re-add a removed virtual speaker with `speaker_add_virtual {name}`)
- Rebuild: `host/src/aurasync/panel/cadena.js` and its stamp (`cd host/web && npm run build`)
- Test: `host/web/test/undo.test.ts` (vitest), `host/tests_browser/test_panel_virtual.py` (new)

**Interfaces:**
- Consumes: snapshot `output`, `output_kind`; op `speaker_add_virtual`.

- [ ] **Step 0: Old reader, new data — before touching `app.js`.** Write browser test `test_new_snapshot_on_the_old_panel` (a routed snapshot with a speaker `{sink: null, address: null, output: "virtual", output_kind: "virtual"}`): no console error, the row renders. Run it on the unmodified panel — it must PASS; this is the published PWA's behaviour against the new service.
- [ ] **Step 1: Write the failing tests**
  - vitest `undo restores a virtual speaker by name`: plan for a removed `{name: "Virtual 1", sink: null, address: null}` is `{op: "speaker_add_virtual", name: "Virtual 1"}`.
  - browser (against `aurasync service --simular`, the `tests_browser/conftest.py` fixture): click **Agregar parlante virtual** → a row "Virtual 1" with status "virtual"; start → still "virtual"; no console errors.
  - browser `test_old_snapshot_without_output_still_renders`: route a fixture snapshot without `output` (the demo fixture) → statuses derived from `playing`/`connected` as today.
- [ ] **Step 2: Run** `cd host/web && npm test` and `cd host && hatch run browser:test -k virtual` — FAIL.
- [ ] **Step 3: Implement.** `speakerState(s, sp)` returns `sp.output` when present (`virtual` → "virtual", `absent` → "sin conectar", `playing` → "sonando", `lost` → "perdido"), else today's derivation. The button **Agregar parlante virtual** in the devices card (admin token) sends `speaker_add_virtual`. `npm run build`.
- [ ] **Step 4: Run** both commands — PASS; `PY=python3 scripts/check.sh` — the web stamp passes.

### Task 7: Phase 1 documentation and the `HP-O16` protocol

**Files:**
- Modify: `host/README.md` (virtual speakers, sessions with speakers absent, how to hear them on the monitor)
- Modify: `docs/roadmap.md` (new item `### Parlantes virtuales, sesión sin parlantes reales y entrada en caliente · i-7c8794-757041` under "Plan desde el 2026-10-02": phase 1 built, phase 2 pending, the two experiments)
- Create: `docs/research/experimentos/18-parlantes-virtuales-y-monitor-en-hp-o16.md` (protocol only, no result: blocks in real time with all virtual — Dummy-Driver INFERIDO —, monitor drops per minute, no `pw-play` to a speaker in `pw-dump`; precondition: the user allows the WH-CH520 in A2DP)
- Modify: `.claude/logs/agent-changelog.md` (entry at the top, format at the end of the file)

- [ ] **Step 1:** Write the four documents.
- [ ] **Step 2:** `PY=python3 scripts/check.sh` — `check: ok`.

## Phase 2

### Task 8: Joining and leaving in the session

**Files:**
- Modify: `host/src/aurasync/session.py` (`request_output(playing: set[str], done)`; a worker that builds and feeds the new player; the swap in `step`)
- Modify: `host/src/aurasync/simulated.py` (`SimulatedSession` builds a `SimulatedPlayer` for a new set)
- Test: `host/tests/test_session_join.py` (new)

**Interfaces:**
- Consumes: `OutputSet.attach`, `motor.cortar(action)`.
- Produces: `AudioSession.request_output(playing: set[str], done: Callable[[str | None], None]) -> None` — non-blocking; `done(None)` after the swap, `done(message)` if preparation failed (state untouched). One request at a time: a second while one is pending → `SessionError("conflict", "a speaker change is already in progress")`. In `combinado` the new player is named with the alternate `_salida`/`_salida_b`; while preparing, the worker keeps the new player fed with silence on a `Pacer`; at the next `step` after it is ready, the session calls `motor.cortar(swap)`, and `swap` runs `outputs.attach`, hands the old player to the routing pool to close, and — if the recalibration loop is on — restarts it (`disable_recalibration` + `enable_recalibration`) over the new playing set.

- [ ] **Step 1: Write the failing tests** (fake player factory; fake motor whose `cortar(action)` records the action and runs it on the next `procesar`)

```python
def test_join_swaps_at_the_bottom_of_the_cut(): ...        # attach not called before the cut's action runs
def test_failed_preparation_leaves_state_intact(): ...     # factory's routing check fails → done("…"), states unchanged
def test_leave_keeps_the_speaker_computed(): ...           # A leaves → "absent", still in monitor blocks
def test_second_request_while_pending_is_a_conflict(): ...
def test_old_player_is_closed_off_the_engine_thread(): ... # cerrar called from the pool, not from step()
def test_loop_restarts_over_the_new_playing_set(): ...   # loop on: after the swap its emission windows cover the new playing names
```

- [ ] **Step 2: Run** — FAIL. **Step 3: Implement.** **Step 4: Run** `cd host && hatch test` — PASS.

### Task 9: Service — join, leave, automatic return

**Files:**
- Create: `host/src/aurasync/rejoin.py` (`RejoinPolicy`)
- Modify: `host/src/aurasync/control.py` (`speaker_join`, `speaker_leave`), `host/src/aurasync/clients.py` (`CONTROL_OPS`), `host/src/aurasync/rest.py` (`POST /v1/speakers/{name}/join|leave`)
- Modify: `host/src/aurasync/service.py` (`speaker_join`, `speaker_leave`, the automatic return in the tick that refreshes views)
- Modify: `host/docs/control-api.md`
- Test: `host/tests/test_rejoin.py` (new), `host/tests/test_service.py`, `host/tests/test_rest.py`

**Interfaces:**
- Consumes: `AudioSession.request_output`, `output_states()`, `observer.view["outputs"]`.
- Produces: `RejoinPolicy(clock: Callable[[], float] = time.monotonic)` with `note_lost(name)`, `note_left(name)` (user `leave`: never automatic), `may_try(name) -> bool` (`lost`, not left, ≥ 10 s since last try, < 3 drops in 300 s), `note_try(name)`, `gave_up(name) -> bool`, `clear(name)`; ops `speaker_join {speaker}`, `speaker_leave {speaker}` with the errors of spec §6.

- [ ] **Step 1: Write the failing tests**

```python
def test_policy_waits_ten_seconds_between_tries(): ...
def test_policy_gives_up_after_three_drops_in_five_minutes(): ...
def test_policy_never_tries_a_left_or_absent_speaker(): ...
def test_join_errors(service): ...           # no session → conflict; virtual/playing → conflict; sink missing → unavailable
def test_leave_of_not_playing_is_conflict(service): ...
def test_lost_speaker_returns_when_its_sink_reappears(service): ...  # fake observer view; one join requested; log "volvió"
def test_join_route_decodes_the_name(): assert route("POST", "/v1/speakers/JBL%20Go%204%20Red/join", None)["speaker"] == "JBL Go 4 Red"
def test_join_needs_control_scope(): ...
```

- [ ] **Step 2: Run** — FAIL. **Step 3: Implement.** The automatic return never calls `connect` (no Bluetooth reconnect). **Step 4: Run** `cd host && hatch test` — PASS.
- [ ] **Step 5: Docs.** `host/docs/control-api.md`: the two ops, the routes, the automatic return and its brake.

### Task 10: Panel, phase 2, and closing documentation

**Files:**
- Modify: `host/src/aurasync/panel/app.js` (per-speaker **Hacer entrar** / **Sacar** / **Reintentar**; "la alineación puede haber cambiado: recalibrá" after a join with the loop off; calibration lists who is left out)
- Rebuild if `host/web/src/` changed: `cd host/web && npm run build`
- Create: `docs/research/experimentos/19-entrada-en-caliente-con-3-go-4.md` (protocol for `PC-Ryzen5`: switch one Go 4 off/on, join, leave, offset before/after with the microphone)
- Modify: `host/README.md` (phase 2: **sin validar con parlantes**), `docs/roadmap.md` (i-7c8794-757041 state), `.claude/logs/agent-changelog.md`
- Test: `host/tests_browser/test_panel_virtual.py`

- [ ] **Step 1: Write the failing browser tests** against `--simular`: a simulated speaker disconnected and reconnected through `SimulatedObserver` returns to "sonando" with the log line; **Sacar** → "sin conectar"; **Hacer entrar** → "sonando"; the warning appears with the loop off.
- [ ] **Step 2: Run** `cd host && hatch run browser:test -k virtual` — FAIL. **Step 3: Implement.** **Step 4: Run** — PASS.
- [ ] **Step 5:** Write the documents; `PY=python3 scripts/check.sh` — `check: ok`.
