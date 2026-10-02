"""The volume in the speakers (`volume.avrcp`, spec 2026-10-02 §5) against a faithful `pactl`:
every order is read back, a speaker that does not take it is reported and nothing pretends,
and changing the mode keeps the level heard."""

import math
import re
import subprocess
import threading

import pytest

from aurasync import bt_volume
from aurasync.bt_volume import BluetoothVolume, PactlVolume, db_of, percent_of
from aurasync.config import Instalacion, Parlante
from aurasync.service import Service
from tests.test_service import FakeSession, _ok, _wait


class FakePactl:
    """`pactl` as PipeWire answers it: `set-sink-volume` stores the volume, `get-sink-volume`
    prints it per channel the way pactl does (whole percents). A `deaf` sink answers 0 to the
    order and keeps its volume (a speaker that did not take it)."""

    def __init__(self, volumes: dict[str, float]) -> None:
        self.volumes = dict(volumes)
        self.deaf: set[str] = set()
        self.calls: list[list[str]] = []
        self.lock = threading.Lock()

    def run(self, args):
        with self.lock:
            self.calls.append(list(args))
            if args[1] == "set-sink-volume":
                m = re.fullmatch(r"(\d+(?:\.\d+)?)%", args[3])
                if m is None:
                    return subprocess.CompletedProcess(args, 1, "", "bad volume")
                if args[2] not in self.deaf:
                    self.volumes[args[2]] = float(m.group(1))
                return subprocess.CompletedProcess(args, 0, "", "")
            if args[1] == "get-sink-volume":
                if args[2] not in self.volumes:
                    return subprocess.CompletedProcess(args, 1, "", "No such entity")
                pct = self.volumes[args[2]]
                raw = round(pct / 100 * 65536)
                db = f"{60 * math.log10(pct / 100):.2f}" if pct > 0 else "-inf"
                line = (
                    f"Volume: front-left: {raw} / {pct:3.0f}% / {db} dB,   front-right: {raw} / {pct:3.0f}% / {db} dB\n"
                )
                return subprocess.CompletedProcess(args, 0, line + "        balance 0.00\n", "")
            return subprocess.CompletedProcess(args, 1, "", "unknown")

    def sets(self):
        return [c for c in self.calls if c[1] == "set-sink-volume"]


def _backend(fake):
    return PactlVolume(run=fake.run, which=lambda name: f"/usr/bin/{name}")


def _volume(fake):
    return BluetoothVolume(_backend(fake), sleep=lambda _s: None)


SINKS = {"Red": "s0", "Blue": "s1"}


def test_the_cubic_curve_is_pipewires():
    assert db_of(80.0) == pytest.approx(-5.81, abs=0.01)
    assert percent_of(-5.81) == pytest.approx(80.0, abs=0.02)
    assert percent_of(-200) == pytest.approx(percent_of(bt_volume.FLOOR_DB))


def test_pactl_is_read_back_from_its_own_output():
    fake = FakePactl({"s0": 80.0})
    backend = _backend(fake)
    assert backend.get_percent("s0") == 80.0
    assert backend.set_percent("s0", 46.42)
    assert fake.volumes["s0"] == pytest.approx(46.42)
    assert backend.get_percent("nope") is None
    assert PactlVolume(which=lambda _n: None).unavailable()


def test_entering_keeps_the_level_heard_and_each_speakers_difference():
    fake = FakePactl({"s0": 80.0, "s1": 100.0})
    v = _volume(fake)
    got = []
    v.enter(SINKS, -20.0, got.append)
    assert v.wait_idle()
    # The loudest (Blue, 100 % = 0 dB) goes to -20 dB; Red keeps its -5.81 dB under it.
    assert got == [pytest.approx(-20.0)]
    assert v.state == "on"
    assert v.active
    assert db_of(fake.volumes["s1"]) == pytest.approx(-20.0, abs=0.01)
    assert db_of(fake.volumes["s0"]) == pytest.approx(-25.81, abs=0.01)
    # Heard before: digital -20 + speaker. After: digital 0 + speaker. The same, per speaker.
    for sink, before in (("s0", -5.81), ("s1", 0.0)):
        assert 0.0 + db_of(fake.volumes[sink]) == pytest.approx(-20.0 + before, abs=0.01)
    status = v.status()
    assert status["speakers"]["Red"]["ok"]
    assert status["speakers"]["Blue"]["ok"]
    assert v.warnings() == []


def test_a_speaker_that_does_not_take_it_is_reported_and_put_back():
    fake = FakePactl({"s0": 80.0, "s1": 100.0})
    fake.deaf.add("s1")
    v = _volume(fake)
    got = []
    v.enter(SINKS, -20.0, got.append)
    assert v.wait_idle()
    assert got == [None]
    assert v.state == "failed"
    assert not v.active
    assert "Blue" in v.status()["error"]
    assert v.status()["speakers"]["Blue"]["ok"] is False
    # Put back where they were (Red had moved): nothing changed for the ear.
    assert fake.volumes == {"s0": pytest.approx(80.0), "s1": 100.0}
    assert v.warnings()
    assert "digital" in v.warnings()[0]


def test_without_pactl_it_fails_with_the_reason():
    v = BluetoothVolume(PactlVolume(which=lambda _n: None), sleep=lambda _s: None)
    got = []
    v.enter(SINKS, -20.0, got.append)
    assert v.wait_idle()
    assert got == [None]
    assert "pactl" in v.status()["error"]


def test_apply_moves_every_speaker_and_coalesces():
    fake = FakePactl({"s0": 80.0, "s1": 100.0})
    v = _volume(fake)
    v.enter(SINKS, -20.0, lambda _v: None)
    assert v.wait_idle()
    missed = []
    for db in (-19.0, -18.0, -17.0, -10.0):
        v.apply(SINKS, db, missed.append)
    assert v.wait_idle()
    assert db_of(fake.volumes["s1"]) == pytest.approx(-10.0, abs=0.01)
    assert db_of(fake.volumes["s0"]) == pytest.approx(-15.81, abs=0.01)
    assert missed[-1] == []
    fake.deaf.add("s0")
    v.apply(SINKS, -12.0, missed.append)
    assert v.wait_idle()
    assert missed[-1] == ["Red"]
    assert any("Red" in w for w in v.warnings())


def test_leaving_puts_the_speakers_back_and_keeps_the_level():
    fake = FakePactl({"s0": 80.0, "s1": 100.0})
    v = _volume(fake)
    v.enter(SINKS, -20.0, lambda _v: None)
    assert v.wait_idle()
    restore, digital = v.leave_levels(-20.0)
    assert (restore, digital) == (pytest.approx(0.0), pytest.approx(-20.0))
    v.leave(SINKS, -20.0)
    assert v.wait_idle()
    assert v.state == "off"
    assert fake.volumes["s0"] == pytest.approx(80.0, abs=0.01)
    assert fake.volumes["s1"] == pytest.approx(100.0)
    # Raised above where they were: they stay up, the digital volume cannot add.
    assert v.leave_levels(3.0) == (3.0, 0.0)


# -- through the service ---------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_fake():
    FakeSession.instances = []
    FakeSession.fail_open = None
    FakeSession.fail_after = None


@pytest.fixture
def served(tmp_path):
    made = []

    def make(fake):
        Instalacion(parlantes=[Parlante("Red", "s0"), Parlante("Blue", "s1")]).guardar(tmp_path / "inst.json")
        svc = Service(
            tmp_path / "inst.json",
            tmp_path / "presets.json",
            session_factory=FakeSession,
            log=lambda _: None,
            bt_volume=_volume(fake),
        )
        thread = threading.Thread(target=svc.run, daemon=True)
        thread.start()
        made.append((svc, thread))
        return svc

    yield make
    for svc, thread in made:
        svc.handle({"v": 1, "op": "shutdown"})
        thread.join(timeout=5)
        svc.close()


def _settled(svc):
    assert svc.bt_volume.wait_idle()
    _wait(lambda: _ok(svc, op="state")["volume_avrcp"]["state"] in {"on", "failed", "off"})
    _wait(lambda: not svc.motor.en_corte if svc.motor is not None else True)


def test_the_service_switches_to_the_speakers_volume_and_back_without_a_jump(served):
    fake = FakePactl({"s0": 80.0, "s1": 100.0})
    svc = served(fake)
    _ok(svc, op="start")
    motor = FakeSession.instances[0].motor
    assert motor.volumen_db == -20.0

    def heard():
        return {s: motor.volumen_db + db_of(fake.volumes[s]) for s in ("s0", "s1")}

    before = heard()
    reply = _ok(svc, op="chain_set", stage="volume", algorithm="avrcp")
    assert reply["apply"] == "cut"
    _settled(svc)
    _wait(lambda: motor.volumen_db == 0.0)
    state = _ok(svc, op="state")
    assert state["volume_avrcp"]["state"] == "on"
    assert state["global"]["volume_db"] == pytest.approx(-20.0)
    assert heard() == pytest.approx(before, abs=0.01)
    # The panel's volume now moves the speakers, read back; the digital one stays at 0 dB.
    _ok(svc, op="set", changes={"volume_db": -30.0})
    _settled(svc)
    assert motor.volumen_db == 0.0
    assert db_of(fake.volumes["s1"]) == pytest.approx(-30.0, abs=0.01)
    assert _ok(svc, op="state")["volume_avrcp"]["speakers"]["Blue"]["ok"]
    # Back to digital: the speakers go back to where they were, and the level heard stays.
    before = heard()
    _ok(svc, op="chain_set", stage="volume", algorithm="digital")
    _settled(svc)
    _wait(lambda: _ok(svc, op="state")["volume_avrcp"]["state"] == "off")
    assert motor.volumen_db == pytest.approx(-30.0)
    assert fake.volumes["s1"] == pytest.approx(100.0)
    assert heard() == pytest.approx(before, abs=0.01)


def test_the_service_does_not_pretend_when_a_speaker_does_not_take_it(served):
    fake = FakePactl({"s0": 80.0, "s1": 100.0})
    fake.deaf.add("s0")
    svc = served(fake)
    _ok(svc, op="start")
    motor = FakeSession.instances[0].motor
    _ok(svc, op="chain_set", stage="volume", algorithm="avrcp")
    _settled(svc)
    _wait(lambda: _ok(svc, op="state")["volume_avrcp"]["state"] == "failed")
    state = _ok(svc, op="state")
    assert any("not in effect" in w for w in state["warnings"])
    # The digital volume never went to 0 dB: the sound stayed as it was.
    assert motor.volumen_db == -20.0
    assert state["global"]["volume_db"] == -20.0
    # The panel's volume keeps working, digitally.
    _ok(svc, op="set", changes={"volume_db": -25.0})
    _settled(svc)
    assert motor.volumen_db == -25.0


def test_the_simulated_room_hears_the_speakers_volume():
    from aurasync.simulated import Room, SimulatedVolumes

    volumes = SimulatedVolumes()
    room = Room(["A"], 48000, lambda _name: volumes.gain("sA"))
    assert room.volume("A") == 1.0
    volumes.set_percent("sA", 80.0)
    assert volumes.get_percent("sA") == pytest.approx(80.0, abs=0.4)  # an AVRCP step
    assert 20 * math.log10(room.volume("A")) == pytest.approx(-5.81, abs=0.1)
