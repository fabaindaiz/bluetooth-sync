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
    original = motor.cortar
    motor.cortar = lambda accion=None: (cuts.append(1), original(accion))
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
