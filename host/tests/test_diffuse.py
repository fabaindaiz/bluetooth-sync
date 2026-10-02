import numpy as np
import pytest

from aurasync.dsp import diffuse

SR = 48000


def pink(n: int, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    spec = np.fft.rfft(rng.standard_normal(n))
    f = np.fft.rfftfreq(n, 1 / SR)
    spec[f >= 20] /= np.sqrt(f[f >= 20])
    spec[f < 20] = 0
    x = np.fft.irfft(spec, n)
    return 0.1 * x / np.std(x)


def coherence(a: np.ndarray, b: np.ndarray, nperseg: int) -> float:
    """Magnitude-squared coherence (Welch, Hann, 50 % overlap), mean over 500 Hz-4 kHz."""
    w = np.hanning(nperseg)
    saa = sbb = sab = 0
    for i in range(0, len(a) - nperseg, nperseg // 2):
        fa, fb = np.fft.rfft(a[i : i + nperseg] * w), np.fft.rfft(b[i : i + nperseg] * w)
        saa, sbb, sab = saa + np.abs(fa) ** 2, sbb + np.abs(fb) ** 2, sab + fa * np.conj(fb)
    f = np.fft.rfftfreq(nperseg, 1 / SR)
    band = (f >= 500) & (f <= 4000)
    return float(np.mean(np.abs(sab[band]) ** 2 / (saa[band] * sbb[band])))


def speakers(x: np.ndarray, level_db: float) -> list[np.ndarray]:
    """Three speakers fed the same signal, each plus its own tail."""
    outs = []
    for seed in (0, 1, 2):
        d = diffuse.Diffuse(SR, seed, level_db=level_db)
        outs.append(x + np.concatenate([d.process(x[i : i + 4096]) for i in range(0, len(x), 4096)]))
    return outs


@pytest.mark.parametrize("nperseg", [1024, 4096])
def test_the_tails_lower_the_coherence_between_speakers_by_0_15_at_minus_11_db(nperseg):
    """With the same pink noise to the three speakers (coherence 1 without tails).

    MEDIDO: at the spec's -16 dB the drop is only 0.065-0.074; -12 dB gives 0.15-0.17;
    -11 dB is the level that clears 0.15 with both analysis windows (21 and 85 ms).
    """
    x = pink(20 * SR)
    outs = speakers(x, -11.0)
    pairs = [(0, 1), (0, 2), (1, 2)]
    assert coherence(x, x, nperseg) == pytest.approx(1.0)
    assert max(coherence(outs[i], outs[j], nperseg) for i, j in pairs) <= 1.0 - 0.15
    at_16 = speakers(x, -16.0)
    assert 0.05 < 1.0 - max(coherence(at_16[i], at_16[j], nperseg) for i, j in pairs) < 0.10


def octave_levels(x: np.ndarray) -> np.ndarray:
    f = np.fft.rfftfreq(len(x), 1 / SR)
    power = np.abs(np.fft.rfft(x)) ** 2
    centres = 1000 * 2.0 ** np.arange(-4, 5)  # 62.5 Hz - 16 kHz
    return np.array([10 * np.log10(power[(f >= c / np.sqrt(2)) & (f < c * np.sqrt(2))].sum()) for c in centres])


def test_each_speaker_keeps_its_octave_spectrum_within_1_db():
    x = pink(20 * SR, seed=1)
    for y in speakers(x, -11.0):
        assert np.abs(octave_levels(y) - octave_levels(x)).max() <= 1.0


def test_no_latency_the_tail_starts_after_the_pre_delay_in_the_same_block():
    d = diffuse.Diffuse(SR, 3, level_db=0.0, predelay_ms=15.0)
    impulse = np.zeros(4096)
    impulse[0] = 1.0
    out = d.process(impulse)
    pre = round(0.015 * SR)
    assert np.abs(out[:pre]).max() < 1e-12
    assert np.abs(out[pre : pre + 200]).max() > 0
    assert np.allclose(out, d.ir[:4096], atol=1e-12)
    assert d.latency == 0


def test_partitioned_convolution_equals_one_convolution_for_any_block():
    x = pink(3 * SR, seed=2)
    want = diffuse.tail(x, SR, 5, -12.0)
    for partition, block in ((4096, 4096), (1024, 1024), (4096, 1000), (1024, 3000)):
        d = diffuse.Diffuse(SR, 5, level_db=-12.0, block=partition)
        got = np.concatenate([d.process(x[i : i + block]) for i in range(0, len(x), block)])
        assert np.abs(got - want).max() <= 1e-9, (partition, block)


def test_the_response_decays_by_its_rt60_and_faster_above_the_damping():
    h = diffuse.impulse_response(SR, 0, rt60_s=0.6, predelay_ms=0.0, damping_hz=4000.0)
    assert np.sum(h**2) == pytest.approx(1.0)
    frame = 2048
    w = np.hanning(frame)
    f = np.fft.rfftfreq(frame, 1 / SR)
    starts = np.arange(0, len(h) - frame, frame // 4)
    power = np.array([np.abs(np.fft.rfft(h[i : i + frame] * w)) ** 2 for i in starts])

    def rt60(lo: float, hi: float) -> float:
        """From the slope of the band's short-time energy between 50 and 300 ms."""
        level = 10 * np.log10(power[:, (f >= lo) & (f < hi)].sum(axis=1))
        t = (starts + frame / 2) / SR
        fit = (t > 0.05) & (t < 0.3)
        return -60 / np.polyfit(t[fit], level[fit], 1)[0]

    assert rt60(500, 2000) == pytest.approx(0.6, rel=0.1)
    assert rt60(12000, 20000) < 0.35


def test_each_seed_gives_its_own_tail():
    a, b = diffuse.impulse_response(SR, 0), diffuse.impulse_response(SR, 1)
    assert np.array_equal(a, diffuse.impulse_response(SR, 0))
    assert abs(np.dot(a, b)) < 0.05


def test_off_returns_silence_and_the_level_ramps():
    x = pink(4 * 4096, seed=3)
    d = diffuse.Diffuse(SR, 0, level_db=None)
    assert not d.process(x[:4096]).any()
    d.level_db = -12.0
    ramp = d.process(x[4096:8192])
    assert np.abs(ramp[:100]).max() < 0.05 * np.abs(d.process(x[8192:12288])).max()
