"""The masked probe (`dsp/probe.py`) and its place in the engine. SIMULATED."""

import numpy as np
import pytest

from aurasync import estimulos, motor
from aurasync.config import Instalacion, Parlante
from aurasync.dsp import probe
from tests import probe_room

SR = 48000
BLOCK = 4096


def _thirds(y: np.ndarray) -> np.ndarray:
    f = np.fft.rfftfreq(len(y), 1 / SR)
    power = np.abs(np.fft.rfft(y)) ** 2
    return np.array([power[(f >= c / 2 ** (1 / 6)) & (f < c * 2 ** (1 / 6))].sum() for c in probe.THIRDS])


def _run(shaper: probe.ProbeShaper, x: np.ndarray, block: int = BLOCK) -> np.ndarray:
    return np.concatenate([shaper.process(x[i : i + block]) for i in range(0, len(x), block)])


@pytest.mark.parametrize("margin", [-20.0, -30.0])
def test_the_probe_sits_its_margin_under_the_music_in_every_third(margin):
    """Calibrated level: on stationary pink noise, each third within its band is `margin` dB
    under the music without the release (±0.5 dB), and at most 1 dB louder with it."""
    x = estimulos.calibracion(1, 10.0, semilla=3)[0] * 0.1
    inside = (probe.THIRDS >= 400) & (probe.THIRDS <= 6300)
    music = _thirds(x[: len(x) - probe.LATENCY])[inside]
    flat = probe.ProbeShaper(1, margin_db=margin)
    flat._decay = 0.0  # noqa: SLF001 - no release: the bare calibration
    level = 10 * np.log10(_thirds(_run(flat, x)[probe.LATENCY :])[inside] / music)
    assert np.all(np.abs(level - margin) <= 0.5), level
    with_release = 10 * np.log10(
        _thirds(_run(probe.ProbeShaper(1, margin_db=margin), x)[probe.LATENCY :])[inside] / music
    )
    assert np.all((with_release >= margin - 0.5) & (with_release <= margin + 1.0)), with_release


def test_the_probe_stays_inside_its_band():
    x = estimulos.calibracion(1, 6.0, semilla=1)[0] * 0.1
    p = _run(probe.ProbeShaper(2), x)
    f = np.fft.rfftfreq(len(p), 1 / SR)
    power = np.abs(np.fft.rfft(p)) ** 2
    inside = (f >= probe.BAND_HZ[0]) & (f <= probe.BAND_HZ[1])
    outside = (f < probe.BAND_HZ[0] / 1.5) | (f > probe.BAND_HZ[1] * 1.5)
    assert power[outside].sum() < 1e-3 * power[inside].sum()


def test_any_block_size_gives_the_same_number_of_samples():
    x = np.random.default_rng(0).standard_normal(SR) * 0.1
    shaper = probe.ProbeShaper(3)
    sizes = [1, 511, 512, 513, 4096, 37, 9000]
    out = [shaper.process(x[:n]) for n in sizes]
    assert [len(o) for o in out] == sizes


def test_no_probe_in_silence_and_none_before_the_music_starts():
    """The floor, and the latency: the probe trails the music, it never comes before it."""
    rng = np.random.default_rng(4)
    x = np.concatenate([np.zeros(SR), rng.standard_normal(SR) * 0.1, np.zeros(2 * SR)])
    p = _run(probe.ProbeShaper(5), x)
    assert np.all(p[:SR] == 0), "probe before the music: pre-echo"
    assert np.sqrt(np.mean(p[SR + 2 * probe.LATENCY : 2 * SR] ** 2)) > 0.003
    # 20 dB per RELEASE_S after the music stops: 1.5 s later there is nothing left to hear.
    tail = p[int(3.5 * SR) :]
    assert np.sqrt(np.mean(tail**2)) < 1e-6


def test_two_speakers_with_the_same_music_get_independent_probes():
    x = estimulos.calibracion(1, 8.0, semilla=5)[0] * 0.1
    bank = probe.MaskedProbe(["a", "b"])
    bank.enabled = True
    a, b = [], []
    for i in range(0, len(x), BLOCK):
        bank.begin(len(x[i : i + BLOCK]))
        a.append(bank.add("a", x[i : i + BLOCK]) - x[i : i + BLOCK])
        b.append(bank.add("b", x[i : i + BLOCK]) - x[i : i + BLOCK])
    a, b = np.concatenate(a)[SR:], np.concatenate(b)[SR:]
    assert abs(np.corrcoef(a, b)[0, 1]) < 0.02
    assert abs(np.corrcoef(a, x[SR:])[0, 1]) < 0.02


def test_switching_ramps_in_50_ms_and_off_it_is_not_called():
    bank = probe.MaskedProbe(["a"])
    assert not bank.active
    bank.enabled = True
    bank.begin(4800)
    envelope = bank._envelope  # noqa: SLF001
    assert envelope[0] < 0.01
    assert envelope[int(0.05 * SR) - 1] == pytest.approx(1.0)
    bank.enabled = False
    assert bank.active  # still ramping down
    bank.begin(4800)
    assert not bank.active


def _installation(n: int) -> Instalacion:
    return Instalacion(
        parlantes=[
            Parlante(f"s{i}", f"sink{i}", pan=(-0.7, 0.7, 0.7)[i % 3], ambiente=(0.15, 0.15, 0.55)[i % 3])
            for i in range(n)
        ],
    )


def test_attached_and_off_the_engine_is_bit_identical():
    """The golden of `test_chain_golden.py` runs without a probe; this one with a probe that
    was attached and never switched on."""
    rng = np.random.default_rng(6)
    left, right = probe_room.music(3.0, rng)
    plain = motor.procesar_completo(motor.Motor(_installation(3), SR, ecualizar=True), left, right, BLOCK)
    m = motor.Motor(_installation(3), SR, ecualizar=True)
    m.sonda = probe.MaskedProbe([p.nombre for p in m.instalacion.parlantes])
    with_probe = motor.procesar_completo(m, left, right, BLOCK)
    for name, x in plain.items():
        assert np.array_equal(x, with_probe[name])


def test_on_the_engine_adds_exactly_what_it_reports():
    rng = np.random.default_rng(7)
    left, right = probe_room.music(3.0, rng)
    plain = motor.procesar_completo(motor.Motor(_installation(3), SR), left, right, BLOCK)
    m = motor.Motor(_installation(3), SR)
    m.sonda = probe.MaskedProbe([p.nombre for p in m.instalacion.parlantes])
    m.sonda.enabled = True
    added = {p.nombre: [] for p in m.instalacion.parlantes}
    out = {p.nombre: [] for p in m.instalacion.parlantes}
    for i in range(0, len(left), BLOCK):
        for name, x in m.procesar(left[i : i + BLOCK], right[i : i + BLOCK]).items():
            out[name].append(x)
            added[name].append(m.sonda.last[name])
    for name, x in plain.items():
        sent, probe_sent = np.concatenate(out[name]), np.concatenate(added[name])
        # Below the limiter's ceiling the limiter does nothing: output = music + probe.
        assert np.max(np.abs(sent - (x + probe_sent))) < 1e-12
        # In the probe's band (the music's bass and kick are below it), about the margin.
        inside = (probe.THIRDS >= 400) & (probe.THIRDS <= 6300)
        ratio_db = 10 * np.log10(_thirds(probe_sent[SR:])[inside].sum() / _thirds(x[SR:])[inside].sum())
        assert -21.0 < ratio_db < -17.0, ratio_db


def test_the_bottom_of_a_cut_is_still_exact_silence():
    rng = np.random.default_rng(8)
    left, right = probe_room.music(2.0, rng)
    m = motor.Motor(_installation(3), SR)
    m.sonda = probe.MaskedProbe([p.nombre for p in m.instalacion.parlantes])
    m.sonda.enabled = True
    motor.procesar_completo(m, left, right, BLOCK)
    m.cortar()
    out = motor.procesar_completo(m, left[:BLOCK], right[:BLOCK], 256)
    assert all((x == 0).sum() > 0 for x in out.values())
