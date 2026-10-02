import numpy as np
import pytest

from aurasync.dsp import crossover, virtual_bass

SR = 48000


def pink(n: int, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    spec = np.fft.rfft(rng.standard_normal(n))
    f = np.fft.rfftfreq(n, 1 / SR)
    spec[f >= 20] /= np.sqrt(f[f >= 20])
    spec[f < 20] = 0
    x = np.fft.irfft(spec, n)
    return 0.1 * x / np.std(x)


def band_energy(x: np.ndarray, lo: float, hi: float) -> float:
    f = np.fft.rfftfreq(len(x), 1 / SR)
    return float(np.sum(np.abs(np.fft.rfft(x))[(f >= lo) & (f < hi)] ** 2))


def line_db(x: np.ndarray, freq: float) -> float:
    w = np.hanning(len(x))
    s = np.abs(np.fft.rfft(x * w))
    k = round(freq * len(x) / SR)
    return 20 * np.log10(s[k - 2 : k + 3].max() + 1e-30)


@pytest.mark.parametrize("knob", [-6.0, 0.0, 6.0])
def test_with_pink_noise_the_knob_sets_the_harmonics_energy_against_the_bass_band(knob):
    x = pink(1 << 19)
    vb = virtual_bass.VirtualBass(SR, 90.0, harmonics_db=knob)
    y = vb.process(x)
    band = crossover.LowPass(SR, 90.0).process(x)
    assert 10 * np.log10(band_energy(y, 0, SR) / band_energy(band, 0, SR)) == pytest.approx(knob, abs=0.5)


@pytest.mark.parametrize(("knob", "rise"), [(-6.0, 0.9), (0.0, 2.7), (6.0, 6.5)])
def test_with_pink_noise_the_bass_below_80_hz_does_not_rise_and_100_400_hz_rises_with_the_knob(knob, rise):
    """The stage's output is the high-passed path plus the harmonics (what `protect` sends)."""
    x = pink(1 << 19)
    hp = crossover.HighPass(SR, 90.0).process(x)
    made = virtual_bass.VirtualBass(SR, 90.0, harmonics_db=knob).process(x)
    out = hp + made
    assert 10 * np.log10(band_energy(out, 20, 80) / band_energy(x, 20, 80)) < -10
    assert 10 * np.log10(band_energy(made, 20, 80) / band_energy(made, 100, 400)) < -15
    assert 10 * np.log10(band_energy(out, 100, 400) / band_energy(hp, 100, 400)) == pytest.approx(rise, abs=0.5)


def test_two_tones_intermodulate_above_their_harmonics_as_any_memoryless_rectifier_does():
    """R3's '>= 30 dB of inharmonic products below the harmonics' is NOT met, and cannot be.

    |cos a + cos b| = 2 |cos((a+b)/2)| |cos((a-b)/2)|: the strongest product of a full-wave
    rectifier fed two tones is at f1 + f2, ~3x the amplitude of 2 f1 and 2 f2 (any even
    memoryless nonlinearity does the same: x^2 gives f1 + f2 at twice 2 f1). Pinned here so
    that a change to the algorithm shows up; a per-bin (phase-vocoder) generator is what
    avoids it, at tens of ms of latency (Moliner et al., DAFx-20).
    """
    t = np.arange(4 * SR) / SR
    x = 0.25 * np.sin(2 * np.pi * 50 * t) + 0.25 * np.sin(2 * np.pi * 70 * t)
    y = virtual_bass.VirtualBass(SR, 90.0, harmonics_db=0.0).process(x)[SR : 3 * SR]
    harmonic = max(line_db(y, 100), line_db(y, 140))
    intermod = line_db(y, 120)
    assert intermod - harmonic == pytest.approx(7.8, abs=1.5)


def test_one_tone_gives_its_harmonics_and_nothing_below_the_cutoff():
    t = np.arange(4 * SR) / SR
    y = virtual_bass.VirtualBass(SR, 90.0, harmonics_db=0.0).process(0.3 * np.sin(2 * np.pi * 50 * t))[SR : 3 * SR]
    second = line_db(y, 100)
    assert line_db(y, 200) < second - 6
    assert line_db(y, 50) < second - 40
    for f in (75, 125, 150, 175, 225):
        assert line_db(y, f) < second - 60


def test_the_harmonics_follow_the_level_linearly():
    x = pink(1 << 16, seed=4)
    a = virtual_bass.VirtualBass(SR, 90.0, harmonics_db=0.0).process(x)
    b = virtual_bass.VirtualBass(SR, 90.0, harmonics_db=0.0).process(0.1 * x)
    assert np.abs(b - 0.1 * a).max() < 1e-12


def test_blocks_of_1024_and_4096_give_the_same_output():
    x = pink(48000, seed=2)
    outs = []
    for block in (1024, 4096, 999):
        vb = virtual_bass.VirtualBass(SR, 90.0, harmonics_db=3.0)
        outs.append(np.concatenate([vb.process(x[i : i + block]) for i in range(0, len(x), block)]))
    assert np.abs(outs[0] - outs[1]).max() <= 1e-9
    assert np.abs(outs[0] - outs[2]).max() <= 1e-9
    assert np.abs(outs[0] - virtual_bass.harmonics(x, SR, 90.0, 3.0)).max() <= 1e-9


def test_off_is_silence_and_turning_it_on_ramps_the_gain():
    x = pink(3 * 4096, seed=5)
    vb = virtual_bass.VirtualBass(SR, 90.0)
    assert not np.any(vb.process(x[:4096]))
    vb.harmonics_db = 0.0
    on = vb.process(x[4096:8192])
    steady = vb.process(x[8192:])
    assert np.abs(on[:64]).max() < 0.05 * np.abs(steady).max()
    vb.harmonics_db = None
    assert vb.process(x[:4096]).any()
    assert not np.any(vb.process(x[:4096]))
    assert vb.latency == 0
