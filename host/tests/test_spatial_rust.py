"""The spatial / front upmix in Rust (`aurasync_engine.SpatialUpmix`) against the numpy oracle
(`dsp/spatial.py`), through the stage's own dispatch (spec rust-engine §5, plan Task 5).

The extension must be built into the test environment: this file fails, it does not skip, when
`aurasync_engine` is missing.

Golden: with `engine=rust` the stage's output (direct and ambience of every speaker) is within
1e-9 absolute of numpy's (d-7c8794-36dde5), on the inputs of the stage's own tests
(tests/test_spatial.py, test_spatial_front.py), on full-scale signals, on random blocks of odd
sizes, on parameter sweeps and on live changes. The live switch moves the stage's state at the
cut's bottom (exact: a continued run equals the one that never switched), and a Rust failure gives
silence for that block, then numpy from a fresh stage.
"""

from __future__ import annotations

import gc
import types
import weakref

import aurasync_engine
import numpy as np
import pytest

from aurasync import control
from aurasync.chain import ChainValues
from aurasync.config import Instalacion, Parlante
from aurasync.dsp import ambience, backend, spatial
from aurasync.dsp.spatial import SpatialParams, SpatialUpmix, from_character
from aurasync.motor import Motor

SR = 48000
BLOCK = 4096
TOLERANCE = 1e-9
RING3 = {"L": -60.0, "R": 60.0, "B": 180.0}


# -- running a stage with each engine -------------------------------------------------------


def blocks(left, right, sizes=None):
    """`left`/`right` cut into blocks: of `BLOCK`, or of the sizes given (cycled)."""
    sizes = sizes or [BLOCK]
    out, i, k = [], 0, 0
    while i < len(left):
        n = sizes[k % len(sizes)]
        out.append((left[i : i + n], right[i : i + n]))
        i, k = i + n, k + 1
    return out


def run(engine, make, feed, between=None):
    """The stage built by `make()` with `engine` active, fed `feed` block by block; `between(k,
    up)` runs before block k (a live change). Returns {name: (direct, ambience)}."""
    backend.reset()
    backend.use(engine)
    up = make()
    outs = {name: ([], []) for name in up.names}
    for k, (left, right) in enumerate(feed):
        if between is not None:
            between(k, up)
        for name, (d, a) in up.process(left, right).items():
            assert d.shape == a.shape == (len(left),)
            outs[name][0].append(d)
            outs[name][1].append(a)
    # Rust really ran the stage, and never failed over to numpy.
    assert (up._rust is not None) == (engine == backend.RUST)  # noqa: SLF001
    assert backend.failure() is None
    return {name: (np.concatenate(d), np.concatenate(a)) for name, (d, a) in outs.items()}


def max_diff(got, expected):
    assert got.keys() == expected.keys()
    worst = 0.0
    for name in expected:
        for g, e in zip(got[name], expected[name], strict=True):
            assert g.shape == e.shape
            worst = max(worst, float(np.max(np.abs(g - e), initial=0.0)))
    return worst


def assert_golden(make, feed, between=None):
    expected = run(backend.NUMPY, make, feed, between)
    got = run(backend.RUST, make, feed, between)
    diff = max_diff(got, expected)
    assert diff <= TOLERANCE, f"max |diff| = {diff:.3g}"
    # The comparison is not between two silences.
    assert any(np.any(d) or np.any(a) for d, a in expected.values()) or not any(np.any(x) for x, _ in feed)
    return expected


# -- signals --------------------------------------------------------------------------------


def panned(phi, seconds=1.5, seed=0, scale=0.1):
    s = np.random.default_rng(seed).standard_normal(int(seconds * SR)) * scale
    return np.cos(phi) * s, np.sin(phi) * s


def music(seconds=1.5, seed=0, room=0.5):
    """A centre source plus independent room on each side, peaking at full scale (±1)."""
    rng = np.random.default_rng(seed)
    n = int(seconds * SR)
    common = rng.standard_normal(n)
    left, right = common + room * rng.standard_normal(n), common + room * rng.standard_normal(n)
    peak = max(np.abs(left).max(), np.abs(right).max())
    return left / peak, right / peak


def full_scale(seconds=1.5, seed=0):
    rng = np.random.default_rng(seed)
    n = int(seconds * SR)
    return rng.uniform(-1.0, 1.0, n), rng.uniform(-1.0, 1.0, n)


def stage(names, angles, ambient=(), params=None, classic=None):
    return lambda: SpatialUpmix(list(names), angles, set(ambient), SR, params or SpatialParams(), classic=classic)


# -- the inputs of the stage's own tests ----------------------------------------------------


@pytest.mark.parametrize("arc", [60.0, 150.0])
# Not 3π/4 itself: there cos and sin differ by one ulp, so L and R are anti-phase at equal level
# but for rounding, and numpy's own choice of phase source (`el >= er`) flips with a 1-ulp change of
# its input (0.349 of output, numpy against numpy). No engine can match it within 1e-9 there
# (experimentos/20, "Hallazgo"); the exact tie is checked below.
@pytest.mark.parametrize("phi", [0.0, np.pi / 2, np.pi / 4, 3 * np.pi / 4 + 0.01])
def test_a_panned_source_matches_numpy(phi, arc):
    assert_golden(stage(RING3, RING3, params=SpatialParams(arc_deg=arc)), blocks(*panned(phi)))


@pytest.mark.parametrize("arc", [60.0, 150.0])
def test_numpy_itself_changes_0_349_for_one_ulp_at_the_anti_phase_tie(arc):
    """The number experimentos/20 §2.3 gives as MEDIDO: at phi = 3π/4 (anti-phase at equal level
    but for rounding) the left input times (1 + 2⁻⁵²) changes numpy's own output by 0.349 (the
    output's RMS is ~0.035). It is numpy against numpy, Rust plays no part: the reason the golden
    moves that input off the tie. If numpy's FFT rounds otherwise one day, the number can move;
    what must not happen is that it falls below 1e-9 unnoticed (then the tie could go back)."""
    left, right = panned(3 * np.pi / 4)
    make = stage(RING3, RING3, params=SpatialParams(arc_deg=arc))
    base = run(backend.NUMPY, make, blocks(left, right))
    nudged = run(backend.NUMPY, make, blocks(left * (1 + 2**-52), right))
    assert abs(max_diff(nudged, base) - 0.349) < 5e-4


@pytest.mark.parametrize("arc", [60.0, 150.0])
def test_at_the_exact_anti_phase_tie_both_engines_choose_the_same_phase_source(arc):
    """L = -R at exactly equal level: `L + R` is exactly zero and `el == er` exactly in both
    engines (negation commutes with rounding), so both take the phase of L (`el >= er`) and agree.
    A level a hair off the tie (1 + 1e-9) is far enough from rounding to agree as well."""
    s = np.random.default_rng(0).standard_normal(int(1.5 * SR)) * 0.1
    make = stage(RING3, RING3, params=SpatialParams(arc_deg=arc))
    expected = assert_golden(make, blocks(-s, s))
    assert any(np.any(d) for d, _ in expected.values())
    assert_golden(make, blocks(-s * (1 + 1e-9), s))


def test_diffuse_ambience_to_two_ambients_matches_numpy():
    rng = np.random.default_rng(1)
    left, right = rng.standard_normal(SR) * 0.1, rng.standard_normal(SR) * 0.1
    make = stage(["L", "R", "A1", "A2"], {"L": -60.0, "R": 60.0}, {"A1", "A2"}, SpatialParams(ambience=1.0))
    assert_golden(make, blocks(left, right))


@pytest.mark.parametrize("character", [0.0, 0.5, 1.0])
def test_the_character_sweep_matches_numpy(character):
    angles = {"L": -60.0, "R": 60.0, "B": 180.0}
    make = stage(["L", "R", "B", "A"], angles, {"A"}, from_character(character))
    assert_golden(make, blocks(*music(seed=5)))


@pytest.mark.parametrize("arc", [90.0, 105.0, 150.0])
def test_two_principals_and_the_back_gap_match_numpy(arc):
    make = stage(["L", "R", "A"], {"L": -90.0, "R": 90.0}, {"A"}, SpatialParams(arc_deg=arc))
    assert_golden(make, blocks(*panned(0.0)))
    assert_golden(make, blocks(*music(seed=2)))


def test_mono_silence_and_one_channel_match_numpy():
    s = np.random.default_rng(4).standard_normal(SR)
    z = np.zeros(SR)
    for left, right in ((s, s), (z, z), (s, z), (z, s), (s, -s)):
        assert_golden(stage(RING3, RING3), blocks(left, right))
        assert_golden(stage(RING3, RING3, params=SpatialParams(front_intact=True)), blocks(left, right))


def test_all_ambient_matches_numpy():
    assert_golden(stage(["A", "B"], {}, {"A", "B"}), blocks(*panned(np.pi / 4)))


@pytest.mark.parametrize("db", [0.0, 6.0, 12.0])
@pytest.mark.parametrize(
    ("ring", "ambient"),
    [(RING3, ()), ({"a": -45.0, "b": -135.0}, ()), ({"L": -30.0, "R": 30.0}, ("A1", "A2", "A3"))],
    ids=["ring3", "no-front-pair", "pair+3-ambients"],
)
def test_the_front_render_matches_numpy(ring, ambient, db):
    names = [*ring, *ambient]
    make = stage(names, ring, ambient, SpatialParams(front_intact=True, ambient_level_db=db))
    assert_golden(make, blocks(*music(seed=3)))


# -- layouts, full scale, odd blocks, sweeps ------------------------------------------------

LAYOUTS = {
    "one-principal": (["C"], {"C": 0.0}, ()),
    "one-ambient": (["A"], {}, ("A",)),
    "principal+ambient": (["C", "A"], {"C": 0.0}, ("A",)),
    "ring3": (list(RING3), RING3, ()),
    "two-left-one-right": (["a", "b", "c"], {"a": -150.0, "b": -20.0, "c": 70.0}, ()),
    "ring8": ([f"s{i}" for i in range(8)], {f"s{i}": -180 + 360 * (i + 0.5) / 8 for i in range(8)}, ()),
    "ring5+3-ambients": (
        [f"s{i}" for i in range(8)],
        {f"s{i}": -150.0 + 75.0 * i for i in range(5)},
        ("s5", "s6", "s7"),
    ),
    "tied-angles": (["x", "y", "z"], {"x": 30.0, "y": 30.0, "z": -30.0}, ()),
    # -0.0 == 0.0 for Python's stable sort: the speakers keep their order (not -0.0 first).
    "signed-zero-tie": (["x", "y", "z"], {"x": 0.0, "y": -0.0, "z": 120.0}, ()),
    "unplaced-speaker": (["L", "R", "U"], {"L": -60.0, "R": 60.0}, ()),
    "behind-only": (["p", "q"], {"p": 150.0, "q": -150.0}, ()),
    "at-180": (["F", "B"], {"F": 0.0, "B": 180.0}, ()),
}


@pytest.mark.parametrize("front", [False, True], ids=["spatial", "front"])
@pytest.mark.parametrize("layout", list(LAYOUTS))
def test_every_layout_matches_numpy_at_full_scale(layout, front):
    names, angles, ambient = LAYOUTS[layout]
    make = stage(names, angles, ambient, SpatialParams(front_intact=front))
    assert_golden(make, blocks(*full_scale(seed=len(layout))))
    assert_golden(make, blocks(*music(seed=len(layout) + 1, room=0.3)))


def test_the_classic_loudness_target_matches_numpy():
    """The motor passes each speaker's (pan, ambience) so the render keeps the classic loudness."""
    angles = {"L": -60.0, "R": 60.0, "B": 180.0}
    classic = {"L": (-1.0, 0.1), "R": (1.0, 0.1), "B": (0.0, 0.6), "A": (0.0, 1.0)}
    for character in (0.0, 0.7):
        make = stage(["L", "R", "B", "A"], angles, {"A"}, from_character(character), classic=classic)
        assert_golden(make, blocks(*music(seed=8)))


@pytest.mark.parametrize(
    "sizes",
    [[1, 7, 511, 512, 513], [2047, 2048, 2049], [4095, 4097, 0, 4096], [10_000, 3, 9_999], [6_000]],
    ids=["tiny", "around-n_fft", "around-block", "large", "6000"],
)
def test_odd_block_sizes_match_numpy(sizes):
    make = stage(["L", "R", "B", "A"], {"L": -60.0, "R": 60.0, "B": 180.0}, {"A"}, from_character(0.6))
    left, right = music(seconds=0.8, seed=9)
    assert_golden(make, blocks(left, right, sizes))


def test_random_block_sizes_match_numpy():
    rng = np.random.default_rng(11)
    sizes = [int(x) for x in rng.integers(1, 9000, 40)]
    left, right = full_scale(seconds=2.0, seed=12)
    make = stage(list(RING3), RING3, params=SpatialParams(front_intact=False, haas_ms=0.0))
    assert_golden(make, blocks(left, right, sizes))


@pytest.mark.parametrize("seed", range(10))
def test_a_parameter_sweep_matches_numpy(seed):
    rng = np.random.default_rng(100 + seed)
    params = SpatialParams(
        arc_deg=float(rng.uniform(0.0, 200.0)),
        ambience=float(rng.uniform(0.0, 1.0)),
        ambient_level_db=float(rng.uniform(-12.0, 12.0)),
        haas_ms=float(rng.uniform(-5.0, 40.0)),  # outside [0, 30] too: both clamp it
        threshold=float(rng.uniform(0.0, 1.0)),
        lam=float(rng.uniform(0.5, 0.99)),
        front_intact=bool(rng.integers(0, 2)),
    )
    n = int(rng.integers(1, 9))
    names = [f"s{i}" for i in range(n)]
    ambient = {nm for nm in names if rng.uniform() < 0.3}
    angles = {nm: float(rng.uniform(-180.0, 180.0)) for nm in names if nm not in ambient}
    classic = {nm: (float(rng.uniform(-1, 1)), float(rng.uniform(0, 1))) for nm in names} if seed % 2 else None
    sizes = [int(x) for x in rng.integers(100, 6000, 8)]
    make = stage(names, angles, ambient, params, classic)
    assert_golden(make, blocks(*music(seconds=1.0, seed=seed, room=float(rng.uniform(0.1, 2.0))), sizes))


def test_extreme_signals_match_numpy():
    n = SR
    t = np.arange(n) / SR
    bin_tone = np.sin(2 * np.pi * (SR / spatial.N_FFT * 37) * t)  # exactly on a bin
    square = np.sign(np.sin(2 * np.pi * 440 * t))
    impulses = np.zeros(n)
    impulses[::3001] = 1.0
    for left, right in ((bin_tone, -bin_tone), (square, square), (impulses, np.roll(impulses, 7)), (square, bin_tone)):
        assert_golden(stage(["L", "R", "A"], {"L": -60.0, "R": 60.0}, {"A"}), blocks(left, right))


# -- live changes, inputs and errors --------------------------------------------------------


def test_live_param_and_layout_changes_match_numpy():
    angles = {"L": -60.0, "R": 60.0, "B": 180.0}

    def between(k, up):
        changes = {
            2: lambda: up.set_params(SpatialParams(haas_ms=25.0, arc_deg=80.0)),
            3: lambda: up.set_layout({"L": -50.0, "R": 60.0, "B": 170.0}, {"A"}),
            5: lambda: up.set_params(SpatialParams(front_intact=True, ambient_level_db=4.0, haas_ms=3.0)),
            6: lambda: up.set_layout({"L": -30.0, "R": 30.0}, {"A", "B"}, {"L": (-1.0, 0.0), "B": (0.0, 1.0)}),
            8: lambda: up.set_params(from_character(0.9)),
            9: lambda: up.set_layout({"L": -30.0}, set()),
        }
        if k in changes:
            changes[k]()

    make = stage(["L", "R", "B", "A"], angles, {"A"})
    assert_golden(make, blocks(*music(seconds=1.0, seed=13)), between)


def test_strided_and_float32_inputs_match_numpy():
    left, right = music(seconds=1.0, seed=14)
    wide_l, wide_r = np.repeat(left, 2), np.repeat(right, 2)
    strided = [(wide_l[i : i + 2 * BLOCK : 2], wide_r[i : i + 2 * BLOCK : 2]) for i in range(0, len(wide_l), 2 * BLOCK)]
    assert not strided[0][0].flags.c_contiguous
    assert_golden(stage(RING3, RING3), strided)
    single = [(x.astype(np.float32), y.astype(np.float32)) for x, y in blocks(left, right)]
    assert_golden(stage(RING3, RING3), single)


@pytest.mark.parametrize("engine", [backend.NUMPY, backend.RUST])
def test_channels_of_different_lengths_are_refused(engine):
    backend.reset()
    backend.use(engine)
    up = SpatialUpmix(list(RING3), RING3, set(), SR)
    with pytest.raises(ValueError, match="different lengths"):
        up.process(np.zeros(10), np.zeros(11))


def test_the_rust_stage_does_not_modify_its_inputs():
    backend.reset()
    backend.use(backend.RUST)
    up = SpatialUpmix(list(RING3), RING3, set(), SR)
    left, right = full_scale(seconds=0.2)
    before = left.copy(), right.copy()
    up.process(left, right)
    assert np.array_equal(left, before[0])
    assert np.array_equal(right, before[1])


# -- the switch and the failure -------------------------------------------------------------


@pytest.mark.parametrize(("first", "then"), [(backend.NUMPY, backend.RUST), (backend.RUST, backend.NUMPY)])
@pytest.mark.parametrize("at", [1, 4])
def test_the_switch_moves_the_state_exactly(first, then, at):
    """`backend.use` at a cut's bottom tells the stage (`on_engine_switch`), which moves its state
    to the other engine: the run goes on as if it had never switched."""
    make = stage(["L", "R", "B", "A"], {"L": -60.0, "R": 60.0, "B": 180.0}, {"A"}, from_character(0.7))
    feed = blocks(*music(seconds=1.0, seed=15), [3000, 4096, 777])
    expected = run(backend.NUMPY, make, feed)
    backend.reset()
    backend.use(first)
    up = make()
    outs = {name: ([], []) for name in up.names}
    for k, (left, right) in enumerate(feed):
        if k == at:
            backend.use(then)
            assert (up._rust is not None) == (then == backend.RUST)  # noqa: SLF001
        for name, (d, a) in up.process(left, right).items():
            outs[name][0].append(d)
            outs[name][1].append(a)
    assert (up._rust is not None) == (then == backend.RUST)  # noqa: SLF001
    got = {name: (np.concatenate(d), np.concatenate(a)) for name, (d, a) in outs.items()}
    diff = max_diff(got, expected)
    assert diff <= TOLERANCE, f"max |diff| = {diff:.3g}"


def test_the_raw_state_round_trip_is_exact():
    """`state()` and `set_state()` of the Rust object carry everything: a copy goes on bit for bit."""
    a = aurasync_engine.SpatialUpmix(3, SR, spatial.N_FFT, spatial.HOP)
    a.set_layout([-60.0, 60.0, None], [False, False, True], None)
    left, right = music(seconds=0.5, seed=16)
    a.process(left[:5000], right[:5000])
    b = aurasync_engine.SpatialUpmix(3, SR, spatial.N_FFT, spatial.HOP)
    b.set_layout([-60.0, 60.0, None], [False, False, True], None)
    b.set_state(a.state())
    for x, y in zip(a.process(left[5000:], right[5000:]), b.process(left[5000:], right[5000:]), strict=True):
        assert np.array_equal(x, y)
    bad = a.state()
    bad["norm"] = bad["norm"][:-1]
    with pytest.raises(ValueError, match="norm"):
        b.set_state(bad)
    bad = a.state()
    bad["haas_read"] = [10**6, 0, 0]
    with pytest.raises(ValueError, match="haas_read"):
        b.set_state(bad)


def _failing_next(up):
    """Make the stage's next Rust call panic (feature `test-panic` of the test build)."""
    up._rust._panic_next()  # noqa: SLF001


def test_a_rust_panic_is_one_silent_block_then_a_fresh_numpy_stage():
    """Nobody listening (`on_failure` None, no service): the failing block is silence on every
    speaker, and from the next block numpy runs, from a fresh stage (the Rust state may be torn)."""
    make = stage(list(RING3), RING3, params=from_character(0.5))
    feed = blocks(*music(seconds=1.0, seed=17))
    backend.reset()
    backend.use(backend.RUST)
    up = make()
    for left, right in feed[:3]:
        up.process(left, right)
    _failing_next(up)
    out = up.process(*feed[3])
    assert all(not np.any(d) and not np.any(a) for d, a in out.values())
    assert "panicked" in backend.failure()
    assert backend.active() == backend.NUMPY
    rest = {name: ([], []) for name in up.names}
    for left, right in feed[4:]:
        for name, (d, a) in up.process(left, right).items():
            rest[name][0].append(d)
            rest[name][1].append(a)
    assert up._rust is None  # noqa: SLF001
    backend.reset()
    backend.use(backend.NUMPY)
    fresh = make()
    for k, (left, right) in enumerate(feed[4:]):
        for name, (d, a) in fresh.process(left, right).items():
            assert np.array_equal(d, rest[name][0][k])
            assert np.array_equal(a, rest[name][1][k])


def test_a_rust_panic_with_a_service_is_silent_until_the_cut_s_bottom():
    make = stage(["L", "R", "A"], {"L": -60.0, "R": 60.0}, {"A"})
    feed = blocks(*music(seconds=1.0, seed=18))
    heard: list[str] = []
    backend.reset()
    backend.use(backend.RUST)
    backend.on_failure = heard.append
    up = make()
    up.process(*feed[0])
    _failing_next(up)
    for left, right in feed[1:4]:
        out = up.process(left, right)
        assert all(not np.any(d) and not np.any(a) for d, a in out.values())
    assert len(heard) == 1
    backend.use(backend.NUMPY)  # the cut's bottom
    assert up._rust is None  # noqa: SLF001
    out = up.process(*feed[4])
    assert set(out) == {"L", "R", "A"}
    # Choosing Rust again later builds a new Rust stage from numpy's state.
    backend.clear_failure()
    backend.use(backend.RUST)
    assert up._rust is not None  # noqa: SLF001
    up.process(*feed[5])
    assert backend.failure() is None


class _ProxyRust:
    """The real Rust stage with one method replaced (to plant a failure that is not a panic)."""

    def __init__(self, inner, **replaced):
        self._inner, self._replaced = inner, replaced

    def __getattr__(self, name):
        return self._replaced.get(name) or getattr(self._inner, name)


def _raiser(error):
    def raise_it(*_args):
        msg = "planted"
        raise error(msg)

    return raise_it


def _fake_module(**kwargs):
    """A module with a `SpatialUpmix` that wraps the real one, `kwargs` replacing methods."""
    real = aurasync_engine.SpatialUpmix

    class Fake:
        def __new__(cls, *args):
            return _ProxyRust(real(*args), **kwargs)

    return types.SimpleNamespace(SpatialUpmix=Fake)


def _assert_fell_back_to_numpy(up, feed, heard, error):
    assert len(heard) == 1
    assert error.__name__ in heard[0]
    assert error.__name__ in backend.failure()
    for left, right in feed[:2]:  # the silent window: silence on every speaker, nothing raised
        out = up.process(left, right)
        assert all(not np.any(d) and not np.any(a) for d, a in out.values())
    backend.use(backend.NUMPY)  # the cut's bottom
    assert up._rust is None  # noqa: SLF001
    out = up.process(*feed[2])
    assert set(out) == set(up.names)
    assert any(np.any(d) or np.any(a) for d, a in out.values())
    assert len(heard) == 1


@pytest.mark.parametrize("error", [ValueError, TypeError, AttributeError])
@pytest.mark.parametrize("failing", ["constructor", "set_params", "set_layout", "set_state"])
def test_a_rust_stage_that_fails_while_being_built_falls_back_to_numpy(monkeypatch, error, failing):
    """Not a panic: PyO3 argument conversion, or an older extension without the class or method.
    The session keeps its audio (silence until the cut's bottom, then numpy), the handler hears
    once, and nothing escapes."""
    if failing == "constructor":

        class Broken:
            def __init__(self, *_args):
                msg = "planted"
                raise error(msg)

        module = types.SimpleNamespace(SpatialUpmix=Broken)
    else:
        module = _fake_module(**{failing: _raiser(error)})
    monkeypatch.setattr(backend, "module", lambda: module)
    feed = blocks(*music(seconds=1.0, seed=20))
    heard: list[str] = []
    backend.reset()
    backend.use(backend.RUST)
    backend.on_failure = heard.append
    up = stage(["L", "R", "A"], {"L": -60.0, "R": 60.0}, {"A"})()
    _assert_fell_back_to_numpy(up, feed, heard, error)


@pytest.mark.parametrize("error", [ValueError, TypeError, AttributeError])
@pytest.mark.parametrize("call", ["set_params", "set_layout"])
def test_a_live_configuration_that_fails_falls_back_to_numpy(error, call):
    feed = blocks(*music(seconds=1.0, seed=21))
    heard: list[str] = []
    backend.reset()
    backend.use(backend.RUST)
    backend.on_failure = heard.append
    up = stage(["L", "R", "A"], {"L": -60.0, "R": 60.0}, {"A"})()
    up.process(*feed[0])
    up._rust = _ProxyRust(up._rust, **{call: _raiser(error)})  # noqa: SLF001
    if call == "set_params":
        up.set_params(SpatialParams(haas_ms=3.0))
    else:
        up.set_layout({"L": -60.0, "R": 60.0}, {"A"})
    _assert_fell_back_to_numpy(up, feed[1:], heard, error)


def test_a_state_read_that_fails_at_the_switch_to_numpy_restarts_without_a_second_cut():
    """The user chose numpy: a failed state read is logged, not a Rust failure (no `_fail`, no
    silence, no second call to the handler); the stage restarts in numpy and produces audio."""
    feed = blocks(*music(seconds=1.0, seed=22))
    heard: list[str] = []
    backend.reset()
    backend.use(backend.RUST)
    backend.on_failure = heard.append
    up = stage(["L", "R", "A"], {"L": -60.0, "R": 60.0}, {"A"})()
    up.process(*feed[0])
    up._rust = _ProxyRust(up._rust, state=_raiser(TypeError))  # noqa: SLF001
    backend.use(backend.NUMPY)
    assert up._rust is None  # noqa: SLF001
    assert backend.silent() is None
    assert backend.failure() is None
    assert heard == []
    out = up.process(*feed[1])
    assert any(np.any(d) or np.any(a) for d, a in out.values())
    assert heard == []


def test_the_backend_does_not_keep_a_dropped_stage_alive():
    """The motor builds a new stage at each render change: the old one must not stay registered."""
    backend.reset()
    backend.use(backend.RUST)
    up = SpatialUpmix(list(RING3), RING3, set(), SR)
    ref = weakref.ref(up)
    del up
    gc.collect()
    assert ref() is None
    backend.use(backend.NUMPY)  # nobody left to tell; nothing fails


def test_capabilities_carry_the_spatial_constants():
    assert aurasync_engine.capabilities()["spatial"] == {
        "max_haas_ms": spatial.MAX_HAAS_MS,
        "fade_in": spatial.FADE_IN,
        "floor": spatial._FLOOR,  # noqa: SLF001
        "silent": spatial._SILENT,  # noqa: SLF001
        "front_boost_db": spatial.FRONT_AMBIENCE_BOOST_DB,
        "min_energy_ratio": ambience.Parametros().energia_minima,
        "mu0": ambience.Parametros().mu0,
        "mu1": ambience.Parametros().mu1,
        "sigma": ambience.Parametros().sigma,
    }


# -- inside the motor -----------------------------------------------------------------------


def _installation():
    parlantes = []
    for i, a in enumerate(control.auto_angles(3)):
        pan, amb = control.role_from_angle(a)
        parlantes.append(Parlante(f"s{i}", f"sink{i}", pan=pan, ambiente=amb))
    parlantes.append(Parlante("amb", "sink9", role_kind="ambient"))
    return Instalacion(parlantes=parlantes)


@pytest.mark.parametrize("render", ["spatial", "front"])
def test_the_motor_with_the_rust_stage_matches_numpy(render):
    left, right = music(seconds=1.5, seed=19)
    chain = ChainValues.from_json({"spatial": {"algorithm": render, "params": {"character": 0.6}}})
    outs = {}
    for engine in (backend.NUMPY, backend.RUST):
        backend.reset()
        backend.use(engine)
        m = Motor(_installation(), SR, semilla=1, chain=chain)
        got = {p.nombre: [] for p in m.instalacion.parlantes}
        for i in range(0, len(left), BLOCK):
            for name, x in m.procesar(left[i : i + BLOCK], right[i : i + BLOCK]).items():
                got[name].append(x)
        assert (m.espacial._rust is not None) == (engine == backend.RUST)  # noqa: SLF001
        outs[engine] = {name: np.concatenate(v) for name, v in got.items()}
    for name, x in outs[backend.NUMPY].items():
        assert np.any(x)
        diff = float(np.max(np.abs(outs[backend.RUST][name] - x)))
        assert diff <= TOLERANCE, f"{name}: max |diff| = {diff:.3g}"
