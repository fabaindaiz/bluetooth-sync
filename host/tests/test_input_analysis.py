import numpy as np

from aurasync.dsp.input_analysis import InputAnalyzer

SR = 48000
rng = np.random.default_rng(0)


def _feed(left, right, seconds=4.0):
    a = InputAnalyzer(SR)
    n = int(SR * seconds)
    for i in range(0, n, 4096):
        a.update(left[i : i + 4096], right[i : i + 4096])
    return a.summary()


def _noise(n, cutoff=None):
    x = rng.standard_normal(n) * 0.1
    if cutoff:
        spec = np.fft.rfft(x)
        spec[np.fft.rfftfreq(n, 1 / SR) > cutoff] = 0
        x = np.fft.irfft(spec, n)
    return x


def test_mono_is_called_mono():
    x = _noise(SR * 4)
    s = _feed(x, x)
    assert s["kind"] == "mono"
    assert s["correlation"] > 0.99


def test_a_stereo_mix_is_stereo_with_its_width():
    common, a, b = _noise(SR * 4), _noise(SR * 4), _noise(SR * 4)
    s = _feed(common + 0.4 * a, common + 0.4 * b)
    assert s["kind"] == "estéreo"
    assert 0.7 < s["correlation"] < 0.95
    assert -20 < s["side_db"] < -5


def test_polarity_flipped_channel_is_out_of_phase():
    x = _noise(SR * 4)
    assert _feed(x, -x)["kind"] == "fuera de fase"


def test_bandwidth_finds_a_lossy_cutoff():
    x = _noise(SR * 4, cutoff=16000)
    s = _feed(x, x)
    assert 15000 <= s["bandwidth_hz"] <= 16500
    full = _noise(SR * 4)
    assert _feed(full, full)["bandwidth_hz"] >= 19000


def test_clipping_is_counted_and_silence_keeps_the_last_description():
    x = np.clip(_noise(SR * 4) * 20, -1, 1)
    a = InputAnalyzer(SR)
    a.update(x[:48000], x[:48000])
    assert a.summary()["clipped"] > 0
    a.update(np.zeros(4096), np.zeros(4096))
    s = a.summary()
    assert s["stale"]
    assert s["kind"] == "mono"
