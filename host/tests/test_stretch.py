"""The output stretcher (dsp/stretch.py): a cushion refilled by playing slightly slower, not by silence.

Spec docs/superpowers/specs/2026-10-08-seamless-transitions-design.md §4b and §7 (stretcher)."""

from __future__ import annotations

import numpy as np
import pytest

from aurasync.dsp import stretch
from aurasync.dsp.stretch import OutputStretcher

RATE = 48000
BLOCK = 4096


def _sine(n: int, f: float = 1000.0, start: int = 0, amplitude: float = 0.5) -> np.ndarray:
    return amplitude * np.sin(2 * np.pi * f * (start + np.arange(n)) / RATE)


def _frequency(y: np.ndarray) -> float:
    """The frequency of a pure tone, from the slope of its analytic signal's phase (the edges, where
    the transform wraps around, left out)."""
    spectrum = np.fft.fft(y)
    half = len(y) // 2
    spectrum[half + 1 :] = 0
    spectrum[1:half] *= 2
    phase = np.unwrap(np.angle(np.fft.ifft(spectrum)))
    inner = slice(len(y) // 10, len(y) - len(y) // 10)
    slope = np.polyfit(np.arange(len(y))[inner], phase[inner], 1)[0]
    return slope * RATE / (2 * np.pi)


def _thd_n_db(y: np.ndarray, f: float) -> float:
    """What is left after the best sine at `f` (and a constant) is taken out, against that sine."""
    n = np.arange(len(y))
    basis = np.column_stack([np.sin(2 * np.pi * f * n / RATE), np.cos(2 * np.pi * f * n / RATE), np.ones(len(y))])
    coef, *_ = np.linalg.lstsq(basis, y, rcond=None)
    fit = basis @ coef
    return 20 * np.log10(np.std(y - fit) / np.std(fit))


def test_at_zero_epsilon_the_block_passes_through_untouched():
    """Bit for bit, and not even a copy: the same object comes back."""
    s = OutputStretcher(2, RATE)
    x = np.random.default_rng(1).standard_normal((BLOCK, 2))
    assert s.process(x) is x
    blocks = {"a": x[:, 0], "b": x[:, 1]}
    t = OutputStretcher(["a", "b"], RATE)
    assert t.process(blocks) is blocks
    assert not s.active
    assert s.epsilon_ppm == 0
    assert s.pending_frames == 0


@pytest.mark.parametrize("ppm", [1000, -3000])
def test_a_constant_epsilon_gives_n_times_1_plus_epsilon_frames_per_block(ppm):
    """Over 1000 blocks: each within one frame of n·(1+ε), and their sum too (no drift)."""
    s = OutputStretcher(1, RATE, start_ppm=abs(ppm), max_ppm=abs(ppm))
    s.want(int(np.sign(ppm)) * 10**9)
    x = np.zeros(BLOCK)
    while abs(s.epsilon_ppm - ppm) > 1e-6:  # the ramp up to ε
        s.process(x)
    expected = BLOCK * (1 + ppm * 1e-6)
    lengths = [len(s.process(x)) for _ in range(1000)]
    assert all(abs(n - expected) <= 1 for n in lengths)
    assert abs(sum(lengths) - 1000 * expected) <= 1
    assert abs(s.epsilon_ppm - ppm) < 1e-6


@pytest.mark.parametrize("ppm", [1000, 5000, -1000])
def test_a_sine_keeps_its_frequency_and_its_purity(ppm, engine):  # noqa: ARG001 - both engines
    """The tone comes out at f/(1+ε) within 1e-6, with THD+N under -90 dB."""
    s = OutputStretcher(1, RATE, start_ppm=abs(ppm), max_ppm=abs(ppm))
    s.want(int(np.sign(ppm)) * 10**9)
    f = 1000.0
    produced, start = [], 0
    while abs(s.epsilon_ppm - ppm) > 1e-6:
        s.process(_sine(BLOCK, f, start))
        start += BLOCK
    for _ in range(30):
        produced.append(s.process(_sine(BLOCK, f, start)))
        start += BLOCK
    y = np.concatenate(produced)
    expected = f / (1 + ppm * 1e-6)
    measured = _frequency(y)
    assert abs(measured - expected) / expected < 1e-6
    assert _thd_n_db(y, measured) < -90


def test_epsilon_ramps_when_it_changes():
    """Measured every 64 input samples: ε never moves faster than the ramp, up, up a step, and down.
    A block of 64 in gives 64 or 65 out (ε is read per output sample), and ε in ppm moves a little
    faster than the frames per sample the ramp is drawn in (1/(1-a)² ≈ 1.01 at 5000 ppm)."""
    block = 64
    s = OutputStretcher(1, RATE, start_ppm=1000, max_ppm=3000, tolerance=0)
    per_block = stretch.RAMP_PPM_PER_S * (block + 1) / RATE * 1.01
    seen = [s.epsilon_ppm]
    s.want(2000)
    x = np.zeros(block)
    for k in range(100_000):
        if k == 400:
            s.want(int(s.pending_frames) + 500)  # the pipe fell further: one step up
        s.process(x)
        seen.append(s.epsilon_ppm)
        if not s.active:
            break
    steps = np.abs(np.diff(seen))
    assert steps.max() <= per_block
    assert max(seen) == pytest.approx(2000, abs=per_block), "it went up a step, from 1000 to 2000"
    assert seen[-1] == 0


@pytest.mark.parametrize("frames", [500, -500, 1, 37])
def test_want_moves_exactly_that_many_frames_then_returns_to_zero(frames):
    s = OutputStretcher(1, RATE)
    s.want(frames)
    assert s.pending_frames == frames
    total_in = total_out = 0
    x = np.zeros(BLOCK)
    for _ in range(10_000):
        total_in += BLOCK
        total_out += len(s.process(x))
        if not s.active:
            break
    assert total_out - total_in == frames
    assert s.epsilon_ppm == 0
    assert s.pending_frames == 0
    assert s.stretched_frames == abs(frames)
    assert s.process(x) is x, "back to passing through"


def test_the_signal_comes_out_whole_and_lands_on_the_same_samples(engine):  # noqa: ARG001 - both engines
    """A tone through idle → stretch → idle: no step in it anywhere (no click), and once idle the
    output is the input exactly, delayed by the frames added (the position landed on a whole sample)."""
    s = OutputStretcher(1, RATE, start_ppm=5000, max_ppm=5000)
    signal = _sine(BLOCK * 200, 3000.0)
    out, lengths = [], []
    for k, i in enumerate(range(0, len(signal), BLOCK)):
        if k == 3:
            s.want(700)
        y = s.process(signal[i : i + BLOCK])
        out.append(y)
        lengths.append(len(y))
    assert not s.active
    y = np.concatenate(out)
    added = len(y) - len(signal)
    assert added == 700
    # The same samples, bit for bit.
    np.testing.assert_array_equal(y[-BLOCK * 20 :], signal[-BLOCK * 20 :])
    # The output's own sample-to-sample step stays within the tone's steepest (2·A·sin(πf/sr)): a
    # jump of the read position would step over it.
    assert np.max(np.abs(np.diff(y))) <= 2 * 0.5 * np.sin(np.pi * 3000.0 / RATE) * 1.0001


def test_every_channel_gets_the_same_frames_at_the_same_positions():
    s = OutputStretcher(["a", "b", "c"], RATE)
    rng = np.random.default_rng(3)
    s.want(300)
    lengths = []
    for _ in range(200):
        x = rng.standard_normal(BLOCK)
        out = s.process({"a": x, "b": 2 * x, "c": rng.standard_normal(BLOCK)})
        assert len({len(v) for v in out.values()}) == 1
        np.testing.assert_array_equal(out["b"], 2 * out["a"])  # the same positions, read alike
        lengths.append(len(out["a"]))
        if not s.active:
            break
    assert sum(lengths) - BLOCK * len(lengths) == 300


def test_a_channel_that_joins_or_leaves_mid_stretch_keeps_the_others_in_step():
    s = OutputStretcher(["a", "b"], RATE)
    s.want(300)
    x = np.ones(BLOCK)
    s.process({"a": x, "b": x})
    joined = s.process({"a": x, "b": x, "c": x})
    assert len({len(v) for v in joined.values()}) == 1
    # From its second block the new channel is read from its own samples, like the others.
    y = np.random.default_rng(4).standard_normal(BLOCK)
    second = s.process({"a": y, "b": y, "c": y})
    np.testing.assert_array_equal(second["c"], second["a"])
    left = s.process({"a": x})
    assert set(left) == {"a"}
    assert s.process({}) == {}


def test_a_two_dimensional_block_keeps_its_shape():
    s = OutputStretcher(2, RATE)
    s.want(100)
    x = np.ones((BLOCK, 2))
    s.process(x)
    y = s.process(x)
    assert y.ndim == 2
    assert y.shape[1] == 2
    assert len(y) > BLOCK


def test_cancel_lands_on_a_whole_frame_with_a_ramp():
    s = OutputStretcher(1, RATE, start_ppm=5000, max_ppm=5000)
    s.want(10**6)
    x = np.zeros(BLOCK)
    total = 0
    for _ in range(20):
        total += len(s.process(x)) - BLOCK
    before = s.epsilon_ppm
    assert before > 0
    s.cancel()
    assert s.active, "a ramp down, not a stop"
    for _ in range(1000):
        total += len(s.process(x)) - BLOCK
        if not s.active:
            break
    assert not s.active
    assert s.epsilon_ppm == 0
    assert s.stretched_frames == total


def test_with_a_limit_at_zero_it_does_not_stretch():
    s = OutputStretcher(1, RATE, start_ppm=0, max_ppm=5000)
    assert not s.enabled
    s.want(1000)
    assert not s.active
    s.set_limits(1000, 0)
    assert not s.enabled
    s.set_limits(1000, 5000)
    assert s.enabled


def test_lowering_the_limit_while_stretching_ramps_down_to_it():
    s = OutputStretcher(1, RATE, start_ppm=1000, max_ppm=5000, tolerance=0)
    s.want(10**6)
    x = np.zeros(BLOCK)
    for _ in range(20):
        s.process(x)
    s.want(10**7)  # one step up
    s.want(10**8)  # and another
    for _ in range(40):
        s.process(x)
    assert s.epsilon_ppm == pytest.approx(3000)
    s.set_limits(1000, 2000)
    seen = []
    for _ in range(40):
        s.process(x)
        seen.append(s.epsilon_ppm)
    assert seen[-1] == pytest.approx(2000)
    assert np.max(np.abs(np.diff(seen))) <= stretch.RAMP_PPM_PER_S * BLOCK / RATE * 1.01


def test_a_stretch_the_disabled_knob_stops_lands_too():
    s = OutputStretcher(1, RATE)
    s.want(10**6)
    x = np.zeros(BLOCK)
    for _ in range(10):
        s.process(x)
    s.set_limits(0, 5000)
    for _ in range(100):
        s.process(x)
        if not s.active:
            break
    assert not s.active


class _Racy(OutputStretcher):
    """A stretcher whose plan ends (on the writer thread) right after anyone first reads it: each
    read of `_plan` after the first finds it gone, as a second read could in a real race."""

    @property
    def _plan(self):
        plan, self.__dict__["_racy"] = self.__dict__.get("_racy"), None
        return plan

    @_plan.setter
    def _plan(self, value):
        self.__dict__["_racy"] = value


def test_reading_the_state_while_the_plan_ends_never_raises():
    """The monitor's stretcher runs on the writer thread and its state is read on the engine thread
    (the panel's view): a plan that ends between two reads must not raise."""
    for prop in ("epsilon_ppm", "pending_frames", "stretched_frames", "direction"):
        s = OutputStretcher(1, RATE)
        s.want(1000)
        s.process(np.zeros(BLOCK))
        racy = _Racy(1, RATE)
        racy.__dict__.update({k: v for k, v in s.__dict__.items() if k != "_plan"})
        racy._plan = s._plan  # noqa: SLF001
        value = getattr(racy, prop)
        assert isinstance(value, (int, float))


def test_the_group_read_matches_a_read_per_channel(engine):  # noqa: ARG001 - both engines
    """The weights are computed once for the whole group: the same output as reading each channel
    on its own, within 1e-12."""
    from aurasync.dsp import interpolation

    rng = np.random.default_rng(5)
    bufs = {k: rng.standard_normal(4200) for k in "abcd"}
    positions = 20 + np.cumsum(np.full(4096, 0.997))
    out = stretch._read(bufs, positions)  # noqa: SLF001
    for k, buf in bufs.items():
        np.testing.assert_allclose(out[k], interpolation.read(buf, positions), rtol=0, atol=1e-12)
