"""Fixtures shared by the host's tests: the engine of the stages ported to Rust (`dsp/backend.py`)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pytest

from aurasync import priority
from aurasync.dsp import backend

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture(autouse=True)
def _engine_as_the_program_starts() -> Iterator[None]:
    """The backend's choice is the process's: each test leaves it as a fresh program has it
    (nothing chosen, no failure, nobody listening), so one test's service cannot steer the next."""
    yield
    backend.reset()


@dataclass
class Engine:
    name: str
    rust_calls: list[int] = field(default_factory=list)
    """One entry per call into the extension's `Reader.read`: proof that Rust really ran."""


@pytest.fixture(autouse=True)
def _no_real_priority(request, monkeypatch) -> None:
    """No test changes a thread's priority on the machine: `service.json` asks for -15 by default
    (`priority.py`), and a service started by a test would otherwise call RealtimeKit for real.
    `test_priority.py` tests the real function with its own fakes."""
    if request.module.__name__.endswith("test_priority"):
        return
    monkeypatch.setattr(
        priority,
        "raise_engine_priority",
        lambda wanted, **_: priority.PriorityResult(wanted, None, None, "tests do not change priorities"),
    )


@pytest.fixture(params=[backend.NUMPY, backend.RUST])
def engine(request, monkeypatch) -> Engine:
    """Runs a test once per engine; Rust only when the extension is built (it always is in the
    test environment, which builds it before pytest: host/pyproject.toml)."""
    chosen = Engine(request.param)
    backend.reset()
    if request.param == backend.RUST:
        extension = pytest.importorskip("aurasync_engine")
        original = extension.Reader

        class Counting:
            """`Reader` with a count of its reads (a pyclass cannot be patched in place)."""

            def __init__(self, *args, **kwargs) -> None:
                self._reader = original(*args, **kwargs)

            def read(self, data, position):
                chosen.rust_calls.append(len(position))
                return self._reader.read(data, position)

        monkeypatch.setattr(extension, "Reader", Counting)
    backend.use(request.param)
    return chosen
