"""The band-limited read: same output as the direct formula, at a fraction of the cost."""

from __future__ import annotations

import time

import numpy as np
import pytest

from aurasync.dsp import interpolation


def reference(data: np.ndarray, position: np.ndarray) -> np.ndarray:
    """The formula as it was written first (2026-10-01): one Kaiser window per sample."""
    h, beta = interpolation.HALF, interpolation.BETA
    offsets = np.arange(-h + 1, h + 1)
    i0 = np.floor(position).astype(int)
    t = (position - i0)[:, None] - offsets[None, :]
    window = np.i0(beta * np.sqrt(np.clip(1 - (t / h) ** 2, 0.0, 1.0))) / np.i0(beta)
    weights = np.sinc(t) * window
    weights /= weights.sum(axis=1, keepdims=True)
    return np.sum(data[i0[:, None] + offsets[None, :]] * weights, axis=1)


@pytest.fixture
def data() -> np.ndarray:
    return np.random.default_rng(3).standard_normal(8192)


def test_a_still_delay_reads_exactly_what_the_formula_gives(data):
    position = np.arange(4096) + 40 + 0.37
    assert np.array_equal(interpolation.read(data, position), reference(data, position))


def test_a_moving_delay_stays_within_1e_10_of_the_formula(data):
    # A recalibration ramp: the fraction sweeps more than one sample inside the block.
    position = np.arange(4096) + 40 + 0.37 + np.linspace(0.0, 1.7, 4096)
    got = interpolation.read(data, position)
    assert np.max(np.abs(got - reference(data, position))) < 1e-10


def test_random_positions_stay_within_1e_10(data):
    position = np.sort(np.random.default_rng(5).uniform(40, 8000, 4096))
    assert np.max(np.abs(interpolation.read(data, position) - reference(data, position))) < 1e-10


def test_integer_positions_are_the_samples_themselves(data):
    position = np.arange(100, 4196).astype(float)
    assert np.allclose(interpolation.read(data, position), data[100:4196], atol=1e-12)


def test_it_costs_far_less_than_the_formula(data):
    """The direct formula cost 7.3 ms per block and speaker on the Mac (2026-10-02): with
    three speakers, a quarter of the 85 ms block, and the engine missed its deadline under
    load (experimentos/12). The still case must be at least 8x cheaper, a ramp at least 2x.
    1e-10 is -200 dB: four orders under the golden's 1e-9 and far under SBC's 16 bits."""

    def cost(f, position, n=7):
        # The fastest of several runs: other processes only ever add time.
        f(data, position)
        times = []
        for _ in range(n):
            start = time.perf_counter()
            f(data, position)
            times.append(time.perf_counter() - start)
        return min(times)

    still = np.arange(4096) + 40 + 0.37
    ramp = still + np.linspace(0.0, 1.7, 4096)
    assert cost(interpolation.read, still) * 8 < cost(reference, still)
    assert cost(interpolation.read, ramp) * 2 < cost(reference, ramp)
