"""The true-peak limiter in Rust (`aurasync_engine.TruePeakLimiter`) against numpy (spec rust-engine, Task 9).

`limiter.TruePeakLimiter` keeps the design (the look-ahead, attack and hold lengths and the
interpolation kernels) and owns one Rust object when the engine is Rust. This file checks, within
1e-9 absolute, that the two engines play the same samples and report the same metrics
(`gain`, `max_reduction_db`, `active_fraction`, after every block) on the inputs of
`test_limiter.py`, random blocks of odd sizes, a sweep of the design values, a live `configure`,
silence after a peak and an engine switch mid-stream; and that a Rust failure is silence until the
cut's bottom and then numpy: from a limiter at rest when the failure tore the Rust object's state,
from numpy's own state when it came while loading that state (`set_state`; user, 2026-10-09). Every
comparison asserts the signal is not all zeros.
"""

from __future__ import annotations

import aurasync_engine  # fails, does not skip, when the extension is not built
import numpy as np
import pytest

from aurasync.dsp import backend, limiter
from tests import test_limiter

TOLERANCE = 1e-9
SR = 48000


@pytest.fixture(autouse=True)
def _clean_backend():
    backend.reset()
    yield
    backend.reset()


def _run(lim, x, sizes):
    """`x` through `lim` in blocks of `sizes` (cycled): the output and each block's metrics."""
    out, metrics, at, k = [], [], 0, 0
    while at < len(x):
        n = sizes[k % len(sizes)]
        out.append(lim.process(x[at : at + n]))
        metrics.append((lim.gain, lim.max_reduction_db, lim.active_fraction))
        at, k = at + n, k + 1
    if backend.failure() is None:
        assert (lim._rust is not None) == backend.rust_active()  # noqa: SLF001 - Rust really ran
    return np.concatenate(out), np.array(metrics)


def _on(engine, script):
    backend.reset()
    backend.use(engine)
    result = script()
    assert backend.failure() is None
    return result


def _same(got, want):
    (y, m), (y0, m0) = got, want
    assert np.any(y0)
    assert len(y) == len(y0)
    diff = float(np.max(np.abs(y - y0)))
    assert diff <= TOLERANCE, f"max |diff| = {diff:.3g}"
    worst = float(np.max(np.abs(m - m0)))
    assert worst <= TOLERANCE, f"metrics: max |diff| = {worst:.3g}"


def _both(script):
    want = _on(backend.NUMPY, script)
    got = _on(backend.RUST, script)
    _same(got, want)
    return want


def _loud_sine(seconds=2.0, over_db=6.0, f0=60.0):
    t = np.arange(int(seconds * SR)) / SR
    return 10 ** ((test_limiter.CEILING_DB + over_db) / 20) * np.sin(2 * np.pi * f0 * t)


def test_capabilities_carry_the_limiter_constants():
    """The limiter's two own constants are numpy's, so a build with others is refused at load."""
    assert aurasync_engine.capabilities()["limiter"] == {
        "version": 1,
        "margin_db": limiter.MARGIN_DB,
        "near_ceiling": limiter.NEAR_CEILING,
    }


# -- ownership --------------------------------------------------------------------------------


def test_the_limiter_owns_a_rust_object_after_one_block_only_with_rust():
    backend.use(backend.RUST)
    lim = limiter.TruePeakLimiter(SR)
    lim.process(np.zeros(16))
    assert lim._rust is not None  # noqa: SLF001
    backend.reset()
    backend.use(backend.NUMPY)
    lim = limiter.TruePeakLimiter(SR)
    lim.process(np.zeros(16))
    assert lim._rust is None  # noqa: SLF001


def test_peak_limiter_stays_numpy():
    backend.use(backend.RUST)
    lim = limiter.PeakLimiter(SR)
    lim.process(2.0 * np.ones(64))
    assert not hasattr(lim, "_rust")


# -- the inputs of test_limiter.py ------------------------------------------------------------


def test_a_60_hz_sine_6_db_over_matches_numpy():
    x = _loud_sine(4.0)
    want = _both(lambda: _run(limiter.TruePeakLimiter(SR, ceiling_db=test_limiter.CEILING_DB), x, [4096]))
    assert want[1][:, 1].max() > 5.0  # it limited


@pytest.mark.parametrize("index", range(7))
def test_the_inter_sample_peak_corpus_matches_numpy(index):
    x = np.concatenate([test_limiter.corpus()[index], np.zeros(4096)])
    assert test_limiter.loudness.true_peak_dbtp(x, SR) > test_limiter.CEILING_DB + 0.5
    _both(lambda: _run(limiter.TruePeakLimiter(SR, ceiling_db=test_limiter.CEILING_DB), x, [4096]))


def test_random_blocks_of_odd_sizes_match_numpy():
    rng = np.random.default_rng(17)
    x = test_limiter.corpus()[2][: SR // 2]
    sizes = [int(s) for s in rng.integers(1, 3000, 40) | 1]
    _both(lambda: _run(limiter.TruePeakLimiter(SR), x, sizes))


@pytest.mark.parametrize(
    "sizes",
    [[4096, 1024], [100, 7, 1, 143, 4096]],
    ids=["alternating-4096-1024", "shorter-than-the-look-ahead"],
)
def test_the_plan_s_block_patterns_match_numpy(sizes):
    """Alternating 4096/1024, and blocks shorter than the look-ahead (144 samples at 48 kHz)."""
    x = np.concatenate([_loud_sine(0.5), test_limiter.corpus()[0][: SR // 4], np.zeros(SR // 4)])
    assert min(sizes) < limiter.TruePeakLimiter(SR).latency or 1024 in sizes
    _both(lambda: _run(limiter.TruePeakLimiter(SR), x, sizes))


@pytest.mark.parametrize("lookahead_ms", [1.0, 3.0, 5.0])
@pytest.mark.parametrize("release_ms", [50.0, 250.0, 1000.0])
@pytest.mark.parametrize("hold_ms", [0.0, 15.0])
def test_the_design_sweep_matches_numpy(lookahead_ms, release_ms, hold_ms):
    x = np.concatenate([test_limiter.corpus()[5][: SR // 2], _loud_sine(0.25), np.zeros(SR // 4)])

    def script():
        lim = limiter.TruePeakLimiter(SR, lookahead_ms=lookahead_ms, release_ms=release_ms, hold_ms=hold_ms)
        return _run(lim, x, [4096, 1000, 333])

    _both(script)


def test_a_live_configure_between_blocks_matches_numpy():
    x = np.concatenate([_loud_sine(1.0, over_db=3.0), test_limiter.corpus()[4][: SR // 2]])

    def script():
        lim = limiter.TruePeakLimiter(SR)
        a = _run(lim, x[:20000], [4096])
        lim.configure(-6.0, 50.0)  # positionally, as the motor calls it
        b = _run(lim, x[20000:40000], [4096])
        lim.configure(release_ms=1000.0)
        c = _run(lim, x[40000:60000], [4096])
        lim.configure(ceiling_db=0.0)
        d = _run(lim, x[60000:], [4096])
        assert lim.ceiling == 1.0
        return np.concatenate([a[0], b[0], c[0], d[0]]), np.concatenate([a[1], b[1], c[1], d[1]])

    _both(script)


def test_silence_after_a_peak_matches_numpy():
    x = np.zeros(3 * SR)
    x[5000] = 3.0
    x[5001:9000] = 0.05 * np.sin(np.arange(3999) / 7)

    def script():
        return _run(limiter.TruePeakLimiter(SR), x, [4096])

    want = _both(script)
    # The release is exponential: the gain is still climbing back at the end, never exactly 1.
    assert 0.999 < want[1][-1, 0] < 1.0


def test_a_quiet_block_takes_the_shortcut_and_reports_zero_in_both_engines():
    x = 0.5 * np.sin(np.arange(3 * 4096) / 10)
    want = _both(lambda: _run(limiter.TruePeakLimiter(SR), x, [4096]))
    assert np.array_equal(want[1], [[1.0, 0.0, 0.0]] * 3)


def test_a_nan_block_gives_what_numpy_gives():
    x = _loud_sine(0.5)
    x[3000] = np.nan

    def script():
        lim = limiter.TruePeakLimiter(SR)
        with np.errstate(invalid="ignore"):
            return _run(lim, x, [4096])

    want = _on(backend.NUMPY, script)
    got = _on(backend.RUST, script)
    np.testing.assert_array_equal(np.isnan(got[0]), np.isnan(want[0]))
    np.testing.assert_allclose(got[0], want[0], rtol=0, atol=TOLERANCE, equal_nan=True)
    np.testing.assert_allclose(got[1], want[1], rtol=0, atol=TOLERANCE, equal_nan=True)


# -- the engine switch and failures -----------------------------------------------------------


@pytest.mark.parametrize(("first", "then"), [(backend.NUMPY, backend.RUST), (backend.RUST, backend.NUMPY)])
def test_switching_engine_between_blocks_changes_no_sample(first, then):
    x = np.concatenate([_loud_sine(1.0), test_limiter.corpus()[6][: SR // 2]])

    def straight():
        return _run(limiter.TruePeakLimiter(SR), x, [4096])

    def switched():
        lim = limiter.TruePeakLimiter(SR)
        a = _run(lim, x[: 6 * 4096], [4096])
        assert lim.gain < 1.0  # the switch carries a reduction
        backend.use(then)
        b = _run(lim, x[6 * 4096 :], [4096])
        assert (lim._rust is not None) == (then == backend.RUST)  # noqa: SLF001
        return np.concatenate([a[0], b[0]]), np.concatenate([a[1], b[1]])

    want = _on(first, straight)
    got = _on(first, switched)
    assert backend.active() == then
    _same(got, want)


def test_a_refused_configure_is_a_failure_and_numpy_goes_on():
    """A knob Rust refuses (numpy takes it) disables Rust, as any failure building or configuring."""
    backend.use(backend.RUST)
    lim = limiter.TruePeakLimiter(SR)
    lim.process(np.zeros(64))
    lim.configure(ceiling_db=float("inf"))
    assert "ValueError" in backend.failure()
    assert backend.active() == backend.NUMPY


def _panic_in_process(lim, _monkeypatch):
    lim._rust._panic_next()  # noqa: SLF001


def _panic_in_configure(lim, _monkeypatch):
    lim._rust._panic_next()  # noqa: SLF001
    lim.configure(release_ms=250.0)  # the value it has: numpy's knobs are the same either way


def _panic_in_set_state(_lim, monkeypatch):
    """Back to numpy, then to Rust again with a build whose `set_state` (loading numpy's state
    into the new Rust object, `_build_rust`) panics."""
    backend.use(backend.NUMPY)
    real = backend.module().TruePeakLimiter

    def armed(*args):
        rust = real(*args)
        rust._panic_next()  # noqa: SLF001
        return rust

    monkeypatch.setattr(backend.module(), "TruePeakLimiter", armed)
    backend.use(backend.RUST)


@pytest.mark.parametrize(
    ("plant", "torn"),
    [(_panic_in_process, True), (_panic_in_configure, True), (_panic_in_set_state, False)],
    ids=["process", "configure", "set_state"],
)
def test_a_planted_panic_is_silence_until_the_cut_then_numpy(monkeypatch, plant, torn):
    """A panic in any Rust call of the limiter: the failure is reported once, the blocks until the
    cut's bottom are silence, and numpy plays from the cut. When the panic tore a Rust object that
    held the state (`process`, `configure`), numpy starts from a limiter at rest; when it came while
    loading numpy's state into a new one (`set_state`), numpy's own state was never handed over and
    numpy goes on from it."""
    heard: list[str] = []
    backend.use(backend.RUST)
    backend.on_failure = heard.append
    x = _loud_sine(1.0)
    lim = limiter.TruePeakLimiter(SR)
    _run(lim, x[: 2 * 4096], [4096])
    assert lim.gain < 1.0
    plant(lim, monkeypatch)
    numpy_state = None if torn else {key: np.copy(value) for key, value in lim._numpy_state().items()}  # noqa: SLF001
    assert not np.any(lim.process(x[2 * 4096 : 3 * 4096]))
    assert lim.max_reduction_db == 0.0
    assert lim.active_fraction == 0.0
    assert "panicked" in backend.failure()
    assert len(heard) == 1
    # Until the cut's bottom: silence.
    assert not np.any(lim.process(x[3 * 4096 : 4 * 4096]))
    # The cut's bottom: numpy.
    backend.use(backend.NUMPY)
    assert lim._rust is None  # noqa: SLF001
    reference = limiter.TruePeakLimiter(SR)
    if numpy_state is not None:
        reference._load_state(numpy_state)  # noqa: SLF001
    tail = x[4 * 4096 :]
    got, got_metrics = _run(lim, tail, [4096])
    want, want_metrics = _run(reference, tail, [4096])
    assert np.any(want)
    assert np.array_equal(got, want)
    assert np.array_equal(got_metrics, want_metrics)


def test_without_a_listener_a_panic_is_one_silent_block_then_numpy_at_once():
    backend.use(backend.RUST)
    x = _loud_sine(0.5)
    lim = limiter.TruePeakLimiter(SR)
    _run(lim, x[:4096], [4096])
    lim._rust._panic_next()  # noqa: SLF001
    assert not np.any(lim.process(x[4096 : 2 * 4096]))
    fresh = limiter.TruePeakLimiter(SR)
    nxt = x[2 * 4096 : 3 * 4096]
    got = lim.process(nxt)
    assert lim._rust is None  # noqa: SLF001
    assert np.array_equal(got, fresh.process(nxt))


def test_the_rust_object_refuses_a_state_of_another_size_and_positional_knobs():
    backend.use(backend.RUST)
    rust = limiter.TruePeakLimiter(SR)._build_rust()  # noqa: SLF001
    state = rust.state()
    assert set(state) == {"x", "gain"}
    with pytest.raises(ValueError, match="x: 10 values where"):
        rust.set_state({"x": np.zeros(10), "gain": 1.0})
    assert np.array_equal(rust.state()["x"], state["x"])
    with pytest.raises(TypeError):
        rust.configure(-1.0)
