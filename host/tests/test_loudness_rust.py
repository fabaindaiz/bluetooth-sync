"""The loudness meter's per-block work in Rust (`aurasync_engine.LoudnessMeter`) against numpy (spec rust-engine, Task 14).

`loudness.LoudnessMeter` keeps the design (the step, `_k_power`, the weights and the kernels) and
the readings, and owns one Rust object when the engine is Rust, which measures each block: its true
peak and the K-weighted energies of the 100 ms steps it completes. This file checks, within 1e-9
absolute, that the two engines give the same readings after every block (momentary, short-term,
integrated, true peak, the last 3 s's true peak, PSR, the step energies and their count) on the
signals of `test_loudness.py`, full-scale noise, blocks of odd sizes, other weights and rates, and
an engine switch mid-stream; that the state moves exactly; and that a Rust failure leaves the meter
measuring in numpy, on the same step grid. Every comparison asserts the signal is not all zeros.
"""

from __future__ import annotations

import copy
import math

import aurasync_engine  # fails, does not skip, when the extension is not built
import numpy as np
import pytest

from aurasync import quality
from aurasync.dsp import backend, loudness
from tests import test_loudness

TOLERANCE = 1e-9
SR = 48000


@pytest.fixture(autouse=True)
def _clean_backend():
    backend.reset()
    yield
    backend.reset()


def _readings(meter: loudness.LoudnessMeter) -> list[float]:
    values = [
        meter.momentary,
        meter.short_term,
        meter.true_peak_dbtp,
        meter.true_peak_short_dbtp,
        meter.psr,
        float(meter.steps_total),
        *meter.last_steps(30),
    ]
    if meter.history:
        values.append(meter.integrated)
    return values


def _run(meter, x, sizes):
    """`x` through `meter` in blocks of `sizes` (cycled): the readings after every block."""
    out, at, k = [], 0, 0
    while at < len(x):
        n = sizes[k % len(sizes)]
        meter.push(x[at : at + n])
        out.append(_readings(meter))
        at, k = at + n, k + 1
    if backend.failure() is None:
        assert (meter._rust is not None) == backend.rust_active()  # noqa: SLF001 - Rust really ran
    return out


def _on(engine, script):
    backend.reset()
    backend.use(engine)
    result = script()
    assert backend.failure() is None
    return result


def _close(a: float, b: float) -> bool:
    if math.isnan(a) or math.isnan(b):
        return math.isnan(a) and math.isnan(b)
    if math.isinf(a) or math.isinf(b):
        return a == b
    return abs(a - b) <= TOLERANCE


def _same(got, want):
    assert len(got) == len(want)
    assert any(math.isfinite(row[0]) for row in want)  # something was measured
    worst = 0.0
    for g, w in zip(got, want, strict=True):
        assert len(g) == len(w)
        for a, b in zip(g, w, strict=True):
            assert _close(a, b), f"{a!r} != {b!r}"
            if math.isfinite(a) and math.isfinite(b):
                worst = max(worst, abs(a - b))
    return worst


def _both(script):
    want = _on(backend.NUMPY, script)
    got = _on(backend.RUST, script)
    _same(got, want)
    return want


def _noise(seconds: float, channels: int, scale: float = 1.0, seed: int = 3) -> np.ndarray:
    x = scale * np.random.default_rng(seed).uniform(-1, 1, (int(seconds * SR), channels))
    return x[:, 0] if channels == 1 else x


def _music(seconds: float, seed: int) -> np.ndarray:
    """`test_loudness.music_like` in stereo: two seeds, one per channel."""
    return np.column_stack([test_loudness.music_like(seconds, seed), test_loudness.music_like(seconds, seed + 100)])


def test_capabilities_carry_the_meter_constant():
    """The meter's one own constant is numpy's, so a build with another is refused at load."""
    assert aurasync_engine.capabilities()["loudness"] == {"version": 1, "near_peak": loudness.NEAR_PEAK}


# -- ownership --------------------------------------------------------------------------------


def test_the_meter_owns_a_rust_object_after_one_block_only_with_rust():
    backend.use(backend.RUST)
    meter = loudness.LoudnessMeter(SR, 1)
    assert meter._rust is None  # noqa: SLF001 - built on the first block, not before
    meter.push(np.zeros(16))
    assert meter._rust is not None  # noqa: SLF001
    backend.reset()
    backend.use(backend.NUMPY)
    meter = loudness.LoudnessMeter(SR, 1)
    meter.push(np.zeros(16))
    assert meter._rust is None  # noqa: SLF001


# -- the signals of test_loudness.py and more -------------------------------------------------


@pytest.mark.parametrize("history", [True, False], ids=["history", "fixed"])
@pytest.mark.parametrize(
    "signal",
    ["stereo-sine", "pink", "music-like", "full-scale-noise", "quiet-and-loud"],
)
def test_the_readings_match_numpy_after_every_block(signal, history):
    if signal == "stereo-sine":
        x = test_loudness.stereo(test_loudness.sine(-23.0, 5.0, 997.0), test_loudness.sine(-20.0, 5.0, 3000.0))
    elif signal == "pink":
        x = np.column_stack([test_loudness.pink(5 * SR, 1), test_loudness.pink(5 * SR, 2)])
    elif signal == "music-like":
        x = test_loudness.music_like(6.0, 4)
    elif signal == "full-scale-noise":
        x = _noise(4.0, 2)
    else:
        x = np.concatenate([_noise(1.5, 2, 0.001), _noise(2.0, 2, 0.9), np.zeros((SR, 2)), _noise(1.5, 2, 0.05)])
    channels = 1 if x.ndim == 1 else x.shape[1]
    _both(lambda: _run(loudness.LoudnessMeter(SR, channels, history=history), x, [4096]))


@pytest.mark.parametrize(
    "sizes",
    [[4800], [1, 7, 4799, 13], [100, 4096, 9600, 3], [20000]],
    ids=["one-step", "odd-and-tiny", "mixed", "several-steps-at-once"],
)
def test_block_sizes_match_numpy(sizes):
    x = _noise(3.0, 1, 0.3, seed=9)
    _both(lambda: _run(loudness.LoudnessMeter(SR, 1), x, sizes))


def test_random_blocks_of_odd_sizes_match_numpy():
    rng = np.random.default_rng(17)
    x = _music(4.0, 7)
    sizes = [int(s) for s in rng.integers(1, 6000, 60) | 1]
    _both(lambda: _run(loudness.LoudnessMeter(SR, 2), x, sizes))


def test_channel_weights_and_another_rate_match_numpy():
    x = _noise(3.0, 3, 0.2, seed=5)
    _both(lambda: _run(loudness.LoudnessMeter(44100, 3, [1.0, 1.41, 0.5]), x, [4096, 1000]))


def test_an_inter_sample_peak_matches_numpy():
    """A sine at fs/4 phased between samples: the true peak is 3 dB over the sample peak."""
    x = test_loudness.sine(-3.0, 1.0, SR / 4, 45.0)
    want = _both(lambda: _run(loudness.LoudnessMeter(SR, 1), x, [4096]))
    assert want[-1][2] > -3.01  # it found the peak between samples


def test_the_pure_functions_match_numpy():
    x = test_loudness.music_like(5.0, 2)

    def script():
        return [[loudness.integrated_lufs(x, SR), loudness.true_peak_dbtp(x, SR)]]

    _both(script)


def test_a_nan_block_gives_what_numpy_gives():
    x = _noise(2.0, 1, 0.2)
    x[5000] = np.nan

    def script():
        with np.errstate(invalid="ignore"):
            return _run(loudness.LoudnessMeter(SR, 1), x, [4096])

    want = _on(backend.NUMPY, script)
    got = _on(backend.RUST, script)
    assert any(math.isnan(v) for row in want for v in row)
    _same(got, want)


def test_a_block_with_the_wrong_channels_is_refused_as_in_numpy():
    for engine in backend.ENGINES:
        backend.reset()
        backend.use(engine)
        meter = loudness.LoudnessMeter(SR, 1)
        meter.push(np.zeros(100))
        with pytest.raises(ValueError):  # noqa: PT011 - numpy's own message differs
            meter.push(np.zeros((100, 2)))


def test_the_quality_meter_s_summary_matches_numpy():
    x = _music(5.0, 8)

    def script():
        meter = quality.QualityMeter(SR, ["a", "b", "c"])
        try:
            rows = []
            for at in range(0, len(x) - 4096, 4096):
                block = x[at : at + 4096]
                meter.push((block[:, 0], block[:, 1]), {"a": block[:, 0], "b": 0.5 * block[:, 1], "c": block[:, 0]})
                rows.append([meter.outputs_short_term, meter.input_momentary, meter.net_lu(10), meter.steps_total])
            return rows
        finally:
            meter.close()

    _both(script)


# -- the engine switch and the state ----------------------------------------------------------


@pytest.mark.parametrize(("first", "then"), [(backend.NUMPY, backend.RUST), (backend.RUST, backend.NUMPY)])
def test_switching_engine_between_blocks_changes_no_reading(first, then):
    x = _music(6.0, 3)

    def straight():
        return _run(loudness.LoudnessMeter(SR, 2), x, [4096, 1000])

    def switched():
        meter = loudness.LoudnessMeter(SR, 2)
        a = _run(meter, x[: 7 * 5096 + 4096], [4096, 1000])
        assert meter._pending.shape[1] > 0 or meter._rust is not None  # noqa: SLF001 - a step is half done
        backend.use(then)
        b = _run(meter, x[7 * 5096 + 4096 :], [1000, 4096])
        assert (meter._rust is not None) == (then == backend.RUST)  # noqa: SLF001
        return a + b

    want = _on(first, straight)
    got = _on(first, switched)
    assert backend.active() == then
    _same(got, want)


def test_the_state_round_trips_exactly():
    backend.use(backend.RUST)
    meter = loudness.LoudnessMeter(SR, 2)
    meter.push(_noise(0.3, 2))  # 14400 samples: three steps and nothing pending
    meter.push(_noise(0.05, 2, seed=8))  # 2400 pending
    state = meter._rust.state()  # noqa: SLF001
    assert state["context"].shape == (2, 2 * loudness.HALF_WIDTH)
    assert state["pending"].shape == (2, 2400)
    fresh = meter._build_rust()  # noqa: SLF001 - from numpy's (stale) state ...
    fresh.set_state(state)  # ... then Rust's
    again = fresh.state()
    for key in ("context", "pending"):
        assert np.array_equal(again[key], state[key])
    backend.use(backend.NUMPY)  # into numpy, exactly
    assert np.array_equal(meter._context, state["context"])  # noqa: SLF001
    assert np.array_equal(meter._pending, state["pending"])  # noqa: SLF001


def test_the_rust_object_refuses_a_state_of_another_size_and_positional_arguments():
    backend.use(backend.RUST)
    rust = loudness.LoudnessMeter(SR, 1)._build_rust()  # noqa: SLF001
    state = rust.state()
    with pytest.raises(ValueError, match="context"):
        rust.set_state({"context": np.zeros((1, 10)), "pending": np.zeros((1, 0))})
    with pytest.raises(ValueError, match="pending"):
        rust.set_state({"context": state["context"], "pending": np.zeros((1, 4800))})
    assert np.array_equal(rust.state()["context"], state["context"])
    with pytest.raises(TypeError):
        aurasync_engine.LoudnessMeter(np.zeros(72), 12, np.zeros(2401), np.ones(1), 4800)


# -- failures ---------------------------------------------------------------------------------


def test_a_panic_in_push_leaves_the_meter_measuring_in_numpy_on_the_same_grid():
    """The failing block is measured in numpy from a zero context and a zero pending step of the
    same length; the readings are numpy's again once that step has left the 3 s window."""
    heard: list[str] = []
    x = _music(8.0, 5)
    sizes = [4096]
    # Without history, as the engine thread's meters: the integrated loudness keeps the step the
    # failure zeroed for ever.
    straight = _on(backend.NUMPY, lambda: _run(loudness.LoudnessMeter(SR, 2, history=False), x, sizes))
    backend.reset()
    backend.use(backend.RUST)
    backend.on_failure = heard.append
    meter = loudness.LoudnessMeter(SR, 2, history=False)
    before = _run(meter, x[: 5 * 4096], sizes)
    meter._rust._panic_next()  # noqa: SLF001
    after = _run(meter, x[5 * 4096 :], sizes)
    assert "panicked" in backend.failure()
    assert len(heard) == 1
    assert meter._rust is None  # noqa: SLF001 - numpy from the failing block on
    got = before + after
    # Never silent, and the steps stay on the grid: the same count after every block.
    assert [row[5] for row in got] == [row[5] for row in straight]
    assert got[5][0] > -40.0
    # 3 s and a step after the failure the windows hold only blocks measured whole.
    clean = 5 + math.ceil((loudness.SHORT_TERM_S + 2 * loudness.STEP_S) * SR / 4096)
    _same(got[clean:], straight[clean:])


def test_a_panic_loading_numpy_s_state_into_rust_leaves_numpy_s_own(monkeypatch):
    """Back to Rust with a build whose `set_state` panics: numpy's state was never handed over,
    so numpy goes on from it, exactly."""
    x = _music(4.0, 6)
    backend.use(backend.RUST)
    meter = loudness.LoudnessMeter(SR, 2)
    _run(meter, x[:20000], [4096])
    backend.use(backend.NUMPY)
    assert meter._rust is None  # noqa: SLF001
    reference = copy.deepcopy(meter)
    real = backend.module().LoudnessMeter

    def armed(**kwargs):
        rust = real(**kwargs)
        rust._panic_next()  # noqa: SLF001
        return rust

    monkeypatch.setattr(backend.module(), "LoudnessMeter", armed)
    backend.use(backend.RUST)
    assert "panicked" in backend.failure()
    tail = x[20000:]
    got = _run(meter, tail, [4096])
    want = _run(reference, tail, [4096])
    assert meter._rust is None  # noqa: SLF001
    assert got == want
