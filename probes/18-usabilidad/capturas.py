"""Usability, step 1: what a newcomer sees. A full capture of each tab on a phone and on a PC, with the
session playing in the simulated room (no speakers). From host/:

    hatch run browser:python ../probes/18-usabilidad/capturas.py OUT_DIR
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, ".")
from playwright.sync_api import sync_playwright

from tests_browser.test_panel import THREE, TOKEN, Running

TABS = ("Escuchar", "Cadena", "Parlantes", "Calibrar", "Diagnóstico", "Ajustes")
VIEWPORTS = {"telefono": (390, 844), "pc": (1366, 900)}


def main(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp, sync_playwright() as p:
        svc = Running(Path(tmp), THREE)
        browser = p.chromium.launch()
        try:
            for vp, (w, h) in VIEWPORTS.items():
                page = browser.new_page(viewport={"width": w, "height": h})
                page.goto(f"{svc.url}/?t={TOKEN}")
                page.wait_for_selector("body[data-ready='1']", state="attached")
                page.wait_for_timeout(800)
                page.screenshot(path=str(out / f"{vp}-0-inicio-sin-sesion.png"))
                page.locator("#run").click()
                page.wait_for_timeout(2500)
                for i, tab in enumerate(TABS, 1):
                    page.get_by_role("button", name=tab, exact=True).first.click()
                    page.wait_for_timeout(700)
                    page.screenshot(path=str(out / f"{vp}-{i}-{tab.lower()}.png"), full_page=True)
                page.close()
        finally:
            browser.close()
            svc.stop()


if __name__ == "__main__":
    main(Path(sys.argv[1]))
