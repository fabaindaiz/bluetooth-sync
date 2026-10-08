"""Choosing the engine of the sinc read: numpy or Rust (`dsp/backend.py`, spec rust-engine §2).

The setting (`"engine"` in `service.json`, numpy by default) and `AURASYNC_ENGINE` choose; Rust
without the extension, or built with other constants, falls back to numpy and says why. The
live switch (`engine_set`) happens only at the bottom of a cut, and a Rust failure gives silence
from the failing block until the cut's bottom, then numpy, without stopping the music.

The extension is replaced here by fake modules in `sys.modules`, so these tests say what the
backend does whatever the build; `tests/test_engine_rust.py` tests the build itself.
"""

from __future__ import annotations

import json
import sys
import threading
import time
import types
from typing import TYPE_CHECKING

import numpy as np
import pytest

from aurasync import clients, control
from aurasync.config import Instalacion, Parlante
from aurasync.dsp import ambience, backend, eq, interpolation
from aurasync.service import ConfigError, Service, load_config

if TYPE_CHECKING:
    from pathlib import Path

HALF = interpolation.HALF


class NumpyAmbienceExtractor:
    """`aurasync_engine.AmbienceExtractor`'s interface done by numpy's own extractor, so a real
    motor (which always builds one) runs on a fake extension: what these tests look at is the
    read. Built without `Extractor.__init__`, which would ask the fake for another one."""

    def __init__(self, n_fft: int, hop: int, max_block: int = 8192) -> None:
        del max_block
        ex = ambience.Extractor.__new__(ambience.Extractor)
        ex._rust, ex._rust_broken, ex._p = None, False, ambience.Parametros()  # noqa: SLF001
        ex.n_fft, ex.salto, ex.latencia = n_fft, hop, n_fft
        ex._reiniciar_numpy()  # noqa: SLF001
        self._ex = ex

    def set_params(self, lam, threshold, mu0, mu1, sigma, min_energy) -> None:
        self._ex._p = ambience.Parametros(lam, threshold, mu0, mu1, sigma, min_energy)  # noqa: SLF001

    def reset(self) -> None:
        self._ex._reiniciar_numpy()  # noqa: SLF001

    def process(self, left, right):
        return self._ex._procesar_numpy(left, right)  # noqa: SLF001

    def state(self):
        return self._ex._numpy_state()  # noqa: SLF001

    def set_state(self, state) -> None:
        self._ex._load_state(state)  # noqa: SLF001


class NumpyStreamingFIR:
    """`aurasync_engine.StreamingFIR`'s interface done by numpy's own filter (its numpy paths, so
    it never asks the fake for a Rust filter)."""

    def __init__(self, taps, block=4096) -> None:  # noqa: ARG002 - the real signature
        self._f = eq.StreamingFIR(taps)

    def set_taps(self, taps) -> None:
        self._f.set_taps(taps)

    def replace_taps(self, taps) -> None:
        self._f.taps = taps

    def process(self, x):
        return self._f._process_numpy(x)  # noqa: SLF001

    def state(self):
        return self._f._numpy_state()  # noqa: SLF001

    def set_state(self, state) -> None:
        self._f._load_state(state)  # noqa: SLF001


class NumpyPartitionedFIR:
    """`aurasync_engine.PartitionedFIR`'s interface done by numpy's own filter."""

    def __init__(self, taps, block) -> None:
        self._f = eq.PartitionedFIR(taps, block)

    def process(self, x):
        return self._f._process_numpy(x)  # noqa: SLF001

    def skip(self, x) -> None:
        self._f._skip_numpy(x)  # noqa: SLF001

    def state(self):
        return self._f._numpy_state()  # noqa: SLF001

    def set_state(self, state) -> None:
        self._f._load_state(state)  # noqa: SLF001


def fake_engine(read=None, **capabilities) -> types.ModuleType:
    """A stand-in for `aurasync_engine`: numpy's read, or `read`; the real constants unless
    `capabilities` overrides one."""
    module = types.ModuleType("aurasync_engine")
    module.calls = []

    def default_read(data, position):
        module.calls.append((data, position))
        return interpolation.read_numpy(data, position)

    module.read = read or default_read
    module.AmbienceExtractor = NumpyAmbienceExtractor
    module.StreamingFIR = NumpyStreamingFIR
    module.PartitionedFIR = NumpyPartitionedFIR
    constants = {"half": interpolation.HALF, "beta": interpolation.BETA, "steps": interpolation._STEPS}  # noqa: SLF001
    # The other stages' constants as numpy has them, so only `capabilities` can make it stale.
    module.capabilities = lambda: {**backend._expected(), "interpolation": {**constants, **capabilities}}  # noqa: SLF001
    return module


@pytest.fixture
def rust(monkeypatch) -> types.ModuleType:
    """A working fake extension."""
    module = fake_engine()
    monkeypatch.setitem(sys.modules, "aurasync_engine", module)
    backend.reset()
    return module


@pytest.fixture
def no_extension(monkeypatch) -> None:
    """`import aurasync_engine` raises ImportError, as on a machine that never built it."""
    monkeypatch.setitem(sys.modules, "aurasync_engine", None)
    backend.reset()


@pytest.fixture
def failing(monkeypatch) -> types.ModuleType:
    """An extension whose read fails as a caught Rust panic does: `RuntimeError`."""

    def read(data, position):
        module.calls.append((data, position))
        msg = "aurasync_engine panicked: planted"
        raise RuntimeError(msg)

    module = fake_engine(read=read)
    monkeypatch.setitem(sys.modules, "aurasync_engine", module)
    backend.reset()
    return module


@pytest.fixture
def data() -> np.ndarray:
    return np.random.default_rng(3).uniform(-1.0, 1.0, 6000)


def still(n: int = 1024) -> np.ndarray:
    return np.arange(n) + 40 + 0.37


# -- choosing -------------------------------------------------------------------------------


def test_default_is_numpy(rust):
    assert backend.wanted(None) == "numpy"
    assert backend.resolve("numpy") == backend.Resolved("numpy", available=True, reason=None)
    # Nobody chose: the first read takes AURASYNC_ENGINE, which is not set here.
    assert backend.active() == "numpy"
    assert backend.read(np.ones(100), np.array([40.25])).shape == (1,)
    assert rust.calls == []


def test_env_overrides_setting(rust, monkeypatch):
    monkeypatch.setenv(backend.ENV, "numpy")
    assert backend.wanted("rust") == "numpy"
    monkeypatch.setenv(backend.ENV, "rust")
    assert backend.wanted("numpy") == "rust"
    assert backend.wanted(None) == "rust"
    # Without a service, the first read takes the variable (the CLI and the tests).
    backend.reset()
    backend.read(np.ones(100), np.array([40.25]))
    assert backend.active() == "rust"
    assert len(rust.calls) == 1


@pytest.mark.usefixtures("rust")
def test_env_overrides_the_service_setting(monkeypatch, tmp_path):
    monkeypatch.setenv(backend.ENV, "numpy")
    svc = _service(tmp_path, engine="rust")
    assert svc.snapshot["engine"] == {"wanted": "numpy", "active": "numpy", "available": True, "reason": None}


@pytest.mark.usefixtures("rust")
def test_an_unknown_engine_name_is_numpy_with_a_reason(monkeypatch):
    resolved = backend.resolve("fortran")
    assert resolved.active == "numpy"
    assert "fortran" in resolved.reason
    monkeypatch.setenv(backend.ENV, "fortran")
    backend.reset()
    backend.read(np.ones(100), np.array([40.25]))
    assert backend.active() == "numpy"


@pytest.mark.usefixtures("no_extension")
def test_rust_without_extension_falls_back_with_reason(tmp_path):
    resolved = backend.resolve("rust")
    assert resolved.active == "numpy"
    assert resolved.available is False
    assert "not installed" in resolved.reason
    assert "engine-build" in resolved.reason
    # A service asked for Rust plays with numpy, says so once in its log, and nothing fails.
    lines: list[str] = []
    svc = _service(tmp_path, engine="rust", log=lines.append)
    engine = svc.snapshot["engine"]
    assert engine["wanted"] == "rust"
    assert engine["active"] == "numpy"
    assert engine["available"] is False
    assert "not installed" in engine["reason"]
    assert sum("not installed" in line for line in lines) == 1
    assert interpolation.read(np.ones(100), np.array([40.25])).shape == (1,)


def test_a_build_with_other_constants_is_not_used(monkeypatch):
    monkeypatch.setitem(sys.modules, "aurasync_engine", fake_engine(half=15))
    backend.reset()
    resolved = backend.resolve("rust")
    assert resolved.active == "numpy"
    assert resolved.available is False
    assert "half" in resolved.reason


@pytest.mark.parametrize("stage", ["fir", "virtual_bass"])
def test_a_build_with_a_stale_stage_version_is_not_used(monkeypatch, stage):
    module = fake_engine()
    module.capabilities = lambda: {**backend._expected(), stage: {"version": 0}}  # noqa: SLF001
    monkeypatch.setitem(sys.modules, "aurasync_engine", module)
    backend.reset()
    resolved = backend.resolve("rust")
    assert resolved.active == "numpy"
    assert stage in resolved.reason


@pytest.mark.usefixtures("no_extension")
def test_use_refuses_rust_when_it_cannot_read():
    with pytest.raises(ValueError, match="not installed"):
        backend.use("rust")
    with pytest.raises(ValueError, match="unknown engine"):
        backend.use("fortran")


# -- reading --------------------------------------------------------------------------------


def test_interpolation_read_goes_through_the_chosen_engine(rust, data):
    position = still()
    backend.use("numpy")
    expected = interpolation.read(data, position)
    assert rust.calls == []
    backend.use("rust")
    got = interpolation.read(data, position)
    assert len(rust.calls) == 1
    assert np.array_equal(got, expected)


@pytest.mark.usefixtures("rust")
def test_the_fake_extension_has_the_filters():
    """The FIR filters (EQ, crossover, bass, diffuse) run through the fake as they would through
    the real extension, so a test with a fake never fails over for a missing class."""
    x = np.random.default_rng(5).standard_normal(5000)
    backend.use("numpy")
    expected = (eq.StreamingFIR(np.ones(300)).process(x), eq.PartitionedFIR(np.ones(300), 1024).process(x))
    backend.use("rust")
    streaming, partitioned = eq.StreamingFIR(np.ones(300)), eq.PartitionedFIR(np.ones(300), 1024)
    got = (streaming.process(x), partitioned.process(x))
    assert isinstance(streaming._rust, NumpyStreamingFIR)  # noqa: SLF001
    assert isinstance(partitioned._rust, NumpyPartitionedFIR)  # noqa: SLF001
    assert all(np.array_equal(a, b) for a, b in zip(got, expected, strict=True))
    assert backend.failure() is None


def test_the_dispatcher_hands_rust_contiguous_float64(rust):
    """A future caller passing float32 or a strided view must not look like a Rust failure."""
    backend.use("rust")
    data = np.random.default_rng(1).uniform(-1, 1, 4000).astype(np.float32)[::2]
    position = still(64)[::-1]
    backend.read(data, position)
    given_data, given_position = rust.calls[-1]
    for array in (given_data, given_position):
        assert array.dtype == np.float64
        assert array.flags.c_contiguous
    # Float64 contiguous input is passed as it is, without a copy.
    contiguous = np.ones(200)
    backend.read(contiguous, np.array([40.5]))
    assert rust.calls[-1][0] is contiguous


def test_a_rust_value_error_is_not_a_failure(monkeypatch):
    """Only `RuntimeError` (a caught panic) is the engine failing; a bad argument is the caller's
    bug and propagates, as numpy's own error would."""

    def read(_data, _position):
        msg = "position 0 is out of range"
        raise ValueError(msg)

    monkeypatch.setitem(sys.modules, "aurasync_engine", fake_engine(read=read))
    backend.reset()
    backend.use("rust")
    failures: list[str] = []
    backend.on_failure = failures.append
    with pytest.raises(ValueError, match="out of range"):
        backend.read(np.ones(100), np.array([40.5]))
    assert failures == []
    assert backend.failure() is None


def test_rust_failure_is_silent_until_the_cut_then_numpy(failing, data):
    backend.use("rust")
    failures: list[str] = []
    backend.on_failure = failures.append
    position = still()
    first = backend.read(data, position)
    # That block is silence, of the right length; the reason goes to whoever listens, once.
    # (The silence lasts until the cut's bottom, and the engine does not claim to be reading.)
    assert np.array_equal(first, np.zeros(len(position)))
    assert len(failures) == 1
    assert "planted" in failures[0]
    assert backend.failure() == failures[0]
    # Until the cut's bottom, Rust is not called again, and nothing more is reported.
    assert np.array_equal(backend.read(data, position), np.zeros(len(position)))
    assert len(failing.calls) == 1
    assert len(failures) == 1
    assert backend.silent()
    assert not backend.rust_active()
    assert backend.active() == "numpy"
    # At the bottom of the cut, resolving Rust gives numpy (it failed), with the reason.
    resolved = backend.resolve("rust")
    assert resolved == backend.Resolved("numpy", available=True, reason=failures[0])
    backend.use(resolved.active)
    assert np.array_equal(backend.read(data, position), interpolation.read_numpy(data, position))
    assert len(failing.calls) == 1


@pytest.mark.parametrize("error", [ValueError, TypeError, AttributeError, RuntimeError])
def test_building_or_configuring_falls_back_on_any_exception(rust, error):
    """`built` (construction and configuration) treats any `Exception` as Rust failing: the handler
    is told once with the type, the fallback is returned, and silence lasts until the cut's bottom."""
    del rust
    backend.use("rust")
    failures: list[str] = []
    backend.on_failure = failures.append

    def boom():
        msg = "planted"
        raise error(msg)

    assert backend.built(boom, lambda: "fallback") == "fallback"
    assert backend.built(boom, lambda: "fallback") == "fallback"
    assert len(failures) == 1
    assert error.__name__ in failures[0]
    assert "planted" in failures[0]
    assert backend.failure() == failures[0]
    assert backend.silent() == failures[0]
    assert not backend.rust_active()
    backend.use("numpy")  # the cut's bottom
    assert backend.silent() is None


def test_building_a_value_passes_through_and_base_exceptions_propagate(rust):
    del rust
    backend.use("rust")
    assert backend.built(lambda: 7, lambda: 0) == 7
    assert backend.failure() is None

    def interrupt():
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        backend.built(interrupt, lambda: 0)
    assert backend.failure() is None


def test_a_type_error_in_the_per_block_call_still_propagates(monkeypatch):
    """Controller ruling: only construction and configuration are soft; a `TypeError` in the hot
    path is a caller's bug and must stay visible."""

    def read(_data, _position):
        msg = "argument 'data': wrong type"
        raise TypeError(msg)

    monkeypatch.setitem(sys.modules, "aurasync_engine", fake_engine(read=read))
    backend.reset()
    backend.use("rust")
    failures: list[str] = []
    backend.on_failure = failures.append
    with pytest.raises(TypeError, match="wrong type"):
        backend.read(np.ones(100), np.array([40.5]))
    assert failures == []
    assert backend.failure() is None


@pytest.mark.usefixtures("failing")
def test_a_failure_with_nobody_to_cut_reads_numpy_from_the_next_block(data):
    """Without a service (the CLI), nobody would ever cut: the next block is numpy, not silence."""
    backend.use("rust")
    backend.on_failure = None
    position = still()
    assert np.array_equal(backend.read(data, position), np.zeros(len(position)))
    assert backend.active() == "numpy"
    assert np.array_equal(backend.read(data, position), interpolation.read_numpy(data, position))


@pytest.mark.usefixtures("failing")
def test_choosing_rust_again_clears_the_failure(monkeypatch):
    backend.use("rust")
    backend.on_failure = None
    backend.read(np.ones(200), np.array([40.5]))
    assert backend.failure() is not None
    monkeypatch.setitem(sys.modules, "aurasync_engine", fake_engine())
    backend.clear_failure()
    assert backend.resolve("rust").active == "rust"


def test_the_production_check_refuses_a_test_build(monkeypatch, capsys):
    module = fake_engine()
    monkeypatch.setitem(sys.modules, "aurasync_engine", module)
    backend.reset()
    assert backend.main(["--check-production"]) == 0
    module._panic = lambda: None  # noqa: SLF001
    assert backend.main(["--check-production"]) == 1
    assert "test-panic" in capsys.readouterr().err
    monkeypatch.setitem(sys.modules, "aurasync_engine", None)
    backend.reset()
    assert backend.main(["--check-production"]) == 1


# -- the service: the setting, `engine_set`, the cut and the fallback ------------------------


class FakeMotor:
    """Only what the service calls; `cortar` keeps the action until the test runs it."""

    latencia = 2048

    def __init__(self, *_args) -> None:
        self.actions: list = []
        self.silenciados: set = set()

    def cortar(self, accion=None) -> None:
        self.actions.append(accion)

    def bottom(self) -> None:
        """The bottom of the cut: what `Motor._saltar` does with the actions."""
        actions, self.actions = self.actions, []
        for action in actions:
            if action is not None:
                action()

    def procesar(self, _izq, _der):
        return {}

    def retardos_actuales_ms(self) -> dict:
        return {}


class FakeSession:
    def __init__(self, installation, motor, _options, _log) -> None:
        self.installation = installation
        self.motor = motor

    def open(self) -> None:
        pass

    def step(self) -> None:
        self.motor.procesar(np.zeros(512), np.zeros(512))
        time.sleep(0.002)

    def close(self) -> None:
        pass

    def output_states(self) -> dict[str, str]:
        """What the service's rejoin watch reads every tick (`Service._rejoin_lost`)."""
        return {p.nombre: "playing" for p in self.installation.parlantes}


def _service(tmp_path: Path, *, engine: str | None = None, motor_factory=FakeMotor, log=None, config_path=None):
    if not (tmp_path / "inst.json").exists():
        Instalacion(parlantes=[Parlante("Go 4 Red", "s0", pan=-0.7), Parlante("Go 4 Blue", "s1", pan=0.7)]).guardar(
            tmp_path / "inst.json"
        )
    return Service(
        tmp_path / "inst.json",
        tmp_path / "presets.json",
        session_factory=FakeSession,
        motor_factory=motor_factory,
        log=log or (lambda _: None),
        config_path=config_path,
        engine=engine,
    )


def _config(tmp_path: Path, **extra) -> Path:
    path = tmp_path / "service.json"
    path.write_text(json.dumps({"token": "x" * 40, **extra}))
    path.chmod(0o600)
    return path


def test_engine_set_is_a_control_operation_with_two_choices():
    assert clients.required_scope("engine_set") == "control"
    assert control.parse({"v": 1, "op": "engine_set", "engine": "rust"}).args == {"engine": "rust"}
    with pytest.raises(control.ContractError) as caught:
        control.parse({"v": 1, "op": "engine_set", "engine": "fortran"})
    assert caught.value.code == "out_of_range"


def test_the_setting_is_read_and_checked_in_service_json(tmp_path):
    assert load_config(_config(tmp_path)).engine == "numpy"
    assert load_config(_config(tmp_path, engine="rust")).engine == "rust"
    with pytest.raises(ConfigError, match="engine"):
        load_config(_config(tmp_path, engine="fortran"))


@pytest.mark.usefixtures("rust")
def test_engine_set_switches_only_at_the_cut(tmp_path):
    svc = _service(tmp_path)
    svc.start()
    motor = svc.motor
    assert backend.active() == "numpy"
    result = svc.engine_set("rust")
    # Asked, not done: the switch waits for the cut's bottom.
    assert result["wanted"] == "rust"
    assert backend.active() == "numpy"
    assert len(motor.actions) == 1
    svc._publish()  # noqa: SLF001 - the engine loop publishes after every order
    assert svc.snapshot["engine"] == {"wanted": "rust", "active": "numpy", "available": True, "reason": None}
    motor.bottom()
    assert backend.active() == "rust"
    svc._publish()  # noqa: SLF001
    assert svc.snapshot["engine"]["active"] == "rust"
    # And back: again through a cut.
    svc.engine_set("numpy")
    assert backend.active() == "rust"
    motor.bottom()
    assert backend.active() == "numpy"
    # Choosing what already plays costs no cut.
    svc.engine_set("numpy")
    assert motor.actions == []
    svc.stop()
    svc.close()


@pytest.mark.usefixtures("no_extension")
def test_engine_set_rust_without_extension_keeps_numpy(tmp_path):
    svc = _service(tmp_path)
    svc.start()
    result = svc.engine_set("rust")
    # No cut is wasted on a switch that cannot happen; the reason says why.
    assert svc.motor.actions == []
    assert backend.active() == "numpy"
    assert result["active"] == "numpy"
    assert "not installed" in result["reason"]
    svc._publish()  # noqa: SLF001
    assert svc.snapshot["engine"]["wanted"] == "rust"
    assert "not installed" in svc.snapshot["engine"]["reason"]
    svc.stop()
    svc.close()


@pytest.mark.usefixtures("rust")
def test_engine_set_without_session_only_saves_the_setting(tmp_path):
    config = _config(tmp_path)
    svc = _service(tmp_path, config_path=config)
    svc.engine_set("rust")
    assert json.loads(config.read_text())["engine"] == "rust"
    # No audio flows: there is nothing to cut, and the next session starts with it.
    assert svc.motor is None
    assert backend.active() == "rust"
    svc._publish()  # noqa: SLF001
    assert svc.snapshot["engine"] == {"wanted": "rust", "active": "rust", "available": True, "reason": None}
    svc.start()
    assert svc.motor.actions == []
    assert backend.active() == "rust"
    svc.stop()
    svc.engine_set("numpy")
    assert json.loads(config.read_text())["engine"] == "numpy"
    svc.close()
    # A simulation (no file) keeps nothing, and fails nothing.
    simulated = _service(tmp_path)
    simulated.engine_set("rust")
    assert json.loads(config.read_text())["engine"] == "numpy"
    simulated.close()


@pytest.mark.usefixtures("failing")
def test_a_session_starts_with_the_setting_even_after_a_failure(tmp_path):
    """A failure disables Rust until the user chooses it again; a new session does not."""
    svc = _service(tmp_path, engine="rust")
    backend.read(np.ones(200), np.array([40.5]))
    assert backend.failure() is not None
    svc.start()
    assert backend.active() == "numpy"
    svc.stop()
    svc.close()


def test_rust_failure_in_a_session_falls_back_to_numpy_and_says_why(failing, tmp_path):
    """The real motor and the real cut: one failure, then numpy, with the reason in the state and
    the log, and the session still playing."""
    lines: list[str] = []
    from aurasync.service import _default_motor

    svc = _service(tmp_path, engine="rust", motor_factory=_default_motor, log=lines.append)
    thread = threading.Thread(target=svc.run, daemon=True)
    thread.start()
    try:
        assert svc.handle({"v": 1, "op": "start"})["ok"]
        _wait(lambda: svc.snapshot["engine"]["active"] == "numpy")
        engine = svc.snapshot["engine"]
        assert engine["wanted"] == "rust"
        assert "planted" in engine["reason"]
        assert svc.snapshot["session"]["status"] == "playing"
        # Rust was tried once (the first block's first speaker) and never again.
        assert len(failing.calls) == 1
        assert sum("planted" in line for line in lines) == 1
        # Choosing Rust again clears the failure (and fails again, here).
        assert svc.handle({"v": 1, "op": "engine_set", "engine": "rust"})["ok"]
        _wait(lambda: len(failing.calls) == 2)
        _wait(lambda: svc.snapshot["engine"]["active"] == "numpy")
        assert svc.snapshot["session"]["status"] == "playing"
    finally:
        svc.handle({"v": 1, "op": "shutdown"})
        thread.join(timeout=5)
        svc.close()


def _working_read(module):
    """Make a `failing` extension healthy again (what a user hopes for when choosing Rust again)."""

    def read(data, position):
        module.calls.append((data, position))
        return interpolation.read_numpy(data, position)

    module.read = read


def _fail_once_with_the_cut_pending(svc, data):
    """A started service whose Rust just failed: the cut that will switch to numpy is queued on
    the motor and has not reached its bottom."""
    svc.start()
    assert backend.active() == "rust"
    backend.read(data, still())
    svc._run_engine_calls()  # noqa: SLF001 - the engine loop does this between blocks
    assert len(svc.motor.actions) == 1


def test_choosing_rust_while_the_failure_cut_is_pending_does_not_stay_silent(failing, tmp_path, data):
    svc = _service(tmp_path, engine="rust")
    _fail_once_with_the_cut_pending(svc, data)
    # While the cut is going down the output is silent and the state does not say Rust reads.
    view = svc.engine_view()
    assert view["active"] == "numpy"
    assert "planted" in view["reason"]
    _working_read(failing)
    svc.engine_set("rust")
    assert backend.silent()
    assert svc.engine_view()["active"] == "numpy"
    svc.motor.bottom()
    position = still()
    assert not backend.silent()
    assert np.array_equal(backend.read(data, position), interpolation.read_numpy(data, position))
    assert svc.engine_view() == {"wanted": "rust", "active": "rust", "available": True, "reason": None}
    svc.stop()
    svc.close()


def test_stop_choose_rust_start_while_the_failure_cut_is_pending(failing, tmp_path, data):
    svc = _service(tmp_path, engine="rust")
    _fail_once_with_the_cut_pending(svc, data)
    svc.stop()
    _working_read(failing)
    svc.engine_set("rust")
    svc.start()
    position = still()
    assert not backend.silent()
    assert np.array_equal(backend.read(data, position), interpolation.read_numpy(data, position))
    assert svc.engine_view()["active"] == "rust"
    svc.stop()
    svc.close()


class FakeStage:
    """A stateful stage with a Rust port: it hears the switch where it can move its state."""

    def __init__(self) -> None:
        self.heard: list[str] = []

    def on_engine_switch(self, active: str) -> None:
        self.heard.append(active)


def test_a_stage_hears_the_switch_at_the_bottom_and_never_before(failing, data):
    stage = FakeStage()
    backend.register(stage)
    backend.use("numpy")
    assert stage.heard == ["numpy"]
    _working_read(failing)
    backend.use("rust")
    assert stage.heard == ["numpy", "rust"]
    # A failure is not a switch: the stage hears nothing until `use` runs at the cut's bottom, and
    # until then Rust is not to be called (`rust_active`) and the answer is silence (`silent`).
    failing.read = lambda *_: (_ for _ in ()).throw(RuntimeError("planted again"))
    backend.on_failure = lambda _reason: None
    backend.read(data, still())
    assert stage.heard == ["numpy", "rust"]
    assert backend.silent()
    assert not backend.rust_active()
    backend.clear_failure()
    assert backend.silent()
    assert not backend.rust_active()
    backend.use(backend.resolve("rust").active)
    assert stage.heard[-1] == "rust"
    assert not backend.silent()
    assert backend.rust_active()
    # A stage that raises does not stop the switch nor the others.
    other = FakeStage()
    backend.register(types.SimpleNamespace(on_engine_switch=lambda _a: 1 / 0))
    backend.register(other)
    backend.use("numpy")
    assert other.heard == ["numpy"]
    assert backend.active() == "numpy"
    backend.unregister(other)
    backend.use("numpy")
    assert other.heard == ["numpy"]


def test_a_numpy_service_is_silent_in_the_log_about_the_engine(tmp_path):
    lines: list[str] = []
    svc = _service(tmp_path, log=lines.append)
    svc.start()
    svc.stop()
    svc.close()
    assert not [line for line in lines if "engine" in line]


@pytest.mark.usefixtures("rust")
def test_a_rust_service_says_once_that_rust_reads(tmp_path):
    lines: list[str] = []
    svc = _service(tmp_path, engine="rust", log=lines.append)
    svc.close()
    assert sum("engine: rust reads" in line for line in lines) == 1


@pytest.mark.usefixtures("rust")
def test_a_build_failing_inside_the_switch_hook_is_not_logged_as_rust_reading(tmp_path):
    """`use("rust")` ran, but a stage's build failed in its hook: silence until the cut's bottom
    and numpy active, so the log must not say that Rust reads."""
    lines: list[str] = []

    def broken(_active: str) -> None:
        def build():
            msg = "an older build"
            raise AttributeError(msg)

        backend.built(build, lambda: None)

    stage = types.SimpleNamespace(on_engine_switch=lambda active: broken(active) if active == "rust" else None)
    backend.register(stage)
    svc = _service(tmp_path, log=lines.append)  # the stage is there when the service first switches
    svc.engine_set("rust")
    assert backend.active() == "numpy"
    assert backend.silent() is not None
    assert not [line for line in lines if "rust reads" in line]
    svc.close()


@pytest.mark.usefixtures("rust")
def test_closing_the_service_stops_listening_for_failures(tmp_path):
    svc = _service(tmp_path)
    assert backend.on_failure is not None
    svc.close()
    assert backend.on_failure is None


def _wait(predicate, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(0.01)
    pytest.fail("timed out")


# -- the switch keeps the output continuous (the real extension) ------------------------------


def test_switching_at_the_cut_changes_nothing_audible():
    """numpy → Rust through the cut: the output equals staying with numpy within 1e-9."""
    pytest.importorskip("aurasync_engine")
    from aurasync import motor as motor_module

    installation = Instalacion(
        parlantes=[Parlante("A", "s0", pan=-0.7, retardo_ms=3.217), Parlante("B", "s1", pan=0.7, retardo_ms=11.5)]
    )
    rng = np.random.default_rng(11)
    blocks = [(rng.uniform(-0.5, 0.5, 1024), rng.uniform(-0.5, 0.5, 1024)) for _ in range(12)]

    def run(switch_to: str) -> dict[str, np.ndarray]:
        backend.reset()
        backend.use("numpy")
        m = motor_module.Motor(installation, 48000)
        out: dict[str, list[np.ndarray]] = {"A": [], "B": []}
        for i, (left, right) in enumerate(blocks):
            if i == 3:
                m.cortar(lambda: backend.use(switch_to))
            for name, block in m.procesar(left, right).items():
                out[name].append(block)
        assert backend.active() == switch_to
        return {name: np.concatenate(parts) for name, parts in out.items()}

    staying, switching = run("numpy"), run("rust")
    for name in staying:
        assert np.max(np.abs(switching[name] - staying[name])) <= 1e-9
