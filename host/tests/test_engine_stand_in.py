"""The browser tests' stand-in for the extension (`tests/engine_stand_in.py`) keeps up with it.

`tests_browser/test_panel_engine.py` switches a playing service to Rust on the stand-in, and it
runs only where Playwright's browsers are installed. Here the motor plays on the stand-in under
`engine=rust`, chain by chain, so a stage that calls the extension in a way the stand-in does not
answer (a new class, a renamed call, keyword arguments) fails on every machine: Rust would be
disabled with a reason, which these tests refuse. The output must be numpy's, sample for sample,
since the stand-in is numpy's own path.
"""

from __future__ import annotations

import importlib
import pkgutil

import numpy as np
import pytest

import aurasync.dsp
from aurasync import chain, motor
from aurasync.chain import ChainValues
from aurasync.config import Instalacion, Parlante
from aurasync.dsp import backend
from tests import engine_stand_in

SR = 48_000
BLOCK = 4096


@pytest.fixture(autouse=True)
def _clean_backend():
    backend.reset()
    yield
    backend.reset()


def _installation() -> Instalacion:
    # The browser tests' three speakers (tests_browser/test_panel.py `THREE`).
    return Instalacion(
        parlantes=[
            Parlante("JBL Go 4 Red", "s0", pan=-0.7, ambiente=0.15, tipo="go4"),
            Parlante("JBL Go 4 Black", "s1", pan=0.7, ambiente=0.15, tipo="go4"),
            Parlante("JBL Go 4 Blue", "s2", pan=0.0, ambiente=0.55, tipo="go4"),
        ]
    )


def _play(values: ChainValues, blocks: int = 6) -> dict[str, np.ndarray]:
    rng = np.random.default_rng(41)
    left, right = rng.uniform(-0.9, 0.9, blocks * BLOCK), rng.uniform(-0.9, 0.9, blocks * BLOCK)
    m = motor.Motor(_installation(), SR, semilla=1, ecualizar=True, chain=values, bloque=BLOCK)
    got: dict[str, list[np.ndarray]] = {p.nombre: [] for p in m.instalacion.parlantes}
    for i in range(0, len(left), BLOCK):
        for name, x in m.procesar(left[i : i + BLOCK], right[i : i + BLOCK]).items():
            got[name].append(x)
    return {name: np.concatenate(x) for name, x in got.items()}


def _with(values: ChainValues, stage: str, **params) -> ChainValues:
    return values.with_change(chain.validate_set(stage, params=params))


_ALWAYS = {"Extractor", "StreamingFIR"}
"""What every chain here runs through the stand-in: the ambience extractor, the per-speaker EQ and
the decorrelator."""
CHAINS = {
    "default": (ChainValues(), _ALWAYS),
    "spatial-true-peak": (
        ChainValues().with_algorithm("spatial", "spatial").with_algorithm("limiter", "true_peak"),
        _ALWAYS | {"SpatialUpmix", "TruePeakLimiter"},
    ),
    "front": (ChainValues().with_algorithm("spatial", "front"), _ALWAYS | {"SpatialUpmix"}),
    "protect-with-harmonics": (
        _with(ChainValues().with_algorithm("bass", "protect"), "bass", harmonics_db=0.0),
        _ALWAYS | {"VirtualBass"},
    ),
    "crossover-and-diffuse": (
        ChainValues().with_algorithm("bass", "crossover").with_algorithm("diffuse", "noise_tail"),
        _ALWAYS | {"PartitionedFIR"},
    ),
}


@pytest.mark.parametrize(("values", "stages"), CHAINS.values(), ids=CHAINS.keys())
def test_the_motor_plays_on_the_stand_in_under_rust_without_a_failure(monkeypatch, values, stages):
    want = _play(values)
    module = engine_stand_in.install(monkeypatch)
    backend.use(backend.RUST)
    got = _play(values)
    assert backend.failure() is None
    assert backend.silent() is None
    assert backend.active() == backend.RUST
    assert module.calls > 0  # the delay line read through the stand-in's `Reader`
    assert stages <= {type(through.owner).__name__ for through in module.throughs}
    for name, x in want.items():
        assert np.any(x)
        assert np.array_equal(got[name], x), name


def test_every_stage_with_a_rust_object_is_covered():
    """A stage that gains a Rust object (`_build_rust`) must join `OWNERS`, or the stand-in would
    let it call the missing class and disable Rust."""
    found = set()
    for info in pkgutil.iter_modules(aurasync.dsp.__path__):
        module = importlib.import_module(f"aurasync.dsp.{info.name}")
        for name, value in vars(module).items():
            if (
                isinstance(value, type)
                and not name.startswith("_")
                and value.__module__ == module.__name__
                and "_build_rust" in vars(value)
            ):
                found.add(value)
    assert found == set(engine_stand_in.OWNERS)
