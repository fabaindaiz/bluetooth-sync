import numpy as np

from aurasync.dsp import limiter


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
