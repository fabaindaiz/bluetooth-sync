"""The measured audit of the panel in simulated mode. Writes data/results.json and screenshots.

Run from this directory with the hatch `browser` env python and XDG_* pointed at the scratch dir.
"""

from __future__ import annotations

import base64
import json
import sys
import time
import traceback

from playwright.sync_api import expect, sync_playwright

from harness import AUDIT, TAB_IDS, go, open_panel, service

AXE = AUDIT / "vendor" / "axe.min.js"
HELPERS = AUDIT / "scripts" / "audit.js"
OUT = AUDIT / "data" / "results.json"
VIEWPORTS = {"pc1440": (1440, 900), "pc1920": (1920, 1080), "phone390": (390, 844)}
TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]
LONGTASKS = """
window.__lt = [];
try { new PerformanceObserver((l) => { for (const e of l.getEntries()) window.__lt.push([e.startTime, e.duration]); })
  .observe({ type: 'longtask', buffered: true }); } catch (e) {}
"""
DIFF = """async ([a, b]) => {
  const A = await window.__A.pixels(a), B = await window.__A.pixels(b);
  let changed = 0, maxR = 1;
  for (let i = 0; i < A.data.length; i += 4) {
    const d = Math.max(Math.abs(A.data[i]-B.data[i]), Math.abs(A.data[i+1]-B.data[i+1]), Math.abs(A.data[i+2]-B.data[i+2]));
    if (d > 24) { changed++; const r = window.__A.ratio([A.data[i],A.data[i+1],A.data[i+2]], [B.data[i],B.data[i+1],B.data[i+2]]); if (r > maxR) maxR = r; }
  }
  return { changed, maxRatio: +maxR.toFixed(2), total: A.data.length / 4 };
}"""
NATIVE = """async (b64) => {
  const P = await window.__A.pixels(b64);
  const counts = new Map();
  const ring = new Map();
  for (let y = 0; y < P.h; y++) for (let x = 0; x < P.w; x++) {
    const i = (y * P.w + x) * 4;
    const k = (P.data[i] << 16) | (P.data[i+1] << 8) | P.data[i+2];
    counts.set(k, (counts.get(k) || 0) + 1);
    if (x < 3 || y < 3 || x >= P.w - 3 || y >= P.h - 3) ring.set(k, (ring.get(k) || 0) + 1);
  }
  const rgb = (k) => [k >> 16, (k >> 8) & 255, k & 255];
  const bgK = [...ring.entries()].sort((a, b) => b[1] - a[1])[0][0];
  const bg = rgb(bgK);
  const total = P.w * P.h;
  const top = [...counts.entries()].filter(([k]) => k !== bgK).sort((a, b) => b[1] - a[1]).slice(0, 4)
    .map(([k, n]) => ({ color: window.__A.hex(rgb(k)), share: +(n / total).toFixed(3), ratio: +window.__A.ratio(rgb(k), bg).toFixed(2) }));
  return { bg: window.__A.hex(bg), top };
}"""


def inject(page) -> None:
    page.add_script_tag(path=str(HELPERS))
    page.add_script_tag(path=str(AXE))


def run_axe(page) -> dict:
    res = page.evaluate(
        """async (tags) => {
      const r = await axe.run(document, { runOnly: { type: 'tag', values: tags }, resultTypes: ['violations', 'incomplete'] });
      const s = (v) => ({ id: v.id, impact: v.impact, tags: v.tags.filter(t => t.startsWith('wcag')), help: v.help, n: v.nodes.length,
        nodes: v.nodes.slice(0, 12).map(n => ({ target: n.target.join(' '), html: n.html.slice(0, 160), summary: (n.failureSummary || '').slice(0, 300) })) });
      return { violations: r.violations.map(s), incomplete: r.incomplete.map(s), passes: r.passes ? r.passes.length : null };
    }""",
        TAGS,
    )
    return res


def native_controls(page, scheme: str) -> list:
    out = []
    picks = {
        "range (volumen, cabecera)": "#volume",
        "range (primero en la vista)": "[data-view]:not([hidden]) input[type=range]",
        "checkbox marcado": "[data-view]:not([hidden]) input[type=checkbox]:checked",
        "checkbox sin marcar": "[data-view]:not([hidden]) input[type=checkbox]:not(:checked)",
        "radio marcado": "[data-view]:not([hidden]) input[type=radio]:checked",
        "radio sin marcar": "[data-view]:not([hidden]) input[type=radio]:not(:checked)",
    }
    for label, sel in picks.items():
        loc = page.locator(sel).locator("visible=true").first
        if loc.count() == 0:
            continue
        box = loc.bounding_box()
        if not box or box["width"] == 0:
            continue
        loc.scroll_into_view_if_needed()
        box = loc.bounding_box()
        clip = {"x": max(box["x"] - 4, 0), "y": max(box["y"] - 4, 0), "width": box["width"] + 8, "height": box["height"] + 8}
        png = page.screenshot(clip=clip)
        stats = page.evaluate(NATIVE, base64.b64encode(png).decode())
        out.append({"control": label, "w": round(box["width"], 1), "h": round(box["height"], 1), **stats})
    return out


def keyboard(page, view: str, pixel_diff: bool) -> dict:
    page.evaluate("() => { document.activeElement && document.activeElement.blur(); window.scrollTo(0, 0); }")
    # The sequential-focus starting point goes to the top: a click on the empty left margin of the
    # header (nothing there is interactive), so the first Tab lands on the first control.
    page.mouse.click(3, 3)
    tagged = page.evaluate("() => window.__A.tagAll()")
    seen, order = set(), []
    first = None
    nones = 0
    for _ in range(600):
        page.keyboard.press("Tab")
        info = page.evaluate("() => window.__A.focusInfo()")
        if info is None:
            order.append({"sel": "(body / fuera de la página)"})
            nones += 1
            if nones > 3:
                break
            continue
        key = info["id"] or info["sel"] + str(info["y"])
        if first is None:
            first = key
        elif key == first:
            break
        if key in seen:
            info["repeat"] = True
        seen.add(key)
        if pixel_diff and info["inView"]:
            clip = {"x": max(info["cx"], 0), "y": max(info["cy"], 0), "width": max(info["cw"], 2), "height": max(info["ch"], 2)}
            try:
                a = page.screenshot(clip=clip)
                page.evaluate("() => { window.__last = document.activeElement; document.activeElement.blur(); }")
                b = page.screenshot(clip=clip)
                page.evaluate("() => window.__last.focus()")
                info["diff"] = page.evaluate(DIFF, [base64.b64encode(a).decode(), base64.b64encode(b).decode()])
            except Exception as exc:  # noqa: BLE001
                info["diff"] = {"error": str(exc)[:120]}
        order.append(info)
    reached = {o.get("id") for o in order if o.get("id") is not None}
    # A radio that is not the checked one of its group is reached with the arrows, not with Tab.
    radios_ok = set(page.evaluate("""() => [...document.querySelectorAll('input[type=radio][data-audit-id]')]
        .filter(r => !r.checked && r.name && document.querySelector(`input[type=radio][name="${CSS.escape(r.name)}"]:checked`))
        .map(r => r.dataset.auditId)"""))
    unreached = [t for t in tagged if t["id"] not in reached and t["id"] not in radios_ok and t["where"] != "nav-abajo"]
    return {"view": view, "tabStops": len([o for o in order if "id" in o]), "order": order, "unreached": unreached,
            "clickableNotFocusable": page.evaluate("() => window.__A.clickableNotFocusable()")}


def ax_unnamed(cdp) -> dict:
    tree = cdp.send("Accessibility.getFullAXTree")["nodes"]
    roles = {"button", "link", "checkbox", "radio", "slider", "spinbutton", "combobox", "textbox", "switch", "tab", "menuitem", "listbox", "searchbox", "meter", "progressbar", "img", "ListBox", "PopUpButton"}
    unnamed, meters, live = [], [], []
    for n in tree:
        if n.get("ignored"):
            continue
        role = (n.get("role") or {}).get("value")
        name = (n.get("name") or {}).get("value", "")
        props = {p["name"]: p.get("value", {}).get("value") for p in n.get("properties", [])}
        if role in roles and not str(name).strip():
            unnamed.append({"role": role, "backendId": n.get("backendDOMNodeId")})
        if role in {"meter", "progressbar"}:
            meters.append({"name": name, "valuetext": props.get("valuetext"), "value": (n.get("value") or {}).get("value")})
        if props.get("live") and props.get("live") != "off" and role in {"status", "alert", "log", "region", "generic", "paragraph", "list"}:
            live.append({"role": role, "name": name, "live": props.get("live")})
    return {"unnamed": unnamed, "meters": meters, "nodes": len(tree)}


def describe_backend(cdp, bid) -> str:
    try:
        node = cdp.send("DOM.describeNode", {"backendNodeId": bid})["node"]
        attrs = node.get("attributes", [])
        a = dict(zip(attrs[::2], attrs[1::2], strict=False))
        return f"{node['localName']}{'#' + a['id'] if 'id' in a else ''}{'.' + a.get('class', '').split(' ')[0] if a.get('class') else ''}"
    except Exception:  # noqa: BLE001
        return "?"


def perf(page, cdp, seconds: float = 10.0) -> dict:
    cdp.send("Performance.enable")
    reqs = []
    handler = lambda r: reqs.append(r.url.split("?")[0].rsplit("/", 1)[-1])  # noqa: E731
    page.on("request", handler)
    m0 = {m["name"]: m["value"] for m in cdp.send("Performance.getMetrics")["metrics"]}
    lt0 = page.evaluate("() => performance.now()")
    time.sleep(seconds)
    m1 = {m["name"]: m["value"] for m in cdp.send("Performance.getMetrics")["metrics"]}
    lts = page.evaluate("(t0) => window.__lt.filter(([s]) => s >= t0)", lt0)
    page.remove_listener("request", handler)
    d = lambda k: m1.get(k, 0) - m0.get(k, 0)  # noqa: E731
    counts = {}
    for r in reqs:
        counts[r] = counts.get(r, 0) + 1
    return {
        "seconds": seconds,
        "mainThreadBusyPct": round(100 * d("TaskDuration") / seconds, 1),
        "scriptPct": round(100 * d("ScriptDuration") / seconds, 1),
        "layoutPct": round(100 * d("LayoutDuration") / seconds, 2),
        "stylePct": round(100 * d("RecalcStyleDuration") / seconds, 2),
        "layoutsPerSec": round(d("LayoutCount") / seconds, 1),
        "styleRecalcsPerSec": round(d("RecalcStyleCount") / seconds, 1),
        "heapMB": round(m1.get("JSHeapUsedSize", 0) / 1e6, 1),
        "nodes": int(m1.get("Nodes", 0)),
        "longTasks": len(lts),
        "longTaskMaxMs": round(max([x[1] for x in lts], default=0), 1),
        "longTaskTotalMs": round(sum(x[1] for x in lts), 1),
        "requests": counts,
    }


def setup_state(page, svc, log) -> None:
    go(page, "parlantes")
    page.get_by_role("button", name="Agregar parlante virtual").click()
    page.wait_for_timeout(600)
    go(page, "escuchar")
    page.locator("#run").click()
    expect(page.locator("#run")).to_have_text("Detener", timeout=15000)
    log("playing")
    # Calibration (simulated room; measurement_save refuses a simulated one).
    go(page, "calibrar")
    page.locator("#cal-seconds").fill("5")
    page.locator("#cal-run").click()
    page.wait_for_function("() => document.querySelectorAll('#cal-results tr').length >= 3", timeout=40000)
    page.locator("#cal-apply").click()
    page.wait_for_timeout(800)
    log("calibrated")
    go(page, "escuchar")
    page.locator("#preset-name").fill("cerrado")
    page.locator("#preset-save").click()
    page.wait_for_timeout(500)
    page.locator("#preset-name").fill("amplio")
    page.locator("#preset-save").click()
    page.wait_for_timeout(500)
    page.locator("#monitor-target").select_option("simulated_headphones")
    page.locator("#monitor-mode").select_option("binaural")
    box = page.locator("[data-card=now] label", has_text="Mantener").locator("input")
    if box.count() and not box.first.is_checked():
        box.first.check()
    page.wait_for_timeout(4000)
    log("state ready")


def escape_tests(page) -> list:
    out = []
    # 1. The (?) help popups of Ajustes: hover, then Escape.
    go(page, "ajustes")
    btn = page.locator(".help-btn").first
    btn.scroll_into_view_if_needed()
    btn.hover()
    page.wait_for_timeout(300)
    pop = page.locator(".help-pop").first
    vis = lambda: pop.evaluate("(e) => getComputedStyle(e).visibility")  # noqa: E731
    before = vis()
    page.keyboard.press("Escape")
    page.wait_for_timeout(300)
    after = vis()
    out.append({"test": "ayuda (?) de Ajustes con el mouse encima → Escape", "antes": before, "después": after, "ok": after != "visible"})
    page.mouse.move(5, 890)
    page.wait_for_timeout(200)
    btn.focus()
    page.wait_for_timeout(300)
    before = vis()
    page.keyboard.press("Escape")
    page.wait_for_timeout(300)
    after = vis()
    out.append({"test": "ayuda (?) de Ajustes con foco de teclado → Escape", "antes": before, "después": after, "ok": after != "visible"})
    # Hoverable: move from the button onto the popup.
    btn.hover()
    page.wait_for_timeout(200)
    pb = pop.bounding_box()
    if pb:
        page.mouse.move(pb["x"] + pb["width"] / 2, pb["y"] + pb["height"] / 2, steps=8)
        page.wait_for_timeout(300)
        out.append({"test": "ayuda (?): se puede pasar el mouse sobre el texto (1.4.13 hoverable)", "visible": vis(), "ok": vis() == "visible"})
    page.mouse.move(5, 890)
    # 2. Cadena: the (?) of a stage.
    go(page, "cadena")
    q = page.locator("[data-view=cadena] button", has_text="?").first
    if q.count():
        q.scroll_into_view_if_needed()
        q.focus()
        page.keyboard.press("Enter")
        page.wait_for_timeout(400)
        expanded = q.get_attribute("aria-expanded")
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)
        out.append({"test": "Cadena: (?) de una etapa, Enter abre → Escape", "aria-expanded antes": expanded,
                    "aria-expanded después": q.get_attribute("aria-expanded"), "ok": q.get_attribute("aria-expanded") in (None, "false") and expanded == "true"})
        if q.get_attribute("aria-expanded") == "true":
            q.press("Enter")
    # 3. Chip "cortes" in the header.
    chip = page.locator("#chip-quality")
    chip.focus()
    page.keyboard.press("Enter")
    page.wait_for_timeout(500)
    out.append({"test": "chip «cortes» con Enter", "resultado": page.evaluate("() => location.hash + ' foco: ' + (document.activeElement.id || document.activeElement.tagName)")})
    # 4. A <details> disclosure: Enter opens it; Escape (not required by WCAG) closes?
    go(page, "calibrar")
    det = page.locator("#est-explain summary")
    if det.count():
        det.focus()
        page.keyboard.press("Enter")
        opened = page.locator("#est-explain").evaluate("(e) => e.open")
        page.keyboard.press("Escape")
        closed = not page.locator("#est-explain").evaluate("(e) => e.open")
        out.append({"test": "<details> Configuración (Calibrar): Enter abre, Escape cierra", "abre": opened, "cierra con Escape": closed})
        if not closed:
            det.press("Enter")
    return out


def slider_keys(page) -> list:
    out = []
    for view, sel in [("escuchar", "#volume"), ("parlantes", "[data-view=parlantes] input[type=range]"), ("cadena", "[data-view=cadena] input[type=range]"), ("escuchar", "#monitor-level, [data-card=monitor] input[type=range]")]:
        go(page, view)
        loc = page.locator(sel).locator("visible=true").first
        if loc.count() == 0:
            out.append({"view": view, "sel": sel, "found": False})
            continue
        loc.scroll_into_view_if_needed()
        loc.focus()
        v0 = loc.input_value()
        page.keyboard.press("ArrowRight")
        page.wait_for_timeout(250)
        v1 = loc.input_value()
        page.keyboard.press("ArrowLeft")
        page.wait_for_timeout(400)
        v2 = loc.input_value()
        name = loc.evaluate("(e) => e.getAttribute('aria-label') || (e.labels && e.labels[0] && e.labels[0].innerText.trim().slice(0,40)) || e.id")
        vt = loc.get_attribute("aria-valuetext")
        out.append({"view": view, "name": name, "antes": v0, "→": v1, "←": v2, "cambia con flechas": v0 != v1, "aria-valuetext": vt})
    return out


def task_positions(page) -> list:
    tasks = [
        ("Cambiar el volumen", "escuchar", "#volume"),
        ("Cambiar el modo (perilla principal de la cadena, en la cabecera)", "escuchar", "#now-render"),
        ("Ambiente de la Cadena (1.ª etapa)", "cadena", "[data-view=cadena] input[type=range]"),
        ("Ver si un parlante va atrasado (fila Sincronía de Ahora)", "escuchar", "[data-card=now] dl"),
        ("Ver el retardo por parlante (Diagnóstico)", "diagnostico", "[data-card=health] svg, [data-card=health] .chart"),
        ("Calibrar (Ahora: Calibrar y aplicar)", "escuchar", "[data-card=now] button:has-text('Calibrar')"),
        ("Calibrar (pestaña)", "calibrar", "#cal-run"),
        ("A/B: Empezar", "escuchar", "#ab-start"),
    ]
    out = []
    for label, view, sel in tasks:
        go(page, view)
        page.evaluate("() => window.scrollTo(0, 0)")
        loc = page.locator(sel).locator("visible=true").first
        if loc.count() == 0:
            out.append({"tarea": label, "vista": view, "encontrado": False})
            continue
        box = loc.bounding_box()
        vh = page.viewport_size["height"]
        out.append({"tarea": label, "vista": view, "y": round(box["y"]), "h": round(box["height"]), "sobre el pliegue": box["y"] + box["height"] <= vh,
                    "pantallas de scroll": round(max(0, box["y"] + box["height"] - vh) / vh, 2)})
    return out


def main() -> None:
    which = sys.argv[1:] or ["pc1440", "pc1920", "phone390"]
    results = json.loads(OUT.read_text()) if OUT.exists() else {}
    svc, tmp = service()
    t0 = time.time()
    log = lambda m: print(f"[{time.time() - t0:6.1f}s] {m}", flush=True)  # noqa: E731
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            results["versions"] = {"chromium": browser.version, "playwright": "1.60.0",
                                   "axe": open(AXE).read(40).split("v")[1].split()[0]}
            state_ready = False
            for vp in which:
                w, h = VIEWPORTS[vp]
                phone = vp.startswith("phone")
                for scheme in ("light", "dark"):
                    key = f"{vp}-{scheme}"
                    ctx = browser.new_context(viewport={"width": w, "height": h}, color_scheme=scheme, is_mobile=phone,
                                              has_touch=phone, device_scale_factor=1, reduced_motion="no-preference")
                    ctx.add_init_script(LONGTASKS)
                    page = ctx.new_page()
                    errors = []
                    page.on("pageerror", lambda e, errors=errors: errors.append(str(e)))
                    open_panel(page, svc)
                    if not state_ready:
                        setup_state(page, svc, log)
                        state_ready = True
                    inject(page)
                    cdp = ctx.new_cdp_session(page)
                    cdp.send("DOM.enable")
                    r = results.setdefault(key, {})
                    r["header_graphics"] = page.evaluate("() => window.__A.graphics(document.getElementById('topbar'))")
                    for v in TAB_IDS:
                        go(page, v)
                        page.wait_for_timeout(1200)
                        page.evaluate("() => window.scrollTo(0, 0)")
                        t = r.setdefault(v, {})
                        try:
                            t["layout"] = page.evaluate("() => window.__A.layout()")
                            t["axe"] = run_axe(page)
                            t["targets24"] = page.evaluate("() => window.__A.targetSizes(24)")
                            if phone:
                                t["targets44"] = page.evaluate("() => window.__A.targetSizes(44)")
                            t["graphics"] = page.evaluate("() => window.__A.graphics([...document.querySelectorAll('[data-view]')].find(v => !v.hidden))")
                            t["native"] = native_controls(page, scheme)
                            page.evaluate("() => window.scrollTo(0, 0)")
                            page.screenshot(path=str(AUDIT / "shots" / f"{vp}-{scheme}-{v}-fold.png"))
                            page.screenshot(path=str(AUDIT / "shots" / f"{vp}-{scheme}-{v}-full.png"), full_page=True)
                            if vp == "pc1440":
                                ax = ax_unnamed(cdp)
                                for u in ax["unnamed"]:
                                    u["el"] = describe_backend(cdp, u.get("backendId"))
                                t["ax"] = ax
                                t["keyboard"] = keyboard(page, v, pixel_diff=(scheme == "light"))
                            if scheme == "light" and vp in ("pc1440", "phone390"):
                                snap = page.locator("body").aria_snapshot()
                                (AUDIT / "data" / f"aria-{vp}-{v}.yaml").write_text(snap)
                                t["aria_lines"] = snap.count("\n") + 1
                            if key == "pc1440-light":
                                page.evaluate("() => window.scrollTo(0, 0)")
                                t["watch"] = page.evaluate("() => window.__A.watch(5000)")
                                t["perf"] = perf(page, cdp, 10.0)
                        except Exception as exc:  # noqa: BLE001
                            t["error"] = traceback.format_exc()[-1500:]
                            log(f"{key} {v} error {exc}")
                        log(f"{key} {v} done")
                    if key == "pc1440-light":
                        try:
                            r["escape"] = escape_tests(page)
                        except Exception:  # noqa: BLE001
                            r["escape_error"] = traceback.format_exc()[-1500:]
                        try:
                            r["sliders"] = slider_keys(page)
                        except Exception:  # noqa: BLE001
                            r["sliders_error"] = traceback.format_exc()[-1500:]
                    if scheme == "light":
                        try:
                            r["tasks"] = task_positions(page)
                        except Exception:  # noqa: BLE001
                            r["tasks_error"] = traceback.format_exc()[-1500:]
                    r["pageErrors"] = errors
                    ctx.close()
                    OUT.write_text(json.dumps(results, indent=1, ensure_ascii=False, default=list))
            # Reduced motion, PC.
            if "pc1440" in which:
                ctx = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce", device_scale_factor=1)
                ctx.add_init_script(LONGTASKS)
                page = ctx.new_page()
                open_panel(page, svc)
                inject(page)
                cdp = ctx.new_cdp_session(page)
                rm = results.setdefault("reduced_motion", {})
                for v in ("escuchar", "calibrar", "diagnostico", "cadena"):
                    go(page, v)
                    page.wait_for_timeout(1000)
                    rm[v] = {"watch": page.evaluate("() => window.__A.watch(5000)"), "perf": perf(page, cdp, 10.0)}
                    log(f"reduced {v}")
                # Meters paused (Pausar in Niveles and Entrada).
                go(page, "escuchar")
                for card in ("levels", "input"):
                    b = page.locator(f"[data-card={card}] button", has_text="Pausar")
                    if b.count():
                        b.first.click()
                page.wait_for_timeout(500)
                rm["escuchar_paused"] = {"watch": page.evaluate("() => window.__A.watch(5000)"), "perf": perf(page, cdp, 10.0)}
                ctx.close()
                # A/B live: the card while a round runs.
                ctx = browser.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=1)
                page = ctx.new_page()
                open_panel(page, svc)
                inject(page)
                go(page, "escuchar")
                page.locator("#ab-a").select_option("cerrado")
                page.locator("#ab-b").select_option("amplio")
                page.locator("#ab-start").click()
                expect(page.locator("#ab-live")).to_be_visible()
                page.wait_for_timeout(800)
                page.locator("[data-card=ab]").screenshot(path=str(AUDIT / "shots" / "pc1440-light-ab-vivo.png"))
                results["ab_live"] = {"axe": run_axe(page), "targets24": page.evaluate("() => window.__A.targetSizes(24)")}
                page.locator("#ab-stop").click()
                page.wait_for_timeout(500)
                ctx.close()
                log("ab done")
            browser.close()
    finally:
        OUT.write_text(json.dumps(results, indent=1, ensure_ascii=False, default=list))
        svc.stop()
        tmp.cleanup()
        log("service stopped")


if __name__ == "__main__":
    main()
