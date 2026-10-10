"""The decorrelator's per-block convolution through `eq.StreamingFIR` (spec rust-engine, Task 8).

The motor holds one `StreamingFIR` per speaker (`_decorreladores`, None without a filter), so the
convolution runs in Rust when the engine is Rust and in numpy (FFT overlap-add) otherwise. The
bank's design stays numpy: it runs at a cut. This file checks, within 1e-9 absolute, that the two
engines play the same samples with the decorrelator on, through a bank change at a cut's bottom,
through a `classic -> direct -> classic` switch, and through an engine switch between blocks; that
the tails really empty where they must; and that a Rust failure is one silent block and then numpy.
"""

from __future__ import annotations

import aurasync_engine  # noqa: F401 - fails, does not skip, when the extension is not built
import numpy as np
import pytest

from aurasync import motor
from aurasync.chain import ChainValues
from aurasync.dsp import backend
from aurasync.dsp.eq import StreamingFIR
from tests import golden_motor

TOLERANCE = 1e-9
BLOCK = 1024


@pytest.fixture(autouse=True)
def _clean_backend():
    backend.reset()
    yield
    backend.reset()


def _signal(blocks):
    rng = np.random.default_rng(91)
    n = blocks * BLOCK
    return 0.3 * rng.standard_normal(n), 0.3 * rng.standard_normal(n)


def _motor(values=None):
    return motor.Motor(golden_motor.installation(), golden_motor.SR, chain=values or ChainValues(), bloque=BLOCK)


def _play(m, left, right, start, stop):
    """Blocks `start`..`stop` of the signal through `m`: {speaker: concatenated samples}."""
    out = {p.nombre: [] for p in m.instalacion.parlantes}
    for k in range(start, stop):
        s = slice(k * BLOCK, (k + 1) * BLOCK)
        for name, x in m.procesar(left[s], right[s]).items():
            out[name].append(x)
    return {name: np.concatenate(v) for name, v in out.items()}


def _same(got, want):
    for name, x in want.items():
        assert np.any(x)
        diff = float(np.max(np.abs(got[name] - x)))
        assert diff <= TOLERANCE, f"{name}: max |diff| = {diff:.3g}"


def _on(engine, script):
    backend.reset()
    backend.use(engine)
    result = script()
    assert backend.failure() is None
    return result


def _both(script):
    want = _on(backend.NUMPY, script)
    got = _on(backend.RUST, script)
    _same(got, want)


def test_every_speaker_s_decorrelator_owns_a_rust_filter_after_one_block():
    backend.use(backend.RUST)
    m = _motor()
    left, right = _signal(1)
    _play(m, left, right, 0, 1)
    assert set(m._decorreladores) == {"L", "R", "B"}  # noqa: SLF001
    assert all(f._rust is not None for f in m._decorreladores.values())  # noqa: SLF001


def test_without_the_decorrelator_there_are_no_filters():
    m = motor.Motor(golden_motor.installation(), golden_motor.SR, decorrelar=False)
    assert m._decorreladores == {"L": None, "R": None, "B": None}  # noqa: SLF001


def test_the_numpy_engine_owns_no_rust_filter():
    backend.use(backend.NUMPY)
    m = _motor()
    left, right = _signal(2)
    _play(m, left, right, 0, 2)
    assert all(f._rust is None for f in m._decorreladores.values())  # noqa: SLF001


def test_procesar_completo_matches_numpy_with_the_decorrelator_on():
    left, right = golden_motor.signal()

    def script():
        m = motor.Motor(golden_motor.installation(), golden_motor.SR, bloque=4096)
        return motor.procesar_completo(m, left, right, 4096)

    _both(script)


def test_a_bank_change_at_a_cut_s_bottom_matches_numpy():
    left, right = _signal(14)

    def script():
        m = _motor()
        first = _play(m, left, right, 0, 4)
        values = m.cadena.with_change(_set_seed(7))
        m.aplicar_cadena(values)
        second = _play(m, left, right, 4, 14)
        return {n: np.concatenate([first[n], second[n]]) for n in first}

    _both(script)


def _set_seed(seed):
    from aurasync import chain

    return chain.validate_set("decorrelate", params={"seed": seed})


def test_a_new_bank_starts_from_silence_in_both_engines():
    """The cut's bottom builds fresh filters: no tail of the old bank leaks into the new one."""
    for engine in (backend.NUMPY, backend.RUST):
        backend.reset()
        backend.use(engine)
        m = _motor()
        left, right = _signal(4)
        _play(m, left, right, 0, 4)
        before = dict(m._decorreladores)  # noqa: SLF001
        m.aplicar_cadena(m.cadena.with_change(_set_seed(5)))
        m.cortar()
        _play(m, left, right, 0, 12)
        after = m._decorreladores  # noqa: SLF001
        assert all(after[n] is not before[n] for n in before)


def test_classic_direct_classic_empties_the_tails_in_both_engines_alike():
    left, right = _signal(24)

    def script():
        m = _motor()
        parts = [_play(m, left, right, 0, 4)]
        m.aplicar_cadena(m.cadena.with_algorithm("spatial", "direct"))
        parts.append(_play(m, left, right, 4, 12))
        assert m.render == motor.DIRECT
        m.aplicar_cadena(m.cadena.with_algorithm("spatial", "classic"))
        parts.append(_play(m, left, right, 12, 24))
        assert m.render == "classic"
        return {n: np.concatenate([p[n] for p in parts]) for n in parts[0]}

    _both(script)


def test_the_switch_to_direct_builds_fresh_filters():
    for engine in (backend.NUMPY, backend.RUST):
        backend.reset()
        backend.use(engine)
        m = _motor()
        left, right = _signal(12)
        _play(m, left, right, 0, 4)
        before = dict(m._decorreladores)  # noqa: SLF001
        m.aplicar_cadena(m.cadena.with_algorithm("spatial", "direct"))
        _play(m, left, right, 4, 12)
        assert m.render == motor.DIRECT
        assert all(m._decorreladores[n] is not before[n] for n in before)  # noqa: SLF001


def test_reiniciar_builds_fresh_filters_with_empty_tails():
    backend.use(backend.NUMPY)
    m = _motor()
    left, right = _signal(8)
    _play(m, left, right, 0, 8)
    old = m._decorreladores["L"]  # noqa: SLF001
    assert np.any(old._tail)  # noqa: SLF001
    m.reiniciar()
    fresh = m._decorreladores["L"]  # noqa: SLF001
    assert fresh is not old
    assert not np.any(fresh._tail)  # noqa: SLF001
    assert len(fresh._tail) == len(m._filtros["L"]) - 1  # noqa: SLF001


@pytest.mark.parametrize(("first", "then"), [(backend.NUMPY, backend.RUST), (backend.RUST, backend.NUMPY)])
def test_switching_engine_between_blocks_changes_no_sample(first, then):
    left, right = _signal(10)

    def straight():
        m = _motor()
        return _play(m, left, right, 0, 10)

    def switched():
        m = _motor()
        a = _play(m, left, right, 0, 5)
        backend.use(then)
        b = _play(m, left, right, 5, 10)
        return {n: np.concatenate([a[n], b[n]]) for n in a}

    want = _on(first, straight)
    got = _on(first, switched)
    assert backend.active() == then
    _same(got, want)


def test_a_planted_panic_is_one_silent_block_then_numpy_from_the_cut():
    backend.use(backend.RUST)
    m = _motor()
    left, right = _signal(8)
    _play(m, left, right, 0, 2)
    name = "R"
    m._decorreladores[name]._rust._panic_next()  # noqa: SLF001
    block = np.random.default_rng(3).standard_normal(BLOCK)
    assert not np.any(m._convolucionar(m._decorreladores, name, block))  # noqa: SLF001
    assert "panicked" in backend.failure()
    assert backend.active() == backend.NUMPY
    # From the next block on: a fresh numpy filter (the Rust state may be torn).
    fresh = StreamingFIR(m._filtros[name])  # noqa: SLF001
    backend.reset()
    backend.use(backend.NUMPY)
    nxt = np.random.default_rng(4).standard_normal(BLOCK)
    got = m._convolucionar(m._decorreladores, name, nxt)  # noqa: SLF001
    assert m._decorreladores[name]._rust is None  # noqa: SLF001
    assert np.array_equal(got, fresh.process(nxt))


def test_a_panic_in_the_running_motor_is_heard_once_and_the_motor_goes_on():
    heard: list[str] = []
    backend.use(backend.RUST)
    backend.on_failure = heard.append
    m = _motor()
    left, right = _signal(6)
    _play(m, left, right, 0, 2)
    m._decorreladores["L"]._rust._panic_next()  # noqa: SLF001
    _play(m, left, right, 2, 3)
    assert len(heard) == 1
    assert "panicked" in backend.failure()
