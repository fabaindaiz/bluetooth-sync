"""The virtual bass on Rust against the numpy stage (spec rust-engine §5, plan Task 11).

`VirtualBass` (`dsp/virtual_bass.py`) owns one Rust object when the engine is Rust, with its two
partitioned filters inside (one call per block): the band, the rectifier, the harmonics band, the
calibration and the gain's ramp. The design (`_filters`) stays numpy. This file checks, within 1e-9
absolute on full-scale signals, that the Rust object is the numpy stage on random blocks of odd
sizes, on sweeps of cutoff and level, on live level changes (the ramp), on the inputs of
`test_virtual_bass.py`; that an engine switch in the middle of a cycle (off, switch, on; on,
switch, off) moves the state exactly; that the `_dirty` rebuild of Task 10 is bit-identical to the
old always-rebuild; and that the stage really holds its Rust object.

The extension must be built into the test environment: this file fails, it does not skip, when
`aurasync_engine` is missing.
"""

from __future__ import annotations

from typing import ClassVar

import aurasync_engine
import numpy as np
import pytest

from aurasync.dsp import backend, virtual_bass
from aurasync.dsp.eq import PartitionedFIR
from aurasync.dsp.virtual_bass import VirtualBass

SR = 48000
BLOCK = 4096
TOLERANCE = 1e-9
CUTOFFS = (31.0, 60.0, 90.0, 150.0, 400.0)


@pytest.fixture(autouse=True)
def _clean_backend():
    backend.reset()
    yield
    backend.reset()


def full_scale(n, seed=0):
    return np.random.default_rng(seed).uniform(-1.0, 1.0, n)


def pink(n, seed=0):
    rng = np.random.default_rng(seed)
    spec = np.fft.rfft(rng.standard_normal(n))
    f = np.fft.rfftfreq(n, 1 / SR)
    spec[f >= 20] /= np.sqrt(f[f >= 20])
    spec[f < 20] = 0
    x = np.fft.irfft(spec, n)
    return 0.1 * x / np.std(x)


def odd_sizes(total, seed):
    rng = np.random.default_rng(seed)
    sizes = []
    while sum(sizes) < total:
        sizes.append(int(rng.choice([1, 7, 100, 513, 1500, 4095, 4096, 4097, 6000, int(rng.integers(1, 9000))])))
    sizes[-1] -= sum(sizes) - total
    if sizes[-1] <= 0:
        sizes.pop()
    return sizes


def cut(x, sizes):
    out, i = [], 0
    for n in sizes:
        out.append(x[i : i + n])
        i += n
    return out


def run(engine, x, sizes, *, cutoff=90.0, level=0.0, levels=None, block=BLOCK):
    """The harmonics of `x` block by block; `levels` (one per block, None = off) changes the level
    live before each block. Returns (signal, added_db per block, the stage)."""
    backend.reset()
    backend.use(engine)
    vb = VirtualBass(SR, cutoff, level, block)
    out, added = [], []
    for k, b in enumerate(cut(x, sizes)):
        if levels is not None:
            vb.harmonics_db = levels[k % len(levels)]
        out.append(vb.process(b))
        added.append(vb.added_db)
    assert backend.failure() is None
    return np.concatenate(out), added, vb


def assert_same_added(got, want):
    assert len(got) == len(want)
    for g, w in zip(got, want, strict=True):
        assert (g is None) == (w is None)
        if w is not None:
            assert g == pytest.approx(w, abs=1e-6)


def golden(x, sizes, **kw):
    want, want_added, vb_numpy = run(backend.NUMPY, x, sizes, **kw)
    got, got_added, vb_rust = run(backend.RUST, x, sizes, **kw)
    assert vb_numpy._rust is None  # noqa: SLF001
    assert float(np.max(np.abs(got - want))) <= TOLERANCE
    assert_same_added(got_added, want_added)
    return want, vb_rust


# -- the golden ----------------------------------------------------------------------------


@pytest.mark.parametrize("level", [-12.0, 0.0, 6.0, 12.0])
@pytest.mark.parametrize("cutoff", CUTOFFS)
def test_random_blocks_of_odd_sizes_match_numpy_over_a_sweep_of_cutoff_and_level(cutoff, level):
    x = full_scale(2 * SR, seed=int(cutoff))
    want, vb = golden(x, odd_sizes(len(x), seed=int(level) + 20), cutoff=cutoff, level=level)
    assert np.any(want)
    assert vb._rust is not None  # noqa: SLF001


@pytest.mark.parametrize("block", [1024, 4096])
def test_a_filter_block_other_than_the_service_s_matches_numpy(block):
    x = full_scale(SR, seed=3)
    golden(x, odd_sizes(len(x), seed=4), level=3.0, block=block)


@pytest.mark.parametrize("cutoff", [60.0, 90.0, 150.0])
@pytest.mark.parametrize("ratio", [0.25, 0.5, 1.0, 0.9, 1.1, 2.0, 3.5])
def test_full_scale_sines_at_and_below_the_cutoff_match_numpy(cutoff, ratio):
    t = np.arange(2 * SR) / SR
    x = np.sin(2 * np.pi * cutoff * ratio * t)
    want, _ = golden(x, odd_sizes(len(x), seed=int(cutoff)), cutoff=cutoff, level=0.0)
    assert np.any(want)


def test_silence_gives_silence_and_no_energy_ratio():
    x = np.zeros(SR)
    want, _ = golden(x, odd_sizes(len(x), seed=5), level=0.0)
    assert not np.any(want)
    _, added, _ = run(backend.RUST, x, odd_sizes(len(x), seed=5))
    assert all(a is None for a in added)


def test_the_inputs_of_the_numpy_tests_match():
    """test_virtual_bass.py's: pink noise at three levels, two tones, one tone, three block sizes."""
    for knob in (-6.0, 0.0, 6.0):
        x = pink(1 << 18)
        golden(x, [BLOCK] * (len(x) // BLOCK), level=knob)
    t = np.arange(4 * SR) / SR
    for x in (
        0.25 * np.sin(2 * np.pi * 50 * t) + 0.25 * np.sin(2 * np.pi * 70 * t),
        0.3 * np.sin(2 * np.pi * 50 * t),
    ):
        golden(x, [BLOCK] * (len(x) // BLOCK) + [len(x) % BLOCK], level=0.0)
    x = pink(48000, seed=2)
    for size in (1024, 4096, 999):
        sizes = [size] * (len(x) // size) + ([len(x) % size] if len(x) % size else [])
        want, _ = golden(x, sizes, level=3.0)
        assert np.max(np.abs(want - virtual_bass.harmonics(x, SR, 90.0, 3.0))) <= TOLERANCE


def test_one_block_longer_than_the_filter_block_and_empty_blocks_match_numpy():
    x = full_scale(3 * BLOCK + 17, seed=6)
    golden(x, [len(x)], level=0.0)
    golden(x, [0, 100, 0, len(x) - 100, 0], level=0.0)


def test_off_is_silence_and_turning_it_on_ramps_the_gain_on_both_engines():
    x = pink(3 * BLOCK, seed=5)
    for engine in (backend.NUMPY, backend.RUST):
        backend.reset()
        backend.use(engine)
        vb = VirtualBass(SR, 90.0)
        assert not np.any(vb.process(x[:BLOCK]))
        vb.harmonics_db = 0.0
        on = vb.process(x[BLOCK : 2 * BLOCK])
        steady = vb.process(x[2 * BLOCK :])
        assert np.abs(on[:64]).max() < 0.05 * np.abs(steady).max()
        vb.harmonics_db = None
        assert vb.process(x[:BLOCK]).any()
        assert not np.any(vb.process(x[:BLOCK]))


LEVEL_SEQUENCES = {
    "ramp-up-and-down": [0.0, 6.0, -6.0, 0.0, 12.0, -12.0],
    "off-on-off": [None, 0.0, 0.0, None, None, 3.0],
    "chain": [None, 0.0, 0.0, None, None, 3.0, 3.0, -6.0, None, 6.0],
}


@pytest.mark.parametrize("name", list(LEVEL_SEQUENCES))
def test_live_level_changes_match_numpy_on_whole_blocks_and_odd_ones(name):
    levels = LEVEL_SEQUENCES[name]
    x = full_scale(len(levels) * BLOCK, seed=7)
    want, _ = golden(x, [BLOCK] * len(levels), levels=levels, level=None)
    assert np.any(want)
    x = full_scale(40 * 1500, seed=8)
    golden(x, odd_sizes(len(x), seed=9), levels=levels, level=None)


# -- the carried tests of Task 10's review -------------------------------------------------------


def levels_to_block_run(engines, levels, x, sizes_for, switches):
    """Run `levels` (one per block, None = off) switching to `engines[i]` before block `switches[i]`;
    the whole thing as one VirtualBass."""
    backend.reset()
    backend.use(engines[0])
    vb = VirtualBass(SR, 90.0, None, BLOCK)
    out, at = [], 0
    for k, level in enumerate(levels):
        for engine, when in zip(engines[1:], switches, strict=True):
            if when == k:
                backend.use(engine)  # the cut's bottom: `use` is only called there
        vb.harmonics_db = level
        n = sizes_for[k]
        out.append(vb.process(x[at : at + n]))
        at += n
    assert backend.failure() is None
    return np.concatenate(out), vb


SWITCH_CASES = {
    "off-switch-on numpy to rust": ([backend.NUMPY, backend.RUST], [None, None, None, 0.0, 0.0, 3.0], [3]),
    "off-switch-on rust to numpy": ([backend.RUST, backend.NUMPY], [None, None, None, 0.0, 0.0, 3.0], [3]),
    "on-switch-off numpy to rust": ([backend.NUMPY, backend.RUST], [0.0, 0.0, 3.0, None, None, None, 0.0], [3]),
    "on-switch-off rust to numpy": ([backend.RUST, backend.NUMPY], [0.0, 0.0, 3.0, None, None, None, 0.0], [3]),
    "switch with the level change": ([backend.NUMPY, backend.RUST], [0.0, 0.0, 6.0, 6.0, None, 3.0], [2]),
    "switch back on the first block of a ramp": ([backend.RUST, backend.NUMPY], [None, 0.0, 0.0, -6.0], [1]),
    "switch while the level ramps to off": ([backend.RUST, backend.NUMPY], [0.0, 0.0, None, None, 0.0], [2]),
    "there and back": ([backend.NUMPY, backend.RUST, backend.NUMPY], [0.0, 0.0, 3.0, 3.0, None, 0.0, 6.0], [2, 5]),
    "back and there": ([backend.RUST, backend.NUMPY, backend.RUST], [0.0, 0.0, 3.0, 3.0, None, 0.0, 6.0], [2, 5]),
    "off for the whole switch": ([backend.NUMPY, backend.RUST], [None, None, None, None], [2]),
    "on the whole switch": ([backend.RUST, backend.NUMPY], [3.0, 3.0, 3.0, 3.0], [2]),
}


ODD = [4097, 100, 6000, 1, 4096, 513, 4095, 7000, 2000, 4096, 999]


@pytest.mark.parametrize("case", list(SWITCH_CASES))
@pytest.mark.parametrize("sizing", ["service", "odd"])
def test_a_switch_in_the_middle_of_a_cycle_equals_numpy_all_along(case, sizing):
    engines, levels, switches = SWITCH_CASES[case]
    sizes = [BLOCK] * len(levels) if sizing == "service" else ODD[: len(levels)]
    x = full_scale(sum(sizes), seed=11)
    want, _ = levels_to_block_run([backend.NUMPY], levels, x, sizes, [])
    got, vb = levels_to_block_run(engines, levels, x, sizes, switches)
    if any(level is not None for level in levels):
        assert np.any(want)
    assert float(np.max(np.abs(got - want))) <= TOLERANCE
    if engines[-1] == backend.NUMPY:
        assert vb._rust is None  # noqa: SLF001


def test_a_switch_to_numpy_hands_the_filters_their_histories_back():
    """After `use(NUMPY)` the numpy filters carry the Rust ones' state: they continue, alone, to
    what an all-numpy run gives."""
    x = full_scale(6 * BLOCK, seed=12)
    sizes = [BLOCK] * 6
    want, _ = levels_to_block_run([backend.NUMPY], [0.0] * 6, x, sizes, [])
    got, vb = levels_to_block_run([backend.RUST, backend.NUMPY], [0.0] * 6, x, sizes, [3])
    assert vb._rust is None  # noqa: SLF001
    assert vb._fir_band._rust is None  # noqa: SLF001
    assert float(np.max(np.abs(got - want))) <= TOLERANCE


class AlwaysRebuild(VirtualBass):
    """Task 10's reference: `_reset` as it was, two fresh filters on every call."""

    def _reset(self) -> None:
        self._fir_band = PartitionedFIR(self._band, self.block)
        self._fir_out = PartitionedFIR(self._out, self.block)


SEQUENCES = {
    "task-10": [-24.0, 0.0, 0.0, -24.0, -24.0, 3.0],
    "always-on": [0.0, 0.0, 0.0, 0.0],
    "always-off": [-24.0] * 5,
    "off-first-then-on": [-24.0, -24.0, -24.0, 6.0, 6.0, -24.0, 6.0],
    "on-then-off-for-long": [6.0, 6.0, -24.0, -24.0, -24.0, -24.0, 0.0],
    "level-moves": [0.0, 3.0, -6.0, -24.0, 12.0, -24.0, -24.0, 0.0],
}


def drive_levels(cls, levels, x, sizes):
    vb = cls(SR, 90.0, None, BLOCK)
    out = []
    for level, b in zip(levels, cut(x, sizes), strict=True):
        vb.harmonics_db = None if level <= -24.0 else level
        out.append(vb.process(b))
    return np.concatenate(out)


@pytest.mark.parametrize("seed", [0, 1, 2])
@pytest.mark.parametrize("sequence", list(SEQUENCES))
def test_the_dirty_flag_is_bit_identical_to_the_old_always_rebuild(sequence, seed):
    levels = SEQUENCES[sequence]
    sizes = [BLOCK] * len(levels) if seed == 0 else odd_sizes(len(levels) * 3000, seed)[: len(levels)]
    sizes += [1500] * (len(levels) - len(sizes))
    x = full_scale(sum(sizes), seed=seed + 40)
    backend.reset()
    backend.use(backend.NUMPY)
    old = drive_levels(AlwaysRebuild, levels, x, sizes)
    new = drive_levels(VirtualBass, levels, x, sizes)
    assert np.array_equal(new, old)  # bit for bit, not within a tolerance
    backend.reset()
    backend.use(backend.RUST)
    assert float(np.max(np.abs(drive_levels(VirtualBass, levels, x, sizes) - old))) <= TOLERANCE


def test_the_dirty_flag_builds_fewer_filters_and_the_old_reference_builds_every_block(monkeypatch):
    """The comparison above would pass vacuously if `AlwaysRebuild` stopped rebuilding."""
    built = []
    real = PartitionedFIR.__init__

    def counting(self, taps, block=4096):
        built.append(1)
        real(self, taps, block)

    monkeypatch.setattr(PartitionedFIR, "__init__", counting)
    backend.reset()
    backend.use(backend.NUMPY)
    levels = [-24.0] * 6
    x = full_scale(6 * BLOCK, seed=3)
    drive_levels(AlwaysRebuild, levels, x, [BLOCK] * 6)
    old = len(built)
    built.clear()
    drive_levels(VirtualBass, levels, x, [BLOCK] * 6)
    assert old == 2 * (1 + 6)
    assert len(built) == 2


# -- who holds what ------------------------------------------------------------------------------


def test_the_stage_owns_one_rust_object_and_its_numpy_filters_never_build_one():
    x = full_scale(2 * BLOCK, seed=13)
    for engine in (backend.NUMPY, backend.RUST):
        backend.reset()
        backend.use(engine)
        vb = VirtualBass(SR, 90.0, 0.0, BLOCK)
        vb.process(x[:BLOCK])
        assert (vb._rust is not None) == (engine == backend.RUST)  # noqa: SLF001
        assert vb._fir_band._rust is None  # noqa: SLF001
        assert vb._fir_out._rust is None  # noqa: SLF001


def test_harmonics_off_all_along_builds_no_rust_object_and_registers_nothing():
    backend.reset()
    backend.use(backend.RUST)
    registered = len(backend._stages)  # noqa: SLF001
    vb = VirtualBass(SR, 90.0, None, BLOCK)
    for b in cut(full_scale(SR, seed=14), odd_sizes(SR, seed=14)):
        assert not np.any(vb.process(b))
    assert vb._rust is None  # noqa: SLF001
    assert len(backend._stages) == registered  # noqa: SLF001


def test_capabilities_carry_the_virtual_bass_key():
    assert aurasync_engine.capabilities()["virtual_bass"] == {"version": 1}


# -- the failure paths --------------------------------------------------------------------------


def test_a_failing_construction_goes_through_the_soft_path(monkeypatch):
    backend.reset()
    backend.use(backend.RUST)
    told = []
    backend.on_failure = told.append
    x = full_scale(3 * BLOCK, seed=15)

    def refuses(*_args):
        msg = "an older build"
        raise AttributeError(msg)

    vb = VirtualBass(SR, 90.0, 0.0, BLOCK)
    monkeypatch.setattr(aurasync_engine, "VirtualBass", refuses, raising=False)
    assert not np.any(vb.process(x[:BLOCK]))  # silence until the cut's bottom
    assert len(told) == 1
    backend.use(backend.NUMPY)  # the cut's bottom
    fresh = VirtualBass(SR, 90.0, 0.0, BLOCK)
    assert np.max(np.abs(vb.process(x[BLOCK:]) - fresh.process(x[BLOCK:]))) <= TOLERANCE


def test_a_rust_panic_is_one_silent_block_then_a_fresh_numpy_stage():
    backend.reset()
    backend.use(backend.RUST)
    x = full_scale(4 * BLOCK, seed=16)
    vb = VirtualBass(SR, 90.0, 0.0, BLOCK)
    vb.process(x[:BLOCK])
    vb._rust._panic_next()  # noqa: SLF001
    assert not np.any(vb.process(x[BLOCK : 2 * BLOCK]))
    assert backend.failure() is not None
    after = vb.process(x[2 * BLOCK :])  # nobody listens: numpy from the next block
    fresh = VirtualBass(SR, 90.0, 0.0, BLOCK)
    assert vb._rust is None  # noqa: SLF001
    assert np.max(np.abs(after - fresh.process(x[2 * BLOCK :]))) <= TOLERANCE


def test_harmonics_off_in_the_silent_window_do_not_call_the_torn_rust_object():
    """Rust panics while the harmonics ramp up from 0 (`_current` stays 0); the harmonics go off
    inside the silent window: `_reset` must not call `reset()` on the torn object, and the
    handler was told once."""
    backend.reset()
    backend.use(backend.RUST)
    told = []
    backend.on_failure = told.append
    x = full_scale(3 * BLOCK, seed=17)
    vb = VirtualBass(SR, 90.0, None, BLOCK)
    assert vb._ready()  # noqa: SLF001 - builds the Rust object
    vb._rust._panic_next()  # noqa: SLF001
    vb.harmonics_db = 0.0
    assert vb._current == 0.0  # noqa: SLF001
    assert not np.any(vb.process(x[:BLOCK]))
    assert vb._current == 0.0  # noqa: SLF001 - ramping up from 0 never got anywhere
    assert len(told) == 1

    class Spy:
        calls: ClassVar[list[str]] = []

        def __getattr__(self, name):
            return lambda *_a, **_k: self.calls.append(name)

    spy = vb._rust = Spy()  # noqa: SLF001
    vb.harmonics_db = None
    assert not np.any(vb.process(x[BLOCK : 2 * BLOCK]))
    assert spy.calls == []
    assert len(told) == 1
    backend.use(backend.NUMPY)  # the cut's bottom
    assert vb._rust is None  # noqa: SLF001
