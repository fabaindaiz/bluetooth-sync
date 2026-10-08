"""The chain refactor changes no sound: the engine reproduces the golden run (spec §4.2).

`tests/data/golden_motor.npz` was recorded by `tests/golden_motor.py` with the engine as it
was before the chain existed. With the chain at its defaults the engine must reproduce it
within 1e-9. Seen to fail on 2026-10-02 with a stage broken on purpose, twice: the
extractor's `lam` default moved from 0.9 to 0.91 in `chain.py` (4 of 4 runs red, max |diff|
5.1e-4), and the limiter's ceiling read 0.01 dB lower in `motor.py` (4 of 4 red, 1.0e-3).
Green again each time the plant was taken out.

Since 2026-10-08 the run plays with the `transition` stage in its `cut` mode
(`golden_motor.run`): its slow changes were recorded as cuts, and that path must stay exactly
as it was; the default crossfade has its own tests (`tests/test_motor_transitions.py`).

It runs once per engine of the stages ported to Rust (`engine` in conftest.py): the Rust read
must reproduce the same golden within the same 1e-9.
"""

import numpy as np
import pytest

from aurasync import motor
from aurasync.chain import ChainValues
from aurasync.dsp import backend
from tests import golden_motor

TOLERANCE = 1e-9


@pytest.fixture(scope="module")
def golden():
    with np.load(golden_motor.PATH) as data:
        return {key: data[key] for key in data.files}


def _with_chain(inst, sr, ecualizar):
    return motor.Motor(inst, sr, ecualizar=ecualizar, chain=ChainValues())


@pytest.mark.parametrize("factory", [golden_motor.legacy_motor, _with_chain], ids=["chain=None", "chain=defaults"])
@pytest.mark.parametrize("label", list(golden_motor.CONFIGS))
def test_the_engine_reproduces_the_golden_run(golden, factory, label, engine):
    out = golden_motor.run(factory, ecualizar=golden_motor.CONFIGS[label])
    for name, x in out.items():
        expected = golden[f"{label}/{name}"]
        assert len(x) == len(expected)
        diff = float(np.max(np.abs(x - expected)))
        assert diff <= TOLERANCE, f"{label}/{name}: max |diff| = {diff:.3g}"
    # With Rust, the delay reads really went through it, and it never failed over to numpy.
    assert bool(engine.rust_calls) == (engine.name == "rust")
    assert backend.active() == engine.name
    assert backend.failure() is None


def test_the_golden_run_exercises_what_it_claims(golden):
    """A golden that never reached the limiter or the cut would prove less than it says."""
    ceiling = 10 ** (-1 / 20)
    for label in golden_motor.CONFIGS:
        loud = [golden[f"{label}/{n}"] for n in ("L", "R")]
        assert all(np.isclose(np.abs(x).max(), ceiling, atol=1e-9) for x in loud)
        # The cut's bottom is exact silence, on every speaker.
        assert all((golden[f"{label}/{n}"][2048:] == 0).sum() > 1000 for n in ("L", "R", "B"))
