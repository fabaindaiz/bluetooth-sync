"""The Rust read (`aurasync_engine`, engine/crates/aurasync-engine) against the numpy oracle.

The extension must be built into the test environment: this file fails, it does not skip, when
`aurasync_engine` is missing, so a broken build cannot pass the suite unnoticed.

Golden: within 1e-9 absolute of `interpolation.read` on full-scale signals (d-7c8794-36dde5).
"""

from __future__ import annotations

import aurasync_engine
import numpy as np
import pytest

from aurasync.dsp import interpolation

HALF = interpolation.HALF
FEW = interpolation._FEW  # noqa: SLF001
STEPS = interpolation._STEPS  # noqa: SLF001
TOLERANCE = 1e-9


@pytest.fixture
def data() -> np.ndarray:
    """A full-scale signal, long enough for the largest block plus a ramp."""
    return np.random.default_rng(7).uniform(-1.0, 1.0, 12_000)


def assert_golden(data: np.ndarray, position: np.ndarray) -> np.ndarray:
    got = aurasync_engine.read(data, position)
    expected = interpolation.read(data, position)
    assert got.dtype == np.float64
    assert got.shape == position.shape
    assert got.flags.c_contiguous
    assert np.max(np.abs(got - expected), initial=0.0) <= TOLERANCE
    return got


def distinct(position: np.ndarray) -> int:
    return len(np.unique(position - np.floor(position)))


def still(n: int) -> np.ndarray:
    return np.arange(n) + 40 + 0.37


def moving(n: int) -> np.ndarray:
    # A recalibration ramp: the fraction sweeps more than one sample inside the block.
    return np.arange(n) + 40 + 0.37 + np.linspace(0.0, 1.7, n)


def test_a_still_delay_matches_numpy(data):
    position = still(4096)
    # `k + 0.37` rounds to a few distinct fractions, as a real still delay does.
    assert distinct(position) <= FEW
    assert_golden(data, position)


def test_a_few_fractions_take_the_formula_path_and_match(data):
    # Up to 64 distinct fractions: numpy computes each one's weights with the formula.
    # Dyadic fractions survive `k + frac` exactly, so the count is exact.
    position = np.arange(4096) + 40 + np.tile((np.arange(64) * 64 + 13) / 8192, 64)
    assert distinct(position) == 64
    assert_golden(data, position)


def test_a_moving_delay_matches_numpy(data):
    position = moving(4096)
    assert distinct(position) > FEW
    assert_golden(data, position)


def test_random_positions_match_numpy(data):
    position = np.sort(np.random.default_rng(5).uniform(40, 11_000, 4096))
    assert_golden(data, position)


def test_integer_positions_copy_the_samples(data):
    position = np.arange(100, 4196).astype(float)
    got = assert_golden(data, position)
    # Bit for bit in Rust (numpy's sinc leaves ~1e-17 at nonzero integers).
    assert np.array_equal(got, data[100:4196])
    # And through the table path: integer positions among many fractions.
    mixed = np.arange(100, 4196).astype(float)
    mixed[1::2] += np.random.default_rng(9).uniform(0.0, 1.0, mixed[1::2].size)
    assert distinct(mixed) > FEW
    got = assert_golden(data, mixed)
    assert np.array_equal(got[::2], data[100:4196:2])


# 8193: past the first reader's 8192 positions, so the extension builds a larger one.
@pytest.mark.parametrize("n", [1, 63, 64, 65, 4095, 4096, 4097, 8193])
@pytest.mark.parametrize("shape", [still, moving])
def test_odd_block_sizes_match_numpy(data, n, shape):
    assert_golden(data, shape(n))


@pytest.mark.parametrize("fractions", [64, 65])
def test_the_strategy_boundary_matches_numpy(data, fractions):
    # 64 distinct fractions take the formula, 65 the table: both within 1e-9 of numpy.
    # Dyadic fractions survive `k + frac` exactly, so the count is exact; the 13/8192 keeps them
    # off the table's 1/2048 grid, where the Lagrange coefficients would be (0, 1, 0, 0) and a
    # fault in them would not show.
    position = np.arange(4096) + 40 + np.resize((np.arange(fractions) * 64 + 13) / 8192, 4096)
    assert distinct(position) == fractions
    assert_golden(data, position)


def test_rust_read_accepts_strided_and_rejects_float32(data):
    # A strided view (every other sample, and a reversed position) is copied, never misread.
    strided_data = data[::2]
    strided_position = still(2000)[::-1]
    assert not strided_data.flags.c_contiguous
    assert not strided_position.flags.c_contiguous
    got = aurasync_engine.read(strided_data, strided_position)
    expected = interpolation.read(np.ascontiguousarray(strided_data), np.ascontiguousarray(strided_position))
    assert np.max(np.abs(got - expected)) <= TOLERANCE
    assert got.flags.c_contiguous
    # A moving read over a strided view, too (the table path).
    position = moving(2000)[::3]
    got = aurasync_engine.read(strided_data, position)
    assert np.max(np.abs(got - interpolation.read(strided_data, position))) <= TOLERANCE
    # float32, in either argument, is refused rather than converted silently.
    with pytest.raises(TypeError, match=r"data must be a 1-D float64.*float32"):
        aurasync_engine.read(data.astype(np.float32), still(64))
    with pytest.raises(TypeError, match=r"position must be a 1-D float64.*float32"):
        aurasync_engine.read(data, still(64).astype(np.float32))
    # A float32 view of float64 memory (same bytes, other dtype) as well.
    with pytest.raises(TypeError, match="float32"):
        aurasync_engine.read(data.view(np.float32), still(64))
    # Nor float64 in the other byte order, integer positions, a list or a 2-D array.
    with pytest.raises(TypeError, match=">f8"):
        aurasync_engine.read(data.astype(">f8"), still(64))
    with pytest.raises(TypeError, match="int64"):
        aurasync_engine.read(data, np.arange(40, 104))
    with pytest.raises(TypeError, match="not list"):
        aurasync_engine.read(list(data), still(64))
    with pytest.raises(TypeError, match="2 dimension"):
        aurasync_engine.read(data.reshape(2, -1), still(64))


def test_rust_read_boundaries_and_out_of_range(data):
    lowest, highest = HALF - 1, len(data) - 1 - HALF
    # The exact boundaries, integer and fractional: identical to numpy.
    for position in (
        np.array([lowest, highest], dtype=float),
        np.array([lowest + 0.5, highest + 0.999_999]),
        np.linspace(lowest, highest + 0.5, 4096),
    ):
        assert_golden(data, position)
    # One step outside either end, or not a number: ValueError, never a panic or garbage.
    for bad in (
        np.nextafter(float(lowest), -np.inf),
        float(highest + 1),
        np.nan,
        np.inf,
        -np.inf,
    ):
        position = still(16)
        position[7] = bad
        with pytest.raises(ValueError, match=r"position 7\b"):
            aurasync_engine.read(data, position)
    # Data too short for any read.
    with pytest.raises(ValueError, match=r"position 0\b"):
        aurasync_engine.read(data[: 2 * HALF - 1], np.array([HALF - 1.0]))
    # No positions: an empty result.
    got = aurasync_engine.read(data, np.array([], dtype=float))
    assert got.dtype == np.float64
    assert got.shape == (0,)


def test_reading_does_not_modify_the_inputs(data):
    position = moving(4096)
    data_before, position_before = data.copy(), position.copy()
    aurasync_engine.read(data, position)
    assert np.array_equal(data, data_before)
    assert np.array_equal(position, position_before)


def test_capabilities_match_python_constants():
    """The read's constants; the spatial upmix's and the ambience extractor's are checked in
    tests/test_spatial_rust.py and tests/test_ambience_rust.py."""
    assert aurasync_engine.capabilities()["interpolation"] == {
        "half": interpolation.HALF,
        "beta": interpolation.BETA,
        "steps": STEPS,
    }
    assert set(aurasync_engine.capabilities()) == {"interpolation", "spatial", "ambience", "fir", "virtual_bass"}


def test_a_rust_panic_is_a_runtime_error_and_reading_goes_on(data):
    """The test build carries the `test-panic` feature (host/pyproject.toml): `_panic` panics
    inside the read's guard, with the reader locked."""
    with pytest.raises(RuntimeError, match="planted panic") as caught:
        aurasync_engine._panic()  # noqa: SLF001
    # An `Exception`, so `service._step` catches it; PyO3's PanicException is a BaseException.
    assert isinstance(caught.value, Exception)
    assert type(caught.value).__name__ != "PanicException"
    # The panic left the reader's lock poisoned; the next read recovers.
    assert_golden(data, moving(4096))


def test_a_panic_outside_the_read_is_a_runtime_error_too():
    """The guard covers each exported function's whole body (argument checks and building the
    result too), not only the read: `_panic_outside_the_read` panics before touching anything."""
    with pytest.raises(RuntimeError, match="planted panic before the arguments"):
        aurasync_engine._panic_outside_the_read(np.zeros(3))  # noqa: SLF001
