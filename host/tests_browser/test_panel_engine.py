"""The engine selector in Ajustes and the engine in Diagnóstico (spec rust-engine §2). SIMULATED.

The browser environment does not build the Rust extension, so `aurasync_engine` is a stand-in
here (numpy's read behind the extension's interface): what is tested is the panel and the
service's switch through the cut, not Rust (`tests/test_engine_rust.py` tests that).
"""

from __future__ import annotations

import re
import sys
import time
import types
from collections.abc import Iterator

import pytest
from playwright.sync_api import Page, expect

from aurasync.dsp import ambience, backend, interpolation, spatial

from .test_panel import TOKEN, Running


def _stand_in() -> types.ModuleType:
    module = types.ModuleType("aurasync_engine")
    module.calls = 0

    def read(data, position):
        module.calls += 1
        return interpolation.read_numpy(data, position)

    module.read = read
    # Every ported stage's constants as numpy has them (the spatial upmix and the ambience
    # extractor too since they were ported): a stand-in that lists only one reads as stale.
    module.capabilities = backend._expected  # noqa: SLF001
    return module


class _Through:
    """The stand-in for a ported stage's Rust object (`SpatialUpmix`, `AmbienceExtractor`): the
    extension's interface over the owning stage's own numpy path, so the session plays through the
    switch. Its state is the owner's, so the switch back to numpy carries it unchanged."""

    def __init__(self, owner) -> None:
        self.owner = owner

    def set_params(self, *_args) -> None:
        pass

    def set_layout(self, *_args) -> None:
        pass

    def set_state(self, _state) -> None:
        pass

    def reset(self) -> None:
        pass

    def state(self) -> dict:
        return self.owner._numpy_state()  # noqa: SLF001

    def process(self, left, right):
        owner = self.owner
        if isinstance(owner, ambience.Extractor):
            return owner._procesar_numpy(left, right)  # noqa: SLF001
        out = owner._process_numpy(left, right)  # noqa: SLF001
        return [out[n][0] for n in owner.names], [out[n][1] for n in owner.names]


@pytest.fixture
def rust(monkeypatch) -> Iterator[types.ModuleType]:
    """Before `svc`: the service looks for the extension when it starts."""
    module = _stand_in()
    monkeypatch.setitem(sys.modules, "aurasync_engine", module)
    monkeypatch.setattr(spatial.SpatialUpmix, "_build_rust", lambda stage: _Through(stage))
    monkeypatch.setattr(ambience.Extractor, "_build_rust", lambda stage: _Through(stage))
    backend.reset()
    yield module
    backend.reset()


@pytest.fixture
def no_rust(monkeypatch) -> Iterator[None]:
    monkeypatch.setitem(sys.modules, "aurasync_engine", None)
    backend.reset()
    yield
    backend.reset()


def _show(page: Page, card: str) -> None:
    assert page.evaluate("(s) => window.aurasyncShow(s)", f'[data-card="{card}"]')
    expect(page.locator(f'[data-card="{card}"]')).to_be_visible()


def _wait(cond, timeout=5.0):
    end = time.monotonic() + timeout
    while not cond() and time.monotonic() < end:
        time.sleep(0.05)
    assert cond()


def test_the_selector_switches_and_diagnostico_shows_the_active_engine(rust, page: Page, svc: Running):
    svc.command("start")
    page.goto(f"{svc.url}/?t={TOKEN}")
    expect(page.locator("body[data-ready='1']")).to_be_attached()
    _show(page, "config")
    select = page.locator("#engine-select")
    expect(select).to_have_value("numpy")
    select.select_option("rust")
    _wait(lambda: svc.service.engine_wanted == "rust")
    # The switch waits for the cut's bottom; then Rust reads, and the panel says so.
    expect(page.locator("#engine-state")).to_have_text("Lee con rust.", timeout=5000)
    _wait(lambda: rust.calls > 0)
    _show(page, "health")
    expect(page.locator("#t-engine")).to_have_text("rust")
    expect(page.locator("#t-engine-sub")).to_have_text("la lectura del retardo de cada parlante")
    # And back to numpy.
    _show(page, "config")
    select.select_option("numpy")
    expect(page.locator("#engine-state")).to_have_text("Lee con numpy.", timeout=5000)
    _show(page, "health")
    expect(page.locator("#t-engine")).to_have_text("numpy")
    assert svc.state()["session"]["status"] == "playing"


def test_rust_without_the_extension_stays_numpy_and_says_why(no_rust, page: Page, svc: Running):
    svc.command("start")
    page.goto(f"{svc.url}/?t={TOKEN}")
    expect(page.locator("body[data-ready='1']")).to_be_attached()
    _show(page, "config")
    expect(page.locator("#engine-state")).to_contain_text("Rust no está compilado")
    page.locator("#engine-select").select_option("rust")
    state = page.locator("#engine-state")
    expect(state).to_contain_text("Lee con numpy, no con rust", timeout=5000)
    expect(state).to_contain_text("not installed")
    expect(page.locator("#engine-select")).to_have_value("rust")
    _show(page, "health")
    expect(page.locator("#t-engine")).to_have_text("numpy")
    expect(page.locator("#t-engine")).to_have_class(re.compile(r"\bwarn\b"))
