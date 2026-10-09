"""The crossover and the bass protection on the Rust FIRs, against the numpy stage (spec rust-engine
§5, plan Task 10).

Nothing here is ported to Rust beyond Task 7's `StreamingFIR`: the filter design (`impulses`,
`_butterworth`, `response`, `group_delay_dc_ms`) is design-time and stays numpy, and what the stage
does per block outside the convolution (the mid, two energies, a logarithm, the delay line) is
microseconds (experiment 20 §6). This file checks that the stage, built on the Rust filters, is the
numpy stage: within 1e-9 absolute on full-scale signals, on random blocks of odd sizes, on a sweep of
cutoffs and orders, on live changes of the cutoff and the harmonics, and that **each filter really
holds its Rust object** under `engine=rust` (a wiring regression that left one on numpy would still
be right, only slower, so the output alone cannot show it).

The extension must be built into the test environment: this file fails, it does not skip, when
`aurasync_engine` is missing.
"""

from __future__ import annotations

import types

import aurasync_engine
import numpy as np
import pytest

from aurasync import chain, chain_stages, motor
from aurasync.chain import ChainValues
from aurasync.config import Instalacion, Parlante
from aurasync.dsp import backend, crossover

SR = 48000
BLOCK = 4096
TOLERANCE = 1e-9
CUTOFFS = (40.0, 63.0, 100.0, 137.0, 200.0)


@pytest.fixture(autouse=True)
def _clean_backend():
    backend.reset()
    yield
    backend.reset()


def full_scale(n, seed=0):
    return np.random.default_rng(seed).uniform(-1.0, 1.0, n)


def odd_blocks(total, seed):
    """Block sizes of every kind: tiny, short, the service's, longer than a partition."""
    rng = np.random.default_rng(seed)
    sizes = []
    while sum(sizes) < total:
        sizes.append(int(rng.choice([1, 7, 100, 513, 1500, 4095, 4096, 4097, 6000, int(rng.integers(1, 9000))])))
    return sizes


def cut(x, sizes):
    out, i = [], 0
    for n in sizes:
        out.append(x[i : i + n])
        i += n
    return out


def _set(values, stage, **params):
    return values.with_change(chain.validate_set(stage, params=params))


def holds_rust(f):
    return f._rust is not None  # noqa: SLF001


def stage_filters(stage):
    """Every filter a `BassStage` owns that holds a Rust object under `engine=rust`, by role: the
    `StreamingFIR`s, and each small speaker's `VirtualBass` while its harmonics are on (it owns the
    two partitioned filters of the harmonics inside its one Rust object)."""
    found = {f"high {n}": b._fir for n, b in stage._high.items()}  # noqa: SLF001
    found.update({f"virtual bass {n}": vb for n, vb in stage._harmonics.items() if vb.harmonics_db is not None})  # noqa: SLF001
    found.update({f"allpass {n}": f for n, f in stage._allpass.items()})  # noqa: SLF001
    if stage._feed is not None:  # noqa: SLF001
        found["feed"] = stage._feed._fir  # noqa: SLF001
    return found


def assert_engine_owns(filters, engine):
    for role, f in filters.items():
        assert holds_rust(f) == (engine == backend.RUST), f"{role}: holds Rust = {holds_rust(f)} under {engine}"


# -- the two branches and the feed ------------------------------------------------------------


def run_branch(engine, make, x, sizes):
    backend.reset()
    backend.use(engine)
    f = make()
    out = [f.process(b) for b in cut(x, sizes)]
    assert_engine_owns({"branch": f._fir}, engine)  # noqa: SLF001
    assert backend.failure() is None
    return np.concatenate(out), f.removed_db


@pytest.mark.parametrize("order", crossover.ORDERS)
@pytest.mark.parametrize("cutoff", CUTOFFS)
@pytest.mark.parametrize("cls", [crossover.HighPass, crossover.LowPass])
def test_a_branch_matches_numpy_on_odd_blocks(cls, cutoff, order):
    x = full_scale(3 * SR, seed=int(cutoff))
    sizes = odd_blocks(len(x), seed=order)
    sizes[-1] -= sum(sizes) - len(x)
    if sizes[-1] <= 0:
        sizes.pop()
    want, want_db = run_branch(backend.NUMPY, lambda: cls(SR, cutoff, order), x, sizes)
    got, got_db = run_branch(backend.RUST, lambda: cls(SR, cutoff, order), x, sizes)
    assert np.any(want)
    assert float(np.max(np.abs(got - want))) <= TOLERANCE
    assert got_db == pytest.approx(want_db, abs=1e-9)


@pytest.mark.parametrize("cls", [crossover.HighPass, crossover.LowPass])
def test_a_branch_matches_numpy_on_the_inputs_of_its_own_tests(cls):
    """test_crossover.py's: blocks of 1024 and 4096 of white noise, a sine and silence."""
    t = np.arange(SR) / SR
    for x, size in (
        (np.random.default_rng(0).standard_normal(SR), 1024),
        (np.random.default_rng(0).standard_normal(SR), BLOCK),
        (np.sin(2 * np.pi * 50 * t), BLOCK),
        (np.zeros(SR), BLOCK),
    ):
        sizes = [size] * (len(x) // size) + ([len(x) % size] if len(x) % size else [])
        want, _ = run_branch(backend.NUMPY, lambda: cls(SR, 100.0), x, sizes)
        got, _ = run_branch(backend.RUST, lambda: cls(SR, 100.0), x, sizes)
        assert float(np.max(np.abs(got - want))) <= TOLERANCE


@pytest.mark.parametrize("cutoff", CUTOFFS)
def test_the_bass_feed_matches_numpy_and_holds_its_filter(cutoff):
    left, right = full_scale(2 * SR, seed=3), full_scale(2 * SR, seed=4)
    sizes = odd_blocks(len(left), seed=5)
    sizes[-1] -= sum(sizes) - len(left)
    if sizes[-1] <= 0:
        sizes.pop()
    outs = {}
    for engine in (backend.NUMPY, backend.RUST):
        backend.reset()
        backend.use(engine)
        feed = crossover.BassFeed(SR, cutoff)
        outs[engine] = np.concatenate(
            [feed.process(a, b) for a, b in zip(cut(left, sizes), cut(right, sizes), strict=True)]
        )
        assert_engine_owns({"feed": feed._lp._fir}, engine)  # noqa: SLF001
    assert np.any(outs[backend.NUMPY])
    assert float(np.max(np.abs(outs[backend.RUST] - outs[backend.NUMPY]))) <= TOLERANCE


def test_an_order_that_is_not_offered_is_refused_before_any_engine():
    for order in (2, 3):
        for engine in (backend.NUMPY, backend.RUST):
            backend.reset()
            backend.use(engine)
            with pytest.raises(ValueError, match="order"):
                crossover.HighPass(SR, 100.0, order=order)


# -- the whole bass stage ---------------------------------------------------------------------
# (the chain offers cutoffs of 60 to 150 Hz; the branches above are swept over 40 to 200 Hz)

SPEAKERS = [("Go 4 A", "go4"), ("Go 4 B", "go4"), ("JBL Charge 6", "charge6")]


def new_stage(values, speakers=SPEAKERS, block=BLOCK):
    return chain_stages.BassStage(values, speakers, SR, block)


def drive(stage, left, right, sizes, delays=None):
    """The motor's call sequence per block: `feed`, then per speaker `before_delay` and `process`.
    Returns {speaker: signal} and the metrics after every block."""
    outs = {n: [] for n, _ in SPEAKERS}
    metrics = []
    for k, (a, b) in enumerate(zip(cut(left, sizes), cut(right, sizes), strict=True)):
        feed = stage.feed(a, b, 0 if delays is None else delays[k % len(delays)])
        for name, _ in SPEAKERS:
            x = stage.before_delay(name, a if name != "Go 4 B" else b, feed)
            outs[name].append(stage.process(name, x))
        metrics.append(stage.metrics())
    return {n: np.concatenate(v) for n, v in outs.items()}, metrics


def assert_same_metrics(got, want):
    assert got["to"] == want["to"]
    for key in ("removed_db", "harmonics_db"):
        assert got[key].keys() == want[key].keys()
        for name, v in want[key].items():
            assert (got[key][name] is None) == (v is None), (key, name)
            if v is not None:
                assert got[key][name] == pytest.approx(v, abs=1e-6), (key, name)
    assert (got["feed_dbfs"] is None) == (want["feed_dbfs"] is None)
    if want["feed_dbfs"] is not None:
        assert got["feed_dbfs"] == pytest.approx(want["feed_dbfs"], abs=0.11)  # rounded to 0.1 dB


def stage_golden(values, *, delays=None, seed=0, seconds=3):
    left, right = full_scale(seconds * SR, seed=seed), full_scale(seconds * SR, seed=seed + 100)
    sizes = odd_blocks(len(left), seed=seed)
    sizes[-1] -= sum(sizes) - len(left)
    if sizes[-1] <= 0:
        sizes.pop()
    runs = {}
    for engine in (backend.NUMPY, backend.RUST):
        backend.reset()
        backend.use(engine)
        stage = new_stage(values)
        runs[engine] = drive(stage, left, right, sizes, delays)
        assert_engine_owns(stage_filters(stage), engine)
        assert stage_filters(stage)  # the comparison is not between two stages with nothing in them
        if stage.algorithm == "protect" and float(values.param("bass", "harmonics_db")) > chain_stages.HARMONICS_OFF_DB:
            # every small speaker's harmonics generator is in the inventory, not only the first
            assert {"virtual bass Go 4 A", "virtual bass Go 4 B"} <= stage_filters(stage).keys()
        assert backend.failure() is None
    (want, want_metrics), (got, got_metrics) = runs[backend.NUMPY], runs[backend.RUST]
    for name in want:
        assert float(np.max(np.abs(got[name] - want[name]))) <= TOLERANCE, name
    assert_same_metrics(got_metrics[-1], want_metrics[-1])
    return want


@pytest.mark.parametrize("order", [4, 8])
@pytest.mark.parametrize("cutoff", [60.0, 90.0, 150.0])
@pytest.mark.parametrize("harmonics", [-24.0, 0.0])
def test_protect_matches_numpy(cutoff, order, harmonics):
    values = _set(
        ChainValues().with_algorithm("bass", "protect"), "bass", cutoff_hz=cutoff, order=order, harmonics_db=harmonics
    )
    want = stage_golden(values, seed=int(cutoff) + order)
    assert np.any(want["Go 4 A"])
    assert np.any(want["JBL Charge 6"])


@pytest.mark.parametrize("cutoff", [60.0, 100.0, 150.0])
@pytest.mark.parametrize("delays", [None, [0, 37, 37, 0, 4096 + 5]], ids=["no-delay", "delay-changes"])
def test_crossover_matches_numpy(cutoff, delays):
    values = _set(ChainValues().with_algorithm("bass", "crossover"), "bass", cutoff_hz=cutoff)
    want = stage_golden(values, delays=delays, seed=int(cutoff))
    assert np.any(want["Go 4 A"])
    assert np.any(want["JBL Charge 6"])


def test_harmonics_going_on_and_off_midstream_match_numpy_and_hold_the_virtual_bass():
    """The hand-off to `VirtualBass`: off (nothing runs), on (its two partitioned filters run on
    Rust), off again (they restart at rest), on once more."""
    left = full_scale(6 * BLOCK, seed=8)
    values = _set(ChainValues().with_algorithm("bass", "protect"), "bass", harmonics_db=0.0)
    levels = [-24.0, 0.0, 0.0, -24.0, -24.0, 3.0]
    outs = {}
    for engine in (backend.NUMPY, backend.RUST):
        backend.reset()
        backend.use(engine)
        stage = new_stage(values)
        got = []
        for k, level in enumerate(levels):
            stage.set_harmonics(level)
            s = slice(k * BLOCK, (k + 1) * BLOCK)
            got.append(stage.process("Go 4 A", left[s]))
            if level > chain_stages.HARMONICS_OFF_DB:
                assert holds_rust(stage._harmonics["Go 4 A"]) == (engine == backend.RUST)  # noqa: SLF001
        outs[engine] = np.concatenate(got)
        assert backend.failure() is None
    assert float(np.max(np.abs(outs[backend.RUST] - outs[backend.NUMPY]))) <= TOLERANCE


def test_the_off_stage_and_a_missing_bass_speaker_hold_no_filter():
    for values, speakers in (
        (ChainValues(), SPEAKERS),
        (ChainValues().with_algorithm("bass", "crossover"), SPEAKERS[:2]),
    ):
        backend.reset()
        backend.use(backend.RUST)
        stage = new_stage(values, speakers)
        assert not stage.active
        x = full_scale(BLOCK, seed=1)
        assert stage.process("Go 4 A", x) is x
        assert stage.feed(x, x, 0) is None
        assert backend.failure() is None


# -- a planted fault must be seen --------------------------------------------------------------


def test_the_ownership_check_sees_a_filter_left_on_numpy():
    """Wiring regression: the all-pass runs numpy's body and never builds a Rust object. Its output
    is right; only the ownership assertion can tell."""
    backend.reset()
    backend.use(backend.RUST)
    values = ChainValues().with_algorithm("bass", "crossover")
    stage = new_stage(values)
    allpass = stage._allpass["JBL Charge 6"]  # noqa: SLF001
    allpass.process = allpass._process_numpy  # noqa: SLF001
    x = full_scale(BLOCK, seed=2)
    drive(stage, x, x, [BLOCK])
    with pytest.raises(AssertionError, match="allpass"):
        assert_engine_owns(stage_filters(stage), backend.RUST)


def test_the_ownership_check_sees_a_virtual_bass_left_on_numpy_on_any_speaker():
    """Wiring regression on the second small speaker: its harmonics generator never builds its Rust
    object. Its output is right; only the inventory can tell."""
    backend.reset()
    backend.use(backend.RUST)
    values = _set(ChainValues().with_algorithm("bass", "protect"), "bass", harmonics_db=0.0)
    stage = new_stage(values)
    stage._harmonics["Go 4 B"]._build_rust = lambda: None  # noqa: SLF001
    x = full_scale(BLOCK, seed=2)
    drive(stage, x, x, [BLOCK])
    with pytest.raises(AssertionError, match="virtual bass Go 4 B"):
        assert_engine_owns(stage_filters(stage), backend.RUST)


# -- a live change of the cutoff -----------------------------------------------------------------


def vars_of(module):
    return {n: getattr(module, n) for n in dir(module)}


def installation():
    return Instalacion(
        parlantes=[
            Parlante("Go 4 A", "s0", pan=-0.7, ambiente=0.15, tipo="go4"),
            Parlante("Go 4 B", "s1", pan=0.7, ambiente=0.15, tipo="go4"),
            Parlante("JBL Charge 6", "s2", ambiente=0.0, tipo="charge6"),
        ]
    )


@pytest.mark.parametrize("algorithm", ["protect", "crossover"])
def test_a_live_cutoff_change_through_the_motor_matches_numpy(algorithm):
    """The cutoff is a `cut`-class change: the motor builds the new stage outside the block (its
    Rust objects are made on the new stage's first block, through `backend.built`) and, with the
    default `transition`, crossfades it in, then keeps only it. Both engines hear the same."""
    left, right = full_scale(4 * SR, seed=21), full_scale(4 * SR, seed=22)
    values = _set(ChainValues().with_algorithm("bass", algorithm), "bass", cutoff_hz=80.0)
    outs = {}
    for engine in (backend.NUMPY, backend.RUST):
        backend.reset()
        backend.use(engine)
        m = motor.Motor(installation(), SR, semilla=1, ecualizar=False, chain=values, bloque=BLOCK)
        got = {p.nombre: [] for p in m.instalacion.parlantes}
        old = m._graves  # noqa: SLF001
        swapped = False
        for k, i in enumerate(range(0, len(left), BLOCK)):
            if k == 12:
                assert m.aplicar_cadena(_set(values, "bass", cutoff_hz=130.0)) == "crossfade"
            for name, x in m.procesar(left[i : i + BLOCK], right[i : i + BLOCK]).items():
                got[name].append(x)
            swapped = swapped or m._graves is not old  # noqa: SLF001
        assert swapped, "the transition never started"
        assert not m.en_corte
        assert isinstance(m._graves, chain_stages.BassStage)  # noqa: SLF001  (resolved to the new one)
        assert_engine_owns(stage_filters(m._graves), engine)  # noqa: SLF001
        assert backend.failure() is None
        outs[engine] = {n: np.concatenate(v) for n, v in got.items()}
    for name, x in outs[backend.NUMPY].items():
        assert np.any(x)
        assert float(np.max(np.abs(outs[backend.RUST][name] - x))) <= TOLERANCE, name


def test_a_failing_construction_of_the_new_stage_goes_through_the_soft_path(monkeypatch):
    """After a cutoff change the new stage's filters are built on its first block. If the Rust
    constructor raises, the failure is reported once (a cut to numpy), that block is silence, and
    from the cut's bottom the stage is numpy and produces audio."""

    class Failing:
        def __new__(cls, *_args):
            msg = "planted"
            raise ValueError(msg)

    heard: list[str] = []
    backend.reset()
    backend.use(backend.RUST)
    backend.on_failure = heard.append
    old = new_stage(ChainValues().with_algorithm("bass", "protect"))
    x = full_scale(BLOCK, seed=6)
    assert np.any(old.process("Go 4 A", x))
    fresh = new_stage(_set(ChainValues().with_algorithm("bass", "protect"), "bass", cutoff_hz=120.0))
    monkeypatch.setattr(
        backend,
        "module",
        lambda: types.SimpleNamespace(**{**vars_of(aurasync_engine), "StreamingFIR": Failing}),
    )
    assert not np.any(fresh.process("Go 4 A", x))
    assert len(heard) == 1
    assert "ValueError" in heard[0]
    assert backend.failure() is not None
    assert not np.any(fresh.process("Go 4 A", x))  # the silent window
    monkeypatch.undo()
    backend.use(backend.NUMPY)  # the cut's bottom
    assert not holds_rust(stage_filters(fresh)["high Go 4 A"])
    y = fresh.process("Go 4 A", x)
    assert np.any(y)
    backend.reset()
    backend.use(backend.NUMPY)
    want = new_stage(_set(ChainValues().with_algorithm("bass", "protect"), "bass", cutoff_hz=120.0)).process(
        "Go 4 A", x
    )
    assert float(np.max(np.abs(y - want))) <= TOLERANCE
    assert len(heard) == 1
