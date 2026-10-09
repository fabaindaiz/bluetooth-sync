"""The monitor through the control contract, with the simulated room and a simulated monitor
(spec 2026-10-04-headphone-monitor-design.md §2). SIMULATED: no PipeWire."""

import json
import threading
import time

import pytest

from aurasync import clients
from aurasync.config import Instalacion, Parlante
from aurasync.logbuffer import LogBuffer
from aurasync.service import Service, load_config
from aurasync.session import SessionOptions
from aurasync.simulated import SimulatedMonitor, SimulatedObserver, SimulatedSession


def _service(tmp_path, config_path=None, monitor=None):
    inst = Instalacion(
        parlantes=[Parlante(f"s{i}", f"sink{i}", pan=p, ambiente=0.2) for i, p in enumerate((-0.7, 0.7))]
    )
    inst.guardar(tmp_path / "i.json")
    return Service(
        tmp_path / "i.json",
        tmp_path / "p.json",
        options=SessionOptions(microphone="sim", block=1024),
        session_factory=SimulatedSession,
        observer=SimulatedObserver(inst),
        simulated=True,
        measurements_path=tmp_path / "m",
        log=lambda _: None,
        logs=LogBuffer(),
        config_path=config_path,
        monitor_factory=SimulatedMonitor,
        monitor=monitor,
    )


@pytest.fixture
def svc(tmp_path):
    service = _service(tmp_path)
    thread = threading.Thread(target=service.run, daemon=True)
    thread.start()
    yield service
    service.handle({"v": 1, "op": "shutdown"})
    thread.join(timeout=5)
    service.close()


def call(service, **message):
    return service.handle({"v": 1, **message})


def ok(service, **message):
    reply = call(service, **message)
    assert reply["ok"], reply
    return reply["result"]


def _monitor(service, until=None, timeout=5.0):
    deadline = time.monotonic() + timeout
    while True:
        view = ok(service, op="state")["monitor"]
        if until is None or until(view) or time.monotonic() > deadline:
            return view
        time.sleep(0.05)


def test_the_state_lists_where_the_monitor_can_go(svc):
    view = _monitor(svc)
    assert view["mode"] == "off"
    nodes = [c["node"] for c in view["candidates"]]
    assert "simulated_headphones" in nodes
    assert not any(n.startswith("sink") for n in nodes)  # never a speaker


def test_binaural_while_playing_reaches_the_headphones_and_the_speakers_go_on(svc):
    ok(svc, op="start")
    ok(svc, op="monitor_set", mode="binaural", target="simulated_headphones", gain_db=-20)
    view = _monitor(svc, lambda v: v["state"] == "on")
    assert view["reached"] is True
    assert view["routed_to"] == "simulated_headphones"
    assert view["gain_db"] == -20.0
    monitor = svc.session.monitor
    blocks = svc.session.blocks
    deadline = time.monotonic() + 5
    while monitor.pushed < 3 and time.monotonic() < deadline:
        time.sleep(0.05)
    assert monitor.pushed >= 3
    assert svc.session.blocks > blocks
    ok(svc, op="monitor_set", mode="off")
    assert _monitor(svc, lambda v: v["state"] == "off")["state"] == "off"
    assert svc.session.monitor is None


def test_a_speaker_is_refused_as_target(svc):
    reply = call(svc, op="monitor_set", mode="stereo", target="sink0")
    assert reply["error"]["code"] == "conflict"
    assert "loop" in reply["error"]["message"]


def test_a_mode_needs_a_target(svc):
    reply = call(svc, op="monitor_set", mode="mix")
    assert reply["error"]["code"] == "out_of_range"


def test_the_choice_waits_for_a_session_and_survives_it(svc):
    ok(svc, op="monitor_set", mode="stereo", target="simulated_headphones")
    assert _monitor(svc)["state"] == "waiting"
    ok(svc, op="start")
    assert _monitor(svc, lambda v: v["state"] == "on")["state"] == "on"
    ok(svc, op="stop")
    assert _monitor(svc, lambda v: v["state"] == "waiting")["state"] == "waiting"


def test_a_simulated_service_never_opens_pipewire_for_the_monitor(tmp_path):
    inst = Instalacion(parlantes=[Parlante("s0", "sink0")])
    inst.guardar(tmp_path / "i.json")
    service = Service(tmp_path / "i.json", tmp_path / "p.json", simulated=True, log=lambda _: None, logs=LogBuffer())
    try:
        assert service.monitor.factory is SimulatedMonitor
    finally:
        service.close()


def test_monitor_set_needs_the_control_scope():
    assert clients.required_scope("monitor_set") == "control"


def test_the_choice_is_kept_in_service_json(tmp_path):
    config = tmp_path / "service.json"
    load_config(config)  # writes a new one
    service = _service(tmp_path, config_path=config)
    thread = threading.Thread(target=service.run, daemon=True)
    thread.start()
    try:
        ok(service, op="monitor_set", mode="mix", target="simulated_headphones", gain_db=-6)
    finally:
        service.handle({"v": 1, "op": "shutdown"})
        thread.join(timeout=5)
        service.close()
    assert json.loads(config.read_text())["monitor"] == {
        "mode": "mix",
        "target": "simulated_headphones",
        "gain_db": -6.0,
        "volume_control": "device",
        "device_volume_pct": None,
    }
    assert load_config(config).monitor == {
        "mode": "mix",
        "target": "simulated_headphones",
        "gain_db": -6.0,
        "volume_control": "device",
        "device_volume_pct": None,
    }
    again = _service(tmp_path, config_path=config, monitor=load_config(config).monitor)
    assert again.monitor.settings.mode == "mix"
    again.close()


def test_the_monitor_follows_the_chosen_volume(tmp_path):
    """The reference of every monitor mode is the input at the panel's volume, whichever of the
    two volume modes carries it (brief 2026-10-05, monitor loudness)."""
    service = _service(tmp_path)
    try:
        assert service.monitor.volume is not None
        service.settings.volume_db = -17.0
        assert service.monitor.volume() == -17.0
    finally:
        service.close()


def test_the_simulated_monitor_matches_the_loudness_and_says_so(svc):
    ok(svc, op="start")
    ok(svc, op="monitor_set", mode="mix", target="simulated_headphones")
    view = _monitor(svc, lambda v: v["state"] == "on" and v["loudness_reference"] is not None)
    assert view["match"] in {"measuring", "locked", "frozen"}
    assert view["makeup_db"] is not None
    assert view["loudness_monitor"] is not None
    assert view["match_reason"] is None
    ok(svc, op="monitor_set", mode="binaural", target="simulated_headphones")
    view = _monitor(svc, lambda v: v["state"] == "on" and v["mode"] == "binaural" and v["match"] is not None)
    assert view["match"] != "unmeasured", "the simulated binaural uses the measured HRTF"


def _observe(service, sinks):
    """A new observation of the system, as the observer thread publishes one every few seconds."""
    service.observer.view = {**service.observer.view, "sinks": sinks, "at": time.time()}


def test_a_target_that_vanishes_and_comes_back_reattaches_the_monitor(svc):
    """2026-10-09, HP-O16: opening the headphones' own microphone switched them to the headset
    profile, PipeWire rebuilt their sink under the same name, and the monitor stayed silent while
    the state said it reached them. It must notice, say so, and come back by itself."""
    ok(svc, op="start")
    ok(svc, op="monitor_set", mode="mix", target="simulated_headphones")
    _monitor(svc, lambda v: v["state"] == "on")
    first = svc.session.monitor
    sinks = svc.observer.view["sinks"]
    _observe(svc, [s for s in sinks if s["node"] != "simulated_headphones"])
    gone = _monitor(svc, lambda v: not v["reached"])
    assert gone["reached"] is False
    assert svc.session.monitor is not first
    _observe(svc, sinks)
    back = _monitor(svc, lambda v: v["state"] == "on" and v["reached"])
    assert back["reached"] is True
    assert back["routed_to"] == "simulated_headphones"
    assert svc.session.monitor is not None
    assert svc.session.monitor is not first


def test_the_observer_keeps_the_graph_its_sinks_come_from(monkeypatch):
    """The monitor checks its own stream on the observer's last `pw-dump`, read after it opened,
    without reading the graph again; the sinks carry their ids (a rebuilt sink changes it)."""
    from aurasync import sonido, system

    sink = {
        "type": "PipeWire:Interface:Node",
        "id": 11643,
        "info": {
            "props": {
                "node.name": "bluez_output.14_06_A7_6B_E3_F0.1",
                "media.class": "Audio/Sink",
                "api.bluez5.address": "14:06:A7:6B:E3:F0",
            }
        },
    }
    mic = {
        "type": "PipeWire:Interface:Node",
        "id": 2073,
        "info": {
            "props": {
                "node.name": "headset-mic",
                "media.class": "Audio/Source",
                "api.bluez5.address": "14:06:A7:6B:E3:F0",
            }
        },
    }
    monkeypatch.setattr(sonido, "_pw_dump", lambda: [sink, mic])
    monkeypatch.setattr(system, "system_unit", lambda _unit, *, user: {"state": "active", "user": user})
    monkeypatch.setattr(system, "_bluez_objects", lambda: "")
    monkeypatch.setattr(system, "list_apps", list)
    monkeypatch.setattr(system, "_run", lambda _args, _timeout=5.0: "")
    observer = system.Observer(enabled=False)
    before = time.time()
    view = observer.read()
    assert view["sinks"] == [
        {
            "node": "bluez_output.14_06_A7_6B_E3_F0.1",
            "description": "bluez_output.14_06_A7_6B_E3_F0.1",
            "id": 11643,
            "address": "14:06:A7:6B:E3:F0",
        }
    ]
    # A microphone whose name does not carry its device: the BlueZ property does (microphones.py).
    assert view["microphones"] == [
        {"node": "headset-mic", "description": "headset-mic", "address": "14:06:A7:6B:E3:F0"}
    ]
    read_at, dump = observer.graph
    assert before <= read_at <= view["at"]
    assert dump == [sink, mic]
