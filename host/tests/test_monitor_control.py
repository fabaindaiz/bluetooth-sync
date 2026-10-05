"""The monitor's service side (spec 2026-10-04-headphone-monitor-design.md §2): it opens off the
engine thread, attaches on it, refuses targets that loop back and keeps the choice."""

import threading
from typing import ClassVar

import pytest

from aurasync.config import Instalacion, Parlante
from aurasync.monitor import MonitorError, MonitorSettings
from aurasync.monitor_control import MonitorController

INST = Instalacion(
    parlantes=[
        Parlante("Red", "bluez_output.red", pan=-0.7, ambiente=0.15),
        Parlante("Blue", "bluez_output.blue", pan=0.7, ambiente=0.15),
    ]
)


class _Out:
    instances: ClassVar[list["_Out"]] = []

    def __init__(self, settings, names, angles, rate, sink) -> None:
        self.settings, self.names, self.angles, self.rate, self.sink = settings, names, angles, rate, sink
        self.opened = self.closed = False
        self.opened_on = None
        _Out.instances.append(self)

    def open(self) -> None:
        self.opened = True
        self.opened_on = threading.current_thread().name

    def where(self) -> str | None:
        return self.settings.target

    def push(self, pair, blocks) -> None:
        pass

    def close(self) -> None:
        self.closed = True


class _Session:
    def __init__(self) -> None:
        self.monitor = None

    def attach_monitor(self, m) -> None:
        if self.monitor is not None and self.monitor is not m:
            self.monitor.close()
        self.monitor = m


def _controller(saved=None):
    engine: list = []
    saved = saved if saved is not None else []
    c = MonitorController(MonitorSettings(), _Out, on_engine=engine.append, save=saved.append)
    return c, engine, saved


def _run_engine(c, engine):
    c.wait()
    while engine:
        engine.pop(0)()


def test_setting_a_mode_opens_off_the_engine_thread_and_attaches_on_it():
    _Out.instances.clear()
    c, engine, saved = _controller()
    session = _Session()
    c.set(MonitorSettings(mode="binaural", target="alsa_out"), session, INST, sink_name="aurasync", rate=48000)
    assert session.monitor is None  # not yet: it opens on a worker
    _run_engine(c, engine)
    out = _Out.instances[-1]
    assert out.opened
    assert out.opened_on != threading.current_thread().name
    assert session.monitor is out
    assert out.names == ["Red", "Blue"]
    assert out.angles["Red"] < 0 < out.angles["Blue"]
    assert out.sink == "aurasync_monitor"
    view = c.view(
        [{"node": "alsa_out", "description": "PC"}, {"node": "bluez_output.red", "description": "Red"}],
        INST,
        "aurasync",
    )
    assert view["state"] == "on"
    assert view["routed_to"] == "alsa_out"
    assert view["candidates"] == [{"node": "alsa_out", "description": "PC"}]
    assert saved[-1] == {"mode": "binaural", "target": "alsa_out", "gain_db": -12.0}


def test_a_speaker_as_target_is_refused_before_anything_opens():
    _Out.instances.clear()
    c, _engine, saved = _controller()
    with pytest.raises(MonitorError, match="loop"):
        c.set(
            MonitorSettings(mode="stereo", target="bluez_output.red"),
            _Session(),
            INST,
            sink_name="aurasync",
            rate=48000,
        )
    assert _Out.instances == []
    assert saved == []


def test_without_a_session_the_choice_is_kept_and_applied_when_one_opens():
    _Out.instances.clear()
    c, engine, _ = _controller()
    c.set(MonitorSettings(mode="mix", target="alsa_out"), None, INST, sink_name="aurasync", rate=48000)
    assert c.view([], INST, "aurasync")["state"] == "waiting"
    session = _Session()
    c.session_opened(session, INST, sink_name="aurasync", rate=48000)
    _run_engine(c, engine)
    assert session.monitor is _Out.instances[-1]


def test_turning_it_off_detaches_and_a_late_open_is_closed():
    _Out.instances.clear()
    c, engine, _ = _controller()
    session = _Session()
    c.set(MonitorSettings(mode="stereo", target="alsa_out"), session, INST, sink_name="aurasync", rate=48000)
    c.wait()
    # Turned off before the engine attached the one that opened: that one must not attach.
    c.set(MonitorSettings(), session, INST, sink_name="aurasync", rate=48000)
    _run_engine(c, engine)
    assert session.monitor is None
    assert _Out.instances[0].closed
    assert c.view([], INST, "aurasync")["state"] == "off"


def test_a_monitor_that_fails_to_open_says_why():
    class _Broken(_Out):
        def open(self) -> None:
            msg = "no HRTF"
            raise MonitorError(msg)

    engine: list = []  # deferred, as the service runs it: between blocks, after the worker returned
    c = MonitorController(MonitorSettings(), _Broken, on_engine=engine.append, save=lambda _: None)
    session = _Session()
    c.set(MonitorSettings(mode="binaural", target="alsa_out"), session, INST, sink_name="aurasync", rate=48000)
    _run_engine(c, engine)
    view = c.view([], INST, "aurasync")
    assert view["state"] == "failed"
    assert "HRTF" in view["error"]
    assert session.monitor is None
