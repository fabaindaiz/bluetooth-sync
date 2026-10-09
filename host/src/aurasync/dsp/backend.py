"""Which engine runs the DSP stages that have a Rust port: numpy or Rust (spec rust-engine §2).

The numpy code stays and is the oracle; `aurasync_engine` (engine/crates/aurasync-engine, PyO3)
is an optional extension. Six stages have a port: the band-limited read of the delay line
(`interpolation.read`, which dispatches through `read` here), the spatial / front upmix
(`spatial.SpatialUpmix`), the ambience extractor (`ambience.Extractor`), the FIR filters by FFT
convolution (`eq.StreamingFIR`, `eq.PartitionedFIR`, under the EQ, the crossover, the bass stage,
the virtual bass, the diffuse tail and the decorrelator), the virtual bass's harmonic generator
(`virtual_bass.VirtualBass`, which owns two Rust FIRs directly) and the true-peak limiter
(`limiter.TruePeakLimiter`); each of the last five owns its Rust object and registers for the
switch.

**Choosing** (d-7c8794-196e0c). `"engine"` in `service.json` (`numpy` by default), overridden by
`AURASYNC_ENGINE` for the tests and the CLI (`wanted`). `resolve` turns the wish into what can
run: Rust without the extension, or built with constants other than numpy's, is numpy with a
`reason`, never an error. Without a service nobody calls `use`, and the first read takes
`AURASYNC_ENGINE` (so `AURASYNC_ENGINE=rust hatch test` runs the whole suite with Rust).

**Switching** happens only through `use`, which the service calls between two blocks (an order
on the engine thread, with no cut: every stage moves its state exactly between engines, spec
seamless-transitions 2026-10-08 §2), at the bottom of a cut after a failure, when a session
opens, or when no session plays. The sinc read keeps no state, so a switch leaves nothing
half-done.

**Failing.** The extension raises `EnginePanic` (a `RuntimeError`, as is its base `EngineError`
for a broken internal invariant) for a Rust panic (it catches every one at its boundary). Rust is disabled with the reason, and `on_failure` (the service) is told once; it asks
for a cut, whose bottom resolves again and gets numpy. From the failing block until that bottom
(`use`) every read is silence on every speaker (`silent()`; the cut's fade-out is going to zero
anyway), so numpy enters with the fade-in and the music goes on. During that window `active()`
says numpy (it does not claim that Rust reads) and the service reports the failure as the reason.
Without anyone listening (`on_failure` is None), nobody would cut, so the next read is numpy.
Building or configuring a stage's Rust object is softer (`built`): any `Exception` there falls back.
Rust stays disabled until `clear_failure` (the user chose it again) or `reset` (the program
restarted); `clear_failure` does not end the silence, only `use` does. Any other exception
in a per-block call (`ValueError`: a position out of range) is the caller's bug and propagates,
as numpy's own error would.

**Later stages** (spec §5) plug in the same way, with one contract for the silence window:
`rust_active()` says whether to call the stage's Rust object (built from `module()`); it is False
from the failing block until `use` runs at the cut's bottom. In that window `silent()` is True and
the stage returns silence; once it is False and `rust_active()` is False the stage is numpy.
`guarded(call, silence)` runs a per-block call with this same failure handling; `built(call,
fallback)` does it for construction and configuration (see there). A stage with state
registers (`register(stage)`) and hears `stage.on_engine_switch(active)` from `use`, on the engine
thread between blocks (or at a cut's bottom after a failure), to move or reset its state there;
it is held weakly, so a stage the motor drops (a render change builds a new one) is not kept
alive. A failure without a cut (nobody listening) calls no `use`: the stage notices that
`rust_active()` turned False on its next block.
The stage's constants join `_expected()` so a stale build is refused.

Every function here runs on the engine thread (the one that calls `motor.procesar` and the
service's orders), so the module's state needs no lock.
"""

from __future__ import annotations

import importlib
import logging
import os
import sys
import weakref
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, TypeVar

import numpy as np

from aurasync.dsp import interpolation

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import ModuleType

NUMPY = "numpy"
RUST = "rust"
ENGINES = (NUMPY, RUST)
ENV = "AURASYNC_ENGINE"
"""Overrides the `engine` setting (tests, the CLI)."""
EXTENSION = "aurasync_engine"
BUILD_HINT = "build it with `hatch run engine-build` in host/ (needs cargo, see engine/README.md)"

T = TypeVar("T")
_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Resolved:
    """What a wish for an engine gives: the engine that will read, whether the Rust extension can
    be used at all, and why `active` is not what was wanted (None when it is)."""

    active: str
    available: bool
    reason: str | None


on_failure: Callable[[str], None] | None = None
"""Told once, with the reason, when Rust fails; the service sets it and asks for a cut."""

_selected: str | None = None
"""The engine `use` chose; None until someone chooses (the first read takes `AURASYNC_ENGINE`)."""
_failure: str | None = None
"""Why Rust is disabled, after a failure."""
_silent: str | None = None
"""Rust failed and a cut was asked for: why. Every read is silence until `use` runs at its bottom;
only `use` ends it (not `clear_failure`)."""
_stages: list[Callable[[], Any]] = []
"""Stages with state that want to hear the switch (`register`): weak references, or a plain
holder for an object that cannot be weakly referenced."""
_module: ModuleType | None = None
_reader: Any = None
"""The extension's `Reader`, built on the first Rust read; dropped on a failure (a torn reader is
never used again) and rebuilt after the cut."""
_module_problem: str | None = None
_loaded = False


def _expected() -> dict[str, dict[str, Any]]:
    """The constants each ported stage must have been built with: numpy's own."""
    from aurasync.dsp import ambience, limiter, spatial  # noqa: PLC0415 - they import this module

    curve = ambience.Parametros()
    return {
        "interpolation": {
            "half": interpolation.HALF,
            "beta": interpolation.BETA,
            "steps": interpolation._STEPS,  # noqa: SLF001 - the table's resolution, shared with the port
        },
        "spatial": {
            "max_haas_ms": spatial.MAX_HAAS_MS,
            "fade_in": spatial.FADE_IN,
            "floor": spatial._FLOOR,  # noqa: SLF001 - shared with the port
            "silent": spatial._SILENT,  # noqa: SLF001 - shared with the port
            "front_boost_db": spatial.FRONT_AMBIENCE_BOOST_DB,
            "min_energy_ratio": curve.energia_minima,
            "mu0": curve.mu0,
            "mu1": curve.mu1,
            "sigma": curve.sigma,
        },
        "ambience": {
            "floor": ambience._PISO_NORMA,  # noqa: SLF001 - shared with the port
        },
        # The FIR filters share no constant with numpy: `version` is bumped (here and in
        # capabilities.rs) whenever the Rust behaviour of the stage changes, so a stale build is
        # refused here instead of failing on the first filter. Version 2 changed no filter: it is
        # there for a host from before `api` (main without a rebuild), which checks only this key
        # and would otherwise take a build without `read` and fail every block.
        "fir": {"version": 2},
        # Likewise the virtual bass (it owns two of those filters, its calibration comes per call).
        "virtual_bass": {"version": 1},
        # Likewise the true-peak limiter: its design values come from numpy per call; its two own
        # constants are numpy's, checked here like the other stages' constants.
        "limiter": {"version": 1, "margin_db": limiter.MARGIN_DB, "near_ceiling": limiter.NEAR_CEILING},
        # The binding's own Python-visible shape: bumped, here and in the Rust `capabilities`, when
        # it changes. Version 2: the exceptions `EngineError` and `EnginePanic`, the `Reader` class
        # in place of the module's `read`, keyword-only `set_params`, and no `_panic` function. A host
        # from before this key cannot check it: `fir` at 2 is what makes such a host refuse the build.
        "api": {"version": 2},
    }


def _load() -> None:
    """Import the extension once and check its constants; remember why it cannot be used."""
    global _module, _module_problem, _loaded  # noqa: PLW0603 - the module is the engine's single state
    if _loaded:
        return
    _loaded = True
    try:
        module = importlib.import_module(EXTENSION)
    except ImportError as exc:
        _module, _module_problem = None, f"the Rust extension is not installed ({exc}); {BUILD_HINT}"
        return
    try:
        built = module.capabilities()
    except Exception as exc:  # noqa: BLE001 - a broken build is reported, not raised
        _module, _module_problem = None, f"the Rust extension does not answer capabilities(): {exc!r}"
        return
    for stage, constants in _expected().items():
        if built.get(stage) != constants:
            _module = None
            _module_problem = (
                f"the Rust extension was built with other constants for {stage} "
                f"({built.get(stage)} instead of {constants}); {BUILD_HINT}"
            )
            return
    _module, _module_problem = module, None


def module() -> ModuleType | None:
    """The extension, when it can be used; None otherwise (`resolve` says why)."""
    _load()
    return _module


def wanted(setting: str | None) -> str:
    """The engine asked for: `AURASYNC_ENGINE` when set, else the setting, else numpy."""
    return os.environ.get(ENV) or setting or NUMPY


def resolve(name: str) -> Resolved:
    """The engine that `name` gives now, without changing anything."""
    _load()
    available = _module is not None
    if name not in ENGINES:
        return Resolved(NUMPY, available, f"unknown engine {name!r}; the engines are {list(ENGINES)}")
    if name == NUMPY:
        return Resolved(NUMPY, available, None)
    if not available:
        return Resolved(NUMPY, available, _module_problem)
    if _failure is not None:
        return Resolved(NUMPY, available, _failure)
    return Resolved(RUST, available, None)


def use(name: str) -> None:
    """Read with `name` from now on. Between two blocks (on the engine thread), at a cut's
    bottom after a failure, at a session's start, or with no session playing; `name` must be what
    `resolve` gave (Rust that cannot read is a `ValueError`)."""
    global _selected, _silent
    if name not in ENGINES:
        msg = f"unknown engine {name!r}; the engines are {list(ENGINES)}"
        raise ValueError(msg)
    if name == RUST:
        resolved = resolve(RUST)
        if resolved.active != RUST:
            raise ValueError(resolved.reason)
    _selected, _silent = name, None
    for ref in tuple(_stages):
        stage = ref()
        if stage is None:
            _stages.remove(ref)
            continue
        try:
            stage.on_engine_switch(name)
        except Exception:  # noqa: BLE001 - one stage's trouble must not undo the switch
            _log.exception("engine: %r failed to follow the switch to %s", stage, name)


def register(stage: Any) -> None:
    """`stage.on_engine_switch(active)` is called from every `use`, on the engine thread
    between blocks (or at a cut's bottom after a failure), so a stage with state can move or
    reset it there. Held weakly: registering
    does not keep the stage alive, and the dead ones are dropped here too (filters built and
    dropped block after block must not pile up between two `use`)."""
    _stages[:] = [ref for ref in _stages if ref() is not None]
    if any(ref() is stage for ref in _stages):
        return
    try:
        ref: Callable[[], Any] = weakref.ref(stage)
    except TypeError:  # no weak references (a SimpleNamespace): held as is

        def ref(stage: Any = stage) -> Any:
            return stage

    _stages.append(ref)


def unregister(stage: Any) -> None:
    _stages[:] = [ref for ref in _stages if ref() is not stage]


def active() -> str:
    """The engine reading now. While the output is silent after a Rust failure nobody reads:
    numpy is reported (it takes over at the cut's bottom) and `silent()` says why."""
    if _selected is None:
        _choose_from_environment()
    return NUMPY if _silent is not None else _selected


def silent() -> str | None:
    """Why every read is silence until the cut's bottom (a Rust failure); None when it is not."""
    return _silent


def rust_active() -> bool:
    """Whether a ported stage should call Rust now: chosen, not failed and not in the silent
    window that ends at the cut's bottom (see the module's note on later stages)."""
    return active() == RUST and _failure is None and _silent is None


def failure() -> str | None:
    """Why Rust is disabled, after a failure; None if it has not failed."""
    return _failure


def clear_failure() -> None:
    """The user chose Rust again: let it be tried. A silence already under way goes on until the
    cut's bottom (`use`): this does not touch it."""
    global _failure  # noqa: PLW0603
    _failure = None


def reset() -> None:
    """Back to how the program starts: nothing chosen, no failure, the extension not yet looked
    at, nobody listening. For the tests (the service sets everything when it starts)."""
    global _selected, _failure, _silent, _module, _module_problem, _loaded, _reader, on_failure  # noqa: PLW0603
    _selected, _failure, _silent, _reader = None, None, None, None
    _module, _module_problem, _loaded = None, None, False
    on_failure = None
    _stages.clear()


def _choose_from_environment() -> None:
    global _selected  # noqa: PLW0603
    resolved = resolve(wanted(None))
    if resolved.reason is not None:
        _log.warning("engine %s: using numpy (%s)", wanted(None), resolved.reason)
    _selected = resolved.active


def guarded(call: Callable[[], T], silence: Callable[[], T]) -> T:
    """Run a Rust stage's `call`; if Rust fails (`RuntimeError`), disable it and return `silence()`.

    After a failure the stage must not call Rust again (`rust_active()` is False) and gives
    silence (`silent()`) until `use` runs at the cut's bottom, or numpy at once when nobody cuts."""
    try:
        return call()
    except RuntimeError as exc:
        _fail(f"Rust failed ({exc}); numpy reads from the next cut")
        return silence()


def built(call: Callable[[], T], fallback: Callable[[], T]) -> T:
    """Build or configure a stage's Rust object (constructor, `set_params`, `set_layout`, `state`,
    `set_state`, `reset`); if it raises **any** `Exception`, disable Rust and return `fallback()`.

    Use this, not `guarded`, outside the per-block path. There the extension may raise things that
    are not a Rust panic and are not a caller's bug: `ValueError` / `TypeError` from PyO3 argument
    conversion, `AttributeError` because an older build lacks the class or method. The session must
    not die of them, so they are a failure exactly like `RuntimeError` in `guarded` (`_fail`:
    the handler is told once, silence until the cut's bottom, numpy after), with the exception's
    type and message as the reason and the traceback in the log. `BaseException` still propagates.

    Keep `guarded` for the per-block processing calls: a `TypeError` there is a caller's bug and
    must stay visible (controller ruling, 2026-10-08)."""
    try:
        return call()
    except Exception as exc:  # noqa: BLE001 - the whole point: nothing from building takes the session down
        _log.exception("engine: building or configuring a Rust stage raised %s", type(exc).__name__)
        _fail(f"Rust failed ({type(exc).__name__}: {exc}); numpy reads from the next cut")
    return fallback()


def log_switch_failure(exc: Exception) -> None:
    """A stage could not read its Rust state while the switch's target is numpy: nothing to report
    to the failure handler (the user already chose numpy and the stage restarts there), only a log."""
    _log.warning("engine: reading the Rust state at the switch to numpy raised %s: %s", type(exc).__name__, exc)


def _fail(reason: str) -> None:
    global _failure, _silent, _selected  # noqa: PLW0603
    if _failure is not None:
        return
    _failure = reason
    _log.error("engine: %s", reason)
    handler = on_failure
    if handler is None:
        # Nobody will cut: numpy from the next block.
        _selected = NUMPY
        return
    _silent = reason
    try:
        handler(reason)
    except Exception:  # noqa: BLE001 - whatever the handler did, the music must go on
        _log.exception("engine: the failure handler failed; numpy from the next block")
        _selected, _silent = NUMPY, None


def _rust_read(data: np.ndarray, position: np.ndarray) -> np.ndarray:
    """The extension's `Reader.read`; a failure drops the reader, so a torn one is never used again.

    The reader is built through `built` (any exception from its constructor disables Rust and is
    reported), and the read gives silence when it could not be."""
    global _reader  # noqa: PLW0603
    if _reader is None:
        _reader = built(lambda: _module.Reader(), lambda: None)
        if _reader is None:
            return np.zeros(np.shape(position))
    try:
        return _reader.read(data, position)
    except RuntimeError:
        # Only a Rust failure tears the reader; a `ValueError` (a position out of range) is the
        # caller's bug, raised before anything is written, and the reader stays usable.
        _reader = None
        raise


def read(data: np.ndarray, position: np.ndarray) -> np.ndarray:
    """`interpolation.read` with the active engine: numpy's `read_numpy`, or Rust's `Reader.read`."""
    if _silent is not None:
        return np.zeros(np.shape(position))
    if rust_active():
        # Rust takes float64 C-contiguous only (and refuses anything else with TypeError): convert
        # here, so a caller's dtype is never mistaken for a Rust failure. No copy when it already is.
        data = np.ascontiguousarray(data, dtype=np.float64)
        position = np.ascontiguousarray(position, dtype=np.float64)
        return guarded(lambda: _rust_read(data, position), lambda: np.zeros(np.shape(position)))
    return interpolation.read_numpy(data, position)


def main(argv: list[str] | None = None) -> int:
    """`python -m aurasync.dsp.backend [--check-production]`: what `rust` resolves to here.

    `--check-production` (the default environment's `engine-build`) also fails when the extension
    is missing or carries the `test-panic` feature, which only the test build may have."""
    args = sys.argv[1:] if argv is None else argv
    resolved = resolve(RUST)
    if "--check-production" in args:
        if _module is None:
            print(f"aurasync_engine: {resolved.reason}", file=sys.stderr)  # noqa: T201
            return 1
        if hasattr(_module, "_panic_outside_the_read"):
            print(  # noqa: T201
                "aurasync_engine: this build carries the `test-panic` feature (`_panic_outside_the_read`); "
                "the service's build must not: rebuild with `hatch run engine-build`",
                file=sys.stderr,
            )
            return 1
    print(f"engine rust: {'available' if resolved.active == RUST else resolved.reason}")  # noqa: T201
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
