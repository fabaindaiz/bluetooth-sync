import numpy as np
import pytest

from aurasync.dsp import loudness

SR = 48000


def sine(level_dbfs: float, seconds: float, freq: float = 1000.0, phase_deg: float = 0.0) -> np.ndarray:
    t = np.arange(round(seconds * SR)) / SR
    return 10 ** (level_dbfs / 20) * np.sin(2 * np.pi * freq * t + np.radians(phase_deg))


def stereo(*parts: np.ndarray) -> np.ndarray:
    mono = np.concatenate(parts)
    return np.stack([mono, mono], axis=1)


def measure(x: np.ndarray, block: int = 4096) -> loudness.LoudnessMeter:
    meter = loudness.LoudnessMeter(SR, x.shape[1] if x.ndim == 2 else 1)
    for i in range(0, len(x), block):
        meter.push(x[i : i + block])
    return meter


def iir_response(b, a, freqs, sr=SR):
    z = np.exp(-1j * 2 * np.pi * np.asarray(freqs) / sr)
    return np.polyval(np.asarray(b)[::-1], z) / np.polyval(np.asarray(a)[::-1], z)


def test_the_k_weighting_fir_follows_the_iir_of_the_standard():
    """|K(f)| of the FIR against the IIR of BS.1770-5 tables 1 and 2, 20 Hz-20 kHz."""
    freqs = np.geomspace(20, 20000, 300)
    want = iir_response(*loudness.SHELF_48K, freqs) * iir_response(*loudness.HIGHPASS_48K, freqs)
    h = loudness.k_weighting_fir(SR)
    n = 1 << 18
    got = np.interp(freqs, np.fft.rfftfreq(n, 1 / SR), np.abs(np.fft.rfft(h, n)))
    assert np.abs(20 * np.log10(got / np.abs(want))).max() < 0.01


def test_the_analog_design_reproduces_the_tables_at_48_khz():
    for (b, a), (tb, ta) in zip(
        loudness.k_weighting_biquads(SR), (loudness.SHELF_48K, loudness.HIGHPASS_48K), strict=True
    ):
        assert np.allclose(b, tb, atol=1e-8)
        assert np.allclose(a, ta, atol=1e-8)


def test_k_weighting_gains_0_691_db_at_997_hz():
    k = iir_response(*loudness.SHELF_48K, [997.0]) * iir_response(*loudness.HIGHPASS_48K, [997.0])
    assert 20 * np.log10(np.abs(k[0])) == pytest.approx(0.691, abs=0.002)


@pytest.mark.parametrize("level", [-23.0, -33.0])
def test_a_1_khz_stereo_sine_reads_its_level_in_lufs(level):
    meter = measure(stereo(sine(level, 5.0)))
    assert meter.momentary == pytest.approx(level, abs=0.1)
    assert meter.short_term == pytest.approx(level, abs=0.1)
    assert meter.integrated == pytest.approx(level, abs=0.1)


def test_block_size_does_not_change_the_reading():
    x = stereo(sine(-20.0, 4.0, 300.0))
    a, b = measure(x, 4096), measure(x, 1000)
    assert a.momentary == pytest.approx(b.momentary, abs=1e-9)
    assert a.integrated == pytest.approx(b.integrated, abs=1e-9)
    assert a.true_peak_dbtp == pytest.approx(b.true_peak_dbtp, abs=1e-9)


def test_tech_3341_case_3_the_relative_gate_leaves_out_the_quiet_parts():
    meter = measure(stereo(sine(-36, 10), sine(-23, 60), sine(-36, 10)))
    assert meter.integrated == pytest.approx(-23.0, abs=0.1)


def test_tech_3341_case_4_silence_and_quiet_parts_are_gated():
    meter = measure(stereo(sine(-72, 10), sine(-36, 10), sine(-23, 60), sine(-36, 10), sine(-72, 10)))
    assert meter.integrated == pytest.approx(-23.0, abs=0.1)


def test_tech_3341_case_5_loud_and_quiet_parts_average_by_energy():
    meter = measure(stereo(sine(-26, 20), sine(-20, 20.1), sine(-26, 20)))
    assert meter.integrated == pytest.approx(-23.0, abs=0.1)


def test_the_absolute_gate_leaves_out_everything_below_minus_70():
    assert measure(stereo(sine(-75, 5))).integrated == -np.inf
    assert measure(stereo(sine(-65, 5))).integrated == pytest.approx(-65.0, abs=0.1)


def test_momentary_and_short_term_follow_a_step():
    meter = measure(stereo(sine(-23, 4), sine(-33, 1)))
    assert meter.momentary == pytest.approx(-33.0, abs=0.1)
    assert -33 < meter.short_term < -23


@pytest.mark.parametrize(
    ("freq", "phase", "sample_peak_db"),
    [(SR / 4, 45.0, -3.01), (SR / 6, 60.0, -1.25), (SR / 8, 67.5, -0.69), (997.0, 0.0, 0.0)],
)
def test_true_peak_finds_the_peaks_between_samples(freq, phase, sample_peak_db):
    # Faded in and out: an abrupt start is a step, and its band-limited reconstruction really
    # does overshoot (Gibbs), which is not what this test is about.
    x = sine(-6.0, 1.0, freq, phase)
    fade = np.sin(np.linspace(0, np.pi / 2, 960)) ** 2
    x[:960] *= fade
    x[-960:] *= fade[::-1]
    assert 20 * np.log10(np.abs(x).max()) == pytest.approx(-6.0 + sample_peak_db, abs=0.05)
    assert loudness.true_peak_dbtp(x, SR) == pytest.approx(-6.0, abs=0.2)
    meter = measure(np.stack([x, 0.5 * x], axis=1))
    assert meter.true_peak_dbtp == pytest.approx(-6.0, abs=0.2)


def test_psr_is_true_peak_minus_short_term():
    meter = measure(stereo(sine(-20.0, 4.0)))
    # A sine: peak -20 dBFS, short-term -20 LUFS (stereo, 1 kHz), so PSR is 0 dB.
    assert meter.psr == pytest.approx(0.0, abs=0.2)
    assert meter.psr == pytest.approx(meter.true_peak_short_dbtp - meter.short_term, abs=1e-9)


def test_the_pure_functions_agree_with_the_meter():
    x = stereo(sine(-26, 3), sine(-18, 3, 200.0))
    assert loudness.integrated_lufs(x, SR) == pytest.approx(measure(x).integrated, abs=1e-9)


def test_an_empty_meter_reads_minus_infinity():
    meter = loudness.LoudnessMeter(SR, 2)
    assert meter.momentary == -np.inf
    assert meter.integrated == -np.inf
    assert meter.true_peak_dbtp == -np.inf
    meter.push(np.zeros((0, 2)))
    assert meter.short_term == -np.inf


def pink(n: int, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    spec = np.fft.rfft(rng.standard_normal(n))
    f = np.fft.rfftfreq(n, 1 / SR)
    spec[f >= 20] /= np.sqrt(f[f >= 20])
    spec[f < 20] = 0
    x = np.fft.irfft(spec, n)
    return x / np.std(x)


def music_like(seconds: float, seed: int = 0) -> np.ndarray:
    """Bass notes, pink noise and drum-like bursts, with the level moving by sections."""
    rng = np.random.default_rng(seed)
    n = round(seconds * SR)
    t = np.arange(n) / SR
    x = 0.05 * pink(n, seed)
    for f in (41.2, 55.0, 82.4):
        x += 0.15 * np.sin(2 * np.pi * f * t) * (0.5 + 0.5 * np.sin(2 * np.pi * 0.3 * t + f))
    for start in rng.integers(0, n - SR // 4, int(seconds * 2)):
        burst = rng.standard_normal(SR // 4) * np.exp(-np.arange(SR // 4) / 1500)
        x[start : start + SR // 4] += 0.3 * burst
    sections = np.repeat(10 ** (rng.uniform(-12, 0, int(seconds) + 1) / 20), SR)[:n]
    return x * sections


def time_domain_reference(x: np.ndarray) -> tuple[float, list[float]]:
    """Integrated loudness and step energies with K applied in the time domain (the IIR's FIR)."""
    h = loudness.k_weighting_fir(SR)
    size = 1 << int(np.ceil(np.log2(len(x) + len(h))))
    y = np.fft.irfft(np.fft.rfft(x, size, axis=0) * np.fft.rfft(h, size)[:, None], size, axis=0)[: len(x)]
    energy = (y**2).sum(axis=1)
    step = SR // 10
    steps = energy[: len(energy) // step * step].reshape(-1, step).sum(axis=1)
    blocks = np.convolve(steps, np.ones(4), mode="valid") / (4 * step)
    levels = -0.691 + 10 * np.log10(blocks)
    kept = blocks[levels > -70]
    relative = -0.691 + 10 * np.log10(kept.mean()) - 10
    kept = blocks[(levels > -70) & (levels > relative)]
    return float(-0.691 + 10 * np.log10(kept.mean())), list(steps)


@pytest.mark.parametrize(
    "signal",
    [
        lambda: stereo(0.1 * pink(30 * SR)),
        lambda: np.stack([music_like(30, 1), music_like(30, 2)], axis=1),
        lambda: stereo(sine(-26, 20), sine(-20, 20.1), sine(-26, 20)),
    ],
)
def test_the_frequency_domain_weighting_matches_the_time_domain_filter(signal):
    x = signal()
    want, steps = time_domain_reference(x)
    meter = measure(x)
    # The spec's tolerance. MEDIDO: pink 0.004 LU, the EBU signal ~0, the bass-heavy
    # music-like signal -0.06 LU (a bass tone leaks into bins where K is lower).
    assert meter.integrated == pytest.approx(want, abs=0.1)
    got = meter.step_energies[: len(steps)]
    loud = np.asarray(steps) > 1e-3 * np.max(steps)
    assert np.abs(10 * np.log10(got[loud] / np.asarray(steps)[loud])).max() < 0.6


def _every_position_dbtp(x: np.ndarray) -> float:
    w = loudness.HALF_WIDTH
    padded = np.concatenate([np.zeros(2 * w), x, np.zeros(2 * w)])
    full = max(np.abs(np.convolve(padded, k)).max() for k in loudness.interpolation_kernels())
    return 20 * np.log10(max(full, np.abs(x).max()))


def _faded(x: np.ndarray) -> np.ndarray:
    fade = np.sin(np.linspace(0, np.pi / 2, 480)) ** 2
    x = x.copy()
    x[:480] *= fade
    x[-480:] *= fade[::-1]
    return x


def test_interpolating_only_near_the_sample_peak_finds_the_same_true_peak():
    """The shortcut in `_true_peak` against interpolating every position, on a varied corpus."""
    rng = np.random.default_rng(9)
    corpus = [np.clip(3 * pink(SR, seed), -1, 1) for seed in range(3)]
    corpus += [music_like(2, 3), rng.standard_normal(SR) * 0.3, np.sign(rng.standard_normal(SR)) * 0.5]
    corpus += [sine(-3, 0.5, f, p) for f in (SR / 4, SR / 3, 0.45 * SR, 19000.0) for p in (0.0, 30.0, 45.0)]
    for x in corpus:
        assert loudness.true_peak_dbtp(_faded(x), SR) == pytest.approx(_every_position_dbtp(_faded(x)), abs=1e-9)


def test_an_abrupt_high_frequency_onset_is_where_the_shortcut_can_miss_a_little():
    """A tone that starts at full level rings before its first sample (Gibbs): a point there can
    rise more than 6 dB over both of its neighbours. MEDIDO: at most 0.26 dB missed, over
    5-23 kHz and 13 phases."""
    worst = 0.0
    for f in np.linspace(5000, 23000, 19):
        for phase in np.linspace(0, 180, 7):
            x = sine(-3, 0.05, f, phase)
            worst = max(worst, _every_position_dbtp(x) - loudness.true_peak_dbtp(x, SR))
    assert 0.0 <= worst < 0.3
