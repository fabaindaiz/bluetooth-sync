"""Costo de dibujo del panel con el stream vivo: cuadros por segundo, tareas largas, layouts y
tiempo del hilo principal (CDP `Performance.getMetrics`), en Chromium con viewport de teléfono,
con y sin freno de CPU (`Emulation.setCPUThrottlingRate`). Servicio simulado, como los tests de
navegador. El freno de CPU es solo una aproximación a un teléfono.

Criterio (spec 2026-10-02 §7.3.1): con freno ×4, layouts/s ≤ 65 y hilo principal ≤ 150 ms/s.
`--tarjeta chain` mide la pantalla Cadena (Preact, métricas a 5 Hz y calidad a 2 Hz).

El hilo principal depende de la carga del equipo (el servicio simulado corre en el mismo
proceso que mide, y el Mac compite con otros trabajos). Por eso `--antes REV` mide también el
panel de esa versión de git (sirviendo sus `index.html`, `app.js` y `tailwind.css` con
`page.route`), **intercalado** con el actual y `--repetir` veces: la comparación vale con la
misma carga, y se informa la mediana.

Uso (desde host/):
    hatch run browser:python ../probes/10-panel-organizacion/render_cost.py --antes f44efb2 --repetir 3
Agrega una corrida a docs/research/experimentos/datos/10/render-cost-<fecha>.json.
Adaptado del script de la investigación D (2026-10-02).
"""

import argparse
import datetime
import json
import os
import platform
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, ".")
from playwright.sync_api import expect, sync_playwright  # noqa: E402

from tests_browser.test_panel import TOKEN, Running  # noqa: E402

SECONDS = 10
PANEL = "host/src/aurasync/panel"
ROOT = Path(__file__).resolve().parents[2]
JS = """(secs) => new Promise(res => {
  const out = {frames: 0, long: 0, longMs: 0, gaps: []};
  let last = performance.now();
  const po = new PerformanceObserver(l => { for (const e of l.getEntries()) { out.long++; out.longMs += e.duration; } });
  try { po.observe({type: 'longtask', buffered: false}); } catch {}
  const t0 = performance.now();
  function f(t) {
    out.frames++; out.gaps.push(t - last); last = t;
    if (t - t0 < secs * 1000) { requestAnimationFrame(f); return; }
    po.disconnect(); out.secs = (t - t0) / 1000; out.gaps.sort((a, b) => a - b);
    out.p95 = out.gaps[Math.floor(out.gaps.length * 0.95)]; out.max = out.gaps[out.gaps.length - 1];
    delete out.gaps; res(out);
  }
  requestAnimationFrame(f);
})"""


def metrics(cdp) -> dict:
    return {m["name"]: m["value"] for m in cdp.send("Performance.getMetrics")["metrics"]}


def panel_at(rev: str) -> dict[str, tuple[str, bytes]]:
    """The three panel files as they were at `rev`, keyed by the path the service serves."""
    files = {"/": "index.html", "/static/app.js": "app.js", "/static/tailwind.css": "tailwind.css"}
    types = {"index.html": "text/html; charset=utf-8", "app.js": "text/javascript; charset=utf-8",
             "tailwind.css": "text/css; charset=utf-8"}
    return {
        path: (types[name], subprocess.run(["git", "show", f"{rev}:{PANEL}/{name}"], cwd=ROOT, check=True,
                                           capture_output=True).stdout)
        for path, name in files.items()
    }


def measure_once(browser, svc: Running, throttle: int, old: dict | None, seconds: float, card: str = "levels") -> dict:
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=3, is_mobile=True, has_touch=True)
    page = ctx.new_page()
    if old is not None:
        def serve(route):
            path = urlparse(route.request.url).path
            query = urlparse(route.request.url).query
            if path in old and old[path] is None:
                route.abort()  # `--sin-cadena`: the panel without the Preact screens
            elif path in old and not query:
                kind, body = old[path]
                route.fulfill(status=200, body=body, headers={"Content-Type": kind, "Cache-Control": "no-store"})
            else:
                route.continue_()
        page.route("**/*", serve)
    cdp = ctx.new_cdp_session(page)
    cdp.send("Performance.enable")
    page.goto(f"{svc.url}/?t={TOKEN}")
    page.wait_for_selector("body[data-ready='1']")
    expect(page.locator("#run")).to_have_text("Detener", timeout=10000)
    page.evaluate(f"window.aurasyncShow('[data-card={card}]')")
    page.locator(f"[data-card={card}]").scroll_into_view_if_needed()
    time.sleep(2)
    cdp.send("Emulation.setCPUThrottlingRate", {"rate": throttle})
    a = metrics(cdp)
    r = page.evaluate(JS, seconds)
    b = metrics(cdp)
    ctx.close()
    per_s = {
        k: round((b[k] - a[k]) / r["secs"] * 1000, 1)
        for k in ("ScriptDuration", "LayoutDuration", "RecalcStyleDuration", "TaskDuration")
    }
    return {
        "cpu_x": throttle,
        "fps": round(r["frames"] / r["secs"], 1),
        "p95_ms": round(r["p95"], 1),
        "max_ms": round(r["max"], 1),
        "long_tasks": r["long"],
        "long_ms": round(r["longMs"]),
        "ms_por_s": per_s,
        "layouts_por_s": round((b["LayoutCount"] - a["LayoutCount"]) / r["secs"], 1),
        "recalc_por_s": round((b["RecalcStyleCount"] - a["RecalcStyleCount"]) / r["secs"], 1),
    }


def run(
    throttles: tuple[int, ...],
    before: str | None,
    repeat: int,
    seconds: float = SECONDS,
    cards: tuple[str, ...] = ("levels",),
    without_chain: bool = False,
) -> list[dict]:
    rows = []
    old = panel_at(before) if before else None
    variants = [("actual", None)] + ([(f"antes ({before})", old)] if old else [])
    if without_chain:
        variants.append(("sin cadena.js", {"/static/cadena.js": None}))
    with sync_playwright() as p:
        browser = p.chromium.launch()
        svc = Running(Path(tempfile.mkdtemp()))
        try:
            svc.service.handle({"v": 1, "op": "start"})
            for throttle in throttles:
                for i in range(repeat):
                    for card in cards:
                        for name, files in variants:
                            label = name if cards == ("levels",) else f"{name} · {card}"
                            row = {"panel": label, "vuelta": i + 1, **measure_once(browser, svc, throttle, files, seconds, card)}
                            rows.append(row)
                            print(json.dumps(row, ensure_ascii=False), flush=True)
        finally:
            svc.stop()
            browser.close()
    return rows


def summary(rows: list[dict]) -> list[dict]:
    out = []
    for key in sorted({(r["panel"], r["cpu_x"]) for r in rows}):
        group = [r for r in rows if (r["panel"], r["cpu_x"]) == key]
        out.append({
            "panel": key[0],
            "cpu_x": key[1],
            "n": len(group),
            "fps": statistics.median(r["fps"] for r in group),
            "layouts_por_s": statistics.median(r["layouts_por_s"] for r in group),
            "recalc_por_s": statistics.median(r["recalc_por_s"] for r in group),
            "hilo_ms_por_s": statistics.median(r["ms_por_s"]["TaskDuration"] for r in group),
            "script_ms_por_s": statistics.median(r["ms_por_s"]["ScriptDuration"] for r in group),
            "layout_ms_por_s": statistics.median(r["ms_por_s"]["LayoutDuration"] for r in group),
            "estilo_ms_por_s": statistics.median(r["ms_por_s"]["RecalcStyleDuration"] for r in group),
        })
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--antes", help="versión de git del panel con la que comparar (p. ej. f44efb2)")
    parser.add_argument("--repetir", type=int, default=1)
    parser.add_argument("--freno", type=int, nargs="+", default=[1, 4, 6])
    parser.add_argument("--nota", default="")
    parser.add_argument("--tarjeta", nargs="+", default=["levels"],
                        help="la tarjeta a la vista (data-card): levels (Niveles), chain (la pantalla Cadena)")
    parser.add_argument("--sin-cadena", action="store_true",
                        help="intercala el panel sin cadena.js: lo que cuesta la pantalla Cadena estando oculta")
    args = parser.parse_args()
    rows = run(tuple(args.freno), args.antes, args.repetir, cards=tuple(args.tarjeta), without_chain=args.sin_cadena)
    medians = summary(rows)
    for m in medians:
        print(json.dumps(m, ensure_ascii=False))
    now = datetime.datetime.now().astimezone()
    out = ROOT / f"docs/research/experimentos/datos/10/render-cost-{now:%Y-%m-%d}.json"
    data = json.loads(out.read_text()) if out.exists() else {"corridas": []}
    load = os.getloadavg() if hasattr(os, "getloadavg") else None
    data["corridas"].append({
        "cuando": now.isoformat(timespec="seconds"),
        "equipo": platform.node(),
        "entorno": f"{platform.system()} {platform.release()}, {platform.machine()}, Chromium de Playwright headless",
        "carga_del_equipo_al_terminar": [round(x, 1) for x in load] if load else None,
        "vista": f"390×844, DPR 3, táctil, tarjeta {'/'.join(args.tarjeta)} visible, sesión simulada sonando",
        "segundos_por_fila": SECONDS,
        "nota": args.nota,
        "medianas": medians,
        "filas": rows,
    })
    out.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    print(f"guardado en {out}")


if __name__ == "__main__":
    main()
