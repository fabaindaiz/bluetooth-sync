"""The band-limited read: same output as the direct formula, at a fraction of the cost.

The accuracy tests run once per engine (`engine` in conftest.py): numpy, and the Rust port when
the extension is built."""

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


def test_a_still_delay_reads_exactly_what_the_formula_gives(data, engine):
    position = np.arange(4096) + 40 + 0.37
    got = interpolation.read(data, position)
    if engine.name == "numpy":
        assert np.array_equal(got, reference(data, position))
    else:
        # The same formula, summed in another order: rounding apart.
        assert np.max(np.abs(got - reference(data, position))) < 1e-12
        assert engine.rust_calls == [4096]


def test_a_moving_delay_stays_within_1e_10_of_the_formula(data, engine):
    # A recalibration ramp: the fraction sweeps more than one sample inside the block.
    position = np.arange(4096) + 40 + 0.37 + np.linspace(0.0, 1.7, 4096)
    got = interpolation.read(data, position)
    assert np.max(np.abs(got - reference(data, position))) < 1e-10
    assert len(engine.rust_calls) == (engine.name == "rust")


def test_random_positions_stay_within_1e_10(data, engine):
    position = np.sort(np.random.default_rng(5).uniform(40, 8000, 4096))
    assert np.max(np.abs(interpolation.read(data, position) - reference(data, position))) < 1e-10
    assert len(engine.rust_calls) == (engine.name == "rust")


def test_integer_positions_are_the_samples_themselves(data, engine):
    position = np.arange(100, 4196).astype(float)
    assert np.allclose(interpolation.read(data, position), data[100:4196], atol=1e-12)
    assert len(engine.rust_calls) == (engine.name == "rust")


def test_it_costs_far_less_than_the_formula(data):
    """The direct formula cost 7.3 ms per block and speaker on the Mac (2026-10-02): with
    three speakers, a quarter of the 85 ms block, and the engine missed its deadline under
    load (experimentos/12). Idle on HP-O16 the still case is ~10x cheaper and a ramp ~3x
    (2026-10-07); under the full parallel suite the still ratio fell to 6.3-7.6x and the
    old 8x threshold failed at random (roadmap i-7c8794-a439a5). The two functions are now
    timed interleaved, so both see the same load, and the test asks for 5x still and 1.5x on
    a ramp: far from the formula's 1x. The exact figure belongs to probes/18, not to a test.
    1e-10 is -200 dB: four orders under the golden's 1e-9 and far under SBC's 16 bits."""

    def ratio(position, rounds=15):
        # Interleaved and the fastest of each: other processes only ever add time, and
        # alternating makes the two functions share whatever load there is.
        interpolation.read(data, position)
        reference(data, position)
        fast, slow = [], []
        for _ in range(rounds):
            start = time.perf_counter()
            interpolation.read(data, position)
            fast.append(time.perf_counter() - start)
            start = time.perf_counter()
            reference(data, position)
            slow.append(time.perf_counter() - start)
        return min(slow) / min(fast)

    still = np.arange(4096) + 40 + 0.37
    ramp = still + np.linspace(0.0, 1.7, 4096)
    assert ratio(still) > 5
    assert ratio(ramp) > 1.5
