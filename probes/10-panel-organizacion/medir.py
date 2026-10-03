"""Mide las organizaciones del panel con tareas típicas: toques de navegación y pantallas de
desplazamiento, sobre el render real (Chromium, servicio simulado).

Modelo:
- cada escenario es una secuencia de controles; se arranca en la primera vista, arriba;
- si un control está en otra vista: toques de navegación (pestañas o lateral: 1; "inicio":
  1 desde el inicio o hacia él, 2 entre dos pantallas de detalle) y la vista arranca arriba;
- si el control no está en la zona visible (descontando barras fijas arriba y abajo), se
  desplaza lo justo para centrarlo, y se cuenta en pantallas (px / alto visible);
- costo de un escenario = toques + pantallas; total = suma ponderada por frecuencia.

Uso (desde host/): hatch run browser:python ../probes/10-panel-organizacion/medir.py [--sin-guardar] [--ocho]

Con `--ocho` (experimentos/16 §7) mide `pestanas` con los ocho parlantes de
`tests_browser/test_panel.EIGHT` (la decorrelación apagada: con 7 o más el banco fijo no se arma) y
anota además el alto de cada vista. Un control que está en una tarjeta plegada (4 parlantes o más)
se toca para abrirla: eso cuenta como un toque del control, no de navegación.

`run()` lo usa también `tests_browser/test_panel_quality.py` como guardia de regresión: el
total de `pestanas` no puede pasar de lo anotado allí (`MAX_COST`) y en
docs/research/10-panel-de-control.md §5. Desde la pantalla Cadena (2026-10-02) hay un séptimo
escenario, "Afinar la cadena"; `total_seis` es el total de los seis de antes, para comparar.
"""

import contextlib
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, ".")
from playwright.sync_api import expect, sync_playwright

from tests_browser.test_panel import EIGHT, THREE, TOKEN, Running

LAYOUTS = ["pagina", "pestanas", "inicio", "lateral"]
VIEWPORTS = {"teléfono": (390, 844), "PC": (1366, 900)}
Q = lambda name, cls: f'[data-quick="JBL Go 4 {name}"] .{cls}'  # noqa: E731
SCENARIOS = [
    ("Escuchar música", 20, ["#run", "#source-kind", "#volume", "#presets li button", "#volume", "#run"]),
    ("Ajustar el ambiente", 6, [Q("Blue", "q-ambience"), Q("Red", "q-mute"), "#chip-quality", Q("Red", "q-mute"), "#volume"]),
    ("Calibrar y ecualizar", 2, ["#cal-run", "#cal-apply", "#eq-apply", "#resp-chart", "#chip-quality"]),
    ("Comparar A/B", 1, ["#ab-start", '[data-ab="x"]', '[data-answer="a"]', '[data-ab="x"]', '[data-answer="b"]', "#ab-stop"]),
    ("Resolver un problema", 1, ["#t-xruns", "#services tr", "#log-service", "#logs"]),
    # La decorrelación se mudó de Ajustes → Sonido a Cadena (spec 2026-10-02 §7.1).
    ("Montar la sala", 0.5, ["#scan", "#devices", '[data-room-layout="lcrs"]', "#speakers tr:nth-child(3) .cell-pan input", '[data-stage="decorrelate"] [data-algorithm="off"]']),
    # La pantalla Cadena: mirar la calidad, cambiar el limitador y una perilla, apagar la ecualización.
    # Las perillas de cada etapa van plegadas: abrirlas es un toque del control (no de navegación).
    ("Afinar la cadena", 1, ["#chain-quality", '[data-jump-to="limiter"]', '[data-stage="limiter"] [data-algorithm="true_peak"]', '[data-stage="limiter"] .knobs-summary', '[data-stage="limiter"] [data-param="release_ms"] input[type=range]', '[data-stage="eq"] [data-algorithm="off"]', "#chip-quality"]),
]
SCENARIOS_BEFORE_CADENA = [s[0] for s in SCENARIOS[:6]]
"""Los seis escenarios de las rondas 1-3 y del paquete D: su total se compara con los de antes."""

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


JS_JUMP = """(sel) => {
  const n = document.querySelector(sel);
  const id = n && n.dataset.jumpTo;
  const t = id && document.getElementById('stage-' + id);
  if (!t) return null;
  return t.getBoundingClientRect().top + window.scrollY - parseFloat(getComputedStyle(t).scrollMarginTop || '0');
}"""


def nav_cost(kind: str, first: str, frm: str, to: str) -> int:
    if frm == to:
        return 0
    if kind == "hub":
        return 1 if first in (frm, to) else 2
    return 1


JS_UNFOLD = """(sel) => {
  const n = document.querySelector(sel);
  const folded = n && n.closest('[data-folded]');
  if (!folded || n.checkVisibility()) return false;
  const toggle = folded.querySelector('.fold-btn, .spk-toggle');
  if (toggle) toggle.click();
  return true;
}"""

JS_VIEW_HEIGHTS = """() => Object.fromEntries([...document.querySelectorAll('[data-view]')].map((v) => {
  const hidden = v.hidden; v.hidden = false;
  const h = Math.round(v.getBoundingClientRect().height); v.hidden = hidden;
  return [v.dataset.view, h];
}))"""


def measure(page, layout_name: str, kind: str, first_view: str, steps: list[str]) -> tuple[int, float]:
    page.evaluate("(v) => window.scrollTo(0, 0)", None)
    page.evaluate(f"document.querySelector('[data-view=\"{first_view}\"]') && window.aurasyncShow('[data-view=\"{first_view}\"] *')")
    current, scroll, taps, screens = first_view, 0.0, 0, 0.0
    for sel in steps:
        # Con 7 u 8 parlantes la decorrelación no ofrece todos sus algoritmos: si el control no
        # existe, se mide hasta su etapa.
        if sel.startswith("[data-stage=") and not page.evaluate("(s) => Boolean(document.querySelector(s))", sel):
            sel = sel.split("]")[0] + "]"
        if page.evaluate("(s) => { const n = document.querySelector(s); const v = n && n.closest('[data-view]'); return Boolean(v && !v.hidden); }", sel):
            page.evaluate(JS_UNFOLD, sel)
        g = page.evaluate(JS_GEOMETRY, sel)
        if g is None:
            raise RuntimeError(f"{layout_name}: no existe {sel}")
        if g["sticky"]:
            continue
        if g["view"] != current:
            taps += nav_cost(kind, first_view, current, g["view"])
            page.evaluate("(s) => window.aurasyncShow(s)", sel)
            current, scroll = g["view"], 0.0
            page.evaluate(JS_UNFOLD, sel)
            g = page.evaluate(JS_GEOMETRY, sel)
        visible_top = scroll + g["top"]
        visible_bottom = scroll + g["vh"] - g["bottom"]
        visible_h = visible_bottom - visible_top
        if g["y"] < visible_top or g["y"] + min(g["h"], visible_h) > visible_bottom:
            target = max(0.0, g["y"] + g["h"] / 2 - g["top"] - visible_h / 2)
            screens += abs(target - scroll) / visible_h
            scroll = target
        # Un salto a una etapa de Cadena (`data-jump-to`) es navegación: 1 toque, y la vista queda en
        # la etapa (con el margen de la cabecera fija, `scroll-margin-top`).
        landed = page.evaluate(JS_JUMP, sel)
        if landed is not None:
            taps += 1
            scroll = max(0.0, landed)
        # Un <summary> se toca para abrir lo que pliega: lo que sigue se mide abierto.
        page.evaluate("(s) => { const n = document.querySelector(s); if (n && n.tagName === 'SUMMARY') n.closest('details').open = true; }", sel)
    return taps, screens


def run(layouts: list[str] = LAYOUTS, viewports: dict[str, tuple[int, int]] = VIEWPORTS, browser=None, speakers=THREE) -> dict:
    """Mide `layouts` en `viewports`. Con `browser` usa ese Chromium (el test de regresión)."""
    results = {}
    running = Running(Path(tempfile.mkdtemp()), speakers)
    try:
        if len(speakers) > 6:
            running.service.handle({"v": 1, "op": "set", "changes": {"decorrelate": False}})
        running.service.handle({"v": 1, "op": "preset_save", "name": "cerrado"})
        running.service.handle({"v": 1, "op": "preset_save", "name": "amplio"})
        with contextlib.ExitStack() as stack:
            if browser is None:
                p = stack.enter_context(sync_playwright())
                browser = p.chromium.launch()
                stack.callback(browser.close)
            for vp_name, (w, h) in viewports.items():
                for name in layouts:
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
                    before = sum(r["peso"] * r["costo"] for k, r in rows.items() if k in SCENARIOS_BEFORE_CADENA)
                    results.setdefault(vp_name, {})[name] = {
                        "escenarios": rows, "total": round(total, 1), "total_seis": round(before, 1), "cabecera_px": round(header),
                        "vistas_px": page.evaluate(JS_VIEW_HEIGHTS),
                    }
                    ctx.close()
    finally:
        running.stop()
    return results


def main() -> None:
    eight = "--ocho" in sys.argv
    layouts = ["pestanas"] if eight else LAYOUTS
    results = run(layouts, speakers=EIGHT if eight else THREE)
    for vp_name, by_layout in results.items():
        print(f"\n== {vp_name} {VIEWPORTS[vp_name]}")
        print(f"{'escenario':<24}{'peso':>6}" + "".join(f"{n:>16}" for n in LAYOUTS))
        for label, weight, _ in SCENARIOS:
            cells = "".join(f"{by_layout[n]['escenarios'][label]['toques']:>4}t {by_layout[n]['escenarios'][label]['pantallas']:>5.2f}p ={by_layout[n]['escenarios'][label]['costo']:>4.1f}" for n in layouts)
            print(f"{label:<24}{weight:>6}{cells}")
        print(f"{'TOTAL ponderado':<30}" + "".join(f"{by_layout[n]['total']:>16}" for n in layouts))
        print(f"{'  sin «Afinar la cadena»':<30}" + "".join(f"{by_layout[n]['total_seis']:>16}" for n in layouts))
        print(f"{'cabecera fija (px)':<30}" + "".join(f"{by_layout[n]['cabecera_px']:>16}" for n in layouts))
        for n in layouts:
            print(f"alto de cada vista ({n}, px): {by_layout[n]['vistas_px']}")
    if "--sin-guardar" in sys.argv:
        return
    name = "panel-ocho-parlantes.json" if eight else "panel-organizaciones.json"
    out = Path(__file__).resolve().parents[2] / "docs/research/experimentos/datos/10" / name
    out.write_text(json.dumps(results, indent=2, ensure_ascii=False))
    print(f"\nguardado en {out}")


if __name__ == "__main__":
    main()
