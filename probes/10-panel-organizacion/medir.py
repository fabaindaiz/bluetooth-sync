"""Mide las organizaciones del panel con tareas típicas: toques de navegación y pantallas de
desplazamiento, sobre el render real (Chromium, servicio simulado).

Modelo:
- cada escenario es una secuencia de controles; se arranca en la primera vista, arriba;
- si un control está en otra vista: toques de navegación (pestañas o lateral: 1; "inicio":
  1 desde el inicio o hacia él, 2 entre dos pantallas de detalle) y la vista arranca arriba;
- si el control no está en la zona visible (descontando barras fijas arriba y abajo), se
  desplaza lo justo para centrarlo, y se cuenta en pantallas (px / alto visible);
- costo de un escenario = toques + pantallas; total = suma ponderada por frecuencia.

Uso (desde host/): hatch run browser:python ../probes/10-panel-organizacion/medir.py
"""

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, ".")
from playwright.sync_api import expect, sync_playwright  # noqa: E402

from tests_browser.test_panel import TOKEN, Running  # noqa: E402

LAYOUTS = ["pagina", "pestanas", "inicio", "lateral"]
VIEWPORTS = {"teléfono": (390, 844), "PC": (1366, 900)}
Q = lambda name, cls: f'[data-quick="JBL Go 4 {name}"] .{cls}'  # noqa: E731
SCENARIOS = [
    ("Escuchar música", 20, ["#run", "#source-kind", "#volume", "#presets li button", "#volume", "#run"]),
    ("Ajustar el ambiente", 6, [Q("Blue", "q-ambience"), Q("Red", "q-mute"), "#chip-quality", Q("Red", "q-mute"), "#volume"]),
    ("Calibrar y ecualizar", 2, ["#cal-run", "#cal-apply", "#eq-apply", "#resp-chart", "#chip-quality"]),
    ("Comparar A/B", 1, ["#ab-start", '[data-ab="x"]', '[data-answer="a"]', '[data-ab="x"]', '[data-answer="b"]', "#ab-stop"]),
    ("Resolver un problema", 1, ["#t-xruns", "#services tr", "#log-service", "#logs"]),
    ("Montar la sala", 0.5, ["#scan", "#devices", '[data-room-layout="lcrs"]', "#speakers tr:nth-child(3) .cell-pan input", '[data-card=config] [data-global="decorrelate"]']),
]

JS_GEOMETRY = """(sel) => {
  const n = document.querySelector(sel);
  if (!n) return null;
  const view = n.closest('[data-view]');
  const r = n.getBoundingClientRect();
  const sticky = n.closest('#topbar') !== null;
  const top = document.getElementById('topbar').getBoundingClientRect().height;
  const bn = document.getElementById('bottom-nav');
  const bottom = bn && getComputedStyle(bn).display !== 'none' ? bn.getBoundingClientRect().height : 0;
  return {view: view ? view.dataset.view : null, y: r.top + window.scrollY, h: r.height, sticky, top, bottom, vh: window.innerHeight};
}"""


def nav_cost(kind: str, first: str, frm: str, to: str) -> int:
    if frm == to:
        return 0
    if kind == "hub":
        return 1 if first in (frm, to) else 2
    return 1


def measure(page, layout_name: str, kind: str, first_view: str, steps: list[str]) -> tuple[int, float]:
    page.evaluate("(v) => window.scrollTo(0, 0)", None)
    page.evaluate(f"document.querySelector('[data-view=\"{first_view}\"]') && window.aurasyncShow('[data-view=\"{first_view}\"] *')")
    current, scroll, taps, screens = first_view, 0.0, 0, 0.0
    for sel in steps:
        g = page.evaluate(JS_GEOMETRY, sel)
        if g is None:
            raise RuntimeError(f"{layout_name}: no existe {sel}")
        if g["sticky"]:
            continue
        if g["view"] != current:
            taps += nav_cost(kind, first_view, current, g["view"])
            page.evaluate("(s) => window.aurasyncShow(s)", sel)
            current, scroll = g["view"], 0.0
            g = page.evaluate(JS_GEOMETRY, sel)
        visible_top = scroll + g["top"]
        visible_bottom = scroll + g["vh"] - g["bottom"]
        visible_h = visible_bottom - visible_top
        if g["y"] < visible_top or g["y"] + min(g["h"], visible_h) > visible_bottom:
            target = max(0.0, g["y"] + g["h"] / 2 - g["top"] - visible_h / 2)
            screens += abs(target - scroll) / visible_h
            scroll = target
    return taps, screens


def main() -> None:
    results = {}
    running = Running(Path(tempfile.mkdtemp()))
    try:
        running.service.handle({"v": 1, "op": "preset_save", "name": "cerrado"})
        running.service.handle({"v": 1, "op": "preset_save", "name": "amplio"})
        with sync_playwright() as p:
            browser = p.chromium.launch()
            for vp_name, (w, h) in VIEWPORTS.items():
                for name in LAYOUTS:
                    ctx = browser.new_context(viewport={"width": w, "height": h})
                    page = ctx.new_page()
                    page.goto(f"{running.url}/?t={TOKEN}")
                    page.goto(f"{running.url}/?layout={name}")
                    expect(page.locator("body[data-ready='1']")).to_be_attached()
                    page.wait_for_timeout(600)
                    kind = page.evaluate("document.body.dataset.layout") and {"pagina": "none", "pestanas": "tabs", "inicio": "hub", "lateral": "sidebar"}[name]
                    first = page.evaluate("document.querySelector('[data-view]').dataset.view")
                    header = page.evaluate("document.getElementById('topbar').getBoundingClientRect().height")
                    rows = {}
                    for label, weight, steps in SCENARIOS:
                        taps, screens = measure(page, name, kind, first, steps)
                        rows[label] = {"peso": weight, "toques": taps, "pantallas": round(screens, 2), "costo": round(taps + screens, 2)}
                    total = sum(r["peso"] * r["costo"] for r in rows.values())
                    results.setdefault(vp_name, {})[name] = {"escenarios": rows, "total": round(total, 1), "cabecera_px": round(header)}
                    ctx.close()
            browser.close()
    finally:
        running.stop()
    for vp_name, by_layout in results.items():
        print(f"\n== {vp_name} {VIEWPORTS[vp_name]}")
        print(f"{'escenario':<24}{'peso':>6}" + "".join(f"{n:>16}" for n in LAYOUTS))
        for label, weight, _ in SCENARIOS:
            cells = "".join(f"{by_layout[n]['escenarios'][label]['toques']:>4}t {by_layout[n]['escenarios'][label]['pantallas']:>5.2f}p ={by_layout[n]['escenarios'][label]['costo']:>4.1f}" for n in LAYOUTS)
            print(f"{label:<24}{weight:>6}{cells}")
        print(f"{'TOTAL ponderado':<30}" + "".join(f"{by_layout[n]['total']:>16}" for n in LAYOUTS))
        print(f"{'cabecera fija (px)':<30}" + "".join(f"{by_layout[n]['cabecera_px']:>16}" for n in LAYOUTS))
    out = Path(__file__).resolve().parents[2] / "docs/research/experimentos/datos/10/panel-organizaciones.json"
    out.write_text(json.dumps(results, indent=2, ensure_ascii=False))
    print(f"\nguardado en {out}")


if __name__ == "__main__":
    main()
