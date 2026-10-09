"""The ambience extractor in Rust (`aurasync_engine.AmbienceExtractor`) against the numpy oracle
(`dsp/ambience.py`, `Extractor`), through the stage's own dispatch (spec rust-engine §5, plan
Task 6).

The extension must be built into the test environment: this file fails, it does not skip, when
`aurasync_engine` is missing.

Golden: with `engine=rust` the extractor's mono ambience is within 1e-9 absolute of numpy's
(d-7c8794-36dde5), on the inputs of the stage's own tests (tests/test_ambience.py), on full-scale
signals, on random blocks of odd sizes, on parameter and STFT-size sweeps and on live changes. The
live switch moves the extractor's state at the cut's bottom (exact: a continued run equals the one
that never switched), and a Rust failure gives silence for that block, then numpy from a fresh
extractor. The extractor runs once per input, not per speaker.
"""

from __future__ import annotations

import gc
import types
import weakref

import aurasync_engine
import numpy as np
import pytest

from aurasync.chain import ChainValues
from aurasync.config import Instalacion, Parlante
from aurasync.dsp import ambience, backend
from aurasync.dsp.ambience import Extractor, Parametros
from aurasync.motor import Motor

SR = 48000
BLOCK = 4096
TOLERANCE = 1e-9


# -- running the extractor with each engine -------------------------------------------------


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
    """The extractor built by `make()` with `engine` active, fed `feed` block by block;
    `between(k, ex)` runs before block k (a live change). Returns the whole output."""
    backend.reset()
    backend.use(engine)
    ex = make()
    out = []
    for k, (left, right) in enumerate(feed):
        if between is not None:
            between(k, ex)
        y = ex.procesar(left, right)
        assert y.shape == (len(left),)
        out.append(y)
    # Rust really ran the extractor, and never failed over to numpy.
    assert (ex._rust is not None) == (engine == backend.RUST)  # noqa: SLF001
    assert backend.failure() is None
    return np.concatenate(out) if out else np.zeros(0)


def assert_golden(make, feed, between=None, *, silent_ok=False):
    expected = run(backend.NUMPY, make, feed, between)
    got = run(backend.RUST, make, feed, between)
    assert got.shape == expected.shape
    diff = float(np.max(np.abs(got - expected), initial=0.0))
    assert diff <= TOLERANCE, f"max |diff| = {diff:.3g}"
    # The comparison is not between two silences.
    assert silent_ok or np.any(expected)
    return expected


def extractor(params=None, n_fft=ambience.N_FFT, hop=ambience.SALTO):
    return lambda: Extractor(params, n_fft, hop)


# -- signals --------------------------------------------------------------------------------


def noise(seed, n=SR):
    return np.random.default_rng(seed).standard_normal(n)


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


# -- the inputs of the stage's own tests ----------------------------------------------------


def test_mono_independent_and_one_sided_inputs_match_numpy():
    """tests/test_ambience.py's three cases: L = R, L and R independent, a source on one side."""
    x = noise(1)
    assert_golden(extractor(), blocks(x, x), silent_ok=True)
    assert_golden(extractor(), blocks(noise(2), noise(3)))
    assert_golden(extractor(), blocks(noise(4), np.zeros(SR)), silent_ok=True)
    assert_golden(extractor(Parametros(energia_minima=0.0)), blocks(noise(4), np.zeros(SR)))


def test_the_stream_of_1024_blocks_matches_numpy():
    """tests/test_ambience.py's streaming case (blocks of 1024) and its latency case (512)."""
    assert_golden(extractor(), blocks(noise(20), noise(21), [1024]))
    assert_golden(extractor(), blocks(noise(30, 512), noise(31, 512)), silent_ok=True)


@pytest.mark.parametrize(
    "params",
    [Parametros(mu0=0.3, sigma=8.0), Parametros(sigma=1.0), Parametros(sigma=8.0), Parametros(mu0=0.0, mu1=1.0)],
    ids=["floor", "soft", "steep", "range"],
)
def test_the_curves_of_the_stage_s_tests_match_numpy(params):
    assert_golden(extractor(params), blocks(*music(seed=5)))


def test_a_reset_mid_stream_matches_numpy():
    """`reiniciar` (the motor calls it when it restarts) clears the Rust state too."""
    x, y = noise(32, 2 * BLOCK), noise(33, 2 * BLOCK)
    x, y = np.concatenate([x, x[:BLOCK]]), np.concatenate([y, y[:BLOCK]])

    def between(k, ex):
        if k == 2:
            ex.reiniciar()

    expected = assert_golden(extractor(), blocks(x, y), between)
    # After the reset the stream starts over: the third block (the first input again) gives the
    # first output again.
    assert np.array_equal(expected[2 * BLOCK :], expected[:BLOCK])


# -- full scale, odd blocks, sweeps ---------------------------------------------------------


@pytest.mark.parametrize("seed", range(3))
def test_full_scale_and_music_match_numpy(seed):
    assert_golden(extractor(), blocks(*full_scale(seed=seed)))
    assert_golden(extractor(), blocks(*music(seed=seed + 10, room=0.3)))


@pytest.mark.parametrize(
    "sizes",
    [[1, 7, 511, 512, 513], [2047, 2048, 2049], [4095, 4097, 0, 4096], [10_000, 3, 9_999], [6_000]],
    ids=["tiny", "around-n_fft", "around-block", "large", "6000"],
)
def test_odd_block_sizes_match_numpy(sizes):
    assert_golden(extractor(), blocks(*music(seconds=0.8, seed=9), sizes))


def test_random_block_sizes_match_numpy():
    rng = np.random.default_rng(11)
    sizes = [int(x) for x in rng.integers(1, 9000, 40)]
    assert_golden(extractor(), blocks(*full_scale(seconds=2.0, seed=12), sizes))


@pytest.mark.parametrize("seed", range(10))
def test_a_parameter_sweep_matches_numpy(seed):
    rng = np.random.default_rng(200 + seed)
    mu0 = float(rng.uniform(0.0, 0.6))
    params = Parametros(
        lam=float(rng.uniform(0.0, 0.99)),
        umbral=float(rng.uniform(0.0, 1.0)),
        mu0=mu0,
        mu1=float(rng.uniform(mu0, 1.2)),
        sigma=float(rng.uniform(0.5, 10.0)),
        energia_minima=float(rng.uniform(0.0, 0.9)),
    )
    sizes = [int(x) for x in rng.integers(100, 6000, 8)]
    room = float(rng.uniform(0.1, 2.0))
    assert_golden(extractor(params), blocks(*music(seconds=1.0, seed=seed, room=room), sizes))


@pytest.mark.parametrize(("n_fft", "hop"), [(1024, 256), (512, 128), (1000, 250), (513, 171), (2048, 1024)])
def test_other_stft_sizes_match_numpy(n_fft, hop):
    """`Extractor` takes the STFT's length and hop; odd lengths have no Nyquist bin."""
    assert_golden(extractor(None, n_fft, hop), blocks(*music(seconds=0.6, seed=n_fft), [777, 4096]))


def test_extreme_signals_match_numpy():
    n = SR
    t = np.arange(n) / SR
    bin_tone = np.sin(2 * np.pi * (SR / ambience.N_FFT * 37) * t)  # exactly on a bin
    square = np.sign(np.sin(2 * np.pi * 440 * t))
    impulses = np.zeros(n)
    impulses[::3001] = 1.0
    zeros = np.zeros(n)
    for left, right in (
        (bin_tone, -bin_tone),
        (square, square),
        (impulses, np.roll(impulses, 7)),
        (square, bin_tone),
        (bin_tone, np.cos(2 * np.pi * (SR / ambience.N_FFT * 37) * t)),
    ):
        assert_golden(extractor(), blocks(left, right), silent_ok=True)
    assert_golden(extractor(), blocks(zeros, zeros), silent_ok=True)


def test_at_and_around_the_minimum_energy_ratio_both_engines_agree():
    """The energy criterion (`flojo / fuerte < energia_minima`) is the extractor's one hard edge.
    R = L/2 puts every bin exactly on it (0.25) in both engines: halving commutes with every
    rounding of the FFT and the smoothing. Such material is coherent, though, so its index is 0
    on either side of the edge; a hair off the ratio (1e-9) agrees as well. The exact tie that
    does change the sound is a silent channel with `energia_minima=0` (`0 < 0`), in
    `test_mono_independent_and_one_sided_inputs_match_numpy`: a `<=` planted there gave 2.02."""
    s = noise(40)
    for right in (s / 2, s / 2 * (1 + 1e-9), s / 2 * (1 - 1e-9)):
        assert_golden(extractor(), blocks(s, right), silent_ok=True)


# -- live changes, inputs and errors --------------------------------------------------------


def test_live_param_changes_match_numpy():
    """The motor changes the extractor's params at a cut's bottom by setting `p`."""

    def between(k, ex):
        changes = {
            2: Parametros(lam=0.7, umbral=0.3),
            4: Parametros(sigma=8.0, energia_minima=0.1),
            6: Parametros(mu0=0.2, mu1=0.9, umbral=0.6, lam=0.95),
        }
        if k in changes:
            ex.p = changes[k]

    assert_golden(extractor(), blocks(*music(seconds=1.0, seed=13), [3000, 4096]), between)


def test_strided_and_float32_inputs_match_numpy():
    left, right = music(seconds=1.0, seed=14)
    wide_l, wide_r = np.repeat(left, 2), np.repeat(right, 2)
    strided = [(wide_l[i : i + 2 * BLOCK : 2], wide_r[i : i + 2 * BLOCK : 2]) for i in range(0, len(wide_l), 2 * BLOCK)]
    assert not strided[0][0].flags.c_contiguous
    assert_golden(extractor(), strided)
    single = [(x.astype(np.float32), y.astype(np.float32)) for x, y in blocks(left, right)]
    assert_golden(extractor(), single)


@pytest.mark.parametrize("engine", [backend.NUMPY, backend.RUST])
def test_channels_of_different_lengths_are_refused(engine):
    backend.reset()
    backend.use(engine)
    ex = Extractor()
    with pytest.raises(ValueError, match="largos distintos"):
        ex.procesar(np.zeros(10), np.zeros(11))


def test_the_rust_extractor_does_not_modify_its_inputs():
    backend.reset()
    backend.use(backend.RUST)
    ex = Extractor()
    left, right = full_scale(seconds=0.2)
    before = left.copy(), right.copy()
    ex.procesar(left, right)
    assert np.array_equal(left, before[0])
    assert np.array_equal(right, before[1])


# -- the switch and the failure -------------------------------------------------------------


@pytest.mark.parametrize(("first", "then"), [(backend.NUMPY, backend.RUST), (backend.RUST, backend.NUMPY)])
@pytest.mark.parametrize("at", [1, 4])
def test_the_switch_moves_the_state_exactly(first, then, at):
    """`backend.use` at a cut's bottom tells the extractor (`on_engine_switch`), which moves its
    state to the other engine: the run goes on as if it had never switched."""
    make = extractor(Parametros(lam=0.8, sigma=4.0))
    feed = blocks(*music(seconds=1.0, seed=15), [3000, 4096, 777])
    expected = run(backend.NUMPY, make, feed)
    backend.reset()
    backend.use(first)
    ex = make()
    out = []
    for k, (left, right) in enumerate(feed):
        if k == at:
            backend.use(then)
            assert (ex._rust is not None) == (then == backend.RUST)  # noqa: SLF001
        out.append(ex.procesar(left, right))
    assert (ex._rust is not None) == (then == backend.RUST)  # noqa: SLF001
    diff = float(np.max(np.abs(np.concatenate(out) - expected)))
    assert diff <= TOLERANCE, f"max |diff| = {diff:.3g}"


def test_the_raw_state_round_trip_is_exact():
    """`state()` and `set_state()` of the Rust object carry everything: a copy goes on bit for bit."""
    a = aurasync_engine.AmbienceExtractor(ambience.N_FFT, ambience.SALTO)
    left, right = music(seconds=0.5, seed=16)
    a.process(left[:5000], right[:5000])
    b = aurasync_engine.AmbienceExtractor(ambience.N_FFT, ambience.SALTO)
    b.set_state(a.state())
    assert np.array_equal(a.process(left[5000:], right[5000:]), b.process(left[5000:], right[5000:]))
    bad = a.state()
    bad["norm"] = bad["norm"][:-1]
    with pytest.raises(ValueError, match="norm"):
        b.set_state(bad)
    bad = a.state()
    bad["pending_right"] = bad["pending_right"][:-1]
    with pytest.raises(ValueError, match="pending_right"):
        b.set_state(bad)
    bad = a.state()
    del bad["ready"]
    with pytest.raises(ValueError, match="ready"):
        b.set_state(bad)


def test_the_state_s_keys_are_numpy_s():
    """The numpy extractor's state in the Rust object's terms, and back, is exact."""
    backend.reset()
    backend.use(backend.NUMPY)
    ex = Extractor()
    left, right = music(seconds=0.5, seed=21)
    ex.procesar(left[:7000], right[:7000])
    state = ex._numpy_state()  # noqa: SLF001
    rust = aurasync_engine.AmbienceExtractor(ambience.N_FFT, ambience.SALTO)
    rust.set_state(state)
    back = rust.state()
    assert back.keys() == state.keys()
    for key, value in state.items():
        assert np.array_equal(back[key], value), key


def _failing_next(ex):
    """Make the extractor's next Rust call panic (feature `test-panic` of the test build)."""
    ex._rust._panic_next()  # noqa: SLF001


def test_a_rust_panic_is_one_silent_block_then_a_fresh_numpy_extractor():
    """Nobody listening (`on_failure` None, no service): the failing block is silence, and from
    the next block numpy runs, from a fresh extractor (the Rust state may be torn)."""
    feed = blocks(*music(seconds=1.0, seed=17))
    backend.reset()
    backend.use(backend.RUST)
    ex = Extractor()
    for left, right in feed[:3]:
        ex.procesar(left, right)
    _failing_next(ex)
    assert not np.any(ex.procesar(*feed[3]))
    assert "panicked" in backend.failure()
    assert backend.active() == backend.NUMPY
    rest = [ex.procesar(left, right) for left, right in feed[4:]]
    assert ex._rust is None  # noqa: SLF001
    backend.reset()
    backend.use(backend.NUMPY)
    fresh = Extractor()
    for k, (left, right) in enumerate(feed[4:]):
        assert np.array_equal(fresh.procesar(left, right), rest[k])


def test_a_rust_panic_with_a_service_is_silent_until_the_cut_s_bottom():
    feed = blocks(*music(seconds=1.0, seed=18))
    heard: list[str] = []
    backend.reset()
    backend.use(backend.RUST)
    backend.on_failure = heard.append
    ex = Extractor()
    ex.procesar(*feed[0])
    _failing_next(ex)
    for left, right in feed[1:4]:
        assert not np.any(ex.procesar(left, right))
    assert len(heard) == 1
    backend.use(backend.NUMPY)  # the cut's bottom
    assert ex._rust is None  # noqa: SLF001
    assert ex.procesar(*feed[4]).shape == (len(feed[4][0]),)
    # Choosing Rust again later builds a new Rust extractor from numpy's state.
    backend.clear_failure()
    backend.use(backend.RUST)
    assert ex._rust is not None  # noqa: SLF001
    ex.procesar(*feed[5])
    assert backend.failure() is None


class _ProxyRust:
    """The real Rust extractor with one method replaced (a failure that is not a panic)."""

    def __init__(self, inner, **replaced):
        self._inner, self._replaced = inner, replaced

    def __getattr__(self, name):
        return self._replaced.get(name) or getattr(self._inner, name)


def _raiser(error):
    def raise_it(*_args, **_kwargs):
        msg = "planted"
        raise error(msg)

    return raise_it


def _assert_fell_back_to_numpy(ex, feed, heard, error):
    assert len(heard) == 1
    assert error.__name__ in heard[0]
    assert error.__name__ in backend.failure()
    for left, right in feed[:2]:  # the silent window: silence, nothing raised
        assert not np.any(ex.procesar(left, right))
    backend.use(backend.NUMPY)  # the cut's bottom
    assert ex._rust is None  # noqa: SLF001
    assert np.any(ex.procesar(*feed[2]))
    assert len(heard) == 1


@pytest.mark.parametrize("error", [ValueError, TypeError, AttributeError])
@pytest.mark.parametrize("failing", ["constructor", "set_params", "set_state"])
def test_a_rust_extractor_that_fails_while_being_built_falls_back_to_numpy(monkeypatch, error, failing):
    """Not a panic: argument conversion, or an older extension without the class or method. The
    session keeps its audio (silence until the cut's bottom, then numpy), the handler hears once."""
    real = aurasync_engine.AmbienceExtractor
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
    monkeypatch.setattr(backend, "module", lambda: types.SimpleNamespace(AmbienceExtractor=cls))
    feed = blocks(*music(seconds=1.0, seed=20))
    heard: list[str] = []
    backend.reset()
    backend.use(backend.RUST)
    backend.on_failure = heard.append
    ex = Extractor()
    _assert_fell_back_to_numpy(ex, feed, heard, error)


@pytest.mark.parametrize("error", [ValueError, TypeError, AttributeError])
@pytest.mark.parametrize("call", ["set_params", "reset"])
def test_a_live_configuration_that_fails_falls_back_to_numpy(error, call):
    feed = blocks(*music(seconds=1.0, seed=21))
    heard: list[str] = []
    backend.reset()
    backend.use(backend.RUST)
    backend.on_failure = heard.append
    ex = Extractor()
    ex.procesar(*feed[0])
    ex._rust = _ProxyRust(ex._rust, **{call: _raiser(error)})  # noqa: SLF001
    if call == "set_params":
        ex.p = Parametros(lam=0.5)
    else:
        ex.reiniciar()
    _assert_fell_back_to_numpy(ex, feed[1:], heard, error)


def test_a_state_read_that_fails_at_the_switch_to_numpy_restarts_without_a_second_cut():
    """The user chose numpy: a failed state read is logged, not a Rust failure (no `_fail`, no
    silence, no second call to the handler); the extractor restarts in numpy and produces audio."""
    feed = blocks(*music(seconds=1.0, seed=22))
    heard: list[str] = []
    backend.reset()
    backend.use(backend.RUST)
    backend.on_failure = heard.append
    ex = Extractor()
    ex.procesar(*feed[0])
    ex._rust = _ProxyRust(ex._rust, state=_raiser(TypeError))  # noqa: SLF001
    backend.use(backend.NUMPY)
    assert ex._rust is None  # noqa: SLF001
    assert backend.silent() is None
    assert backend.failure() is None
    assert heard == []
    assert np.any(ex.procesar(*feed[1]))
    assert heard == []


def test_a_rust_panic_while_setting_params_is_a_fresh_numpy_extractor_at_the_cut():
    """A failure outside `process` (setting `p`) also breaks the Rust object: silence until the
    cut's bottom, then numpy afresh with the new params."""
    feed = blocks(*music(seconds=1.0, seed=19))
    heard: list[str] = []
    backend.reset()
    backend.use(backend.RUST)
    backend.on_failure = heard.append
    ex = Extractor()
    ex.procesar(*feed[0])
    _failing_next(ex)
    ex.p = Parametros(lam=0.5)
    assert len(heard) == 1
    assert not np.any(ex.procesar(*feed[1]))
    backend.use(backend.NUMPY)
    assert ex._rust is None  # noqa: SLF001
    assert ex.p == Parametros(lam=0.5)
    rest = [ex.procesar(left, right) for left, right in feed[2:]]
    fresh = Extractor(Parametros(lam=0.5))
    for k, (left, right) in enumerate(feed[2:]):
        assert np.array_equal(fresh.procesar(left, right), rest[k])


def test_the_backend_does_not_keep_a_dropped_extractor_alive():
    backend.reset()
    backend.use(backend.RUST)
    ex = Extractor()
    ref = weakref.ref(ex)
    del ex
    gc.collect()
    assert ref() is None
    backend.use(backend.NUMPY)  # nobody left to tell; nothing fails


def test_capabilities_carry_the_ambience_constants():
    assert aurasync_engine.capabilities()["ambience"] == {
        "floor": ambience._PISO_NORMA,  # noqa: SLF001
    }


# -- inside the motor -----------------------------------------------------------------------


def _installation():
    return Instalacion(
        parlantes=[
            Parlante("L", "s0", pan=-0.7, ambiente=0.15),
            Parlante("R", "s1", pan=0.7, ambiente=0.15),
            Parlante("B", "s2", pan=0.0, ambiente=0.8),
        ]
    )


def test_the_motor_with_the_rust_extractor_matches_numpy():
    """The classic render with the extractor on (the default chain), a live change of its params
    at a cut, and the motor's `reiniciar`."""
    left, right = music(seconds=1.5, seed=22)
    changed = ChainValues.from_json({"ambience": {"params": {"lam": 0.8, "threshold": 0.4, "mix": 0.9}}})
    outs = {}
    for engine in (backend.NUMPY, backend.RUST):
        backend.reset()
        backend.use(engine)
        m = Motor(_installation(), SR, semilla=1, chain=ChainValues())
        got = {p.nombre: [] for p in m.instalacion.parlantes}
        for k, i in enumerate(range(0, len(left), BLOCK)):
            if k == 3:
                m.aplicar_cadena(changed)
            for name, x in m.procesar(left[i : i + BLOCK], right[i : i + BLOCK]).items():
                got[name].append(x)
        assert (m._extractor._rust is not None) == (engine == backend.RUST)  # noqa: SLF001
        assert backend.failure() is None
        outs[engine] = {name: np.concatenate(v) for name, v in got.items()}
    for name, x in outs[backend.NUMPY].items():
        assert np.any(x)
        diff = float(np.max(np.abs(outs[backend.RUST][name] - x)))
        assert diff <= TOLERANCE, f"{name}: max |diff| = {diff:.3g}"
