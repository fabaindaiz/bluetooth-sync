"""The monitor's volume by the device (the headphones' sink volume) by default, software gain as
the alternative. A fake backend that can refuse or lie stands in for `pactl`: nothing real is
touched."""

import json
import threading
import time
from typing import ClassVar

import pytest

from aurasync import monitor_control
from aurasync.monitor import DEFAULT_GAIN_DB, MonitorError, MonitorOutput, MonitorSettings
from aurasync.monitor_control import FALLBACK_REASON, MonitorController
from tests.test_monitor_control import INST, _Out, _run_engine, _Session
from tests.test_monitor_service import _service, call, ok


class FakeSinkVolume:
    """The `PactlVolume` interface. `refuse`: the order fails; `lie`: it is accepted but the
    volume stays; `unreadable`: nothing can be read."""

    def __init__(self, volumes: dict[str, float] | None = None) -> None:
        self.volumes = dict(volumes or {})
        self.sets: list[tuple[str, float]] = []
        self.refuse = self.lie = self.unreadable = False

    def unavailable(self) -> str | None:
        return None

    def set_percent(self, sink: str, percent: float) -> bool:
        self.sets.append((sink, percent))
        if self.refuse:
            return False
        if not self.lie:
            self.volumes[sink] = percent
        return True

    def get_percent(self, sink: str) -> float | None:
        return None if self.unreadable else self.volumes.get(sink)


def _controller(backend, settings=None, clock=None):
    engine: list = []
    saved: list = []
    c = MonitorController(
        settings or MonitorSettings(),
        _Out,
        on_engine=engine.append,
        save=saved.append,
        backend=backend,
        sleep=lambda _s: None,
        **({"clock": clock} if clock else {}),
    )
    return c, engine, saved


def _open(c, engine, settings):
    c.set(settings, _Session(), INST, sink_name="aurasync", rate=48000)
    _run_engine(c, engine)


def _device(**kw):
    return MonitorSettings(mode="stereo", target="bluez_output.hp", **kw)


def test_the_default_is_the_device_and_it_survives_the_json():
    s = MonitorSettings()
    assert s.volume_control == "device"
    assert s.device_volume_pct is None
    assert MonitorSettings.from_json({}).volume_control == "device"
    again = MonitorSettings.from_json(MonitorSettings(volume_control="software", device_volume_pct=42.0).to_json())
    assert (again.volume_control, again.device_volume_pct) == ("software", 42.0)


@pytest.mark.parametrize(
    "bad", [{"volume_control": "cloud"}, {"device_volume_pct": 120.0}, {"device_volume_pct": -1.0}]
)
def test_bad_values_are_refused(bad):
    with pytest.raises(MonitorError):
        MonitorSettings(**bad)


def test_device_mode_leaves_the_software_gain_at_zero_db():
    args = (["a"], {"a": 0.0}, 48000, "aurasync_monitor")
    device = MonitorOutput(MonitorSettings(mode="stereo", target="t", gain_db=-20.0), *args)
    software = MonitorOutput(
        MonitorSettings(mode="stereo", target="t", gain_db=-20.0, volume_control="software"), *args
    )
    assert device._gain == pytest.approx(1.0)  # noqa: SLF001
    assert software._gain == pytest.approx(0.1)  # noqa: SLF001


def test_setting_the_level_asks_the_sink_and_reads_it_back():
    fake = FakeSinkVolume({"bluez_output.hp": 25.0})
    c, engine, saved = _controller(fake)
    _open(c, engine, _device())
    c.set(_device(), _Session(), INST, sink_name="aurasync", rate=48000, level=40.0)
    c.wait()
    view = c.view([], INST, "aurasync")
    assert fake.sets[-1] == ("bluez_output.hp", 40.0)
    assert view["device_volume_pct"] == 40.0
    assert view["device_volume_reason"] is None
    assert saved[-1]["device_volume_pct"] == 40.0


def test_changing_only_the_level_does_not_reopen_the_monitor():
    fake = FakeSinkVolume({"bluez_output.hp": 25.0})
    c, engine, _ = _controller(fake)
    _open(c, engine, _device())
    opened = len(_Out.instances)
    c.set(_device(), _Session(), INST, sink_name="aurasync", rate=48000, level=40.0)
    _run_engine(c, engine)
    assert len(_Out.instances) == opened


def test_a_sink_that_refuses_is_reported_not_hidden():
    fake = FakeSinkVolume({"bluez_output.hp": 25.0})
    c, engine, _ = _controller(fake)
    _open(c, engine, _device())
    fake.refuse = True
    c.set(_device(), _Session(), INST, sink_name="aurasync", rate=48000, level=40.0)
    c.wait()
    view = c.view([], INST, "aurasync")
    assert view["device_volume_reason"]
    assert view["device_volume_pct"] == 25.0  # what the sink really has


def test_a_sink_that_lies_is_caught_by_the_read_back():
    fake = FakeSinkVolume({"bluez_output.hp": 25.0})
    c, engine, _ = _controller(fake)
    _open(c, engine, _device())
    fake.lie = True
    c.set(_device(), _Session(), INST, sink_name="aurasync", rate=48000, level=40.0)
    c.wait()
    view = c.view([], INST, "aurasync")
    assert view["device_volume_pct"] == 25.0
    assert "40" in view["device_volume_reason"]


def test_opening_lowers_a_sink_above_the_stored_value():
    fake = FakeSinkVolume({"bluez_output.hp": 100.0})
    c, engine, _ = _controller(fake)
    _open(c, engine, _device(device_volume_pct=35.0))
    assert fake.volumes["bluez_output.hp"] == 35.0


def test_the_first_time_the_limit_is_thirty_percent():
    fake = FakeSinkVolume({"bluez_output.hp": 100.0})
    c, engine, _ = _controller(fake)
    _open(c, engine, _device())
    assert fake.volumes["bluez_output.hp"] == 30.0


def test_opening_never_raises_a_lower_sink():
    fake = FakeSinkVolume({"bluez_output.hp": 10.0})
    c, engine, _ = _controller(fake, _device(device_volume_pct=60.0))  # chosen in an earlier run
    _open(c, engine, _device(device_volume_pct=60.0))
    assert fake.sets == []
    assert fake.volumes["bluez_output.hp"] == 10.0


def test_software_mode_never_touches_the_sink():
    fake = FakeSinkVolume({"bluez_output.hp": 100.0})
    c, engine, _ = _controller(fake)
    _open(c, engine, _device(volume_control="software"))
    c.set(_device(volume_control="software"), _Session(), INST, sink_name="aurasync", rate=48000, level=20.0)
    _run_engine(c, engine)
    c.wait()
    assert fake.sets == []
    view = c.view([], INST, "aurasync")
    assert view["device_volume_pct"] is None
    assert view["volume_control"] == "software"


def test_the_view_follows_the_headphone_buttons():
    fake = FakeSinkVolume({"bluez_output.hp": 20.0})
    now = [0.0]
    c, engine, _ = _controller(fake, clock=lambda: now[0])
    _open(c, engine, _device())
    c.view([], INST, "aurasync")
    c.wait()
    fake.volumes["bluez_output.hp"] = 55.0  # the buttons
    now[0] += 10.0
    c.view([], INST, "aurasync")  # starts a read off this thread and answers from the cache
    c.wait()
    assert c.view([], INST, "aurasync")["device_volume_pct"] == 55.0


def test_an_unreadable_sink_says_why():
    fake = FakeSinkVolume({"bluez_output.hp": 20.0})
    fake.unreadable = True
    c, engine, _ = _controller(fake)
    _open(c, engine, _device())
    c.wait()
    view = c.view([], INST, "aurasync")
    assert view["device_volume_pct"] is None
    assert view["device_volume_reason"]


def test_without_a_backend_it_says_so():
    c, engine, _ = _controller(None)
    _open(c, engine, _device())
    c.wait()
    assert c.view([], INST, "aurasync")["device_volume_reason"]


@pytest.fixture
def running(tmp_path):
    cfg = tmp_path / "service.json"
    cfg.write_text("{}")
    service = _service(tmp_path, config_path=cfg)
    thread = threading.Thread(target=service.run, daemon=True)
    thread.start()
    yield service, cfg
    service.handle({"v": 1, "op": "shutdown"})
    thread.join(timeout=5)
    service.close()


def test_the_contract_validates_and_the_service_keeps_it(running):
    svc, cfg = running
    assert (
        call(svc, op="monitor_set", mode="stereo", target="simulated_headphones", device_volume_pct=150)["ok"] is False
    )
    assert call(svc, op="monitor_set", mode="stereo", target="simulated_headphones", volume_control="x")["ok"] is False
    ok(svc, op="monitor_set", mode="off", device_volume_pct=45)
    assert json.loads(cfg.read_text())["monitor"]["device_volume_pct"] == 45.0
    assert svc.monitor.settings.volume_control == "device"


def test_recalibrate_defaults_to_off_and_an_explicit_choice_is_kept(running):
    svc, _ = running
    assert svc.settings.recalibrate is False
    ok(svc, op="set", changes={"recalibrate": True})
    assert svc.settings.recalibrate is True


# -- fix round 1 (review 2026-10-05): the safety never fails open --------------------------------


class _GainOut(MonitorOutput):
    """The real output's gain, without PipeWire. `elsewhere`: where PipeWire "sent" it."""

    instances: ClassVar[list["_GainOut"]] = []
    elsewhere: ClassVar[str | None] = None

    def __init__(self, *args, **kw) -> None:
        super().__init__(*args, **kw)
        _GainOut.instances.append(self)

    def open(self) -> None:
        pass

    def where(self) -> str | None:
        return _GainOut.elsewhere or self.settings.target

    def close(self) -> None:
        pass


SOFTWARE = 10 ** (-20.0 / 20)


def _gain_controller(backend, settings=None, clock=None):
    _GainOut.instances.clear()
    _GainOut.elsewhere = None
    engine: list = []
    c = MonitorController(
        settings or MonitorSettings(gain_db=-20.0),
        _GainOut,
        on_engine=engine.append,
        save=lambda _d: None,
        backend=backend,
        sleep=lambda _s: None,
        **({"clock": clock} if clock else {}),
    )
    return c, engine


def _open_gain(c, engine, settings=None):
    c.set(settings or _device(gain_db=-20.0), _Session(), INST, sink_name="aurasync", rate=48000)
    _run_engine(c, engine)
    return _GainOut.instances[-1]


def _gain(out) -> float:
    return out.gain


def _fell_back(c, out):
    assert _gain(out) == pytest.approx(SOFTWARE)
    reason = c.view([], INST, "aurasync")["device_volume_reason"]
    assert reason.startswith(FALLBACK_REASON), reason


def test_a_verified_sink_opens_at_zero_db():
    fake = FakeSinkVolume({"bluez_output.hp": 100.0})
    c, engine = _gain_controller(fake)
    out = _open_gain(c, engine)
    assert fake.volumes["bluez_output.hp"] == 30.0
    assert _gain(out) == pytest.approx(1.0)
    assert c.view([], INST, "aurasync")["device_volume_reason"] is None


def test_a_sink_that_refuses_to_go_down_opens_with_the_software_gain():
    fake = FakeSinkVolume({"bluez_output.hp": 100.0})
    fake.refuse = True
    c, engine = _gain_controller(fake)
    _fell_back(c, _open_gain(c, engine))


def test_a_sink_that_lies_about_going_down_opens_with_the_software_gain():
    fake = FakeSinkVolume({"bluez_output.hp": 100.0})
    fake.lie = True
    c, engine = _gain_controller(fake)
    _fell_back(c, _open_gain(c, engine))


def test_an_unreadable_sink_opens_with_the_software_gain():
    """The review's repro: unreadable sink at 100 %, gain_db -20 -> no set, and the output got 1.0."""
    fake = FakeSinkVolume({"bluez_output.hp": 100.0})
    fake.unreadable = True
    c, engine = _gain_controller(fake)
    _fell_back(c, _open_gain(c, engine))


def test_without_a_backend_it_opens_with_the_software_gain():
    c, engine = _gain_controller(None)
    _fell_back(c, _open_gain(c, engine))


class _Unavailable(FakeSinkVolume):
    def unavailable(self) -> str | None:
        return "pactl is not installed"


def test_an_unavailable_backend_opens_with_the_software_gain():
    c, engine = _gain_controller(_Unavailable({"bluez_output.hp": 100.0}))
    _fell_back(c, _open_gain(c, engine))


class _Slow(FakeSinkVolume):
    def get_percent(self, sink: str) -> float | None:
        time.sleep(0.5)
        return super().get_percent(sink)


class _Broken(FakeSinkVolume):
    def get_percent(self, sink: str) -> float | None:  # noqa: ARG002 - the interface
        msg = "pactl crashed"
        raise OSError(msg)


def test_a_safety_slower_than_its_limit_opens_with_the_software_gain(monkeypatch):
    monkeypatch.setattr(monitor_control, "SAFETY_TIMEOUT_S", 0.05)
    c, engine = _gain_controller(_Slow({"bluez_output.hp": 100.0}))
    _fell_back(c, _open_gain(c, engine))


def test_a_safety_that_raises_opens_with_the_software_gain():
    c, engine = _gain_controller(_Broken({"bluez_output.hp": 100.0}))
    _fell_back(c, _open_gain(c, engine))


def test_audio_routed_elsewhere_switches_to_the_software_gain_at_once():
    fake = FakeSinkVolume({"bluez_output.hp": 20.0})
    c, engine = _gain_controller(fake)
    _GainOut.elsewhere = "alsa_output.laptop"  # WirePlumber moved it: the sink checked is not the one playing
    _fell_back(c, _open_gain(c, engine))


def test_software_mode_keeps_its_own_gain_without_a_reason():
    c, engine = _gain_controller(None)
    out = _open_gain(c, engine, _device(gain_db=-20.0, volume_control="software"))
    assert _gain(out) == pytest.approx(SOFTWARE)
    assert c.view([], INST, "aurasync")["device_volume_reason"] is None


# -- an explicit level is always applied; nothing else carries one --------------------------------


def test_an_explicit_level_equal_to_the_stored_one_is_still_applied():
    """The review's repro: asked 30 with the sink at 60 (the buttons) and 30 stored -> no set."""
    fake = FakeSinkVolume({"bluez_output.hp": 20.0})
    c, engine, _ = _controller(fake, _device(device_volume_pct=30.0))
    _open(c, engine, _device(device_volume_pct=30.0))
    fake.volumes["bluez_output.hp"] = 60.0  # the headphone buttons
    c.set(_device(device_volume_pct=30.0), _Session(), INST, sink_name="aurasync", rate=48000, level=30.0)
    c.wait()
    assert fake.sets[-1] == ("bluez_output.hp", 30.0)
    assert fake.volumes["bluez_output.hp"] == 30.0


def test_a_target_change_through_the_service_never_raises_the_new_sink(running):
    svc, _cfg = running
    backend = svc.monitor.backend
    backend.volumes.update({"simulated_headphones": 80.0, "simulated_pc_output": 20.0})
    ok(svc, op="monitor_set", mode="stereo", target="simulated_headphones")
    ok(svc, op="monitor_set", mode="stereo", target="simulated_pc_output")
    svc.monitor.wait()
    assert backend.volumes["simulated_pc_output"] == 20.0
    assert svc.monitor.settings.device_volume_pct is None


def test_switching_to_the_device_caps_the_ceiling_at_thirty():
    fake = FakeSinkVolume({"bluez_output.hp": 100.0})
    software = _device(volume_control="software", device_volume_pct=80.0)
    c, engine, saved = _controller(fake, software)
    _open(c, engine, software)
    _open(c, engine, _device(device_volume_pct=80.0))
    assert fake.volumes["bluez_output.hp"] == 30.0
    assert saved[-1]["device_volume_pct"] == 30.0


def test_switching_to_the_device_with_a_level_moved_takes_that_level():
    fake = FakeSinkVolume({"bluez_output.hp": 100.0})
    software = _device(volume_control="software", device_volume_pct=80.0)
    c, engine, _ = _controller(fake, software)
    _open(c, engine, software)
    c.set(_device(), _Session(), INST, sink_name="aurasync", rate=48000, level=45.0)
    _run_engine(c, engine)
    assert fake.volumes["bluez_output.hp"] == 45.0


# -- the reasons (minors 6 and 7) ------------------------------------------------------------------


def test_a_periodic_read_does_not_erase_a_failed_set():
    fake = FakeSinkVolume({"bluez_output.hp": 25.0})
    now = [0.0]
    c, engine, _ = _controller(fake, clock=lambda: now[0])
    _open(c, engine, _device())
    fake.refuse = True
    c.set(_device(), _Session(), INST, sink_name="aurasync", rate=48000, level=40.0)
    c.wait()
    now[0] += 10.0
    c.view([], INST, "aurasync")  # a periodic read
    c.wait()
    assert "40" in c.view([], INST, "aurasync")["device_volume_reason"]
    # ...until the sink is seen at what was asked (it took it late).
    fake.volumes["bluez_output.hp"] = 40.0
    now[0] += 10.0
    c.view([], INST, "aurasync")
    c.wait()
    assert c.view([], INST, "aurasync")["device_volume_reason"] is None


def test_an_unavailable_backend_forgets_the_last_reading():
    fake = FakeSinkVolume({"bluez_output.hp": 20.0})
    now = [0.0]
    c, engine, _ = _controller(fake, clock=lambda: now[0])
    _open(c, engine, _device())
    assert c.view([], INST, "aurasync")["device_volume_pct"] == 20.0
    fake.unavailable = lambda: "pactl is not installed"
    now[0] += 10.0
    c.view([], INST, "aurasync")
    c.wait()
    view = c.view([], INST, "aurasync")
    assert view["device_volume_pct"] is None
    assert view["device_volume_reason"]


# -- fix round 2 (re-review 2026-10-05) ------------------------------------------------------------


AT_MOST = 10 ** (DEFAULT_GAIN_DB / 20)


def test_the_fallback_never_uses_a_software_gain_above_minus_twelve():
    """A `gain_db` of 0 dB stored once would make the fail-closed fallback a full-level one."""
    fake = FakeSinkVolume({"bluez_output.hp": 100.0})
    fake.unreadable = True
    c, engine = _gain_controller(fake)
    out = _open_gain(c, engine, _device(gain_db=0.0))
    assert out.gain <= AT_MOST + 1e-9


def test_routed_elsewhere_never_uses_a_software_gain_above_minus_twelve():
    fake = FakeSinkVolume({"bluez_output.hp": 20.0})
    c, engine = _gain_controller(fake)
    _GainOut.elsewhere = "alsa_output.laptop"
    out = _open_gain(c, engine, _device(gain_db=0.0))
    assert out.gain <= AT_MOST + 1e-9


def test_a_new_target_forgets_the_old_targets_failed_set():
    fake = FakeSinkVolume({"bluez_output.hp": 25.0, "bluez_output.hp2": 20.0})
    c, engine, _ = _controller(fake)
    _open(c, engine, _device())
    fake.refuse = True
    c.set(_device(), _Session(), INST, sink_name="aurasync", rate=48000, level=40.0)
    c.wait()
    assert c.view([], INST, "aurasync")["device_volume_reason"]
    fake.refuse = False
    _open(c, engine, MonitorSettings(mode="stereo", target="bluez_output.hp2"))
    assert c.view([], INST, "aurasync")["device_volume_reason"] is None
