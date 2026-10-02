import numpy as np
import pytest

from aurasync.dsp import limiter, loudness


def test_quiet_signal_passes_untouched():
    x = 0.5 * np.sin(np.arange(4800) / 10)
    assert np.array_equal(limiter.PeakLimiter(48000).process(x), x)


def test_never_exceeds_the_ceiling_and_recovers():
    lim = limiter.PeakLimiter(48000)
    loud = 2.0 * np.sin(np.arange(4800) / 10)
    out = lim.process(loud)
    assert np.abs(out).max() <= limiter.CEILING + 1e-12
    quiet = 0.1 * np.ones(48000)
    out = np.concatenate([lim.process(quiet[i : i + 1024]) for i in range(0, len(quiet), 1024)])
    assert out[0] < 0.1
    assert np.isclose(out[-1], 0.1)
    assert np.all(np.diff(out) >= -1e-12)


SR = 48000
CEILING_DB = -1.0


def oversampled_peak_db(y: np.ndarray) -> float:
    """Ideal 4x oversampling of the whole signal (zero-padded FFT), independent of the limiter."""
    n = len(y) + 2048
    spec = np.fft.rfft(np.concatenate([y, np.zeros(2048)]))
    up = np.fft.irfft(spec, 4 * n) * 4
    return 20 * np.log10(np.abs(up).max())


def pink(n: int, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    spec = np.fft.rfft(rng.standard_normal(n))
    f = np.fft.rfftfreq(n, 1 / SR)
    spec[f >= 20] /= np.sqrt(f[f >= 20])
    spec[f < 20] = 0
    x = np.fft.irfft(spec, n)
    return x / np.std(x)


def corpus() -> list[np.ndarray]:
    """Signals with peaks between samples, all well over the ceiling."""
    from aurasync.dsp import eq
    from aurasync.dsp.response import THIRDS

    t = np.arange(SR) / SR
    rng = np.random.default_rng(5)
    boosted = eq.StreamingFIR(eq.fir(np.full(len(THIRDS), 6.0))).process(0.3 * pink(2 * SR, 1))
    treble = eq.StreamingFIR(eq.fir(np.where(THIRDS > 4000, 6.0, 0.0))).process(0.4 * pink(2 * SR, 2))
    clipped = np.clip(1.5 * pink(SR, 3) / 3, -0.9, 0.9)
    bursts = np.zeros(SR)
    for start in rng.integers(0, SR - 2000, 30):
        bursts[start : start + 2000] += rng.standard_normal(2000) * np.exp(-np.arange(2000) / 300)
    signals = [
        2.0 * np.sin(2 * np.pi * (SR / 4) * t + np.pi / 4),  # sample peak 3 dB under the true peak
        1.5 * np.sin(2 * np.pi * 11025 * t + 0.3),
        boosted,
        treble,
        clipped,
        bursts,
        np.sign(rng.standard_normal(SR)) * 0.95,
    ]
    return [band_limited(x) for x in signals]


def band_limited(x: np.ndarray) -> np.ndarray:
    """Faded in and out (10 ms) and with nothing above 20 kHz, as audio that went through a codec.

    Above 20 kHz the "true peak" depends on the reconstruction filter (no two meters agree), and
    an abrupt start or end is broadband.
    """
    fade = np.sin(np.linspace(0, np.pi / 2, 480)) ** 2
    x = x.copy()
    x[:480] *= fade
    x[-480:] *= fade[::-1]
    spec = np.fft.rfft(x)
    f = np.fft.rfftfreq(len(x), 1 / SR)
    spec *= np.clip((20000 - f) / 2000, 0, 1)
    return np.fft.irfft(spec, len(x))


def run(lim, x: np.ndarray, block: int = 4096) -> np.ndarray:
    return np.concatenate([lim.process(x[i : i + block]) for i in range(0, len(x), block)])


def test_true_peak_latency_is_the_look_ahead_and_quiet_signal_passes_delayed():
    lim = limiter.TruePeakLimiter(SR)
    assert lim.latency == round(0.003 * SR)
    x = 0.5 * np.sin(np.arange(10000) / 10)
    out = run(lim, x)
    assert np.array_equal(out[lim.latency :], x[: -lim.latency])
    assert not out[: lim.latency].any()
    assert lim.reduction_db == 0.0
    assert lim.active_fraction == 0.0


@pytest.mark.parametrize("index", range(7))
def test_no_oversampled_value_goes_over_the_ceiling(index):
    x = corpus()[index]
    lim = limiter.TruePeakLimiter(SR, ceiling_db=CEILING_DB)
    # The stream goes on after the signal (silence), so the output holds the signal's end too.
    y = run(lim, np.concatenate([x, np.zeros(4096)]))
    assert loudness.true_peak_dbtp(x, SR) > CEILING_DB + 0.5
    assert loudness.true_peak_dbtp(y, SR) <= CEILING_DB
    assert oversampled_peak_db(y) <= CEILING_DB + 0.01


def harmonics_db(y: np.ndarray, f0: float = 60.0) -> float:
    """Energy of harmonics 2-40 relative to the fundamental, steady state, in dB."""
    seg = y[SR : 3 * SR]
    spec = np.abs(np.fft.rfft(seg * np.blackman(len(seg)))) ** 2
    k0 = round(f0 * len(seg) / SR)

    def line(k: int) -> float:
        return float(spec[k - 3 : k + 4].sum())

    return 10 * np.log10(sum(line(h * k0) for h in range(2, 41)) / line(k0))


def test_a_loud_60_hz_sine_is_distorted_at_least_20_db_less_than_by_the_peak_limiter():
    t = np.arange(4 * SR) / SR
    x = 10 ** ((CEILING_DB + 6) / 20) * np.sin(2 * np.pi * 60 * t)
    old = harmonics_db(run(limiter.PeakLimiter(SR), x))
    new = harmonics_db(run(limiter.TruePeakLimiter(SR, ceiling_db=CEILING_DB), x))
    assert new <= old - 20


def test_blocks_of_1024_and_4096_give_the_same_output():
    x = corpus()[2]
    outs = [run(limiter.TruePeakLimiter(SR), x, block) for block in (1024, 4096, 777)]
    assert np.abs(outs[0] - outs[1]).max() <= 1e-9
    assert np.abs(outs[0] - outs[2]).max() <= 1e-9


def test_the_attack_is_smooth_and_starts_before_the_peak():
    x = np.full(SR // 2, 0.1)
    x[10000] = 2.0
    lim = limiter.TruePeakLimiter(SR)
    y = run(lim, x)
    gain = y[lim.latency :] / x[: -lim.latency]
    assert gain[10000] * 2.0 <= 10 ** (-1 / 20) + 1e-9
    assert gain[10000 - lim.latency] == 1.0
    assert gain[10000 - 20] < 0.9
    assert np.abs(np.diff(gain[9800:10001])).max() < 0.02


def test_the_release_returns_exponentially_with_the_release_time():
    release_ms = 100.0
    x = np.full(SR, 0.1)
    x[1000] = 2.0
    lim = limiter.TruePeakLimiter(SR, release_ms=release_ms)
    y = run(lim, x)
    gain = y[lim.latency :] / x[: -lim.latency]
    depth = 1 - gain[1000]
    tau = round(release_ms / 1000 * SR)
    hold = int(np.argmax(gain[1000:] > gain[1000] + 1e-9))
    assert (1 - gain[1000 + hold + tau]) / depth == pytest.approx(np.exp(-1), rel=0.05)
    assert gain[-1] > 0.999


def test_reduction_and_time_active_are_reported():
    lim = limiter.TruePeakLimiter(SR)
    lim.process(0.1 * np.ones(4096))
    assert lim.active_fraction == 0.0
    loud = 2.0 * np.sin(np.arange(4096) / 10)
    lim.process(loud)
    lim.process(loud)
    assert lim.reduction_db == pytest.approx(-20 * np.log10(10 ** (-1 / 20) / 2.0), abs=0.3)
    assert lim.active_fraction > 0.9
    assert lim.max_reduction_db >= lim.reduction_db


def test_the_peak_limiter_is_unchanged():
    """`PeakLimiter` is what the engine uses today: its output must not move by a bit."""
    rng = np.random.default_rng(11)
    x = np.concatenate([0.3 * rng.standard_normal(5000), 2.5 * rng.standard_normal(3000), 0.2 * np.ones(9000)])
    lim = limiter.PeakLimiter(48000)
    out = run(lim, x, 1024)
    gain, step, want = 1.0, 1.0 / (0.25 * 48000), np.empty_like(x)
    for i, v in enumerate(x):
        gain = min(1.0, gain + step, limiter.CEILING / max(abs(v), 1e-12))
        want[i] = v * gain
    assert np.allclose(out, want, rtol=0, atol=1e-12)
