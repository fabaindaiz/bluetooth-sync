"""Fixtures shared by the host's tests: the engine of the stages ported to Rust (`dsp/backend.py`)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pytest

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
    """One entry per call into the extension's `read`: proof that Rust really ran."""


@pytest.fixture(params=[backend.NUMPY, backend.RUST])
def engine(request, monkeypatch) -> Engine:
    """Runs a test once per engine; Rust only when the extension is built (it always is in the
    test environment, which builds it before pytest: host/pyproject.toml)."""
    chosen = Engine(request.param)
    backend.reset()
    if request.param == backend.RUST:
        extension = pytest.importorskip("aurasync_engine")
        original = extension.read

        def counting(data, position):
            chosen.rust_calls.append(len(position))
            return original(data, position)

        monkeypatch.setattr(extension, "read", counting)
    backend.use(request.param)
    return chosen
