"""The monitor's service side (spec 2026-10-04-headphone-monitor-design.md §2): it opens off the
engine thread, attaches on it, refuses targets that loop back and keeps the choice."""

import threading
import time
from typing import ClassVar

import pytest

from aurasync.config import Instalacion, Parlante
from aurasync.monitor import MonitorError, MonitorSettings
from aurasync.monitor_control import MonitorController, forbidden_targets

INST = Instalacion(
    parlantes=[
        Parlante("Red", "bluez_output.red", pan=-0.7, ambiente=0.15),
        Parlante("Blue", "bluez_output.blue", pan=0.7, ambiente=0.15),
    ]
)


class _Out:
    instances: ClassVar[list["_Out"]] = []

    def __init__(self, settings, names, angles, rate, sink, block=4096) -> None:
        self.settings, self.names, self.angles, self.rate, self.sink = settings, names, angles, rate, sink
        self.block = block
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

    def detach_monitor(self):
        previous, self.monitor = self.monitor, None
        return previous


def _controller(saved=None):
    engine: list = []
    saved = saved if saved is not None else []
    c = MonitorController(MonitorSettings(), _Out, on_engine=engine.append, save=saved.append)
    return c, engine, saved


def _run_engine(c, engine):
    c.wait()
    while engine:
        engine.pop(0)()
    if c._close_future is not None:  # noqa: SLF001 - what the engine calls handed to the worker (a close)
        c._close_future.result(timeout=10)  # noqa: SLF001


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
    assert saved[-1] == {
        "mode": "binaural",
        "target": "alsa_out",
        "gain_db": -12.0,
        "volume_control": "device",
        "device_volume_pct": None,
    }


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


def test_forbidden_targets_ignore_virtual_and_name_both_combine_nodes():
    inst = Instalacion(parlantes=[Parlante("A", "bluez_output.AA_BB_CC_DD_EE_01.1"), Parlante("V", None)])
    targets = forbidden_targets(inst, "aurasync")
    assert None not in targets
    assert targets == {
        "bluez_output.AA_BB_CC_DD_EE_01.1",
        "aurasync",
        "aurasync_salida",
        "aurasync_salida_b",
        "aurasync_monitor",
    }


def test_the_state_carries_the_cushion_fields():
    from aurasync.monitor import Cushion

    ctl = MonitorController(MonitorSettings(), _Out, on_engine=lambda f: f(), save=lambda _d: None)
    view = ctl.view([], INST, "aurasync")
    assert (view["cushion_ms"], view["level_ms"], view["refills"], view["trims"]) == (None, None, 0, 0)
    assert (view["stretched_frames"], view["stretch_ppm"]) == (0, None)

    class _Session:
        monitor = type("M", (), {"cushion": Cushion(4096, 48000)})()

    ctl._session = _Session()  # noqa: SLF001
    _Session.monitor.cushion.plan(0)  # priming
    _Session.monitor.cushion.plan(0)  # a real starvation
    view = ctl.view([], INST, "aurasync")
    assert view["cushion_ms"] == 128.0
    assert view["level_ms"] == 0.0
    assert view["refills"] == 1
    assert view["trims"] == 0
    assert (view["stretched_frames"], view["stretch_ppm"]) == (0, 0.0), "a cushion without a stretcher"
    assert view["stretch_gave_up"] is False


def test_the_block_size_reaches_the_output():
    _Out.instances.clear()
    c = MonitorController(MonitorSettings(), _Out, on_engine=lambda f: f(), save=lambda _d: None)
    session = type("S", (), {"attach_monitor": lambda _self, _out: None})()
    c.set(
        MonitorSettings(mode="stereo", target="alsa_out"), session, INST, sink_name="aurasync", rate=48000, block=2048
    )
    c.wait()
    assert _Out.instances[-1].block == 2048


# -- the target goes away and comes back (2026-10-09, HP-O16) ------------------------------------
# The headphones' own microphone switched them to the headset profile: PipeWire rebuilt their sink
# under the same name and a new id, WirePlumber destroyed the monitor's stream (`node.dont-reconnect`)
# and the state went on saying it reached them.


def _sink(node="alsa_out", ident=None):
    return {"node": node, "description": node, "id": ident}


class _Graph:
    """What a fresh observation says of the monitor's stream: linked to `to`, or to nothing."""

    def __init__(self, to):
        self.to = to


class _Watched(_Out):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.failed = False

    @property
    def lost(self) -> bool:
        return self.failed

    def routing(self, dump) -> str | None:
        return dump.to


def _on(target="alsa_out"):
    _Out.instances.clear()
    engine: list = []
    c = MonitorController(MonitorSettings(), _Watched, on_engine=engine.append, save=lambda _d: None)
    session = _Session()
    c.set(MonitorSettings(mode="mix", target=target), session, INST, sink_name="aurasync", rate=48000)
    _run_engine(c, engine)
    assert c.state == "on"
    return c, engine, session


def test_a_target_that_vanishes_is_not_reached_and_reopens_when_it_is_back():
    c, engine, session = _on()
    first = session.monitor
    c.watch([_sink(ident=10)])
    assert session.monitor is first
    c.watch([_sink("other")])
    view = c.view([_sink("other")], INST, "aurasync")
    assert view["reached"] is False
    assert view["state"] == "waiting"
    assert view["error"]
    assert view["target_gone"] is True
    assert session.monitor is None
    c.wait()  # closed on the worker, never on the engine thread
    assert first.closed
    c.watch([_sink(ident=11)])
    _run_engine(c, engine)
    view = c.view([_sink(ident=11)], INST, "aurasync")
    assert (view["state"], view["reached"], view["error"]) == ("on", True, None)
    assert session.monitor is not first
    assert session.monitor is _Out.instances[-1]


def test_a_target_rebuilt_under_the_same_name_reopens_the_monitor():
    c, engine, session = _on()
    first = session.monitor
    c.watch([_sink(ident=2075)])
    c.watch([_sink(ident=2075)])
    assert session.monitor is first
    c.watch([_sink(ident=11643)])  # the profile switch: same name, new id; never seen absent
    _run_engine(c, engine)
    assert first.closed
    assert session.monitor is _Out.instances[-1] is not first
    assert c.view([_sink(ident=11643)], INST, "aurasync")["reached"] is True


def test_a_player_that_stopped_is_reopened():
    c, engine, session = _on()
    first = session.monitor
    first.failed = True  # pw-play exited: its pipe broke
    c.watch([_sink()])
    assert c.view([_sink()], INST, "aurasync")["reached"] is False
    _run_engine(c, engine)
    assert session.monitor is _Out.instances[-1] is not first


def test_a_stream_linked_nowhere_is_not_reached_and_is_reopened():
    c, engine, session = _on()
    first = session.monitor
    stale = (c.on_since - 1.0, _Graph(None))
    c.watch([_sink()], stale)  # read before the output opened: says nothing of it
    assert session.monitor is first
    c.watch([_sink()], (c.on_since + 1.0, _Graph("alsa_out")))
    assert session.monitor is first
    c.watch([_sink()], (c.on_since + 2.0, _Graph(None)))
    assert session.monitor is first  # one graph may catch a link being made
    assert c.view([_sink()], INST, "aurasync")["reached"] is True
    c.watch([_sink()], (c.on_since + 3.0, _Graph(None)))  # the next one says the same
    assert c.view([_sink()], INST, "aurasync")["reached"] is False
    _run_engine(c, engine)
    assert session.monitor is _Out.instances[-1] is not first


def test_a_stream_linked_to_another_sink_is_not_reached_and_is_reopened():
    """experimentos/09: a monitor that ends on a speaker loops the audio back into it."""
    c, engine, session = _on()
    first = session.monitor
    c.watch([_sink()], (c.on_since + 1.0, _Graph("bluez_output.red")))
    c.watch([_sink()], (c.on_since + 2.0, _Graph("bluez_output.red")))
    view = c.view([_sink()], INST, "aurasync")
    assert (view["state"], view["reached"]) == ("opening", False)
    _run_engine(c, engine)
    assert session.monitor is _Out.instances[-1] is not first
    assert c.view([_sink()], INST, "aurasync")["reached"] is True


def test_an_open_that_reached_nothing_is_tried_again_and_a_late_link_counts():
    class _Nowhere(_Watched):
        def where(self) -> str | None:
            return None

    _Out.instances.clear()
    engine: list = []
    c = MonitorController(MonitorSettings(), _Nowhere, on_engine=engine.append, save=lambda _d: None)
    session = _Session()
    c.set(MonitorSettings(mode="mix", target="alsa_out"), session, INST, sink_name="aurasync", rate=48000)
    _run_engine(c, engine)
    assert (c.state, c.routed_to) == ("on", None)
    first = session.monitor
    c.watch([_sink()])
    _run_engine(c, engine)
    assert session.monitor is _Out.instances[-1] is not first  # tried again
    # The link came after the open's own check: a fresh graph that sees it is enough.
    c.watch([_sink()], (c.on_since + 1.0, _Graph("alsa_out")))
    assert c.view([_sink()], INST, "aurasync")["reached"] is True


def test_reopens_are_bounded_in_time():
    now = [100.0]
    _Out.instances.clear()
    engine: list = []
    c = MonitorController(
        MonitorSettings(), _Watched, on_engine=engine.append, save=lambda _d: None, clock=lambda: now[0]
    )
    session = _Session()
    c.set(MonitorSettings(mode="mix", target="alsa_out"), session, INST, sink_name="aurasync", rate=48000)
    _run_engine(c, engine)
    session.monitor.failed = True
    c.watch([_sink()])
    _run_engine(c, engine)
    assert len(_Out.instances) == 2
    session.monitor.failed = True  # dies again at once
    now[0] += 1.0
    c.watch([_sink()])
    _run_engine(c, engine)
    assert len(_Out.instances) == 2  # not before REOPEN_MIN_S
    assert c.view([_sink()], INST, "aurasync")["reached"] is False
    now[0] += 5.0
    c.watch([_sink()])
    _run_engine(c, engine)
    assert len(_Out.instances) == 3


def test_the_engine_thread_never_waits_for_a_close():
    """Closing waits for the writer and `pw-play` (seconds when a stream never drains): on the
    engine thread it cut the speakers (review 2026-10-09; ~215 ms late blocks after monitor_set)."""

    class _Slow(_Watched):
        def close(self) -> None:
            time.sleep(0.5)
            super().close()

    _Out.instances.clear()
    engine: list = []
    c = MonitorController(MonitorSettings(), _Slow, on_engine=engine.append, save=lambda _d: None)
    session = _Session()
    c.set(MonitorSettings(mode="mix", target="alsa_out"), session, INST, sink_name="aurasync", rate=48000)
    _run_engine(c, engine)
    first = session.monitor
    started = time.monotonic()
    c.watch([_sink("other")])  # gone: detached
    assert time.monotonic() - started < 0.1
    assert session.monitor is None
    c.watch([_sink()])  # back: reopened
    _run_engine(c, engine)
    second = session.monitor
    started = time.monotonic()
    c.set(MonitorSettings(mode="stereo", target="alsa_out"), session, INST, sink_name="aurasync", rate=48000)
    assert time.monotonic() - started < 0.1
    _run_engine(c, engine)
    assert first.closed
    assert second.closed
    assert session.monitor is _Out.instances[-1]


def test_a_closed_session_leaves_no_stale_reason():
    c, _engine, _session = _on()
    c.watch([_sink("other")])
    assert c.view([], INST, "aurasync")["error"]
    c.session_closed()
    view = c.view([], INST, "aurasync")
    assert (view["state"], view["error"], view["target_gone"]) == ("waiting", None, False)


def test_without_an_observation_nothing_is_decided():
    c, _engine, session = _on()
    first = session.monitor
    c.watch(None)
    c.watch([])  # an observer that lists nothing yet is not a target that vanished
    assert session.monitor is first
    assert c.state == "on"
