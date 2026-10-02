"""The radio in the service (spec 2026-10-02 §3, §6.3): drops into the session's cut log, the
`radio_log` operation, the kill switch on every way out, and the stream's new events."""

import http.client
import json
import os
import signal
import threading
import time

import pytest

from aurasync import service as service_module
from aurasync.config import Instalacion, Parlante
from aurasync.cuts import CutLog
from aurasync.logbuffer import LogBuffer
from aurasync.rest import make_server
from aurasync.service import Service, ServiceConfig, serve
from aurasync.session import SessionError, SessionOptions
from aurasync.simulated import SimulatedObserver, SimulatedRadio, SimulatedSession, simulated_log_level
from tests.test_service import FakeSession, _ok, _wait

SINKS = ("bluez_output.90_F2_60_75_4A_83.1", "bluez_output.90_F2_60_E3_07_39.1")
TOKEN = "r" * 43


class CutSession(FakeSession):
    def __init__(self, *args):
        super().__init__(*args)
        self.cuts = CutLog()


@pytest.fixture(autouse=True)
def _reset_fake():
    FakeSession.instances = []
    FakeSession.fail_open = None
    FakeSession.fail_after = None


def _installation(path):
    inst = Instalacion(parlantes=[Parlante("Red", SINKS[0], pan=-0.7), Parlante("Blue", SINKS[1], pan=0.7)])
    inst.guardar(path)
    return inst


def _service(tmp_path, **kwargs):
    _installation(tmp_path / "inst.json")
    level = simulated_log_level(tmp_path / "cambios-de-sistema.txt")
    defaults = {"session_factory": CutSession, "log": lambda _: None, "log_level": level}
    return Service(tmp_path / "inst.json", tmp_path / "presets.json", **{**defaults, **kwargs}), level


@pytest.fixture
def running(tmp_path):
    made = []

    def make(**kwargs):
        svc, level = _service(tmp_path, **kwargs)
        thread = threading.Thread(target=svc.run, daemon=True)
        thread.start()
        made.append((svc, thread))
        return svc, level

    yield make
    for svc, thread in made:
        svc.handle({"v": 1, "op": "shutdown"})
        thread.join(timeout=5)
        svc.close()


def _changes(tmp_path):
    return (tmp_path / "cambios-de-sistema.txt").read_text()


def _turn_on(svc, mode="light"):
    reply = _ok(svc, op="radio_log", active=True, mode=mode)
    assert reply["pending"]
    _wait(lambda: (s := _ok(svc, op="state")["radio_log"])["active"] and not s["pending"])


def test_radio_log_writes_the_change_and_its_reversal_before_it_runs(running, tmp_path):
    svc, level = running()
    _turn_on(svc)
    state = _ok(svc, op="state")["radio_log"]
    assert state["mode"] == "light"
    assert state["verified"] is True
    text = _changes(tmp_path)
    assert "cambio temporal: wpctl set-log-level spa.bluez5.sink.media:D,spa.bluez5:D,2" in text
    assert "(revertir: wpctl set-log-level -)" in text
    _ok(svc, op="radio_log", active=False)
    _wait(lambda: not _ok(svc, op="state")["radio_log"]["active"])
    assert "revertido: wpctl set-log-level -" in _changes(tmp_path)
    assert level.read_current() is None


def test_radio_log_heavy_warns_and_the_op_is_validated(running):
    svc, _ = running()
    _turn_on(svc, "heavy")
    assert any("journald" in w for w in _ok(svc, op="state")["warnings"])
    reply = svc.handle({"v": 1, "op": "radio_log", "active": True, "mode": "medium"})
    assert reply["error"]["code"] == "out_of_range"
    reply = svc.handle({"v": 1, "op": "radio_log"})
    assert reply["error"]["code"] == "bad_request"


def test_without_a_log_level_the_op_is_unavailable(tmp_path):
    svc, _ = _service(tmp_path, log_level=None)
    thread = threading.Thread(target=svc.run, daemon=True)
    thread.start()
    try:
        reply = svc.handle({"v": 1, "op": "radio_log", "active": True})
        assert reply["error"]["code"] == "unavailable"
        assert _ok(svc, op="state")["radio_log"]["available"] is False
        assert _ok(svc, op="state")["radio"]["available"] is False
    finally:
        svc.handle({"v": 1, "op": "shutdown"})
        thread.join(timeout=5)
        svc.close()


# -- the kill switch: every way out restores the level (card kill-switch-reaches-every-path) --


def test_shutdown_restores_it(tmp_path):
    svc, level = _service(tmp_path)
    thread = threading.Thread(target=svc.run, daemon=True)
    thread.start()
    _turn_on(svc)
    svc.handle({"v": 1, "op": "shutdown"})
    thread.join(timeout=5)
    svc.close()
    assert level.mode is None
    assert level.read_current() is None
    assert _changes(tmp_path).rstrip().endswith("revertido: wpctl set-log-level -")


def test_a_failing_session_restores_it_and_the_service_lives_on(running, tmp_path):
    svc, level = running()
    _turn_on(svc)
    FakeSession.fail_after = 3
    _ok(svc, op="start")
    _wait(lambda: _ok(svc, op="state")["session"]["status"] == "error")
    _wait(lambda: level.mode is None)
    assert level.read_current() is None
    assert "revertido" in _changes(tmp_path)
    assert _ok(svc, op="state")["radio_log"]["active"] is False


def test_a_session_that_fails_to_start_restores_it(running):
    svc, level = running()
    _turn_on(svc)
    FakeSession.fail_open = SessionError("unavailable", "not connected: Red")
    reply = svc.handle({"v": 1, "op": "start"})
    assert reply["error"]["code"] == "unavailable"
    assert level.mode is None


def test_a_normal_stop_keeps_it(running):
    """Stopping the session on purpose is not a failure: the listener may want the log across sessions."""
    svc, level = running()
    _turn_on(svc)
    _ok(svc, op="start")
    _ok(svc, op="stop")
    assert level.mode == "light"


@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGINT], ids=["SIGTERM", "Ctrl-C"])
def test_a_signal_restores_it(tmp_path, sig):
    svc, level = _service(tmp_path)
    config = ServiceConfig(bind="127.0.0.1", port=0, token=TOKEN)
    previous_int = signal.getsignal(signal.SIGINT)

    def poke():
        _wait(lambda: svc.snapshot.get("sequence") is not None, timeout=5)
        _turn_on(svc)
        os.kill(os.getpid(), sig)

    threading.Thread(target=poke, daemon=True).start()
    try:
        assert serve(svc, config, announce=lambda *_: None, show_token=False) == 0
    finally:
        signal.signal(signal.SIGINT, previous_int)
        svc.close()
    assert level.mode is None
    assert level.read_current() is None
    assert _changes(tmp_path).rstrip().endswith("revertido: wpctl set-log-level -")


def test_a_change_left_by_a_killed_run_is_reverted_at_start(tmp_path):
    first = simulated_log_level(tmp_path / "cambios-de-sistema.txt")
    first.enable("light")  # and then the process "dies" (SIGKILL): no restore
    svc, _ = _service(tmp_path)
    thread = threading.Thread(target=svc.run, daemon=True)
    thread.start()
    try:
        _wait(lambda: "revertido (al arrancar)" in _changes(tmp_path))
    finally:
        svc.handle({"v": 1, "op": "shutdown"})
        thread.join(timeout=5)
        svc.close()


# -- the monitor: drops into the cut log, named by the installation ---------------------------


def test_drops_reach_the_sessions_cut_log_named_by_speaker(running):
    logging_on = {"value": True}
    radio = SimulatedRadio(lambda: list(SINKS), lambda: logging_on["value"], drop_every_s=1.0, seed=0)
    svc, _ = running(radio=radio)
    _ok(svc, op="start")
    radio.tick()  # mapping lines, then a drop per speaker (drop_every_s=1: always)
    radio.tick()
    _wait(lambda: _ok(svc, op="state")["radio"]["drops_seen"] >= 4)
    state = _ok(svc, op="state")
    assert state["radio"]["simulated"] is True
    assert set(state["radio"]["speakers"]) == {"Red", "Blue"}
    assert state["radio"]["speakers"]["Red"]["drops_total"] >= 2
    cuts = state["health"]["cuts"]
    radio_cuts = [e for e in cuts["events"] if e["kind"] == "radio"]
    assert {e["where"] for e in radio_cuts} == {"Red", "Blue"}
    assert all(e["identified"] for e in radio_cuts)
    assert cuts["likely"].startswith("radio:")


def test_the_radio_without_journalctl_is_unavailable_with_its_reason(running):
    from aurasync.radio import RadioMonitor

    svc, _ = running(radio=RadioMonitor(which=lambda _n: None))
    _wait(lambda: "corriendo" not in _ok(svc, op="state")["radio"]["reason"])
    view = _ok(svc, op="state")["radio"]
    assert view["available"] is False
    assert "journalctl" in view["reason"]


# -- the stream: quality, chain, radio, and the count of open streams --------------------------


def test_the_stream_sends_quality_chain_and_radio_and_counts_itself(tmp_path):
    inst = _installation(tmp_path / "inst.json")
    level = simulated_log_level(tmp_path / "cambios-de-sistema.txt")
    radio = SimulatedRadio(lambda: list(SINKS), lambda: level.mode is not None, drop_every_s=1.0, seed=1)
    svc = Service(
        tmp_path / "inst.json",
        tmp_path / "presets.json",
        options=SessionOptions(block=1024),
        session_factory=SimulatedSession,
        observer=SimulatedObserver(inst),
        simulated=True,
        log=lambda _: None,
        logs=LogBuffer(),
        radio=radio,
        log_level=level,
    )
    thread = threading.Thread(target=svc.run, daemon=True)
    thread.start()
    httpd = make_server(svc, "127.0.0.1", 0, TOKEN)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    port = httpd.server_address[1]
    try:
        _ok(svc, op="start")
        _turn_on(svc)
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        conn.request("GET", "/v1/stream", headers={"Authorization": f"Bearer {TOKEN}"})
        response = conn.getresponse()
        _wait(lambda: _ok(svc, op="state")["health"]["streams_open"] == 1)
        found: dict[str, list] = {}
        name, end = None, time.monotonic() + 4.0
        while time.monotonic() < end:
            line = response.fp.readline().decode().rstrip("\n")
            if line.startswith("event: "):
                name = line[7:]
            elif line.startswith("data: "):
                found.setdefault(name, []).append(json.loads(line[6:]))
        quality = found["quality"][-1]
        assert set(quality["outputs"]) == {"Red", "Blue"}
        assert quality["input"]["s"] is not None
        assert "net_gain_lu" in quality
        assert {"diffuse", "bass", "limiter"} <= set(found["chain"][-1])
        assert len(found["chain"]) > len(found["quality"])  # 5 Hz against 2 Hz
        assert found["radio"][-1]["simulated"] is True
        assert found["radio"][-1]["speakers"]["Red"]["drops_total"] >= 1
        state = found["state"][-1]
        assert state["quality"] is not None
        assert state["radio"]["available"]
        response.close()
        conn.close()
        # Closed by the client: the count goes back within 2 s (the panel's hidden-tab test).
        _wait(lambda: _ok(svc, op="state")["health"]["streams_open"] == 0, timeout=3)
    finally:
        svc.handle({"v": 1, "op": "shutdown"})
        thread.join(timeout=5)
        httpd.shutdown()
        svc.close()
    assert level.mode is None


def test_the_simulated_radio_stays_silent_while_the_log_is_off():
    radio = SimulatedRadio(lambda: list(SINKS), lambda: False, drop_every_s=1.0)
    radio.start()
    try:
        radio.tick()
        view = radio.snapshot()
        assert view["available"] is False
        assert view["speakers"] == {}
    finally:
        radio.stop()


def test_service_module_has_the_new_ops_documented():
    text = (service_module.Path(__file__).parent.parent / "docs" / "control-api.md").read_text()
    for name in ("radio_log", "quality", "streams_open", "match_loudness", "volume_avrcp"):
        assert name in text, name
