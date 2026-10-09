"""The engine selector in Ajustes and the engine in Diagnóstico (spec rust-engine §2). SIMULATED.

The browser environment does not build the Rust extension, so `aurasync_engine` is a stand-in
here (`tests/engine_stand_in.py`: numpy behind the extension's interface, for the read and every
ported stage): what is tested is the panel and the service's switch, not Rust
(`tests/test_engine_rust.py` tests that; `tests/test_engine_stand_in.py` runs the motor on the
stand-in, so it cannot fall behind the extension's interface unnoticed).
"""

from __future__ import annotations

import re
import sys
import time
import types
from collections.abc import Iterator

import pytest
from playwright.sync_api import Page, expect

from aurasync.dsp import backend
from tests import engine_stand_in

from .test_panel import TOKEN, Running


@pytest.fixture
def rust(monkeypatch) -> Iterator[types.ModuleType]:
    """Before `svc`: the service looks for the extension when it starts."""
    module = engine_stand_in.install(monkeypatch)
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
