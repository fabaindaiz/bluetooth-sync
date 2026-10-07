"""Audit harness: the simulated service (the tests' own `Running`) and a page in a realistic state.

Never touches hardware: `Running` builds `Service(simulated=True)` with SimulatedSession,
SimulatedObserver, SimulatedRadio, SimulatedVolumes and SimulatedMonitor (no PipeWire).
"""

from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path

HOST = Path("/home/fadiaz/Desktop/Project/bluetooth-sync/host")
sys.path.insert(0, str(HOST))
sys.path.insert(0, str(HOST / "src"))

from playwright.sync_api import Page, expect  # noqa: E402

from tests_browser.test_panel import TOKEN, Running  # noqa: E402

AUDIT = Path(__file__).resolve().parent.parent
TABS = ["Escuchar", "Cadena", "Parlantes", "Calibrar", "Diagnóstico", "Ajustes"]
TAB_IDS = ["escuchar", "cadena", "parlantes", "calibrar", "diagnostico", "ajustes"]


def service() -> tuple[Running, tempfile.TemporaryDirectory]:
    tmp = tempfile.TemporaryDirectory(dir=AUDIT / "data")
    return Running(Path(tmp.name)), tmp


def open_panel(page: Page, svc: Running) -> None:
    page.goto(f"{svc.url}/?t={TOKEN}")
    expect(page.locator("#connection")).to_have_text(re.compile("En vivo|Consultando"))
    expect(page.locator("body[data-ready='1']")).to_be_attached()


def go(page: Page, view_id: str) -> None:
    page.evaluate("(id) => { location.hash = 'v=' + id; }", view_id)
    btn = page.locator(f"[data-goto='{view_id}']:visible").first
    btn.click()
    page.wait_for_timeout(400)


def realistic(page: Page, svc: Running) -> None:
    """Playing, a virtual speaker added, the headphone monitor on (simulated), meters running."""
    go(page, "parlantes")
    page.get_by_role("button", name="Agregar parlante virtual").click()
    page.wait_for_timeout(500)
    go(page, "escuchar")
    page.locator("#run").click()
    expect(page.locator("#run")).to_have_text("Detener", timeout=15000)
    page.locator("#monitor-target").select_option("simulated_headphones")
    page.locator("#monitor-mode").select_option("binaural")
    page.wait_for_timeout(2500)
