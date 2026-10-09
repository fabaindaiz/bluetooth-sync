"""The FIR filters in Rust (`aurasync_engine.StreamingFIR`, `aurasync_engine.PartitionedFIR`)
against the numpy oracle (`dsp/eq.py`), through the filters' own dispatch (spec rust-engine §5,
plan Task 7).

The extension must be built into the test environment: this file fails, it does not skip, when
`aurasync_engine` is missing.

Golden: with `engine=rust` each filter's output is within 1e-9 absolute of numpy's
(d-7c8794-36dde5), on the inputs of the stage's own tests (tests/test_eq.py), on full-scale
signals, on random blocks of odd sizes (shorter than the taps, equal to the partition, longer,
the short last block), on `set_taps` and `taps = ...` mid-stream with the same and another length,
on `skip` then `process`, on the EQ curves of `eq.fir` with the real `TAPS`, on the crossover's
impulses, and inside every user (the motor's EQ, the bass stage, the virtual bass, the diffuse
tail). The live switch moves each filter's state at the cut's bottom (exact), and a Rust failure
gives silence for that block, then numpy from a fresh filter.
"""

from __future__ import annotations

import gc
import types
import weakref

import aurasync_engine
import numpy as np
import pytest

from aurasync import chain, motor
from aurasync.chain import ChainValues
from aurasync.config import Instalacion, Parlante
from aurasync.dsp import backend, crossover, eq, virtual_bass
from aurasync.dsp.diffuse import Diffuse
from aurasync.dsp.eq import PartitionedFIR, StreamingFIR
from aurasync.dsp.response import THIRDS
from aurasync.dsp.virtual_bass import VirtualBass
from tests.test_crossover_rust import assert_engine_owns, stage_filters

SR = 48000
BLOCK = 4096
TOLERANCE = 1e-9


# -- running a filter with each engine ------------------------------------------------------


def run(engine, make, ops):
    """The filter built by `make()` with `engine` active, fed `ops` in order: an array is a block
    to `process`; a callable `op(f)` is a live change or a `skip` (whatever it returns is ignored).
    Returns the whole output."""
    backend.reset()
    backend.use(engine)
    f = make()
    out = []
    for op in ops:
        if callable(op):
            op(f)
            continue
        y = f.process(op)
        assert y.shape == (len(op),)
        out.append(y)
    # Rust really ran the filter, and never failed over to numpy.
    assert (f._rust is not None) == (engine == backend.RUST)  # noqa: SLF001
    assert backend.failure() is None
    return np.concatenate(out) if out else np.zeros(0)


def assert_golden(make, ops, *, silent_ok=False):
    expected = run(backend.NUMPY, make, ops)
    got = run(backend.RUST, make, ops)
    assert got.shape == expected.shape
    diff = float(np.max(np.abs(got - expected), initial=0.0))
    assert diff <= TOLERANCE, f"max |diff| = {diff:.3g}"
    # The comparison is not between two silences.
    assert silent_ok or np.any(expected)
    return expected


def blocks(x, sizes=None):
    """`x` cut into blocks: of `BLOCK`, or of the sizes given (cycled)."""
    sizes = sizes or [BLOCK]
    out, i, k = [], 0, 0
    while i < len(x):
        n = sizes[k % len(sizes)]
        out.append(x[i : i + n])
        i, k = i + n, k + 1
    return out


def skipping(x):
    """An op that skips `x` (`PartitionedFIR.skip`)."""
    return lambda f: f.skip(x)


def streaming(taps):
    return lambda: StreamingFIR(taps)


def partitioned(taps, block=BLOCK):
    return lambda: PartitionedFIR(taps, block)


# -- signals --------------------------------------------------------------------------------


def full_scale(n, seed=0):
    return np.random.default_rng(seed).uniform(-1.0, 1.0, n)


def pink(n, seed=0):
    rng = np.random.default_rng(seed)
    spec = np.fft.rfft(rng.standard_normal(n))
    f = np.fft.rfftfreq(n, 1 / SR)
    spec[1:] /= np.sqrt(f[1:])
    spec[0] = 0
    x = np.fft.irfft(spec, n)
    return x / np.abs(x).max()


def eq_curves():
    """The EQ curves the motor plays: none (a delayed impulse), a dip lifted, a flat boost, the
    maximum boost, and corrections of random measured responses (with the budget and the cap)."""
    rng = np.random.default_rng(40)
    curves = {
        "none": None,
        "treble-cut": np.where(THIRDS > 2000, -6.0, 0.0),
        "flat-2": np.full(len(THIRDS), 2.0),
        "max": np.full(len(THIRDS), eq.MAX_BOOST_DB),
    }
    for k in range(3):
        measured = rng.uniform(-9.0, 3.0, len(THIRDS))
        curves[f"measured-{k}"] = eq.correction(
            measured, budget_db=[None, 3.0, 4.5][k], treble_cap_db=[None, 3.0, 0.0][k]
        )
    return curves


# -- StreamingFIR: the inputs of the stage's own tests --------------------------------------


def test_the_streaming_case_of_the_stage_s_tests_matches_numpy():
    """tests/test_eq.py: blocks of 1500 through the treble-cut EQ."""
    x = np.random.default_rng(1).standard_normal(20000)
    h = eq.fir(np.where(THIRDS > 2000, -6.0, 0.0))
    assert_golden(streaming(h), blocks(x, [1500]))


def test_the_cache_case_of_the_stage_s_tests_matches_numpy():
    """tests/test_eq.py's cache sequence: odd sizes and a 0, `set_taps` with the same length at
    block 10 and with another (1000 taps) at block 20."""
    x = np.random.default_rng(7).standard_normal(60000)
    h1 = eq.fir(np.where(THIRDS > 2000, -6.0, 0.0))
    h2 = eq.fir(np.where(THIRDS < 300, 4.0, 0.0))
    ops, i = [], 0
    for k, n in enumerate([4096, 4096, 1024, 4096, 333, 4096, 4096, 0, 2048] * 3):
        if k == 10:
            ops.append(lambda f: f.set_taps(h2))
        if k == 20:
            ops.append(lambda f: f.set_taps(h1[:1000]))
        ops.append(x[i : i + n])
        i += n
    assert_golden(streaming(h1), ops)


def test_one_long_block_of_pink_noise_matches_numpy():
    """tests/test_eq.py's budget case: 2^19 samples in one block (an FFT of 2^20)."""
    capped = eq.correction(np.where(THIRDS > 1000, -12.0, -3.0), budget_db=3.0)
    assert_golden(streaming(eq.fir(capped)), [pink(1 << 19)])


# -- StreamingFIR: full scale, odd blocks, curves, impulses ---------------------------------


@pytest.mark.parametrize(("name", "curve"), list(eq_curves().items()), ids=list(eq_curves()))
def test_the_eq_curves_with_the_real_taps_match_numpy(name, curve):
    h = eq.fir(curve)
    assert len(h) == eq.TAPS
    assert_golden(streaming(h), blocks(full_scale(3 * SR, seed=len(name))))
    assert_golden(streaming(h), blocks(pink(2 * SR, seed=3), [4096, 4096, 1000]))


@pytest.mark.parametrize(
    "sizes",
    [[1, 7, 333, 2047, 2048, 2049], [4095, 4097, 0, 4096], [10_000, 3, 9_999], [6_000], [1]],
    ids=["shorter-than-the-taps", "around-the-block", "large", "6000", "one-sample"],
)
def test_odd_block_sizes_match_numpy(sizes):
    h = eq.fir(np.where(THIRDS < 200, 5.0, 1.5))
    x = full_scale(SR // 2 if sizes != [1] else 3000, seed=8)
    assert_golden(streaming(h), blocks(x, sizes))


@pytest.mark.parametrize("seed", range(4))
def test_random_block_sizes_match_numpy(seed):
    rng = np.random.default_rng(100 + seed)
    sizes = [int(n) for n in rng.integers(0, 9000, 40)]
    taps = rng.standard_normal(int(rng.integers(1, 5000))) * 0.2
    assert_golden(streaming(taps), blocks(full_scale(2 * SR, seed=seed), sizes))


@pytest.mark.parametrize("cutoff", [60.0, 100.0, 150.0, 250.0])
@pytest.mark.parametrize("order", crossover.ORDERS)
def test_the_crossover_impulses_match_numpy(cutoff, order):
    """The crossover's branches (`crossover.HighPass` / `LowPass`) and the bass stage's all-pass
    (LP + HP), as `chain_stages` builds them."""
    lp, hp = crossover.impulses(SR, cutoff, order)
    x = full_scale(2 * SR, seed=int(cutoff) + order)
    for taps in (lp, hp, lp + hp):
        assert_golden(streaming(taps), blocks(x, [4096, 4096, 777]))


def test_the_crossover_branches_themselves_match_numpy():
    x = full_scale(SR, seed=9)
    for make in (lambda: crossover.HighPass(SR, 120.0), lambda: crossover.LowPass(SR, 80.0, 8)):
        outs = {}
        for engine in (backend.NUMPY, backend.RUST):
            backend.reset()
            backend.use(engine)
            branch = make()
            outs[engine] = np.concatenate([branch.process(b) for b in blocks(x)])
            assert (branch._fir._rust is not None) == (engine == backend.RUST)  # noqa: SLF001
        assert np.max(np.abs(outs[backend.RUST] - outs[backend.NUMPY])) <= TOLERANCE


def test_a_one_tap_filter_matches_numpy():
    assert_golden(streaming(np.array([0.7])), blocks(full_scale(5000, seed=10), [1, 2, 3, 4096, 100]))


# -- StreamingFIR: live changes, inputs and errors ------------------------------------------


@pytest.mark.parametrize("call", ["set_taps", "setter"])
@pytest.mark.parametrize("length", [eq.TAPS, eq.TAPS + 500, 1000], ids=["same", "longer", "shorter"])
def test_changing_the_taps_mid_stream_matches_numpy(call, length):
    """`set_taps` resets the tail when the length changes; `taps = ...` keeps it (the motor uses
    `set_taps` at a cut's bottom; the setter is the property's)."""
    h1 = eq.fir(np.where(THIRDS > 2000, -6.0, 0.0))
    h2 = eq.fir(np.full(len(THIRDS), 3.0), taps=length) if length != eq.TAPS else eq.fir(np.full(len(THIRDS), 3.0))

    def change(f):
        if call == "set_taps":
            f.set_taps(h2)
        else:
            f.taps = h2

    x = full_scale(SR, seed=11)
    ops = [*blocks(x[: 3 * BLOCK]), change, *blocks(x[3 * BLOCK :], [4096, 1500])]
    assert_golden(streaming(h1), ops)


@pytest.mark.parametrize("engine", [backend.NUMPY, backend.RUST])
def test_a_block_the_kept_tail_does_not_fit_is_refused_by_both_engines(engine):
    """After `taps = ...` with fewer taps the tail is longer than a short block's convolution:
    numpy's broadcast error, a `ValueError`, in both engines, and nothing changes."""
    backend.reset()
    backend.use(engine)
    f = StreamingFIR(eq.fir(None))
    f.process(full_scale(BLOCK, seed=12))
    f.taps = np.array([1.0, 0.5])
    with pytest.raises(ValueError, match=r"broadcast|tail"):
        f.process(np.ones(10))
    assert backend.failure() is None
    assert f.process(np.ones(BLOCK)).shape == (BLOCK,)


def test_strided_and_integer_inputs_and_float32_taps_match_numpy():
    h = eq.fir(np.where(THIRDS > 4000, 4.0, 0.0))
    x = full_scale(SR, seed=13)
    wide = np.repeat(x, 2)
    strided = [wide[i : i + 2 * BLOCK : 2] for i in range(0, len(wide), 2 * BLOCK)]
    assert not strided[0].flags.c_contiguous
    assert_golden(streaming(h), strided)
    assert_golden(streaming(h), [np.round(b * 100).astype(np.int64) for b in blocks(x)])
    assert_golden(streaming(h.astype(np.float32)), blocks(x))
    assert_golden(partitioned(h, 1024), [b.astype(np.float32) for b in blocks(x)])


def test_float32_blocks_are_filtered_in_double_precision():
    """numpy's `StreamingFIR` does not convert its input, and numpy >= 2 runs the FFT of a
    float32 block in single precision: ~1e-7 from the same block in float64 (MEDIDO here,
    8.9e-8). The Rust path converts every block to float64 (as every ported stage does), so it
    equals numpy on the float64 block. No user passes float32 (the motor's blocks are float64 by
    the time they reach a filter, and `PartitionedFIR` and the crossover convert); numpy's
    single-precision path is left as it is."""
    h = eq.fir(np.where(THIRDS > 4000, 4.0, 0.0))
    x = full_scale(SR, seed=13)
    single = [b.astype(np.float32) for b in blocks(x)]
    as_double = [b.astype(np.float64) for b in single]
    rust = run(backend.RUST, streaming(h), single)
    numpy_double = run(backend.NUMPY, streaming(h), as_double)
    numpy_single = run(backend.NUMPY, streaming(h), single)
    assert np.max(np.abs(rust - numpy_double)) <= TOLERANCE
    assert 1e-8 < np.max(np.abs(numpy_single - numpy_double)) < 1e-6


def test_the_rust_filters_do_not_modify_their_inputs():
    backend.reset()
    backend.use(backend.RUST)
    h = eq.fir(np.full(len(THIRDS), 2.0))
    x = full_scale(BLOCK, seed=14)
    before, taps = x.copy(), h.copy()
    StreamingFIR(h).process(x)
    PartitionedFIR(h, 1024).process(x)
    assert np.array_equal(x, before)
    assert np.array_equal(h, taps)


def test_read_only_taps_are_taken():
    """`chain_stages._lr_taps` hands out read-only arrays (its cache)."""
    _, hp = crossover.impulses(SR, 100.0, 4)
    hp.flags.writeable = False
    assert_golden(streaming(hp), blocks(full_scale(SR, seed=15)))


# -- PartitionedFIR -------------------------------------------------------------------------


@pytest.mark.parametrize(("partition", "sizes"), [(4096, [4096]), (1024, [1024, 1024, 300, 1024, 5000]), (512, [700])])
def test_the_partition_cases_of_the_stage_s_tests_match_numpy(partition, sizes):
    rng = np.random.default_rng(3)
    x = rng.standard_normal(30000)
    h = rng.standard_normal(5000) * np.exp(-np.arange(5000) / 800)
    assert_golden(partitioned(h, partition), blocks(x, sizes))


def test_the_skip_case_of_the_stage_s_tests_matches_numpy():
    rng = np.random.default_rng(4)
    x = rng.standard_normal(20000)
    h = rng.standard_normal(3000)
    ops = [x[:4096], skipping(x[4096:8192]), *blocks(x[8192:], [1024])]
    assert_golden(partitioned(h, 1024), ops)


@pytest.mark.parametrize(
    ("taps", "partition"),
    [(5000, 4096), (100, 1024), (1024, 1024), (3072, 1024), (3000, 1000), (1, 64), (777, 333)],
    ids=["two-parts", "shorter-than-a-part", "one-part", "three-exact", "odd-partition", "one-tap", "odd-both"],
)
@pytest.mark.parametrize("seed", range(3))
def test_random_blocks_and_skips_match_numpy(taps, partition, seed):
    """Blocks shorter than the partition, equal, longer, empty, and skips of any length (longer
    than the whole history too), in random order: the delay line is invalidated and rebuilt."""
    rng = np.random.default_rng(1000 * seed + taps)
    h = rng.standard_normal(taps) * np.exp(-np.arange(taps) / max(taps / 4, 1))
    x = full_scale(SR, seed=seed)
    ops, i = [], 0
    while i < len(x):
        kind = rng.choice(["full", "short", "long", "zero", "skip"], p=[0.45, 0.2, 0.15, 0.05, 0.15])
        n = {
            "full": partition,
            "short": int(rng.integers(1, partition)) if partition > 1 else 1,
            "long": int(rng.integers(partition + 1, 3 * partition + 2)),
            "zero": 0,
            "skip": int(rng.integers(0, 3 * (taps + partition))),
        }[kind]
        chunk = x[i : i + n]
        ops.append(skipping(chunk) if kind == "skip" else chunk)
        i += n
    assert_golden(partitioned(h, partition), ops)


def test_the_virtual_bass_filters_match_numpy():
    """`VirtualBass`'s two filters as it uses them: the band, then the harmonics band on |bass|."""
    band, out, _ = virtual_bass._filters(SR, 90.0)  # noqa: SLF001
    x = pink(3 * SR, seed=16)
    assert_golden(partitioned(band), blocks(x, [4096, 4096, 4096, 1000]))
    assert_golden(partitioned(out), blocks(np.abs(x), [4096, 2048, 4096]))


def test_the_virtual_bass_itself_matches_numpy():
    """Harmonics on, a live change of their level, off (the filters stay at rest, rebuilt only
    after they ran), and on again."""
    x = pink(4 * SR, seed=17)
    outs = {}
    for engine in (backend.NUMPY, backend.RUST):
        backend.reset()
        backend.use(engine)
        vb = VirtualBass(SR, 90.0, 0.0, BLOCK)
        got = []
        for k, b in enumerate(blocks(x)):
            vb.harmonics_db = {8: -6.0, 20: None, 30: 3.0}.get(k, vb.harmonics_db)
            got.append(vb.process(b))
        assert (vb._rust is not None) == (engine == backend.RUST)  # noqa: SLF001
        outs[engine] = np.concatenate(got)
    assert np.any(outs[backend.NUMPY])
    assert np.max(np.abs(outs[backend.RUST] - outs[backend.NUMPY])) <= TOLERANCE


def test_the_diffuse_tail_matches_numpy():
    """`Diffuse` skips its input while it is off and processes it while on."""
    x = pink(4 * SR, seed=18)
    outs = {}
    for engine in (backend.NUMPY, backend.RUST):
        backend.reset()
        backend.use(engine)
        d = Diffuse(SR, 3, None, block=BLOCK)
        got = []
        for k, b in enumerate(blocks(x, [4096, 4096, 1111])):
            d.level_db = {4: -12.0, 20: None, 26: -6.0}.get(k, d.level_db)
            got.append(d.process(b))
        assert (d._fir._rust is not None) == (engine == backend.RUST)  # noqa: SLF001
        outs[engine] = np.concatenate(got)
    assert np.any(outs[backend.NUMPY])
    assert np.max(np.abs(outs[backend.RUST] - outs[backend.NUMPY])) <= TOLERANCE


# -- the switch -----------------------------------------------------------------------------


def _streaming_ops():
    x = full_scale(SR, seed=19)
    h2 = eq.fir(np.full(len(THIRDS), 2.0))
    return [*blocks(x[: 2 * BLOCK]), lambda f: f.set_taps(h2), *blocks(x[2 * BLOCK :], [4096, 777, 3000])]


def _partitioned_ops():
    x = full_scale(SR, seed=20)
    return [*blocks(x[:5000], [1024]), skipping(x[5000:7000]), *blocks(x[7000:], [1024, 1024, 300])]


CASES = {
    "streaming": (streaming(eq.fir(np.where(THIRDS > 2000, -6.0, 0.0))), _streaming_ops),
    "partitioned": (partitioned(np.random.default_rng(21).standard_normal(3000) * 0.1, 1024), _partitioned_ops),
}


@pytest.mark.parametrize("case", list(CASES))
@pytest.mark.parametrize(("first", "then"), [(backend.NUMPY, backend.RUST), (backend.RUST, backend.NUMPY)])
@pytest.mark.parametrize("at", [1, 4, 6])
def test_the_switch_moves_the_state_exactly(case, first, then, at):
    """`backend.use` at a cut's bottom tells the filter (`on_engine_switch`), which moves its
    state to the other engine: the run goes on as if it had never switched."""
    make, ops = CASES[case]
    ops = ops()
    expected = run(backend.NUMPY, make, ops)
    backend.reset()
    backend.use(first)
    f = make()
    out = []
    for k, op in enumerate(ops):
        if k == at:
            backend.use(then)
        if callable(op):
            op(f)
        else:
            out.append(f.process(op))
    assert (f._rust is not None) == (then == backend.RUST)  # noqa: SLF001
    diff = float(np.max(np.abs(np.concatenate(out) - expected)))
    assert diff <= TOLERANCE, f"max |diff| = {diff:.3g}"


def test_the_raw_streaming_state_round_trip_is_exact():
    h = eq.fir(np.full(len(THIRDS), 2.0))
    x = full_scale(20000, seed=22)
    a = aurasync_engine.StreamingFIR(h)
    a.process(x[:5000])
    b = aurasync_engine.StreamingFIR(np.array([1.0]))
    b.set_state(a.state())
    assert np.array_equal(a.process(x[5000:]), b.process(x[5000:]))
    bad = a.state()
    bad["taps"] = np.zeros(0)
    with pytest.raises(ValueError, match="tap"):
        b.set_state(bad)
    bad = a.state()
    del bad["tail"]
    with pytest.raises(ValueError, match="tail"):
        b.set_state(bad)


def test_the_raw_partitioned_state_round_trip_is_exact():
    h = np.random.default_rng(23).standard_normal(3000)
    x = full_scale(20000, seed=23)
    a = aurasync_engine.PartitionedFIR(h, 1024)
    a.process(x[:5000])
    b = aurasync_engine.PartitionedFIR(h, 1024)
    b.set_state(a.state())
    assert np.array_equal(a.process(x[5000:]), b.process(x[5000:]))
    re, im = a.state()["fdl_re"], a.state()["fdl_im"]
    for key, broken in (
        ("history", {"history": a.state()["history"][:-1]}),
        ("fdl", {"fdl_re": re[:-1]}),
        ("fdl", {"fdl_im": im[:, :-1]}),
        ("fdl", {"fdl_re": re[:, :-1], "fdl_im": im[:, :-1]}),
        ("fdl", {"fdl_re": re[:-1], "fdl_im": im[:-1]}),
        ("head", {"head": 7}),
    ):
        bad = {**a.state(), **broken}
        with pytest.raises(ValueError, match=key):
            b.set_state(bad)
    assert np.array_equal(a.process(x[:999]), b.process(x[:999]))


@pytest.mark.parametrize("case", list(CASES))
def test_the_state_s_keys_are_numpy_s(case):
    """The numpy filter's state in the Rust filter's terms, and back, is exact."""
    make, ops = CASES[case]
    backend.reset()
    backend.use(backend.NUMPY)
    f = make()
    for op in ops()[:5]:
        op(f) if callable(op) else f.process(op)
    state = f._numpy_state()  # noqa: SLF001
    if case == "streaming":
        rust = aurasync_engine.StreamingFIR(np.array([1.0]))
    else:
        rust = aurasync_engine.PartitionedFIR(f.taps, f.block)
    rust.set_state(state)
    back = rust.state()
    assert back.keys() == state.keys()
    for key, value in state.items():
        assert np.array_equal(back[key], value), key


# -- the failure ----------------------------------------------------------------------------


def _feed():
    return blocks(full_scale(SR, seed=24))


def _rust_filters():
    return {
        "streaming": lambda: StreamingFIR(eq.fir(np.full(len(THIRDS), 2.0))),
        "partitioned": lambda: PartitionedFIR(np.random.default_rng(25).standard_normal(3000) * 0.1, BLOCK),
    }


@pytest.mark.parametrize("kind", list(_rust_filters()))
def test_a_rust_panic_is_one_silent_block_then_a_fresh_numpy_filter(kind):
    """Nobody listening (`on_failure` None, no service): the failing block is silence, and from
    the next block numpy runs, from a fresh filter (the Rust state may be torn)."""
    make = _rust_filters()[kind]
    feed = _feed()
    backend.reset()
    backend.use(backend.RUST)
    f = make()
    for x in feed[:3]:
        f.process(x)
    f._rust._panic_next()  # noqa: SLF001
    assert not np.any(f.process(feed[3]))
    assert "panicked" in backend.failure()
    assert backend.active() == backend.NUMPY
    rest = [f.process(x) for x in feed[4:]]
    assert f._rust is None  # noqa: SLF001
    backend.reset()
    backend.use(backend.NUMPY)
    fresh = make()
    for k, x in enumerate(feed[4:]):
        assert np.array_equal(fresh.process(x), rest[k])


@pytest.mark.parametrize("kind", list(_rust_filters()))
def test_a_rust_panic_with_a_service_is_silent_until_the_cut_s_bottom(kind):
    """Every filter is silent in the window, the healthy ones too (and a numpy one: the whole
    output goes to zero until the cut's bottom); then numpy, and Rust can be chosen again."""
    make = _rust_filters()[kind]
    feed = _feed()
    heard: list[str] = []
    backend.reset()
    backend.use(backend.RUST)
    backend.on_failure = heard.append
    f, healthy = make(), make()
    f.process(feed[0])
    healthy.process(feed[0])
    f._rust._panic_next()  # noqa: SLF001
    for x in feed[1:4]:
        assert not np.any(f.process(x))
        assert not np.any(healthy.process(x))
    assert len(heard) == 1
    backend.use(backend.NUMPY)  # the cut's bottom
    assert f._rust is None  # noqa: SLF001
    assert healthy._rust is None  # noqa: SLF001
    assert np.any(f.process(feed[4]))
    assert np.any(healthy.process(feed[4]))
    backend.clear_failure()
    backend.use(backend.RUST)
    assert f._rust is not None  # noqa: SLF001
    f.process(feed[5])
    assert backend.failure() is None


def test_a_rust_panic_while_skipping_is_a_fresh_numpy_filter():
    feed = _feed()
    backend.reset()
    backend.use(backend.RUST)
    f = _rust_filters()["partitioned"]()
    f.process(feed[0])
    f._rust._panic_next()  # noqa: SLF001
    f.skip(feed[1])
    assert "panicked" in backend.failure()
    f.process(feed[2])
    assert f._rust is None  # noqa: SLF001


class _ProxyRust:
    """The real Rust filter with one method replaced (a failure that is not a panic)."""

    def __init__(self, inner, **replaced):
        self._inner, self._replaced = inner, replaced

    def __getattr__(self, name):
        return self._replaced.get(name) or getattr(self._inner, name)


def _raiser(error):
    def raise_it(*_args):
        msg = "planted"
        raise error(msg)

    return raise_it


def _assert_fell_back_to_numpy(f, feed, heard, error):
    assert len(heard) == 1
    assert error.__name__ in heard[0]
    assert error.__name__ in backend.failure()
    for x in feed[:2]:  # the silent window: silence, nothing raised
        assert not np.any(f.process(x))
    backend.use(backend.NUMPY)  # the cut's bottom
    assert f._rust is None  # noqa: SLF001
    assert np.any(f.process(feed[2]))
    assert len(heard) == 1


@pytest.mark.parametrize("error", [ValueError, TypeError, AttributeError])
@pytest.mark.parametrize("failing", ["constructor", "set_state"])
@pytest.mark.parametrize("kind", list(_rust_filters()))
def test_a_rust_filter_that_fails_while_being_built_falls_back_to_numpy(monkeypatch, kind, error, failing):
    """Not a panic: argument conversion, or an older extension without the class or method. The
    session keeps its audio (silence until the cut's bottom, then numpy), the handler hears once."""
    name = {"streaming": "StreamingFIR", "partitioned": "PartitionedFIR"}[kind]
    real = getattr(aurasync_engine, name)
    if failing == "constructor":

        class Broken:
            def __init__(self, *_args):
                msg = "planted"
                raise error(msg)

        cls = Broken
    else:

        class Fake:
            def __new__(cls, *args):
                return _ProxyRust(real(*args), **{failing: _raiser(error)})

        cls = Fake
    monkeypatch.setattr(backend, "module", lambda: types.SimpleNamespace(**{name: cls}))
    feed = _feed()
    heard: list[str] = []
    backend.reset()
    backend.use(backend.RUST)
    backend.on_failure = heard.append
    f = _rust_filters()[kind]()
    assert not np.any(f.process(feed[0]))  # the Rust filter is built on the first block
    _assert_fell_back_to_numpy(f, feed[1:], heard, error)


@pytest.mark.parametrize("error", [ValueError, TypeError, AttributeError])
@pytest.mark.parametrize("call", ["set_taps", "replace_taps"])
def test_a_live_change_of_taps_that_fails_falls_back_to_numpy(error, call):
    feed = _feed()
    heard: list[str] = []
    backend.reset()
    backend.use(backend.RUST)
    backend.on_failure = heard.append
    f = _rust_filters()["streaming"]()
    f.process(feed[0])
    f._rust = _ProxyRust(f._rust, **{call: _raiser(error)})  # noqa: SLF001
    h = eq.fir(np.full(len(THIRDS), 1.0))
    if call == "set_taps":
        f.set_taps(h)
    else:
        f.taps = h
    assert np.array_equal(f.taps, h)
    _assert_fell_back_to_numpy(f, feed[1:], heard, error)


@pytest.mark.parametrize("kind", list(_rust_filters()))
def test_a_state_read_that_fails_at_the_switch_to_numpy_restarts_without_a_second_cut(kind):
    """The user chose numpy: a failed state read is logged, not a Rust failure (no `_fail`, no
    silence, no second call to the handler); the filter restarts in numpy and produces audio."""
    feed = _feed()
    heard: list[str] = []
    backend.reset()
    backend.use(backend.RUST)
    backend.on_failure = heard.append
    f = _rust_filters()[kind]()
    f.process(feed[0])
    f._rust = _ProxyRust(f._rust, state=_raiser(TypeError))  # noqa: SLF001
    backend.use(backend.NUMPY)
    assert f._rust is None  # noqa: SLF001
    assert backend.silent() is None
    assert backend.failure() is None
    assert heard == []
    assert np.any(f.process(feed[1]))
    assert heard == []


# -- lifetime -------------------------------------------------------------------------------


def test_a_filter_builds_its_rust_object_on_its_first_block_only():
    """`VirtualBass`'s two `PartitionedFIR`s (built when it is, and rebuilt only after they ran) and
    its own Rust object are never run while its harmonics are off: no Rust object, no registration."""
    backend.reset()
    backend.use(backend.RUST)
    f = PartitionedFIR(np.ones(100), 64)
    assert f._rust is None  # noqa: SLF001
    registered = len(backend._stages)  # noqa: SLF001
    vb = VirtualBass(SR, 90.0, None, BLOCK)
    for b in blocks(full_scale(SR, seed=26)):
        assert not np.any(vb.process(b))
    assert vb._rust is None  # noqa: SLF001
    assert vb._fir_band._rust is None  # noqa: SLF001
    assert len(backend._stages) == registered  # noqa: SLF001
    f.process(np.ones(64))
    assert f._rust is not None  # noqa: SLF001
    assert len(backend._stages) == registered + 1  # noqa: SLF001


def test_dead_stages_do_not_pile_up_in_the_backend():
    """Filters built and dropped block after block (with no `use` in between to sweep the
    registry) leave at most the live ones registered."""
    backend.reset()
    backend.use(backend.RUST)
    for _ in range(50):
        f = StreamingFIR(np.ones(8))
        f.process(np.ones(16))
        del f
        gc.collect()
    assert len(backend._stages) <= 1  # noqa: SLF001


@pytest.mark.parametrize("kind", list(_rust_filters()))
def test_the_backend_does_not_keep_a_dropped_filter_alive(kind):
    backend.reset()
    backend.use(backend.RUST)
    f = _rust_filters()[kind]()
    f.process(np.ones(BLOCK))
    ref = weakref.ref(f)
    del f
    gc.collect()
    assert ref() is None
    backend.use(backend.NUMPY)  # nobody left to tell; nothing fails


def test_capabilities_carry_the_fir_key():
    """No constant is shared with numpy; the key says the build has the filters. Version 2 is for
    a host from before the `api` key, which must refuse a build without the module's `read`."""
    assert aurasync_engine.capabilities()["fir"] == {"version": 2}


# -- inside the motor -----------------------------------------------------------------------


def _installation(eq_db=None):
    return Instalacion(
        parlantes=[
            Parlante("Go 4 A", "s0", pan=-0.7, ambiente=0.15, tipo="go4", ecualizacion_db=eq_db),
            Parlante("Go 4 B", "s1", pan=0.7, ambiente=0.15, tipo="go4", ecualizacion_db=eq_db),
            Parlante("JBL Charge 6", "s2", ambiente=0.0, tipo="charge6"),
        ]
    )


def _set(values, stage, **params):
    return values.with_change(chain.validate_set(stage, params=params))


@pytest.mark.parametrize(
    "values",
    [
        ChainValues(),
        _set(ChainValues().with_algorithm("bass", "protect"), "bass", harmonics_db=0.0),
        ChainValues().with_algorithm("bass", "crossover").with_algorithm("diffuse", "noise_tail"),
    ],
    ids=["eq", "protect-with-harmonics", "crossover-and-diffuse"],
)
def test_the_motor_with_the_rust_filters_matches_numpy(values):
    """The per-speaker EQ (with a live change of the curves at a cut), the bass stage's high-pass,
    all-pass and feed, the virtual bass and the diffuse tail, all through the Rust filters."""
    rng = np.random.default_rng(27)
    left, right = full_scale(3 * SR, seed=28), full_scale(3 * SR, seed=29)
    first = list(np.round(rng.uniform(0.0, 4.0, len(THIRDS)), 2))
    second = [list(np.round(rng.uniform(0.0, 6.0, len(THIRDS)), 2)) for _ in range(3)]
    outs = {}
    for engine in (backend.NUMPY, backend.RUST):
        backend.reset()
        backend.use(engine)
        m = motor.Motor(_installation(first), SR, semilla=1, ecualizar=True, chain=values, bloque=BLOCK)
        got = {p.nombre: [] for p in m.instalacion.parlantes}
        for k, i in enumerate(range(0, len(left), BLOCK)):
            if k == 10:
                for p, curve in zip(m.instalacion.parlantes, second, strict=True):
                    p.ecualizacion_db = curve
                m.actualizar_ecualizacion()
            for name, x in m.procesar(left[i : i + BLOCK], right[i : i + BLOCK]).items():
                got[name].append(x)
        rust_owned = [f._rust is not None for f in m._ecualizador.values()]  # noqa: SLF001
        assert all(rust_owned) == (engine == backend.RUST)
        assert any(rust_owned) == (engine == backend.RUST)
        # The whole inventory: the bass stage's filters and each diffuse tail's FIR own Rust too.
        assert_engine_owns(stage_filters(m._graves), engine)  # noqa: SLF001
        tails = {f"diffuse {n}": d._fir for n, d in m._difusion._tails.items()}  # noqa: SLF001
        if m._difusion.active:  # noqa: SLF001
            assert tails
        assert_engine_owns(tails, engine)
        assert backend.failure() is None
        outs[engine] = {name: np.concatenate(v) for name, v in got.items()}
    for name, x in outs[backend.NUMPY].items():
        assert np.any(x)
        diff = float(np.max(np.abs(outs[backend.RUST][name] - x)))
        assert diff <= TOLERANCE, f"{name}: max |diff| = {diff:.3g}"


# -- the calibration's EQ -------------------------------------------------------------------


def test_the_calibration_with_the_rust_eq_matches_numpy():
    """`Calibration` runs its stimulus through a `StreamingFIR` per speaker: Rust-backed under
    `engine=rust`, within 1e-9 of numpy's."""
    from aurasync.session import Calibration

    names = ["a", "b"]
    curves = {"a": list(np.linspace(-3.0, 4.0, len(THIRDS))), "b": None}
    outs = {}
    for engine in (backend.NUMPY, backend.RUST):
        backend.reset()
        backend.use(engine)
        cal = Calibration(names, 1.0, 0.1, SR, applied={"a": (2.5, -3.0), "b": (0.0, 0.0)}, applied_eq=curves)
        got = {n: [] for n in names}
        while not cal.emitted:
            for n, x in cal.next_blocks(1500).items():
                got[n].append(x)
        assert all((cal._eq[n]._rust is not None) == (engine == backend.RUST) for n in names)  # noqa: SLF001
        assert backend.failure() is None
        outs[engine] = {n: np.concatenate(v) for n, v in got.items()}
    for n in names:
        assert np.any(outs[backend.NUMPY][n])
        assert np.max(np.abs(outs[backend.RUST][n] - outs[backend.NUMPY][n])) <= TOLERANCE


# -- a torn Rust object is not called again -------------------------------------------------


class _Spy:
    """Stands in for a torn Rust object and records every call made on it."""

    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def call(*_args, **_kwargs):
            self.calls.append(name)

        return call


def test_a_queued_set_taps_does_not_call_the_torn_rust_object():
    """A panic, then a `set_taps` / `taps = ...` queued before the cut ran at its bottom: the
    object is torn, the host must not call it again (lib.rs), nor tell the handler twice."""
    backend.reset()
    backend.use(backend.RUST)
    told = []
    backend.on_failure = told.append
    f = StreamingFIR(np.ones(8))
    f.process(np.ones(64))
    f._rust._panic_next()  # noqa: SLF001
    assert not np.any(f.process(np.ones(64)))
    assert len(told) == 1
    spy = f._rust = _Spy()  # noqa: SLF001
    f.set_taps(np.ones(8) * 2)
    f.taps = np.ones(8) * 3
    assert spy.calls == []
    assert len(told) == 1
    backend.use(backend.NUMPY)  # the cut's bottom: a fresh numpy filter
    assert f._rust is None  # noqa: SLF001
    assert np.any(f.process(np.ones(64)))
