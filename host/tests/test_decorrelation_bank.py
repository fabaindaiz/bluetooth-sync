"""The decorrelator with many speakers (`dsp/decorrelation_bank.py`, experiment 16 §2 and §9).

What must hold, and why:

1. **The bank is today's, filter for filter**: the speakers' sound does not change with the
   defaults, at any count (the golden covers 3; this covers 8).
2. **Every output stays flat to ±0.5 dB per third octave** (experiment 16's criterion) with 8.
3. **The separation it reports is the one that survives a small delay**: above 500 Hz, within
   ±1 ms. Below 2 kHz no fixed bank separates, with any number of filters: that is why the
   octave-chosen bank of experiment 16 is not in the product (§9).
4. **`assign` gives the least alike filters to the most alike mixes**, and is a permutation.
"""

import numpy as np
import pytest

from aurasync.dsp import decorrelate
from aurasync.dsp import decorrelation_bank as bank_mod

OCTAGON = [
    (-0.38, 0.1),
    (-0.91, 0.24),
    (-0.91, 0.46),
    (-0.38, 0.55),
    (0.38, 0.55),
    (0.91, 0.46),
    (0.91, 0.24),
    (0.38, 0.1),
]
"""The octagon's (pan, ambience), `control.ROLES["octagon"]` around the ring."""


@pytest.mark.parametrize("n", [3, 8])
def test_the_bank_is_todays_filter_for_filter(n):
    old = decorrelate.banco_decorrelador(n)
    new = bank_mod.bank(n).filters
    assert len(new) == n
    for a, b in zip(old, new, strict=True):
        assert np.array_equal(a, b)


def test_the_cached_filters_cannot_be_written():
    """Every engine with the same knobs shares one bank: none may change it for the others."""
    h = bank_mod.bank(4).filters[0]
    with pytest.raises(ValueError, match="read-only"):
        h[0] = 1.0


def test_eight_outputs_stay_flat_to_half_a_db_per_third():
    fr = np.fft.rfftfreq(16384, 1 / 48000)
    thirds = 1000 * 2 ** (np.arange(-13, 13) / 3)
    thirds = thirds[(thirds >= 50) & (thirds <= 16000)]
    worst = 0.0
    for h in bank_mod.bank(8).filters:
        power = np.abs(np.fft.rfft(h, 16384)) ** 2
        mean = 10 * np.log10(power[(fr > 50) & (fr < 16000)].mean())
        for c in thirds:
            band = (fr >= c / 2 ** (1 / 6)) & (fr < c * 2 ** (1 / 6))
            worst = max(worst, abs(10 * np.log10(power[band].mean()) - mean))
    # Measured 0.17 dB on 2026-10-02 (the same filters as experiment 16's "actual forzado a 8").
    assert worst < 0.5


def test_the_separation_above_500_hz_degrades_slowly_with_the_count():
    """Seed 0, measured 2026-10-02: 0.464 with 3 filters, 0.515 with 8. Experiment 16's criterion
    is < 0.6; with ±1 ms allowed both stay under it."""
    three, eight = bank_mod.bank(3).worst_above_500, bank_mod.bank(8).worst_above_500
    assert three == pytest.approx(0.464, abs=0.005)
    assert eight == pytest.approx(0.515, abs=0.005)
    assert eight < 0.6


def test_the_zero_lag_correlation_undercounts_what_a_small_delay_reveals():
    """Why `pairs` maximises over ±1 ms: at zero lag a pair can look apart that is nearly the
    same signal shifted. Seed 0 with 8, measured 2026-10-02: the lag-robust number is above the
    zero-lag one for every pair, by up to 0.44 (worst pair 0.345 at zero lag, 0.515 within
    ±1 ms)."""
    filters = bank_mod.bank(8).filters
    lagged = bank_mod.pair_correlations(filters)
    gaps = [lagged[i, j] - _zero_lag_above_500(filters[i], filters[j]) for i in range(8) for j in range(i + 1, 8)]
    assert min(gaps) > 0
    assert max(gaps) > 0.3


def _zero_lag_above_500(a, b):
    f = np.fft.rfftfreq(16384, 1 / 48000)
    w = np.where(f >= 500, 1 / np.maximum(f, 1), 0.0)
    ha, hb = np.fft.rfft(a, 16384), np.fft.rfft(b, 16384)
    return abs(np.sum(w * np.real(ha * np.conj(hb)))) / np.sqrt(np.sum(w * abs(ha) ** 2) * np.sum(w * abs(hb) ** 2))


@pytest.mark.parametrize("n", [2, 8])
def test_below_2_khz_no_fixed_bank_separates_with_any_count(n):
    """The fact behind §9: allowing ±0.5 ms, the 500 Hz octave is ≥ 0.9 coherent for some pair
    already with 2 filters (0.92-0.94 measured), not only with 7-8. So a bank chosen by its
    zero-lag worst pair per octave (experiment 16's proposal 5) only moves a delay."""
    filters = bank_mod.bank(n).filters
    f = np.fft.rfftfreq(16384, 1 / 48000)
    w = np.where((f >= 500 / np.sqrt(2)) & (f < 500 * np.sqrt(2)), 1 / np.maximum(f, 1), 0.0)
    h = [np.fft.rfft(x, 16384) for x in filters]
    lag = 24  # 0.5 ms
    worst = 0.0
    for i in range(n):
        for j in range(i + 1, n):
            x = np.fft.irfft(w * h[i] * np.conj(h[j]), 16384)
            norm = np.sqrt(np.sum(w * abs(h[i]) ** 2) * np.sum(w * abs(h[j]) ** 2)) * 2 / 16384
            worst = max(worst, float(np.abs(np.concatenate([x[-lag:], x[: lag + 1]])).max() / norm))
    assert worst > 0.9


# -- the assignment ---------------------------------------------------------------------------


def test_mix_similarity_is_one_for_equal_mixes_and_lower_for_different_ones():
    s = bank_mod.mix_similarity([(-0.7, 0.15), (-0.7, 0.15), (0.7, 0.15), (0.0, 1.0)])
    assert s[0, 1] == pytest.approx(1.0)
    assert np.allclose(np.diag(s), 1.0)
    assert s[0, 2] < s[0, 1]
    # Only ambience against no ambience: nothing in common.
    assert bank_mod.mix_similarity([(0.0, 0.0), (0.0, 1.0)])[0, 1] == pytest.approx(0.0)


def test_assign_is_a_permutation_that_does_not_worsen_the_model():
    b = bank_mod.bank(8)
    order = bank_mod.assign(b, OCTAGON)
    assert sorted(order) == list(range(8))
    before = bank_mod.worst_feeds(b, OCTAGON, list(range(8)))
    after = bank_mod.worst_feeds(b, OCTAGON, order)
    # Measured 2026-10-02, seed 0: 0.376 in the installation's order, 0.357 assigned.
    assert before == pytest.approx(0.376, abs=0.005)
    assert after == pytest.approx(0.357, abs=0.005)


def test_the_two_alike_mixes_get_the_least_alike_pair_of_filters():
    b = bank_mod.bank(4)
    mixes = [(0.0, 0.1), (0.0, 0.1), (-1.0, 0.0), (1.0, 0.6)]
    order = bank_mod.assign(b, mixes)
    pair = b.pairs[order[0], order[1]]
    assert pair == pytest.approx(min(b.pairs[i, j] for i in range(4) for j in range(i + 1, 4)))


def test_with_nothing_to_gain_the_installation_order_stays():
    """Only direct against only ambience: nothing in common, every permutation scores the same,
    and the first (the installation's order) is kept."""
    assert bank_mod.assign(bank_mod.bank(2), [(0.0, 0.0), (0.0, 1.0)]) == [0, 1]


def test_more_than_eight_speakers_go_by_swaps_and_do_not_worsen():
    mixes = [*OCTAGON, (0.0, 0.3), (0.0, 0.32)]
    b = bank_mod.bank(10)
    order = bank_mod.assign(b, mixes)
    assert sorted(order) == list(range(10))
    assert bank_mod.worst_feeds(b, mixes, order) <= bank_mod.worst_feeds(b, mixes, list(range(10)))


def test_assign_refuses_a_count_that_is_not_the_banks():
    with pytest.raises(ValueError, match="mixes"):
        bank_mod.assign(bank_mod.bank(3), OCTAGON)


# -- the notice -------------------------------------------------------------------------------


def test_the_notice_appears_only_past_the_fixed_limit_and_quotes_the_numbers():
    assert bank_mod.notice_for(decorrelate.MAXIMO_FIJOS, 256, 0, 2.5, 1.5) is None
    text = bank_mod.notice_for(8, 256, 0, 2.5, 1.5)
    assert text.startswith("con 8 parlantes la separación es menor")
    assert "0,52" in text
    assert "(con 3, 0,46)" in text
