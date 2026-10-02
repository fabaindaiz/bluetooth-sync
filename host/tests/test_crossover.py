import numpy as np
import pytest

from aurasync.dsp import crossover

SR = 48000


def spectrum_db(h: np.ndarray, freqs: np.ndarray, n: int = 1 << 18) -> np.ndarray:
    grid = np.fft.rfftfreq(n, 1 / SR)
    mag = np.abs(np.fft.rfft(h, n))
    return 20 * np.log10(np.interp(freqs, grid, mag) + 1e-30)


def test_the_two_outputs_sum_to_an_all_pass():
    lp, hp = crossover.impulses(SR, 100.0)
    freqs = np.geomspace(20, 20000, 400)
    total = spectrum_db(lp + hp, freqs)
    assert np.abs(total).max() <= 0.1


def test_the_outputs_are_in_phase_and_6_db_down_at_the_crossover():
    n = 1 << 18
    lp, hp = crossover.impulses(SR, 100.0)
    k = round(100.0 * n / SR)
    lp_f, hp_f = np.fft.rfft(lp, n)[k], np.fft.rfft(hp, n)[k]
    assert abs(np.angle(lp_f / hp_f)) < np.radians(1.0)
    assert 20 * np.log10(abs(lp_f)) == pytest.approx(-6.02, abs=0.05)
    assert 20 * np.log10(abs(hp_f)) == pytest.approx(-6.02, abs=0.05)


def test_the_fir_matches_the_bilinear_design():
    """The truncated FIR follows the bilinear LR4 within 0.01 dB where it matters."""
    lp, hp = crossover.impulses(SR, 100.0)
    freqs = np.geomspace(20, 20000, 200)
    want_lp, want_hp = crossover.response(freqs, SR, 100.0)
    assert np.abs(spectrum_db(hp, freqs) - 20 * np.log10(np.abs(want_hp)))[freqs > 40].max() < 0.01
    assert np.abs(spectrum_db(lp, freqs) - 20 * np.log10(np.abs(want_lp)))[freqs < 400].max() < 0.01


def test_lr4_at_100_hz_leaves_the_bass_below_60_hz_at_minus_19_db():
    """Read point by point, '< -24 dB below 60 Hz' is not what an LR4 at 100 Hz gives: its slope
    leaves -18.8 dB at 60 Hz and -24.6 dB at 50 Hz. An LR8 at 100 Hz, or an LR4 at 118 Hz, meets
    it at every frequency. (In energy, with pink noise, the LR4 meets it: see below.)"""
    _, hp = crossover.impulses(SR, 100.0)
    assert spectrum_db(hp, np.array([60.0]))[0] == pytest.approx(-18.8, abs=0.1)
    assert spectrum_db(hp, np.geomspace(20, 50, 40)).max() < -24
    _, hp8 = crossover.impulses(SR, 100.0, order=8)
    assert spectrum_db(hp8, np.geomspace(20, 60, 40)).max() < -24
    _, hp118 = crossover.impulses(SR, 118.0)
    assert spectrum_db(hp118, np.geomspace(20, 60, 40)).max() < -24


@pytest.mark.parametrize("order", [4, 8])
def test_pink_noise_below_60_hz_through_the_high_pass(order):
    """Energy of pink noise in 20-60 Hz after the high-pass, relative to the input."""
    rng = np.random.default_rng(3)
    n = 1 << 19
    spec = np.fft.rfft(rng.standard_normal(n))
    f = np.fft.rfftfreq(n, 1 / SR)
    spec[1:] /= np.sqrt(f[1:])
    spec[0] = 0
    x = np.fft.irfft(spec, n)
    y = crossover.HighPass(SR, 100.0, order=order).process(x)
    band = (f >= 20) & (f < 60)
    ratio = 10 * np.log10(np.sum(np.abs(np.fft.rfft(y))[band] ** 2) / np.sum(np.abs(np.fft.rfft(x))[band] ** 2))
    # The spec's criterion read as energy: MEDIDO -28.3 dB (LR4) and -46.9 dB (LR8).
    assert ratio < (-45 if order == 8 else -24)


def test_no_latency_the_sum_peaks_at_sample_zero():
    lp, hp = crossover.impulses(SR, 100.0)
    total = lp + hp
    assert int(np.argmax(np.abs(total))) == 0
    assert total[0] > 0.97
    assert int(np.argmax(np.abs(hp))) == 0
    assert crossover.HighPass(SR, 100.0).latency == 0


def test_the_all_pass_group_delay_at_dc_is_documented():
    assert crossover.group_delay_dc_ms(100.0) == pytest.approx(4.50, abs=0.01)


@pytest.mark.parametrize("cls", [crossover.HighPass, crossover.LowPass])
def test_blocks_of_1024_and_4096_give_the_same_output(cls):
    rng = np.random.default_rng(0)
    x = rng.standard_normal(48000)
    outs = []
    for block in (1024, 4096, 1000):
        f = cls(SR, 100.0)
        outs.append(np.concatenate([f.process(x[i : i + block]) for i in range(0, len(x), block)]))
    assert np.abs(outs[0] - outs[1]).max() <= 1e-9
    assert np.abs(outs[0] - outs[2]).max() <= 1e-9
    lp, hp = crossover.impulses(SR, 100.0)
    want = np.convolve(x, hp if cls is crossover.HighPass else lp)[: len(x)]
    assert np.abs(outs[0] - want).max() <= 1e-9


def test_the_bass_feed_is_the_low_passed_mid():
    rng = np.random.default_rng(1)
    left, right = rng.standard_normal(20000), rng.standard_normal(20000)
    feed = crossover.BassFeed(SR, 100.0)
    out = np.concatenate([feed.process(left[i : i + 4096], right[i : i + 4096]) for i in range(0, 20000, 4096)])
    lp, _ = crossover.impulses(SR, 100.0)
    assert np.allclose(out, np.convolve(0.5 * (left + right), lp)[:20000], atol=1e-9)


def test_the_high_pass_reports_how_much_energy_it_removed():
    t = np.arange(SR) / SR
    hp = crossover.HighPass(SR, 100.0)
    hp.process(np.sin(2 * np.pi * 40 * t))
    assert hp.removed_db > 25
    hp.process(np.sin(2 * np.pi * 1000 * t))
    assert hp.removed_db < 0.1


def test_bad_parameters_are_refused():
    with pytest.raises(ValueError, match="order"):
        crossover.HighPass(SR, 100.0, order=3)
    with pytest.raises(ValueError, match="cutoff"):
        crossover.HighPass(SR, 30000.0)
