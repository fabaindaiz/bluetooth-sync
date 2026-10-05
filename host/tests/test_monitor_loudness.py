"""The headphone monitor's modes at the same loudness (brief 2026-10-05, "monitor loudness").

Before: `stereo` sent the input pair before the chain, ignoring the master volume, while `mix`
folded the speakers' outputs after it: with the user's four virtual speakers and the volume at
-20 dB, `mix` was 20.9 dB under `stereo` (MEDIDO offline, experimentos/18). Now `stereo` follows
the volume and a loudness match keeps `mix` and `binaural` at the reference ("the input at the
chosen volume"), remembering each mode's makeup so a switch never jumps.

The real `Motor` with the user's installation (4 virtual speakers, quad roles) and stereo pink
noise at -20 dBFS RMS, as the offline measurement.
"""

import itertools
import math

import numpy as np
import pytest

from aurasync import control, monitor
from aurasync.config import Instalacion, Parlante
from aurasync.dsp.loudness import integrated_lufs
from aurasync.estimulos import ruido_rosa
from aurasync.loudness_match import CAP_DB, LoudnessMatch
from aurasync.monitor import Levels, MonitorOutput, MonitorSettings
from aurasync.monitor_control import MonitorController
from aurasync.motor import Motor

SR = 48000
BLOCK = 4096
UNKNOWN_SHA = "0" * 64


def _pink_pair(seconds: float, rms_db: float = -20.0, seed: int = 1) -> tuple[np.ndarray, np.ndarray]:
    n = int(seconds * SR)
    out = []
    for s in (seed, seed + 1):
        x = ruido_rosa(n, SR, semilla=s)
        out.append(x / np.sqrt(np.mean(x**2)) * 10 ** (rms_db / 20))
    return out[0], out[1]


def _users_installation() -> Instalacion:
    roles = [(-0.7, 0.15), (0.7, 0.15), (-0.7, 0.55), (0.7, 0.55)]
    return Instalacion(
        parlantes=[Parlante(f"Virtual {i + 1}", None, pan=p, ambiente=a) for i, (p, a) in enumerate(roles)]
    )


class _Rig:
    """The engine and the monitor as the session drives them, block by block, without PipeWire."""

    def __init__(self, volume_db: float, *, motor: bool = True, speakers: dict | None = None) -> None:
        self.inst = _users_installation()
        self.motor = Motor(self.inst, SR, volumen_db=volume_db) if motor else None
        if speakers is None:
            self.names = [p.nombre for p in self.inst.parlantes]
            self.angles = {p.nombre: control.angle_of(p.pan, p.ambiente) for p in self.inst.parlantes}
        else:
            self.names, self.angles = list(speakers), dict(speakers)
        self.levels = Levels(volume_db=volume_db, digital_db=volume_db if motor else 0.0)
        self.match = LoudnessMatch()
        self.left, self.right = _pink_pair(200.0)
        self.pos = 0
        self.out: MonitorOutput | None = None

    def use(self, mode: str, hrtf_sha: str | None = None) -> MonitorOutput:
        out = MonitorOutput(
            MonitorSettings(mode=mode, target="hp", gain_db=0.0), self.names, self.angles, SR, "s", BLOCK
        )
        if hrtf_sha is not None:
            out.set_hrtf(hrtf_sha)
        out.bind(self.match, lambda: self.levels)
        self.out = out
        return out

    def run(self, seconds: float, *, silent: bool = False, blocks_of=None) -> np.ndarray:
        assert self.out is not None
        frames = []
        for _ in range(round(seconds * SR / BLOCK)):
            if silent:
                pair = None
                left = right = np.zeros(BLOCK)
            else:
                left = self.left[self.pos : self.pos + BLOCK]
                right = self.right[self.pos : self.pos + BLOCK]
                self.pos += BLOCK
                pair = (left, right)
            blocks = blocks_of(left, right) if blocks_of is not None else self.motor.procesar(left, right)
            frames.append(self.out.render(pair, blocks))
        return np.concatenate(frames)


def test_stereo_follows_the_master_volume():
    """Today stereo comes out at the input's level whatever the volume; it is 20 dB under at -20."""
    rig = _Rig(-20.0, motor=False, speakers={"a": -90.0, "b": 90.0})
    rig.use("stereo")
    left, right = _pink_pair(1.0)
    pair = (left[:BLOCK], right[:BLOCK])
    out = rig.out.render(pair, {"a": pair[0], "b": pair[1]})
    assert np.allclose(out, np.column_stack(pair) * 0.1)


def test_without_a_match_the_volume_still_applies_and_the_level_is_the_gain():
    out = MonitorOutput(
        MonitorSettings(mode="stereo", target="hp", gain_db=-6.0, volume_control="software"), ["a"], {}, SR, "s", BLOCK
    )
    out.levels = lambda: Levels(volume_db=-20.0, digital_db=-20.0)
    pair = (np.full(BLOCK, 0.5), np.full(BLOCK, -0.5))
    rendered = out.render(pair, {"a": np.zeros(BLOCK)})
    assert np.allclose(rendered[:, 0], 0.5 * 0.1 * 10 ** (-6 / 20))


def test_with_the_volume_in_the_speakers_mix_and_stereo_follow_the_knob():
    """`volume.avrcp`: the digital volume is 0 dB and the speakers carry the knob's. The monitor
    hears the engine at 0 dB, so the knob is applied to every mode."""
    pair = (np.full(BLOCK, 0.5), np.full(BLOCK, -0.5))
    for mode in ("stereo", "mix"):
        out = MonitorOutput(
            MonitorSettings(mode=mode, target="hp", gain_db=0.0), ["a", "b"], {"a": -90.0, "b": 90.0}, SR, "s", BLOCK
        )
        out.levels = lambda: Levels(volume_db=-20.0, digital_db=0.0)
        assert np.allclose(out.render(pair, {"a": pair[0], "b": pair[1]})[:, 0], 0.05), mode


@pytest.mark.parametrize("volume_db", [-20.0, 0.0])
def test_mix_and_stereo_end_within_one_lu_with_the_real_engine(volume_db):
    rig = _Rig(volume_db)
    rig.use("mix")
    rig.run(20.0)
    mix = rig.run(10.0)
    rig.use("stereo")
    stereo = rig.run(10.0)
    assert abs(integrated_lufs(mix, SR) - integrated_lufs(stereo, SR)) <= 1.0
    # Stereo is the reference: the input at the chosen volume.
    n = len(stereo)
    start = rig.pos - n
    pair = np.column_stack((rig.left[start : rig.pos], rig.right[start : rig.pos]))
    assert integrated_lufs(stereo, SR) == pytest.approx(integrated_lufs(pair, SR) + volume_db, abs=0.01)


def _jumps(outputs: list[np.ndarray], window_s: float = 3.0) -> list[float]:
    """Loudness after each switch minus before it, over `window_s` on each side."""
    n = int(window_s * SR)
    return [
        integrated_lufs(after[:n], SR) - integrated_lufs(before[-n:], SR)
        for before, after in itertools.pairwise(outputs)
    ]


def test_switching_modes_never_jumps_with_the_real_engine():
    rig = _Rig(-20.0)
    rig.use("stereo")
    segments = [rig.run(10.0)]
    for mode in ("mix", "stereo", "mix"):
        rig.use(mode)
        segments.append(rig.run(10.0))
    jumps = _jumps(segments)
    assert max(abs(j) for j in jumps) <= 1.0, jumps


def test_coming_back_to_a_mode_starts_at_its_remembered_makeup():
    """A mix 9 dB under the reference: the first entry starts at the estimate and is matched in
    seconds; every later return starts where it was left, so it does not jump."""
    rig = _Rig(0.0, motor=False, speakers={"a": -90.0, "b": 90.0})

    def quieter(left, right):
        return {"a": left * 10 ** (-9 / 20), "b": right * 10 ** (-9 / 20)}

    rig.use("stereo")
    segments = [rig.run(5.0, blocks_of=quieter)]
    rig.use("mix")
    segments.append(rig.run(60.0, blocks_of=quieter))
    for mode in ("stereo", "mix", "stereo", "mix"):
        rig.use(mode)
        segments.append(rig.run(5.0, blocks_of=quieter))
    jumps = _jumps(segments)
    assert jumps[0] < -4.0, "the first entry starts from the estimate (+1.9 dB for two): 9 dB to match"
    assert max(abs(j) for j in jumps[1:]) <= 1.0, jumps
    assert rig.match.makeup["mix"] == pytest.approx(9.0, abs=0.5)


def test_a_pause_does_not_move_the_makeup():
    rig = _Rig(-20.0)
    rig.use("mix")
    rig.run(15.0)
    before = rig.match.current_db
    rig.run(10.0, silent=True)
    assert abs(rig.match.current_db - before) <= 0.05
    assert rig.match.status == "frozen"


def test_a_cut_or_a_calibration_holds_it():
    rig = _Rig(0.0, motor=False, speakers={"a": -90.0, "b": 90.0})
    rig.use("mix")
    start = rig.match.current_db
    rig.levels = Levels(hold=True)
    rig.run(5.0, blocks_of=lambda left, right: {"a": left * 0.1, "b": right * 0.1})
    assert rig.match.current_db == start
    assert rig.match.status == "frozen"


def test_a_candidate_30_db_under_stops_at_the_cap():
    rig = _Rig(0.0, motor=False, speakers={"a": -90.0, "b": 90.0})
    rig.use("mix")
    rig.run(90.0, blocks_of=lambda left, right: {"a": left * 10 ** (-30 / 20), "b": right * 10 ** (-30 / 20)})
    assert rig.match.current_db == pytest.approx(CAP_DB, abs=0.05)
    assert max(rig.match.makeup.values()) <= CAP_DB


def test_binaural_with_an_unknown_hrtf_is_not_compensated_and_says_why():
    assert monitor.hrtf_gain_db(UNKNOWN_SHA, [-90.0, 90.0]) is None
    rig = _Rig(0.0, motor=False, speakers={"a": -90.0, "b": 90.0})
    out = rig.use("binaural", hrtf_sha=UNKNOWN_SHA)
    assert out.match_reason == monitor.UNMEASURED_HRTF
    rig.run(5.0, blocks_of=lambda left, right: {"a": left * 0.1, "b": right * 0.1})
    assert rig.match.current_db == 0.0
    assert rig.match.status == "unmeasured"


def test_binaural_with_the_measured_hrtf_applies_its_constant():
    gain = monitor.hrtf_gain_db(monitor.KEMAR_SHA256, [-90.0, 90.0])
    assert gain is not None
    assert math.isfinite(gain)
    rig = _Rig(0.0, motor=False, speakers={"a": -90.0, "b": 90.0})
    out = rig.use("binaural", hrtf_sha=monitor.KEMAR_SHA256)
    assert out.match_reason is None
    assert rig.match.current_db == pytest.approx(-gain), "a mode never seen starts from the measured constant"
    # The two channels sent carry the reference's loudness: the filter adds `gain`, the makeup takes it out.
    rig.run(10.0, blocks_of=lambda left, right: {"a": left, "b": right})
    assert rig.match.current_db == pytest.approx(-gain, abs=0.2)
    assert out.loudness_monitor == pytest.approx(out.loudness_reference, abs=0.3)


def test_the_hrtf_table_covers_every_default_layout_and_any_angle_set():
    for n in range(1, monitor.MAX_INPUTS + 1):
        assert monitor.hrtf_gain_db(monitor.KEMAR_SHA256, control.auto_angles(n)) is not None, n
    assert monitor.hrtf_gain_db(monitor.KEMAR_SHA256, [-10.0, 33.0, 170.0]) is not None
    quad = monitor.hrtf_gain_db(monitor.KEMAR_SHA256, [-45.0, 45.0, -135.0, 135.0])
    assert quad == monitor.hrtf_gain_db(monitor.KEMAR_SHA256, [135.0, -45.0, 45.0, -135.0]), "the order does not matter"


def test_one_binaural_channel_is_a_list_for_pw_play():
    """`--channel-map AUX0` is read as a layout and refused (MEDIDO, pw-play 1.6.9): a trailing
    comma makes it a list of one."""
    assert monitor.play_channel_map("binaural", 1) == "AUX0,"
    assert monitor.play_channel_map("binaural", 3) == "AUX0,AUX1,AUX2"
    assert monitor.play_channel_map("mix", 2) == "FL,FR"


class _Out:
    def __init__(self, settings, names, angles, rate, sink, block=4096) -> None:
        self.real = MonitorOutput(settings, names, angles, rate, sink, block)

    def open(self) -> None:
        pass

    def where(self) -> str | None:
        return self.real.settings.target

    def bind(self, match, levels) -> None:
        self.real.bind(match, levels)

    def close(self) -> None:
        pass


def test_the_controller_binds_the_volume_the_engine_and_the_hold():
    inst = Instalacion(parlantes=[Parlante("a", "bluez_output.a", pan=-0.7, ambiente=0.15)])
    motor = type("M", (), {"volumen_db": 0.0, "en_corte": False})()
    session = type("S", (), {"motor": motor, "calibration": None, "monitor": None})()
    session.attach_monitor = lambda out: setattr(session, "monitor", out)
    c = MonitorController(MonitorSettings(), _Out, on_engine=lambda f: f(), save=lambda _d: None, volume=lambda: -20.0)
    c.set(MonitorSettings(mode="stereo", target="hp"), session, inst, sink_name="aurasync", rate=SR)
    c.wait()
    levels = session.monitor.real.levels()
    assert (levels.volume_db, levels.digital_db, levels.hold) == (-20.0, 0.0, False)
    motor.en_corte = True
    assert session.monitor.real.levels().hold is True


def test_the_state_shows_the_match():
    c = MonitorController(MonitorSettings(), _Out, on_engine=lambda f: f(), save=lambda _d: None)
    view = c.view([], None, "aurasync")
    assert (view["makeup_db"], view["loudness_reference"], view["loudness_monitor"], view["match"]) == (
        None,
        None,
        None,
        None,
    )
    assert view["match_reason"] is None
    rig = _Rig(0.0, motor=False, speakers={"a": -90.0, "b": 90.0})
    rig.match = c.match
    out = rig.use("mix")
    rig.run(4.0, blocks_of=lambda left, right: {"a": left * 0.5, "b": right * 0.5})
    c._session = type("S", (), {"monitor": out})()  # noqa: SLF001
    c.state = "on"
    view = c.view([], None, "aurasync")
    assert view["match"] == "measuring"
    assert 0.0 < view["makeup_db"] < 6.0
    assert -25.0 < view["loudness_reference"] < -15.0
    assert view["loudness_monitor"] is not None


# -- review round 1 ------------------------------------------------------------------------


def _window_rms_db(x: np.ndarray, size: int = 1024) -> np.ndarray:
    """RMS (dBFS) of consecutive windows of `size` samples, both channels together."""
    count = len(x) // size
    w = x[: count * size].reshape(count, size, -1)
    return 10 * np.log10(np.mean(w**2, axis=(1, 2)) + 1e-30)


def test_leaving_avrcp_never_makes_the_monitor_louder():
    """`volume.avrcp` → `digital`: the knob stays at -20 dB, the digital volume jumps from 0 to
    -20 dB at the bottom of a cut. The cut's last block was made at the old 0 dB: the monitor must
    take it down by the volume it was made with, not by the one the motor has after it (the
    reviewer measured that block 14 dB over the steady level, peak -6.8 dBFS)."""
    inst = _users_installation()
    motor = Motor(inst, SR, volumen_db=0.0)
    knob = [-20.0]
    ctl = MonitorController(
        MonitorSettings(), _Out, on_engine=lambda f: f(), save=lambda _d: None, volume=lambda: knob[0]
    )
    session = type("S", (), {"motor": motor, "calibration": None})()
    names = [p.nombre for p in inst.parlantes]
    angles = {p.nombre: control.angle_of(p.pan, p.ambiente) for p in inst.parlantes}
    out = MonitorOutput(MonitorSettings(mode="mix", target="hp", gain_db=0.0), names, angles, SR, "s", BLOCK)
    out.bind(LoudnessMatch(), ctl._levels(session))  # noqa: SLF001
    left, right = _pink_pair(10.0)
    pos = 0

    def run(blocks: int) -> np.ndarray:
        nonlocal pos
        frames = []
        for _ in range(blocks):
            pair = (left[pos : pos + BLOCK], right[pos : pos + BLOCK])
            pos += BLOCK
            frames.append(out.render(pair, motor.procesar(*pair)))
        return np.concatenate(frames)

    steady = run(60)
    reference = _window_rms_db(steady[-24 * BLOCK :]).max()
    motor.cortar(lambda: motor.saltar_volumen(-20.0))
    after = run(12)
    assert _window_rms_db(after).max() <= reference + 1.0
    assert np.abs(after).max() <= np.abs(steady[-24 * BLOCK :]).max() * 10 ** (1 / 20)


def test_a_quiet_knob_does_not_freeze_the_match():
    """The pause floor is on the input, not on the input at the chosen volume: at -40 dB music at
    about -14 LUFS is music, not a pause."""
    rig = _Rig(0.0, motor=False, speakers={"a": -90.0, "b": 90.0})
    rig.levels = Levels(volume_db=-40.0, digital_db=0.0)
    rig.left, rig.right = (x * 10 ** (6 / 20) for x in _pink_pair(200.0))  # about -14 LUFS
    rig.use("mix")

    def quieter(left, right):
        return {"a": left * 10 ** (-6 / 20), "b": right * 10 ** (-6 / 20)}

    rig.run(60.0, blocks_of=quieter)
    assert rig.match.status != "frozen"
    assert rig.match.current_db == pytest.approx(6.0, abs=0.5)
    rig.run(10.0, silent=True, blocks_of=lambda left, right: {"a": left, "b": right})
    assert rig.match.status == "frozen", "a real pause still freezes it"


@pytest.mark.parametrize("mode", ["stereo", "mix"])
def test_a_knob_step_is_ramped(mode):
    """No sample jumps when the knob moves: the volume ramps over the block, like the makeup.
    In `mix` with `volume.avrcp` (digital 0 dB) the knob reaches the monitor through `post`."""
    out = MonitorOutput(
        MonitorSettings(mode=mode, target="hp", gain_db=0.0), ["a", "b"], {"a": -90.0, "b": 90.0}, SR, "s", BLOCK
    )
    knob = [-20.0]
    out.levels = lambda: Levels(volume_db=knob[0], digital_db=0.0)
    pair = (np.full(BLOCK, 0.5), np.full(BLOCK, 0.5))
    blocks = {"a": pair[0], "b": pair[1]}
    first = out.render(pair, blocks)[:, 0]
    knob[0] = -10.0
    second = out.render(pair, blocks)[:, 0]
    signal = np.concatenate([first, second])
    step = 0.5 * (10 ** (-10 / 20) - 10 ** (-20 / 20))  # the whole change, spread over one block
    # The ramp is linear in dB: its steepest sample step is ln(10)/20 x 10 dB x the end value
    # per block, 1.7 times the mean step. Without a ramp the step is the whole change at once.
    assert np.abs(np.diff(signal)).max() <= step / BLOCK * 2.0 + 1e-12
    assert second[-1] == pytest.approx(first[-1] * 10 ** (10 / 20))
