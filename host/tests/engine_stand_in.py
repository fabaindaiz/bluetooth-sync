"""A stand-in for `aurasync_engine`: numpy behind the extension's interface. SIMULATED.

The browser tests (`tests_browser/test_panel_engine.py`) do not build the Rust extension, but they
switch the service to Rust through the panel: what they test is the panel and the switch, so every
ported stage needs a Rust object that answers. `install` puts this module in `sys.modules` and
makes each stage that owns a Rust object own a `Through` instead: the extension's interface over
the owner's own numpy path, so the session plays the same through the switch and the state stays
the owner's. `tests/test_engine_stand_in.py` runs the motor on it, so a change to the extension's
interface (a new stage, a renamed call, keyword arguments) breaks a test that runs on this machine,
not only the browser suite.
"""

from __future__ import annotations

import sys
import types
from typing import Any

from aurasync.dsp import ambience, backend, eq, interpolation, limiter, spatial, virtual_bass

OWNERS = (
    spatial.SpatialUpmix,
    ambience.Extractor,
    eq.StreamingFIR,
    eq.PartitionedFIR,
    virtual_bass.VirtualBass,
    limiter.TruePeakLimiter,
)
"""Every stage that owns a Rust object (`_build_rust`)."""


def stand_in() -> types.ModuleType:
    """The module: `Reader` (numpy's read, counted in `calls`) and every stage's constants as numpy
    has them (`capabilities`), so the backend takes it as a current build."""
    module = types.ModuleType("aurasync_engine")
    module.calls = 0

    def read(data, position):
        module.calls += 1
        return interpolation.read_numpy(data, position)

    class Reader:
        """`aurasync_engine.Reader`'s interface, reading through `module.read`."""

        def __init__(self, max_block: int = 8192) -> None:
            del max_block

        def read(self, data, position):
            return module.read(data, position)

    module.read = read
    module.Reader = Reader
    module.capabilities = backend._expected  # noqa: SLF001
    return module


class Through:
    """A ported stage's Rust object, done by the owner's own numpy path. The configuration calls do
    nothing (the owner has already changed its own values when it makes them), and the state is the
    owner's, so the switch back to numpy carries it unchanged."""

    def __init__(self, owner: Any) -> None:
        self.owner = owner

    def set_params(self, *_args: Any, **_kwargs: Any) -> None:
        pass

    def set_layout(self, *_args: Any, **_kwargs: Any) -> None:
        pass

    def configure(self, *_args: Any, **_kwargs: Any) -> None:
        pass

    def set_taps(self, _taps: Any) -> None:
        pass

    def replace_taps(self, _taps: Any) -> None:
        pass

    def set_state(self, _state: Any) -> None:
        pass

    def reset(self) -> None:
        owner = self.owner
        if isinstance(owner, virtual_bass.VirtualBass):
            # The Rust object's filters rest; here they are the owner's numpy ones.
            owner._fir_band = eq.PartitionedFIR(owner._band, owner.block)  # noqa: SLF001
            owner._fir_out = eq.PartitionedFIR(owner._out, owner.block)  # noqa: SLF001

    def state(self) -> dict:
        return self.owner._numpy_state()  # noqa: SLF001

    def skip(self, x: Any) -> None:
        self.owner._skip_numpy(x)  # noqa: SLF001

    def process(self, *args: Any) -> Any:
        owner = self.owner
        if isinstance(owner, ambience.Extractor):
            return owner._procesar_numpy(*args)  # noqa: SLF001
        if isinstance(owner, spatial.SpatialUpmix):
            out = owner._process_numpy(*args)  # noqa: SLF001
            return [out[n][0] for n in owner.names], [out[n][1] for n in owner.names]
        if isinstance(owner, limiter.TruePeakLimiter):
            out = owner._process_numpy(*args)  # noqa: SLF001
            return out, owner.gain, owner.max_reduction_db, owner.active_fraction
        if isinstance(owner, virtual_bass.VirtualBass):
            x, _current, target = args
            made = owner._process_numpy(x, target)  # noqa: SLF001
            # The energies only make `added_db`, which the numpy path has just set.
            if owner.added_db is None:
                return made, 0.0, 0.0
            return made, 1.0, 10 ** (owner.added_db / 10)
        return owner._process_numpy(*args)  # noqa: SLF001 - the two FIR filters


def install(monkeypatch: Any) -> types.ModuleType:
    """The stand-in in `sys.modules`, every owner building a `Through` (kept in the module's
    `throughs`), and the backend reset (so the next look at the extension finds it). Undone by
    `monkeypatch`; the caller resets the backend."""
    module = stand_in()
    module.throughs = []

    def build(stage: Any) -> Through:
        through = Through(stage)
        module.throughs.append(through)
        return through

    monkeypatch.setitem(sys.modules, "aurasync_engine", module)
    for owner in OWNERS:
        monkeypatch.setattr(owner, "_build_rust", build)
    backend.reset()
    return module
