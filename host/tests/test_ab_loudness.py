"""The blind A/B with loudness (spec 2026-10-02 §7.3.5, service side): the short-term loudness
of the sum of the outputs with A and with B, and an optional gain that matches them."""

import threading

import numpy as np
import pytest

from aurasync.config import Instalacion, Parlante
from aurasync.quality import QualityMeter
from aurasync.service import Service
from tests.test_service import FakeSession, _ok, _wait

SR = 48000
BLOCK = 4096


class MusicSession(FakeSession):
    """A fake session that plays pink noise through the real motor into a `QualityMeter`."""

    def __init__(self, installation, motor, options, log):
        super().__init__(installation, motor, options, log)
        self.quality = QualityMeter(SR, [p.nombre for p in installation.parlantes])
        rng = np.random.default_rng(0)
        spectrum = np.fft.rfft(rng.standard_normal(SR * 8))
        f = np.fft.rfftfreq(SR * 8, 1 / SR)
        spectrum[1:] /= np.sqrt(f[1:])
        spectrum[0] = 0
        x = np.fft.irfft(spectrum)
        self.signal = 0.05 * x / np.sqrt(np.mean(x**2))
        self.pos = 0
        self.level = 1.0
        """The music's own level: a test turns it up between A and B, as a song does."""

    def step(self):
        self.steps += 1
        idx = (self.pos + np.arange(BLOCK)) % len(self.signal)
        self.pos = (self.pos + BLOCK) % len(self.signal)
        block = self.level * self.signal[idx]
        pair = (block, np.roll(block, 7))
        self.quality.push(pair, self.motor.procesar(*pair))


@pytest.fixture
def ab(tmp_path):
    FakeSession.instances = []
    Instalacion(parlantes=[Parlante("Red", "s0", pan=-0.7), Parlante("Blue", "s1", pan=0.7)]).guardar(
        tmp_path / "inst.json"
    )
    svc = Service(tmp_path / "inst.json", tmp_path / "presets.json", session_factory=MusicSession, log=lambda _: None)
    thread = threading.Thread(target=svc.run, daemon=True)
    thread.start()
    _ok(svc, op="preset_save", name="quiet")
    for name in ("Red", "Blue"):
        _ok(svc, op="set", speaker=name, changes={"gain_db": 3.0})
    _ok(svc, op="preset_save", name="loud")
    _ok(svc, op="start")
    yield svc
    svc.handle({"v": 1, "op": "shutdown"})
    thread.join(timeout=5)
    svc.close()


def _loudness(svc):
    return _ok(svc, op="state")["ab"]["loudness_lu"]


def _measure(svc, which):
    """Play `which` and wait for its loudness to settle (measured, then stable)."""
    _ok(svc, op="ab_play", which=which)
    _wait(lambda: (svc.ab.taken[which] or 0) >= svc.ab.measure_from + 2 * BLOCK, timeout=20)


def test_the_ab_measures_a_3_db_difference_and_does_not_touch_the_presets(ab):
    _ok(ab, op="ab_start", a="quiet", b="loud")
    _measure(ab, "a")
    _measure(ab, "b")
    loud = _loudness(ab)
    assert loud["diff"] == pytest.approx(3.0, abs=0.3)
    state = _ok(ab, op="state")["ab"]
    assert state["match_loudness"] is False
    assert state["compensation_db"] == {"a": 0.0, "b": 0.0}
    # X is never measured: its loudness would say which preset it is.
    _ok(ab, op="ab_play", which="x")
    assert _loudness(ab) == loud


def test_matching_brings_the_difference_under_0_3_lu(ab):
    _ok(ab, op="ab_start", a="quiet", b="loud", match_loudness=True)
    _measure(ab, "a")
    _measure(ab, "b")
    # B was the louder: it gets the compensation, and once heard again the two match.
    _wait(lambda: ab.ab.compensation["b"] < -2.5, timeout=20)
    _measure(ab, "b")
    _wait(lambda: _ok(ab, op="state")["ab"]["loudness_lu"] == ab.ab.loudness())
    assert abs(_loudness(ab)["diff"]) < 0.3
    comp = _ok(ab, op="state")["ab"]["compensation_db"]
    assert comp["a"] == 0.0
    assert comp["b"] == pytest.approx(-3.0, abs=0.3)
    # The presets are untouched, and stopping takes the compensation away.
    assert ab.preset_store.get("loud")["speakers"]["Red"]["gain_db"] == 3.0
    result = _ok(ab, op="ab_stop")
    assert result["loudness_lu"]["diff"] is not None
    assert FakeSession.instances[-1].motor.ganancia_comparacion_db == 0.0


def test_the_music_getting_louder_between_a_and_b_is_not_a_difference(ab):
    """Experiment 23 §6.4: A and B play at different moments of a song. The comparison is the net
    gain (outputs minus input in the same window), so a passage 12 dB louder while B plays does not
    make B "louder" (before: compensation -1.86 dB to A, then -5.17 dB to B within a minute)."""
    _ok(ab, op="preset_save", name="same")
    _ok(ab, op="ab_start", a="loud", b="same", match_loudness=True)
    _measure(ab, "a")
    FakeSession.instances[-1].level = 4.0  # +12 dB in the music itself
    _measure(ab, "b")
    assert abs(_loudness(ab)["diff"]) < 0.3
    comp = _ok(ab, op="state")["ab"]["compensation_db"]
    assert comp == {"a": 0.0, "b": 0.0}
