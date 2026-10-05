"""Usability, step 2: the flows a person does, done in the panel as a person would (Chromium, the
simulated service). Per flow: taps (clicks and selections), tab changes, scrolling (in screens),
whether it ended where it should, and the dead ends seen on the way. From host/:

    hatch run browser:python ../probes/18-usabilidad/flujos.py [--pc] [--json OUT.json]

Each flow starts on Escuchar, at the top, with the session stopped (as the panel opens). A tap on a
control that is not on screen scrolls to it first, and that scroll is counted (like
probes/10-panel-organizacion/medir.py, but over the real clicks of the flow, in order).
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

sys.path.insert(0, ".")
from playwright.sync_api import Page, expect, sync_playwright

from tests_browser.test_panel import THREE, TOKEN, Running

TABS = ("Escuchar", "Cadena", "Parlantes", "Calibrar", "Diagnóstico", "Ajustes")


@dataclass
class Run:
    flow: str
    ok: bool = False
    taps: int = 0
    tabs: int = 0
    screens: float = 0.0
    seconds: float = 0.0
    notes: list[str] = field(default_factory=list)


class Person:
    """Taps like a person: scrolls a control into view first, and counts what it cost."""

    def __init__(self, page: Page, run: Run) -> None:
        self.page, self.run = page, run

    def _scroll_to(self, locator) -> None:
        box = locator.bounding_box()
        vh = self.page.viewport_size["height"]
        if box is None:
            self.run.notes.append("no se ve un control que hacía falta")
            return
        top = self.page.evaluate("document.getElementById('topbar').getBoundingClientRect().height")
        if box["y"] < top or box["y"] + box["height"] > vh - 70:
            before = self.page.evaluate("scrollY")
            locator.scroll_into_view_if_needed()
            self.page.evaluate("(t) => window.scrollBy(0, -t)", 0)
            after = self.page.evaluate("scrollY")
            self.run.screens += abs(after - before) / vh

    def tab(self, name: str) -> None:
        self.page.get_by_role("button", name=name, exact=True).first.click()
        self.run.tabs += 1
        self.page.evaluate("window.scrollTo(0, 0)")

    def tap(self, locator) -> None:
        self._scroll_to(locator)
        if not locator.is_visible():
            self.run.notes.append(f"un control quedó fuera de la vista: {locator}")
        locator.click()
        self.run.taps += 1

    def choose(self, locator, value: str) -> None:
        self._scroll_to(locator)
        locator.select_option(value)
        self.run.taps += 1

    def fill(self, locator, text: str) -> None:
        self._scroll_to(locator)
        locator.fill(text)
        self.run.taps += 1


def playing(page: Page) -> None:
    expect(page.locator("#run")).to_have_text("Detener", timeout=10000)


def flow_play(p: Person, svc: Running) -> bool:
    """Poner música: iniciar, elegir la fuente, bajar el volumen."""
    p.tap(p.page.locator("#run"))
    playing(p.page)
    p.choose(p.page.locator("#source-kind"), "tone")
    p.page.locator("#volume").evaluate("(n) => { n.value = -30; n.dispatchEvent(new Event('input', { bubbles: true })); n.dispatchEvent(new Event('change', { bubbles: true })); }")
    p.run.taps += 1
    deadline = time.monotonic() + 3
    while svc.state()["global"]["volume_db"] != -30 and time.monotonic() < deadline:  # noqa: PLR2004
        time.sleep(0.1)
    return svc.state()["global"]["volume_db"] == -30  # noqa: PLR2004


def flow_mode(p: Person, svc: Running) -> bool:
    """Probar el modo espacial (lo que el usuario quiere oír distinto)."""
    p.tap(p.page.locator("#run"))
    playing(p.page)
    selector = p.page.locator("[data-render-quick]")
    if selector.count() and selector.first.is_visible():
        p.choose(selector.first, "spatial")
    else:
        p.run.notes.append("el modo no está a la vista: hay que buscarlo en Parlantes → Espacial")
        p.tab("Parlantes")
        p.choose(p.page.locator("#spatial-render"), "spatial")
    time.sleep(0.5)
    return svc.service.settings.chain.algorithm("spatial") == "spatial"


def flow_quiet_speaker(p: Person, svc: Running) -> bool:
    """Un parlante no suena: identificarlo con un tono y silenciarlo."""
    p.tap(p.page.locator("#run"))
    playing(p.page)
    name = svc.service.installation.parlantes[2].nombre
    row = p.page.locator(f'[data-quick="{name}"]')
    title = row.locator(".speaker-name").inner_text()
    if title.endswith("…") or row.locator(".speaker-name").evaluate("(n) => n.scrollWidth > n.clientWidth"):
        p.run.notes.append(f"el nombre del parlante se corta («{title}»): no se distingue cuál es")
    p.tap(row.locator(".q-tone"))
    p.tap(row.locator(".q-mute"))
    time.sleep(0.5)
    return name in svc.service.settings.muted


def flow_calibrate(p: Person, svc: Running) -> bool:
    """Calibrar y aplicar, desde Escuchar (el atajo)."""
    p.tap(p.page.locator("#run"))
    playing(p.page)
    p.tap(p.page.locator("#now-calibrate"))
    gate = p.page.locator("#now-gate")
    if gate.is_visible():
        p.tap(gate.get_by_role("button", name="Calibrar igual"))
    expect(p.page.locator("#now-sync")).not_to_have_attribute("data-zone", "none", timeout=40000)
    return svc.state()["calibration"] is not None


def flow_preset(p: Person, svc: Running) -> bool:
    """Guardar cómo suena ahora y volver a eso después."""
    p.fill(p.page.locator("#preset-name"), "noche")
    p.tap(p.page.locator("#preset-save"))
    expect(p.page.locator("#presets li", has_text="noche")).to_have_count(1)
    p.tap(p.page.locator("#presets li", has_text="noche").get_by_role("button", name="Cargar"))
    return "noche" in svc.state()["presets"]


def flow_headphones(p: Person, svc: Running) -> bool:
    """Escuchar por audífonos mientras suenan los parlantes."""
    p.tap(p.page.locator("#run"))
    playing(p.page)
    p.choose(p.page.locator("#monitor-target"), "simulated_headphones")
    p.choose(p.page.locator("#monitor-mode"), "binaural")
    expect(p.page.locator("#monitor-state")).to_have_attribute("data-state", "on", timeout=8000)
    return True


def flow_add_speaker(p: Person, svc: Running) -> bool:
    """Sumar un parlante nuevo que está cerca."""
    p.tab("Parlantes")
    p.tap(p.page.locator("#scan"))
    row = p.page.locator("#devices tr", has_text="JBL Flip 7")
    expect(row).to_be_visible(timeout=5000)
    button = row.get_by_role("button", name="Emparejar y conectar")
    box = button.bounding_box()
    if box and box["x"] + box["width"] > p.page.viewport_size["width"]:
        p.run.notes.append("el botón «Emparejar y conectar» queda fuera de la pantalla: la tabla se corta")
    p.tap(button)
    time.sleep(1.5)
    return any(d["name"] == "JBL Flip 7" and d["paired"] for d in svc.state()["devices"])


def flow_room(p: Person, svc: Running) -> bool:
    """Decir dónde está cada parlante (tres parlantes)."""
    p.tab("Parlantes")
    fix = p.page.locator("[data-room-auto]")
    if fix.count() and fix.first.is_visible():
        p.tap(fix.first)  # the panel says a speaker has no place and offers «Automático»
    else:
        if p.page.get_by_text("sin rol", exact=False).count():
            p.run.notes.append("con 3 parlantes la sala de fábrica (cuadrafonía) deja uno «sin rol», sin decir qué hacer")
        p.tap(p.page.locator('[data-room-layout="auto"]'))
    time.sleep(0.5)
    return svc.state()["global"]["layout"] == "auto"


FLOWS = {
    "Poner música": flow_play,
    "Probar el modo espacial": flow_mode,
    "Un parlante no suena": flow_quiet_speaker,
    "Calibrar": flow_calibrate,
    "Guardar un preset": flow_preset,
    "Audífonos": flow_headphones,
    "Sumar un parlante": flow_add_speaker,
    "Ubicar los parlantes": flow_room,
}


def main() -> None:
    pc = "--pc" in sys.argv
    out = sys.argv[sys.argv.index("--json") + 1] if "--json" in sys.argv else None
    viewport = {"width": 1366, "height": 900} if pc else {"width": 390, "height": 844}
    runs: list[Run] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        for name, flow in FLOWS.items():
            with tempfile.TemporaryDirectory() as tmp:
                svc = Running(Path(tmp), THREE)
                page = browser.new_page(viewport=viewport)
                run = Run(name)
                try:
                    page.goto(f"{svc.url}/?t={TOKEN}")
                    page.wait_for_selector("body[data-ready='1']", state="attached")
                    page.wait_for_timeout(600)
                    started = time.monotonic()
                    run.ok = bool(flow(Person(page, run), svc))
                    run.seconds = round(time.monotonic() - started, 1)
                except Exception as exc:  # noqa: BLE001 - a flow that breaks is a finding, not a crash
                    run.notes.append(f"se trabó: {type(exc).__name__}: {str(exc).splitlines()[0][:160]}")
                finally:
                    page.close()
                    svc.stop()
                run.screens = round(run.screens, 2)
                runs.append(run)
        browser.close()
    print(f"{'flujo':28} ok  toques pestañas pantallas  notas")  # noqa: T201
    for r in runs:
        print(f"{r.flow:28} {'sí' if r.ok else 'NO':3} {r.taps:6} {r.tabs:8} {r.screens:9}  {' | '.join(r.notes)}")  # noqa: T201
    if out:
        Path(out).write_text(json.dumps([asdict(r) for r in runs], ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
