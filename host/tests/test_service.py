"""The service with a fake audio session of the same shape as `session.AudioSession`."""

import json
import os
import stat
import threading
import time
from typing import ClassVar

import numpy as np
import pytest

from aurasync import service as service_module
from aurasync.config import Instalacion, Parlante
from aurasync.service import ConfigError, Service, load_config
from aurasync.session import SessionError


class FakeSession:
    instances: ClassVar[list] = []
    fail_open: SessionError | None = None
    fail_after: int | None = None

    def __init__(self, _installation, motor, options, _log):
        self.motor = motor
        self.installation = _installation
        self.options = options
        self.steps = 0
        self.closed = 0
        self.last_recalibration = None
        self.applied_between_steps = []
        FakeSession.instances.append(self)

    def open(self):
        if FakeSession.fail_open is not None:
            raise FakeSession.fail_open

    def step(self):
        self.steps += 1
        if FakeSession.fail_after is not None and self.steps > FakeSession.fail_after:
            msg = "every speaker disconnected"
            raise SessionError("unavailable", msg)  # noqa: EM101 - the code, not the message
        self.motor.procesar(np.zeros(512), np.zeros(512))
        time.sleep(0.002)

    def close(self):
        self.closed += 1

    def output_states(self):
        lost = getattr(self, "lost", [])
        return {
            p.nombre: "virtual" if p.virtual else ("lost" if p.nombre in lost else "playing")
            for p in self.installation.parlantes
        }

    def set_probe(self, active, margin_db=None):
        self.probe_calls = [*getattr(self, "probe_calls", []), (active, margin_db)]

    def probe_state(self):
        calls = getattr(self, "probe_calls", [])
        return {"active": bool(calls and calls[-1][0]), "margin_db": -20.0, "reference": "music"}


@pytest.fixture(autouse=True)
def _reset_fake():
    FakeSession.instances = []
    FakeSession.fail_open = None
    FakeSession.fail_after = None


def _installation(path):
    Instalacion(
        parlantes=[Parlante("Go 4 Red", "s0", pan=-0.7), Parlante("Go 4 Blue", "s1", pan=0.7, ambiente=0.5)]
    ).guardar(path)


@pytest.fixture
def running(tmp_path):
    _installation(tmp_path / "inst.json")
    svc = Service(tmp_path / "inst.json", tmp_path / "presets.json", session_factory=FakeSession, log=lambda _: None)
    thread = threading.Thread(target=svc.run, daemon=True)
    thread.start()
    yield svc
    svc.handle({"v": 1, "op": "shutdown"})
    thread.join(timeout=5)
    assert not thread.is_alive()


def _ok(svc, **message):
    reply = svc.handle({"v": 1, **message})
    assert reply["ok"], reply
    return reply["result"]


def _err(svc, **message):
    reply = svc.handle({"v": 1, **message})
    assert not reply["ok"], reply
    return reply["error"]["code"]


def _wait(predicate, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(0.01)
    pytest.fail("timed out")


def test_start_set_stop(running):
    _ok(running, op="start")
    assert _ok(running, op="state")["session"]["status"] == "playing"
    seq = _ok(running, op="set", speaker="Go 4 Red", changes={"pan": 0.2})["sequence"]
    state = _ok(running, op="state")
    assert state["sequence"] == seq
    assert state["speakers"][0]["pan"] == 0.2
    _ok(running, op="stop")
    assert _ok(running, op="state")["session"]["status"] == "stopped"
    assert FakeSession.instances[0].closed == 1


def test_start_twice_is_a_conflict(running):
    _ok(running, op="start")
    assert _err(running, op="start") == "conflict"


def test_set_works_with_the_session_stopped_and_is_what_plays_on_start(running):
    _ok(running, op="set", changes={"volume_db": -30, "rear_delay_ms": 20})
    _ok(running, op="start")
    motor = FakeSession.instances[0].motor
    assert motor.volumen_db == -30
    assert motor.instalacion.retardo_traseros_ms == 20


def test_unknown_speaker_is_not_found_and_changes_nothing(running):
    before = _ok(running, op="state")
    assert _err(running, op="set", speaker="Nope", changes={"pan": 0.1}) == "not_found"
    assert _ok(running, op="state")["sequence"] == before["sequence"]


def test_a_speaker_missing_at_start_is_unavailable_and_nothing_plays(running):
    FakeSession.fail_open = SessionError("unavailable", "not connected: Go 4 Red")
    assert _err(running, op="start") == "unavailable"
    state = _ok(running, op="state")
    assert state["session"]["status"] == "error"
    assert "Go 4 Red" in state["session"]["reason"]
    assert FakeSession.instances[0].closed == 1


def test_a_failing_session_leaves_the_program_alive(running):
    FakeSession.fail_after = 3
    _ok(running, op="start")
    _wait(lambda: _ok(running, op="state")["session"]["status"] == "error")
    assert FakeSession.instances[0].closed == 1
    # Still answering, and able to start again.
    FakeSession.fail_after = None
    _ok(running, op="start")
    assert _ok(running, op="state")["session"]["status"] == "playing"


def test_shutdown_closes_the_session(tmp_path):
    _installation(tmp_path / "inst.json")
    svc = Service(tmp_path / "inst.json", tmp_path / "p.json", session_factory=FakeSession, log=lambda _: None)
    thread = threading.Thread(target=svc.run, daemon=True)
    thread.start()
    _ok(svc, op="start")
    _ok(svc, op="shutdown")
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert FakeSession.instances[0].closed == 1


def test_no_installation_starts_anyway_and_says_so(tmp_path):
    svc = Service(tmp_path / "none.json", tmp_path / "p.json", session_factory=FakeSession, log=lambda _: None)
    assert any("no installation" in w for w in svc.snapshot["warnings"])
    thread = threading.Thread(target=svc.run, daemon=True)
    thread.start()
    assert _err(svc, op="start") == "not_found"
    _ok(svc, op="shutdown")
    thread.join(timeout=5)


def test_presets_round_trip_and_load_goes_through_the_fade(running):
    _ok(running, op="set", speaker="Go 4 Red", changes={"pan": -0.3, "ambience": 0.6})
    _ok(running, op="preset_save", name="wide")
    _ok(running, op="set", speaker="Go 4 Red", changes={"pan": 0.9})
    _ok(running, op="start")
    motor = FakeSession.instances[0].motor
    _ok(running, op="preset_load", name="wide")
    _wait(lambda: _ok(running, op="state")["speakers"][0]["pan"] == -0.3)
    assert _ok(running, op="state")["preset"] == "wide"
    assert "wide" in _ok(running, op="presets")["presets"]
    assert motor is FakeSession.instances[0].motor


def test_preset_load_fades_even_when_nothing_changes(running):
    _ok(running, op="preset_save", name="same")
    _ok(running, op="start")
    motor = FakeSession.instances[0].motor
    cuts = []
    original = motor.cambiar
    motor.cambiar = lambda accion=None: (cuts.append(1), original(accion))
    _ok(running, op="preset_load", name="same")
    assert cuts == [1]


def test_preset_naming_an_unknown_speaker_applies_nothing(running, tmp_path):
    (tmp_path / "presets.json").write_text(
        json.dumps({"v": 1, "presets": {"x": {"speakers": {"Ghost": {"pan": 1.0}, "Go 4 Red": {"pan": 1.0}}}}})
    )
    running.preset_store = type(running.preset_store)(tmp_path / "presets.json")
    assert _err(running, op="preset_load", name="x") == "not_found"
    assert _ok(running, op="state")["speakers"][0]["pan"] == -0.7


def test_decorrelate_off_adds_the_warning_and_goes_through_the_fade(running):
    _ok(running, op="start")
    motor = FakeSession.instances[0].motor
    _ok(running, op="set", changes={"decorrelate": False})
    assert any("decorrelation" in w for w in _ok(running, op="state")["warnings"])
    _wait(lambda: not motor.decorrelacion_activa)


def test_save_writes_the_installation_atomically(running, tmp_path):
    _ok(running, op="set", speaker="Go 4 Red", changes={"gain_db": -4})
    _ok(running, op="save")
    assert Instalacion.cargar(tmp_path / "inst.json").por_nombre("Go 4 Red").ganancia_db == -4


def test_commands_are_applied_only_between_steps(tmp_path):
    """Single writer: a command is applied between two `step` calls, never during one."""
    _installation(tmp_path / "inst.json")
    inside = threading.Event()
    applied_inside = []

    class Watching(FakeSession):
        def step(self):
            inside.set()
            time.sleep(0.01)
            inside.clear()
            time.sleep(0.001)

    class Recording(Service):
        def set_speaker(self, speaker, changes):
            applied_inside.append(inside.is_set())
            return super().set_speaker(speaker, changes)

    svc = Recording(tmp_path / "inst.json", tmp_path / "p.json", session_factory=Watching, log=lambda _: None)
    thread = threading.Thread(target=svc.run, daemon=True)
    thread.start()
    _ok(svc, op="start")
    assert inside.wait(timeout=2), "the session never stepped"
    for i in range(20):
        _ok(svc, op="set", speaker="Go 4 Red", changes={"pan": i / 20})
        time.sleep(0.003)  # spread the commands over many steps
    _ok(svc, op="shutdown")
    thread.join(timeout=5)
    assert len(applied_inside) == 20
    assert not any(applied_inside)


# -- service.json -------------------------------------------------------------------


def test_missing_config_is_created_with_a_token_and_mode_600(tmp_path):
    path = tmp_path / "aurasync" / "service.json"
    config = load_config(path)
    assert len(config.token) >= 43
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert load_config(path).token == config.token


def test_wider_permissions_refuse_to_start(tmp_path):
    path = tmp_path / "service.json"
    load_config(path)
    os.chmod(path, 0o644)
    with pytest.raises(ConfigError, match="chmod 600"):
        load_config(path)


@pytest.mark.parametrize("content", ["{nope", '{"color": "red"}', '{"token": "short"}', "[]"])
def test_invalid_config_refuses_to_start(tmp_path, content):
    path = tmp_path / "service.json"
    path.write_text(content)
    os.chmod(path, 0o600)
    with pytest.raises(ConfigError):
        load_config(path)


def test_the_token_is_written_before_anything_listens(tmp_path, monkeypatch):
    order = []
    real = service_module.write_atomic
    monkeypatch.setattr(service_module, "write_atomic", lambda *a, **k: (order.append("write"), real(*a, **k)))
    load_config(tmp_path / "service.json")
    assert order == ["write"]


def test_the_default_installation_respects_xdg(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    config = load_config(tmp_path / "aurasync" / "service.json")
    assert config.installation_path == tmp_path / "aurasync" / "instalacion.json"


def test_a_lost_speaker_is_reported_and_the_rest_keep_playing(running):
    _ok(running, op="start")
    FakeSession.instances[0].lost = ["Go 4 Blue"]
    _wait(lambda: not _ok(running, op="state")["speakers"][1]["playing"])
    state = _ok(running, op="state")
    assert state["session"]["status"] == "playing"
    assert state["speakers"][0]["playing"]
    assert any("Go 4 Blue" in w for w in state["warnings"])


def test_the_masked_probe_is_off_by_default_and_switches_live(running):
    """The probe (dsp/probe.py) is off until the blind A/B says it is inaudible
    (i-7c8794-e3e40d); switching it, or its margin, reaches the playing session at once."""
    assert _ok(running, op="state")["global"]["probe"] is False
    _ok(running, op="start")
    session = FakeSession.instances[0]
    assert session.options.probe is False
    _ok(running, op="set", changes={"probe": True, "probe_margin_db": -25.0})
    assert session.probe_calls[-1] == (True, -25.0)
    state = _ok(running, op="state")
    assert state["global"]["probe"] is True
    assert state["global"]["probe_margin_db"] == -25.0
    assert state["recalibration"]["probe"]["active"] is True
    assert _err(running, op="set", changes={"probe_margin_db": -5.0}) == "out_of_range"


def test_a_session_starts_with_the_chosen_probe(running):
    _ok(running, op="set", changes={"probe": True, "probe_margin_db": -30.0})
    _ok(running, op="start")
    options = FakeSession.instances[0].options
    assert (options.probe, options.probe_margin_db) == (True, -30.0)


@pytest.mark.parametrize("sink", ["aurasync_salida", "aurasync_salida_b"])
def test_xruns_of_either_combine_sink_are_the_combined_output(tmp_path, sink):
    """A speaker change alternates the two combine sinks (session.request_output): an xrun in the
    `pw-play` feeding either is the combined output's, not an unknown node's."""
    from types import SimpleNamespace

    from aurasync.cuts import CutLog

    _installation(tmp_path / "inst.json")
    svc = Service(tmp_path / "inst.json", tmp_path / "p.json", session_factory=FakeSession, log=lambda _: None)
    svc.session = SimpleNamespace(cuts=CutLog())
    for at, total in ((1.0, 2), (2.0, 5)):
        svc.observer = SimpleNamespace(view={"at": at, "xruns": {sink: {"total": total}}})
        svc._watch_system_cuts()  # noqa: SLF001
    [event] = [e for e in svc.session.cuts.summary()["events"] if e["kind"] == "xrun"]
    assert event["where"] == "salida combinada"
    svc.session.cuts.close()


# -- presets, the A/B and calibrations through the chain's `transition` mode ----------------------


class FrozenSession(FakeSession):
    """Never processes a block: what the motor was asked for stays pending, to be looked at."""

    def step(self):
        self.steps += 1
        time.sleep(0.002)


@pytest.fixture
def frozen(tmp_path):
    _installation(tmp_path / "inst.json")
    svc = Service(tmp_path / "inst.json", tmp_path / "presets.json", session_factory=FrozenSession, log=lambda _: None)
    thread = threading.Thread(target=svc.run, daemon=True)
    thread.start()
    yield svc
    svc.handle({"v": 1, "op": "shutdown"})
    thread.join(timeout=5)
    svc.close()


def test_a_preset_of_pan_gain_and_delay_loads_without_a_cut(frozen):
    _ok(frozen, op="set", speaker="Go 4 Red", changes={"pan": -0.3, "gain_db": -2.0})
    _ok(frozen, op="preset_save", name="wide")
    _ok(frozen, op="set", speaker="Go 4 Red", changes={"pan": 0.9, "gain_db": 0.0})
    _ok(frozen, op="start")
    motor = FrozenSession.instances[0].motor
    _ok(frozen, op="preset_load", name="wide")
    assert not motor._corte.busy  # noqa: SLF001
    assert motor._transicion.busy  # noqa: SLF001
    assert motor.en_corte


def test_a_preset_with_an_eq_change_crossfades(frozen):
    """Stage 1 cut once for any stateful chain change; since stage 2 the EQ (like diffuse, bass, the
    decorrelator, the extractor and a limiter of equal latency) crossfades whole objects, so the
    load goes through the transition with its fields, with no cut."""
    _ok(frozen, op="chain_set", stage="eq", algorithm="boost_only")
    _ok(frozen, op="set", speaker="Go 4 Red", changes={"pan": -0.3})
    _ok(frozen, op="preset_save", name="equalised")
    _ok(frozen, op="chain_set", stage="eq", algorithm="off")
    _ok(frozen, op="set", speaker="Go 4 Red", changes={"pan": 0.9})
    _ok(frozen, op="start")
    motor = FrozenSession.instances[0].motor
    _ok(frozen, op="preset_load", name="equalised")
    assert not motor._corte.busy  # noqa: SLF001
    assert motor._transicion.busy  # noqa: SLF001
    # The fields wait for the start of the transition with everything else.
    assert frozen.installation.por_nombre("Go 4 Red").pan == 0.9
    motor.procesar(np.zeros(8192), np.zeros(8192))
    assert frozen.installation.por_nombre("Go 4 Red").pan == -0.3
    _through(motor)
    assert not motor.en_corte


def test_a_preset_with_a_latency_changing_limiter_still_cuts(frozen):
    """A limiter whose lookahead (its latency) changes cannot be crossfaded: it keeps the stage 1
    behaviour, one cut, with the fields joining it at the bottom."""
    _ok(frozen, op="chain_set", stage="limiter", algorithm="true_peak", params={"lookahead_ms": 4.0})
    _ok(frozen, op="set", speaker="Go 4 Red", changes={"pan": -0.3})
    _ok(frozen, op="preset_save", name="lookahead")
    _ok(frozen, op="chain_reset", stage="limiter", param="lookahead_ms")
    _ok(frozen, op="set", speaker="Go 4 Red", changes={"pan": 0.9})
    _ok(frozen, op="start")
    motor = FrozenSession.instances[0].motor
    _ok(frozen, op="preset_load", name="lookahead")
    assert motor._corte.busy  # noqa: SLF001
    assert not motor._transicion.busy  # noqa: SLF001
    assert frozen.installation.por_nombre("Go 4 Red").pan == 0.9
    motor.procesar(np.zeros(8192), np.zeros(8192))
    assert frozen.installation.por_nombre("Go 4 Red").pan == -0.3
    motor.procesar(np.zeros(16384), np.zeros(16384))
    assert not motor.en_corte


def test_a_preset_in_cut_mode_still_cuts(frozen):
    _ok(frozen, op="preset_save", name="same")
    _ok(frozen, op="chain_set", stage="transition", algorithm="cut")
    _ok(frozen, op="start")
    motor = FrozenSession.instances[0].motor
    _ok(frozen, op="preset_load", name="same")
    assert motor._corte.busy  # noqa: SLF001
    assert not motor._transicion.busy  # noqa: SLF001


def _two_presets(svc):
    _ok(svc, op="preset_save", name="a")
    _ok(svc, op="set", speaker="Go 4 Blue", changes={"ambience": 0.9})
    _ok(svc, op="preset_save", name="b")
    _ok(svc, op="start")
    return FrozenSession.instances[0].motor


def test_ab_play_between_equal_presets_requests_a_transition(frozen):
    _ok(frozen, op="preset_save", name="a")
    _ok(frozen, op="preset_save", name="b")
    _ok(frozen, op="start")
    motor = FrozenSession.instances[0].motor
    _ok(frozen, op="ab_start", a="a", b="b")
    assert motor.en_corte
    assert motor._transicion.busy  # noqa: SLF001
    assert not motor._corte.busy  # noqa: SLF001


def test_ab_play_compensation_joins_the_preset_transition(frozen):
    motor = _two_presets(frozen)
    _ok(frozen, op="ab_start", a="a", b="b")
    began = []
    original = motor._transicion.begin  # noqa: SLF001
    motor._transicion.begin = lambda *args, **kw: (began.append(1), original(*args, **kw))  # noqa: SLF001
    # Nothing was processed since the first play: the next one joins that same transition.
    _ok(frozen, op="ab_play", which="b")
    assert began == []
    # ab_start played a (its fields, its compensation) and ab_play b joined: same order, one fade.
    names = [f.__qualname__.split(".")[-1] for f in motor._transicion._starting]  # noqa: SLF001
    assert names == ["apply_fields", "<lambda>", "apply_fields", "<lambda>"]
    assert motor._transicion._pending == []  # noqa: SLF001


def test_calibration_apply_crossfades_the_delays(frozen):
    _ok(frozen, op="start")
    motor = FrozenSession.instances[0].motor
    cal = type("Cal", (), {"describe": lambda _self: {}})()
    cal.state = "done"
    cal.results = [
        {
            "speaker": name,
            "silent": False,
            "doubtful": False,
            "delay_ms": d,
            "gain_db": 0.0,
            "applied_delay_ms": 0.0,
            "applied_gain_db": 0.0,
        }
        for name, d in (("Go 4 Red", 8.0), ("Go 4 Blue", 0.0))
    ]
    FrozenSession.instances[0].calibration = cal
    FrozenSession.instances[0].loop = None
    _ok(frozen, op="calibration_apply")
    assert not motor._corte.busy  # noqa: SLF001
    assert motor._transicion.busy  # noqa: SLF001
    motor.procesar(np.zeros(512), np.zeros(512))
    assert motor._lineas["Go 4 Red"].fundiendo  # noqa: SLF001
    motor.procesar(np.zeros(1 << 16), np.zeros(1 << 16))
    assert not motor.en_corte
    assert motor._lineas["Go 4 Red"].actual_ms == pytest.approx(8.0, abs=0.05)  # noqa: SLF001


def _seeded_preset_assignment(tmp_path, mode):
    """Load a preset that changes `decorrelate.seed` and the pans; return the motor after its bottom."""
    _installation(tmp_path / "inst.json")
    svc = Service(tmp_path / "inst.json", tmp_path / "presets.json", session_factory=FrozenSession, log=lambda _: None)
    thread = threading.Thread(target=svc.run, daemon=True)
    thread.start()
    try:
        _ok(svc, op="chain_set", stage="decorrelate", params={"seed": 5})
        _ok(svc, op="set", speaker="Go 4 Red", changes={"pan": 0.9, "ambience": 0.9})
        _ok(svc, op="set", speaker="Go 4 Blue", changes={"pan": -0.9, "ambience": 0.1})
        _ok(svc, op="preset_save", name="p")
        _ok(svc, op="chain_reset", stage="decorrelate", param="seed")
        _ok(svc, op="set", speaker="Go 4 Red", changes={"pan": -0.7, "ambience": 0.0})
        _ok(svc, op="set", speaker="Go 4 Blue", changes={"pan": 0.7, "ambience": 0.5})
        _ok(svc, op="chain_set", stage="transition", algorithm=mode)
        _ok(svc, op="start")
        motor = FrozenSession.instances[-1].motor
        _ok(svc, op="preset_load", name="p")
        # Stage 2: the seed change crossfades in `crossfade` mode and cuts in `cut` mode.
        assert motor._corte.busy is (mode == "cut")  # noqa: SLF001
        assert motor._transicion.busy is (mode == "crossfade")  # noqa: SLF001
        _through(motor)
        return list(motor._orden), motor._banco_actual  # noqa: SLF001
    finally:
        svc.handle({"v": 1, "op": "shutdown"})
        thread.join(timeout=5)
        svc.close()


def test_a_stateful_preset_in_crossfade_mode_assigns_the_bank_from_the_new_mixes(tmp_path):
    """Since stage 2 the `decorrelate.seed` change crossfades instead of cutting; the bank is still
    assigned from the speakers' new mixes, the same as through the cut."""
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    crossfade = _seeded_preset_assignment(tmp_path / "a", "crossfade")
    cut = _seeded_preset_assignment(tmp_path / "b", "cut")
    assert crossfade[0] == cut[0]
    assert crossfade[1] is not None
    assert crossfade[1] == cut[1]


def _through(motor, block=4096):
    """Process until the motor is quiet again (a cut's bottom and its fade-in, or a crossfade)."""
    for _ in range(40):
        motor.procesar(np.zeros(block), np.zeros(block))
        if not motor.en_corte:
            return
    pytest.fail("the motor never got quiet")


def _ab_blindness(svc, first_changes, second_changes, *, plays=("x", "b", "a", "x", "a", "b")):
    """Save `a` and `b` from two settings, start the A/B with a known compensation for B, and return,
    per play, (cut?, crossfade?, whether the compensation waited and then landed)."""
    for which, changes in (("a", first_changes), ("b", second_changes)):
        for op in changes:
            _ok(svc, **op)
        _ok(svc, op="preset_save", name=which)
    _ok(svc, op="start")
    motor = FrozenSession.instances[0].motor
    _ok(svc, op="ab_start", a="a", b="b")
    _through(motor)
    svc.ab.compensation = {"a": 0.0, "b": -2.0}
    seen = []
    for which in plays:
        _ok(svc, op="ab_play", which=which)
        cut, faded = motor._corte.busy, motor._transicion.busy  # noqa: SLF001
        before = motor.ganancia_comparacion_db
        _through(motor)
        seen.append((which, cut, faded, before, motor.ganancia_comparacion_db, svc.ab.applied))
    return seen


def test_ab_presets_with_a_latency_changing_limiter_cut_on_every_play(frozen):
    """A and B differ in the limiter's lookahead (a latency change, still a cut): every play of a, b
    and x cuts, whichever plays now, or the hole would tell X apart (spec §4, "Blindness of the A/B").
    The compensation joins the cut."""
    seen = _ab_blindness(
        frozen,
        [{"op": "chain_set", "stage": "limiter", "algorithm": "true_peak", "params": {"lookahead_ms": 4.0}}],
        [{"op": "chain_set", "stage": "limiter", "algorithm": "true_peak", "params": {"lookahead_ms": 5.0}}],
    )
    for which, cut, faded, _before, after, applied in seen:
        assert (cut, faded) == (True, False), which
        # The compensation waited for the bottom and landed there with everything else.
        assert after == applied, which
    assert any(s[3] != s[4] for s in seen)  # it did move between plays (0 and -2 dB)


def test_ab_between_eq_presets_is_blind_and_crossfades(frozen):
    """A and B differ in the EQ (a stateful stage that crossfades since stage 2): every play of a, b
    and x crossfades, with no hole to tell X apart. The compensation lands with the transition."""
    seen = _ab_blindness(
        frozen,
        [{"op": "chain_set", "stage": "eq", "algorithm": "boost_only"}],
        [{"op": "chain_set", "stage": "eq", "algorithm": "off"}],
    )
    for which, cut, faded, _before, after, applied in seen:
        assert (cut, faded) == (False, True), which
        assert after == applied, which
    assert any(s[3] != s[4] for s in seen)


def test_ab_presets_differing_only_in_ramps_always_crossfade(frozen):
    seen = _ab_blindness(
        frozen,
        [{"op": "set", "speaker": "Go 4 Red", "changes": {"pan": -0.3}}],
        [{"op": "set", "speaker": "Go 4 Red", "changes": {"pan": 0.6, "gain_db": -2.0}}],
    )
    for which, cut, faded, _before, after, applied in seen:
        assert (cut, faded) == (False, True), which
        assert after == applied, which
    assert any(s[3] != s[4] for s in seen)


def test_ab_loudness_counts_only_from_the_end_of_the_transition(frozen):
    """The 3 s loudness window starts once the switch's transition (or cut) is over: with `fade_ms`
    up to 500 and one pending transition, a window counted from the request took in ~0.7 s of it."""
    _ok(frozen, op="chain_set", stage="transition", params={"fade_ms": 500.0})
    motor = _two_presets(frozen)
    # The service's loop reads it too (its summary every 0.5 s, the same `_ab_measure`).
    meter = type(
        "Meter", (), {"samples": 0, "net_lu": lambda _self, _steps: -20.0, "short_steps": 30, "summary": lambda *_a: {}}
    )()
    FrozenSession.instances[0].quality = meter
    _ok(frozen, op="ab_start", a="a", b="b")
    settle = round(service_module.AB_SETTLE_S * frozen.options.rate)
    meter.samples = frozen.ab.measure_from + 1
    assert motor.en_corte  # frozen: the transition has not run yet
    frozen._ab_measure(meter)  # noqa: SLF001
    assert frozen.ab.taken["a"] is None
    _through(motor)
    end = meter.samples
    frozen._ab_measure(meter)  # noqa: SLF001
    assert frozen.ab.taken["a"] is None
    meter.samples = end + settle - 1
    frozen._ab_measure(meter)  # noqa: SLF001
    assert frozen.ab.taken["a"] is None
    meter.samples = end + settle
    frozen._ab_measure(meter)  # noqa: SLF001
    assert frozen.ab.taken["a"] == end + settle


def test_a_preset_during_the_fade_in_of_a_cut_crossfades_without_a_second_dip(frozen):
    _ok(frozen, op="set", speaker="Go 4 Red", changes={"pan": -0.3})
    _ok(frozen, op="preset_save", name="wide")
    _ok(frozen, op="set", speaker="Go 4 Red", changes={"pan": 0.9})
    _ok(frozen, op="start")
    motor = FrozenSession.instances[0].motor
    motor.cortar()
    motor.procesar(np.zeros(4096), np.zeros(4096))  # the 80 ms fade-out and its bottom
    assert motor._corte.state == "in"  # noqa: SLF001
    _ok(frozen, op="preset_load", name="wide")
    assert motor._corte.state == "in"  # noqa: SLF001  (not turned around for a second dip)
    assert motor._transicion.busy  # noqa: SLF001
    _through(motor)
    assert frozen.installation.por_nombre("Go 4 Red").pan == -0.3
