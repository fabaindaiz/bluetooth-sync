"""The speakers' loudness match between renders (spec 2026-10-05-virtual-speakers-and-hot-join §9).

`direct` drops the chain's effects (and its EQ boost), so it would not sound as loud as `classic`;
the processed renders change the loudness each their own way too. The reference is `classic`'s
net loudness (the speakers' summed short-term loudness minus the input's, without the volume);
every other render follows it through a makeup gain (`loudness_match.py`, the monitor's unit),
corrected slowly, capped at +/-12 dB, frozen on silence, cuts and calibrations, and remembered per
render so that a switch never jumps after the first visit.

The first half drives `RenderMatch` with stand-ins (exact numbers); the second the real `Motor`
and `QualityMeter` as the session does, with pink noise. SIMULADO.
"""

import math

import numpy as np
import pytest

from aurasync.chain import ChainValues
from aurasync.config import Instalacion, Parlante
from aurasync.dsp.loudness import integrated_lufs
from aurasync.estimulos import ruido_rosa
from aurasync.motor import Motor
from aurasync.quality import QualityMeter
from aurasync.render_match import CAP_DB, RenderMatch

SR = 48000
BLOCK = 4096
SECONDS = BLOCK / SR


# -- stand-ins: the numbers are exact ------------------------------------------------------


class FakeMotor:
    def __init__(self, render: str = "classic") -> None:
        self.render = render
        self.en_corte = False
        self.silenciados: set[str] = set()
        self.volumen_del_bloque_db: float = 0.0
        self.render_makeup_db = 0.0
        self.render_makeup_block_db: float = 0.0
        self.comparison_block_db: float = 0.0
        """The A/B's loudness compensation (`ganancia_comparacion_db`) the block carried."""
        self.on_render_switch = None
        self.instalacion = Instalacion(parlantes=[Parlante("a", "s0"), Parlante("b", "s1")])

    def jump_render_makeup(self, db: float) -> None:
        self.render_makeup_db = self.render_makeup_block_db = db

    def switch(self, render: str) -> None:
        """What the motor does at a cut's bottom."""
        self.render = render
        self.jump_render_makeup(self.on_render_switch(render))


class FakeMeter:
    """The quality meter's view: net loudness of what the speakers got, and the input's momentary.

    What it measures carries the gain each block was made with (volume and makeup), averaged over
    the window as a real meter averages the power."""

    step_samples = 4800
    short_steps = 30

    def __init__(self, raw_net: dict[str, float]) -> None:
        self.raw_net = raw_net
        """Each render's net loudness without volume and makeup (LU)."""
        self.samples = 0
        self.input_momentary = -20.0
        self.motor: FakeMotor | None = None
        self.gains: list[float] = []

    def push(self, n: int) -> None:
        m = self.motor
        self.samples += n
        self.gains.append(10 ** ((m.volumen_del_bloque_db + m.render_makeup_block_db + m.comparison_block_db) / 10))

    @property
    def steps_total(self) -> int:
        return self.samples // self.step_samples

    def net_lu(self, steps: int) -> float:
        blocks = max(1, round(steps * self.step_samples / BLOCK))
        return self.raw_net[self.motor.render] + 10 * math.log10(float(np.mean(self.gains[-blocks:])))


def _rig(raw_net: dict[str, float], render: str = "classic") -> tuple[RenderMatch, FakeMotor, FakeMeter]:
    motor, meter = FakeMotor(render), FakeMeter(raw_net)
    meter.motor = motor
    match = RenderMatch()
    match.bind(motor)
    return match, motor, meter


def _play(
    match: RenderMatch, motor: FakeMotor, meter: FakeMeter, seconds: float, *, hold: bool = False, bypass: bool = False
) -> None:
    for _ in range(round(seconds / SECONDS)):
        motor.render_makeup_block_db = 0.0 if bypass else motor.render_makeup_db
        meter.push(BLOCK)
        match.after_block(motor, meter, BLOCK, hold=hold, bypass=bypass)


def test_classic_is_the_reference_and_never_gets_a_makeup():
    match, motor, meter = _rig({"classic": -3.0, "direct": -7.0})
    _play(match, motor, meter, 5.0)
    assert motor.render_makeup_db == 0.0
    assert match.reference_lu == pytest.approx(-3.0)
    assert match.view()["status"] == "reference"


def test_the_first_visit_converges_within_ten_seconds_and_a_switch_back_never_jumps():
    match, motor, meter = _rig({"classic": -3.0, "direct": -7.0})
    _play(match, motor, meter, 5.0)
    motor.switch("direct")
    assert motor.render_makeup_db == 0.0, "never seen: it starts at 0 dB"
    _play(match, motor, meter, 10.0)
    assert motor.render_makeup_db == pytest.approx(4.0, abs=0.5)
    _play(match, motor, meter, 20.0)
    assert match.view()["status"] == "locked"
    motor.switch("classic")
    assert motor.render_makeup_db == 0.0
    _play(match, motor, meter, 5.0)
    motor.switch("direct")
    assert motor.render_makeup_db == pytest.approx(4.0, abs=0.1), "remembered: the second visit starts matched"


def test_after_the_first_visit_it_corrects_slowly():
    match, motor, meter = _rig({"classic": -3.0, "direct": -7.0})
    _play(match, motor, meter, 5.0)
    motor.switch("direct")
    _play(match, motor, meter, 30.0)
    settled = motor.render_makeup_db
    meter.raw_net["direct"] = -9.0
    _play(match, motor, meter, 3.0)
    moved = motor.render_makeup_db - settled
    assert 0.0 < moved < 0.6, "the monitor's pace: about 10 % of the error per second"


def test_the_makeup_is_capped_at_twelve_db():
    match, motor, meter = _rig({"classic": 0.0, "direct": -30.0})
    _play(match, motor, meter, 5.0)
    motor.switch("direct")
    _play(match, motor, meter, 60.0)
    assert motor.render_makeup_db == pytest.approx(CAP_DB, abs=0.01)
    assert CAP_DB == 12.0
    meter.raw_net["spatial"] = 30.0
    motor.switch("spatial")
    _play(match, motor, meter, 60.0)
    assert motor.render_makeup_db == pytest.approx(-CAP_DB, abs=0.01)


def test_silence_freezes_it():
    """The gate is the input before the volume: a pause freezes it, a quiet knob does not."""
    match, motor, meter = _rig({"classic": -3.0, "direct": -7.0})
    _play(match, motor, meter, 5.0)
    motor.switch("direct")
    _play(match, motor, meter, 2.0)
    before = motor.render_makeup_db
    meter.input_momentary = -math.inf
    meter.raw_net["direct"] = -40.0
    _play(match, motor, meter, 10.0)
    assert motor.render_makeup_db == before
    assert match.view()["status"] == "frozen"
    meter.input_momentary = -20.0
    meter.raw_net["direct"] = -7.0
    motor.volumen_del_bloque_db = -45.0
    _play(match, motor, meter, 3.0)
    assert match.view()["status"] in {"measuring", "locked"}
    assert motor.render_makeup_db > before


def test_cuts_and_calibrations_freeze_it_and_the_window_starts_after_them():
    match, motor, meter = _rig({"classic": -3.0, "direct": -7.0})
    _play(match, motor, meter, 5.0)
    motor.switch("direct")
    _play(match, motor, meter, 2.0)
    before = motor.render_makeup_db
    _play(match, motor, meter, 5.0, hold=True)
    assert motor.render_makeup_db == before
    assert match.view()["status"] == "frozen"
    motor.en_corte = True
    _play(match, motor, meter, 1.0)
    assert motor.render_makeup_db == before
    motor.en_corte = False
    # What the meter holds right after a cut is the fade and the old render: it waits for clean steps.
    _play(match, motor, meter, 0.4)
    assert motor.render_makeup_db == before


def test_the_volume_is_not_taken_for_a_difference_between_renders():
    match, motor, meter = _rig({"classic": -3.0, "direct": -7.0})
    _play(match, motor, meter, 5.0)
    motor.volumen_del_bloque_db = -15.0
    motor.switch("direct")
    _play(match, motor, meter, 30.0)
    assert motor.render_makeup_db == pytest.approx(4.0, abs=0.1)


def test_a_reference_taken_with_other_speakers_muted_is_not_used():
    """Muting a speaker under `direct` lowers its loudness, not the render's: matching it would turn
    the others up. The reference waits for `classic` to be heard with the same speakers."""
    match, motor, meter = _rig({"classic": -3.0, "direct": -7.0})
    _play(match, motor, meter, 5.0)
    motor.switch("direct")
    _play(match, motor, meter, 30.0)
    matched = motor.render_makeup_db
    motor.silenciados = {"b"}
    meter.raw_net["direct"] = -10.0
    _play(match, motor, meter, 10.0)
    assert motor.render_makeup_db == matched
    assert match.view()["status"] == "unmeasured"
    assert match.view()["reason"] == "reference_stale"


def test_without_a_reference_the_render_keeps_its_makeup():
    match, motor, meter = _rig({"classic": -3.0, "direct": -7.0}, render="direct")
    _play(match, motor, meter, 10.0)
    assert motor.render_makeup_db == 0.0
    view = match.view()
    assert view["status"] == "unmeasured"
    assert view["reason"] == "no_reference"


def test_bind_jumps_a_new_motor_to_the_remembered_makeup():
    match, motor, meter = _rig({"classic": -3.0, "direct": -7.0})
    _play(match, motor, meter, 5.0)
    motor.switch("direct")
    _play(match, motor, meter, 30.0)
    again = FakeMotor("direct")
    match.bind(again)
    assert again.render_makeup_db == pytest.approx(4.0, abs=0.1)
    assert again.on_render_switch is not None


def test_the_ab_compensation_is_not_taken_for_the_render():
    """Review 2026-10-06: a matched A/B moves `ganancia_comparacion_db` live; learned into classic's
    reference, the other render's makeup followed it and stayed off after the A/B."""
    match, motor, meter = _rig({"classic": -3.0, "direct": -7.0})
    motor.comparison_block_db = -5.0
    _play(match, motor, meter, 8.0)
    assert match.reference_lu == pytest.approx(-3.0, abs=0.01)
    motor.comparison_block_db = 2.0
    motor.switch("direct")
    _play(match, motor, meter, 30.0)
    assert motor.render_makeup_db == pytest.approx(4.0, abs=0.1)


def test_an_ab_test_holds_the_match():
    """While a blind A/B runs nothing is learned and the makeups stay as they are; a switch still
    starts at the remembered makeup (renders are pre-matched in the A/B)."""
    running = [False]
    match, motor, meter = _rig({"classic": -3.0, "direct": -7.0})
    match.hold_while = lambda: running[0]
    _play(match, motor, meter, 5.0)
    motor.switch("direct")
    _play(match, motor, meter, 30.0)
    matched = motor.render_makeup_db
    running[0] = True
    meter.raw_net["direct"] = -12.0
    meter.raw_net["classic"] = 3.0
    _play(match, motor, meter, 10.0)
    assert motor.render_makeup_db == matched
    assert match.view()["status"] == "frozen"
    motor.switch("classic")
    _play(match, motor, meter, 10.0)
    assert match.reference_lu == pytest.approx(-3.0, abs=0.01), "classic is not learned during the A/B"
    motor.switch("direct")
    assert motor.render_makeup_db == matched


def test_a_change_of_speakers_forgets_the_makeups():
    """As the monitor's unit: with other speakers what each render needs changed."""
    match, motor, meter = _rig({"classic": -3.0, "direct": -7.0})
    _play(match, motor, meter, 5.0)
    motor.switch("direct")
    _play(match, motor, meter, 30.0)
    assert match.locked_once == {"direct"}
    motor.instalacion = Instalacion(parlantes=[Parlante("a", "s0"), Parlante("b", "s1"), Parlante("c", "s2")])
    _play(match, motor, meter, 0.2)
    assert match.match.makeup == {"direct": 0.0}
    assert match.locked_once == set()
    assert match.reference_lu is None
    assert motor.render_makeup_db == 0.0


def test_a_multichannel_source_plays_without_makeup():
    """The render is made elsewhere: no makeup, and back to the render's own when it ends."""
    match, motor, meter = _rig({"classic": -3.0, "direct": -7.0})
    _play(match, motor, meter, 5.0)
    motor.switch("direct")
    _play(match, motor, meter, 30.0)
    matched = motor.render_makeup_db
    _play(match, motor, meter, 5.0, bypass=True)
    assert motor.render_makeup_db == 0.0
    assert match.match.makeup["direct"] == matched
    _play(match, motor, meter, 0.1)
    assert motor.render_makeup_db == matched


def test_the_view():
    match, motor, meter = _rig({"classic": -3.0, "direct": -7.0})
    _play(match, motor, meter, 5.0)
    motor.switch("direct")
    _play(match, motor, meter, 30.0)
    view = match.view()
    assert view["render"] == "direct"
    assert view["makeup_db"] == pytest.approx(4.0, abs=0.1)
    assert view["reference_lu"] == pytest.approx(-3.0, abs=0.05)
    assert view["makeups_db"]["direct"] == view["makeup_db"]
    assert view["reason"] is None


# -- the real motor and meter, as the session drives them ------------------------------------


def _pink(seconds: float, rms_db: float = -20.0, seed: int = 5) -> tuple[np.ndarray, np.ndarray]:
    n = int(seconds * SR)
    pair = []
    for s in (seed, seed + 1):
        x = ruido_rosa(n, SR, semilla=s)
        pair.append(x / np.sqrt(np.mean(x**2)) * 10 ** (rms_db / 20))
    # Partly correlated, as music: the pan laws differ most on what is common to both channels.
    common = 0.6 * (pair[0] + pair[1]) / 2
    return pair[0] * 0.8 + common, pair[1] * 0.8 + common


def _installation() -> Instalacion:
    speakers = [("L", -0.7, 0.2), ("R", 0.7, 0.2), ("C", 0.0, 0.5)]
    # Without EQ, `direct` is about 4 LU louder than `classic` here (SIMULADO, this rig): the
    # constant-power pan against classic's linear one, and no ambience mixed in.
    return Instalacion(parlantes=[Parlante(n, f"s-{n}", pan=p, ambiente=a) for n, p, a in speakers])


def _render(name: str) -> ChainValues:
    return ChainValues.from_json({"spatial": {"algorithm": name}})


class Rig:
    """The order of `AudioSession.step`: the motor, the quality meter, then the match."""

    def __init__(self, volume_db: float = -6.0) -> None:
        self.motor = Motor(_installation(), SR, ecualizar=True, volumen_db=volume_db)
        self.meter = QualityMeter(SR, [p.nombre for p in self.motor.instalacion.parlantes])
        self.match = RenderMatch()
        self.match.bind(self.motor)
        self.left, self.right = _pink(120.0)
        self.pos = 0
        self.taken: list[tuple[np.ndarray, np.ndarray, dict[str, np.ndarray]]] = []

    def play(self, seconds: float, *, silent: bool = False) -> None:
        for _ in range(round(seconds / SECONDS)):
            if silent:
                left = right = np.zeros(BLOCK)
            else:
                left, right = self.left[self.pos : self.pos + BLOCK], self.right[self.pos : self.pos + BLOCK]
                self.pos += BLOCK
            blocks = self.motor.procesar(left, right)
            self.meter.push((left, right), blocks)
            self.match.after_block(self.motor, self.meter, BLOCK, hold=False)
            self.taken.append((left, right, blocks))
            self.taken = self.taken[-200:]

    def switch(self, render: str) -> None:
        """A render switch by the default crossfade (stage 3): it counts from when the new render
        plays alone, after its warm-up (up to 1 s) and the fade, as a cut counted from its bottom."""
        assert self.motor.aplicar_cadena(_render(render)) == "crossfade"
        while self.motor.en_corte:
            self.play(SECONDS)
        assert self.motor.render == render

    def net_lu(self, seconds: float) -> float:
        """The speakers' summed loudness minus the input's over the last `seconds`, measured apart
        from the match (BS.1770 integrated, every speaker weighted 1, as the quality strip)."""
        recent = self.taken[-round(seconds / SECONDS) :]
        left = np.concatenate([b[0] for b in recent])
        right = np.concatenate([b[1] for b in recent])
        names = recent[0][2].keys()
        out = sum(10 ** (integrated_lufs(np.concatenate([b[2][n] for b in recent]), SR) / 10) for n in names)
        inp = sum(10 ** (integrated_lufs(x, SR) / 10) for x in (left, right))
        return 10 * math.log10(out) - 10 * math.log10(inp)

    def close(self) -> None:
        self.meter.close()


@pytest.fixture
def rig():
    r = Rig()
    yield r
    r.close()


def test_switching_classic_direct_classic_keeps_the_loudness_after_the_first_visit(rig):
    rig.play(6.0)
    classic = rig.net_lu(2.0)
    rig.switch("direct")
    rig.play(1.0)
    first_jump = rig.net_lu(0.5) - classic
    rig.play(9.0)
    assert abs(rig.net_lu(1.0) - classic) < 1.0, "the first visit converges within 10 s"
    assert abs(first_jump) > 3.0, "the case is a real one: unmatched, direct sounds different"
    rig.play(10.0)
    rig.switch("classic")
    rig.play(6.0)
    classic = rig.net_lu(2.0)
    rig.switch("direct")
    rig.play(1.0)
    assert abs(rig.net_lu(0.5) - classic) < 1.0, "the second visit starts at its remembered makeup"
    rig.switch("classic")
    rig.play(1.0)
    assert abs(rig.net_lu(0.5) - classic) < 1.0, "and classic is the reference: no makeup"
    assert rig.motor.render_makeup_db == 0.0


def test_a_pause_freezes_the_makeup_on_the_real_engine(rig):
    rig.play(6.0)
    rig.switch("direct")
    rig.play(15.0)
    before = rig.motor.render_makeup_db
    assert before != 0.0
    rig.play(5.0, silent=True)
    assert rig.motor.render_makeup_db == pytest.approx(before, abs=0.05)
    assert rig.match.view()["status"] == "frozen"


def test_the_ab_compensation_on_the_real_engine_is_not_learned(rig):
    rig.play(8.0)
    before = rig.match.reference_lu
    rig.motor.ganancia_comparacion_db = -6.0
    rig.play(8.0)
    assert rig.match.reference_lu == pytest.approx(before, abs=0.2)


def test_a_volume_change_under_direct_does_not_move_the_makeup(rig):
    rig.play(6.0)
    rig.switch("direct")
    rig.play(20.0)
    before = rig.motor.render_makeup_db
    rig.motor.volumen_db = -20.0
    rig.play(6.0)
    assert rig.motor.render_makeup_db == pytest.approx(before, abs=0.5)


# -- through the service: the simulated session plays it ----------------------------------


@pytest.fixture
def simulated(tmp_path):
    import threading

    from aurasync.logbuffer import LogBuffer
    from aurasync.service import Service
    from aurasync.session import SessionOptions
    from aurasync.simulated import SimulatedObserver, SimulatedSession

    inst = Instalacion(parlantes=[Parlante(f"Go 4 {n}", f"sink-{n}", pan=p) for n, p in (("A", -0.7), ("B", 0.7))])
    inst.guardar(tmp_path / "inst.json")
    svc = Service(
        tmp_path / "inst.json",
        tmp_path / "presets.json",
        options=SessionOptions(block=1024),
        session_factory=SimulatedSession,
        observer=SimulatedObserver(inst),
        simulated=True,
        log=lambda _: None,
        logs=LogBuffer(),
    )
    thread = threading.Thread(target=svc.run, daemon=True)
    thread.start()
    yield svc
    svc.handle({"v": 1, "op": "shutdown"})
    thread.join(timeout=10)
    svc.close()


def _until(predicate, timeout: float = 10.0):
    import time

    end = time.monotonic() + timeout
    while time.monotonic() < end:
        value = predicate()
        if value:
            return value
        time.sleep(0.05)
    pytest.fail("timed out")


def test_the_service_binds_the_match_and_reports_it(simulated):
    svc = simulated
    assert svc.handle({"v": 1, "op": "start"})["ok"]
    assert svc.session.render_match is svc.render_match
    assert svc.motor.on_render_switch == svc.render_match.select
    reply = svc.handle({"v": 1, "op": "chain_set", "stage": "spatial", "algorithm": "direct"})
    assert reply["ok"], reply
    assert reply["result"]["apply"] == "crossfade"
    _until(lambda: svc.motor.render == "direct")
    view = _until(lambda: (svc.quality or {}).get("render_match", {}).get("render") == "direct" and svc.quality)
    assert view["render_match"]["status"] in {"measuring", "unmeasured", "frozen", "locked"}
    assert svc.motor.espacial is None


def test_the_service_holds_the_match_while_an_ab_runs(simulated):
    svc = simulated
    assert svc.handle({"v": 1, "op": "start"})["ok"]
    assert svc.render_match.hold_while is not None
    assert not svc.render_match.hold_while()
    for name in ("uno", "dos"):
        assert svc.handle({"v": 1, "op": "preset_save", "name": name})["ok"]
    assert svc.handle({"v": 1, "op": "ab_start", "a": "uno", "b": "dos"})["ok"]
    assert svc.render_match.hold_while()
    assert svc.handle({"v": 1, "op": "ab_stop"})["ok"]
    assert not svc.render_match.hold_while()
