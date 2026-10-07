"""Second pass: re-rendered nodes and focus loss, Escape on popups, primary-button focus, logs, toast, names."""

import base64
import json
import re
import time

from playwright.sync_api import sync_playwright

from harness import AUDIT, TAB_IDS, go, open_panel, service
from measure import DIFF, LONGTASKS, inject, setup_state

OUT = AUDIT / "data" / "probe2.json"
res = {}
svc, tmp = service()
t0 = time.time()
log = lambda m: print(f"[{time.time() - t0:6.1f}s] {m}", flush=True)  # noqa: E731


def focus_diff(page, loc):
    """Keyboard modality, then focus the element; pixels with focus vs without."""
    loc.scroll_into_view_if_needed()
    page.keyboard.press("Shift")
    loc.focus()
    page.wait_for_timeout(150)
    box = loc.bounding_box()
    clip = {"x": max(box["x"] - 6, 0), "y": max(box["y"] - 6, 0), "width": box["width"] + 12, "height": box["height"] + 12}
    fv = loc.evaluate("(e) => e.matches(':focus-visible')")
    outline = loc.evaluate("(e) => { const c = getComputedStyle(e); return c.outlineStyle + ' ' + c.outlineWidth + ' ' + window.__A.hex(window.__A.rgba(c.outlineColor)); }")
    a = page.screenshot(clip=clip)
    loc.evaluate("(e) => e.blur()")
    b = page.screenshot(clip=clip)
    d = page.evaluate(DIFF, [base64.b64encode(a).decode(), base64.b64encode(b).decode()])
    return {"fv": fv, "outline": outline, **d}


try:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for scheme in ("light", "dark"):
            ctx = browser.new_context(viewport={"width": 1440, "height": 900}, color_scheme=scheme, device_scale_factor=1)
            ctx.add_init_script(LONGTASKS)
            page = ctx.new_page()
            open_panel(page, svc)
            if scheme == "light":
                setup_state(page, svc, log)
            inject(page)
            r = res.setdefault(scheme, {})
            # 1. Nodes replaced while nobody touches them (focus on them is lost).
            if scheme == "light":
                rep = {}
                for v in TAB_IDS:
                    go(page, v)
                    page.wait_for_timeout(800)
                    page.evaluate("() => { window.__tagged = window.__A.interactive().filter(e => !e.closest('#cards')); }")
                    page.wait_for_timeout(3000)
                    gone = page.evaluate("""() => { const g = window.__tagged.filter(e => !e.isConnected).map(e => window.__A.desc(e));
                        return { total: window.__tagged.length, gone: g.length, examples: [...new Map(g.map(x => [x.sel + x.text.slice(0,15), x])).values()].slice(0, 15) }; }""")
                    rep[v] = gone
                    log(f"replaced {v}: {gone['gone']}/{gone['total']}")
                r["replaced"] = rep
                # Focus loss, measured: focus the control, wait 3 s, where is the focus?
                checks = [("parlantes", "button", "Desconectar"), ("parlantes", "button", "Usar «Automático»"), ("parlantes", "button.seg", "Ambiental"),
                          ("escuchar", "[data-card=presets] button", "Cargar"), ("escuchar", "[data-card=quick] button", "Silenciar"),
                          ("diagnostico", "[data-card=services] button", "Detener"), ("escuchar", "button.meter-clip", ""),
                          ("parlantes", "[data-card=speakers] input[type=range]", ""), ("parlantes", "[data-card=speakers] select", "")]
                fl = []
                for v, sel, text in checks:
                    go(page, v)
                    loc = page.locator(f"[data-view={v}] {sel}", has_text=text) if text else page.locator(f"[data-view={v}] {sel}")
                    loc = loc.locator("visible=true").first
                    if loc.count() == 0:
                        fl.append({"control": f"{v} {sel} {text}", "found": False})
                        continue
                    loc.scroll_into_view_if_needed()
                    page.keyboard.press("Shift")
                    loc.focus()
                    page.evaluate("() => { window.__f = document.activeElement; }")
                    page.wait_for_timeout(3000)
                    after = page.evaluate("() => ({ same: document.activeElement === window.__f, connected: window.__f.isConnected, now: document.activeElement === document.body ? 'body' : window.__A.desc(document.activeElement).sel })")
                    fl.append({"control": f"{v} {sel} «{text}»", **after})
                r["focus_loss"] = fl
                log("focus loss done")
                # 2. Volume slider's accessible name.
                r["volume_name"] = {
                    "slider named /Volumen/": page.get_by_role("slider", name=re.compile("Volumen")).count(),
                    "status named Volumen": page.get_by_role("status", name="Volumen").count(),
                    "labels of #volume": page.evaluate("() => [...document.getElementById('volume').labels].map(l => l.textContent.trim())"),
                    "label.control": page.evaluate("() => { const l = document.getElementById('volume').closest('label'); return l.control ? (l.control.id || l.control.tagName) : null; }"),
                }
                # Every label whose control is not the input the label wraps.
                r["label_mismatch"] = page.evaluate("""() => [...document.querySelectorAll('label')].filter(l => {
                    const inputs = l.querySelectorAll('input,select,textarea'); return inputs.length && l.control && !['INPUT','SELECT','TEXTAREA'].includes(l.control.tagName);
                  }).map(l => ({ label: l.textContent.trim().replace(/\\s+/g,' ').slice(0,40), control: l.control.tagName + '#' + l.control.id, input: l.querySelector('input,select,textarea').id || l.querySelector('input,select,textarea').className }))""")
                # 3. Logs: duplicated lines.
                go(page, "diagnostico")
                page.wait_for_timeout(1500)
                r["logs"] = page.evaluate("""() => { const t = [...document.querySelectorAll('#logs li')].map(l => l.textContent);
                    return { lines: t.length, unique: new Set(t).size }; }""")
                # 4. Cadena live values: distinct texts over 5 s without a pause control.
                go(page, "cadena")
                page.wait_for_timeout(800)
                page.evaluate("""() => { window.__live = [...document.querySelectorAll('[data-view=cadena] dd, [data-view=cadena] .live-value, [data-view=cadena] [class*=metric]')].slice(0, 400);
                  window.__seen = window.__live.map(e => new Set([e.textContent])); window.__iv = setInterval(() => window.__live.forEach((e, i) => window.__seen[i].add(e.textContent)), 100); }""")
                page.wait_for_timeout(5000)
                r["cadena_live"] = page.evaluate("""() => { clearInterval(window.__iv); const ch = window.__seen.map((s, i) => [s.size, window.__live[i]]).filter(([n]) => n > 1);
                  return { watched: window.__live.length, changing: ch.length, maxDistinct: Math.max(0, ...ch.map(([n]) => n)), examples: ch.slice(0, 6).map(([n, e]) => window.__A.desc(e).sel + ' ' + n),
                           pauseButtons: [...document.querySelectorAll('[data-view=cadena] button')].filter(b => /paus/i.test(b.textContent)).length }; }""")
                log("cadena live done")
                # 5. Undo toast (deleting a preset offers «Deshacer» instead of confirm()): time and coverage.
                go(page, "escuchar")
                row = page.locator("#presets li", has_text="cerrado")
                row.locator("button", has_text="Borrar").click()
                t_start = time.time()
                page.wait_for_selector("#undo:not([hidden])", timeout=5000)
                cover = page.evaluate("""() => { const u = document.getElementById('undo').getBoundingClientRect();
                    return { rect: [Math.round(u.left), Math.round(u.top), Math.round(u.width), Math.round(u.height)], covers: window.__A.interactive().filter(e => !e.closest('#undo') && !e.closest('#cards')).filter(e => { const r = e.getBoundingClientRect(); return r.left < u.right && r.right > u.left && r.top < u.bottom && r.bottom > u.top; }).map(e => window.__A.desc(e).sel + ' ' + window.__A.desc(e).text.slice(0, 20)) }; }""")
                page.mouse.move(5, 5)
                page.wait_for_selector("#undo", state="hidden", timeout=60000)
                r["undo"] = {"visible_s": round(time.time() - t_start, 1), **cover}
                # Hover over it: does it stay (2.2.1)?
                page.locator("#preset-name").fill("cerrado")
                page.locator("#preset-save").click()
                page.wait_for_timeout(500)
                page.locator("#presets li", has_text="cerrado").locator("button", has_text="Borrar").click()
                page.wait_for_selector("#undo:not([hidden])", timeout=5000)
                page.locator("#undo").hover()
                t1 = time.time()
                try:
                    page.wait_for_selector("#undo", state="hidden", timeout=30000)
                    r["undo"]["with_hover_s"] = round(time.time() - t1, 1)
                except Exception:  # noqa: BLE001
                    r["undo"]["with_hover_s"] = ">30 (queda mientras el mouse está encima)"
                    page.locator("#undo button").click()
                # Keyboard: is «Deshacer» reachable before it goes? Tab count from the deleting button.
                page.mouse.move(5, 5)
                page.wait_for_timeout(300)
                if page.locator("#presets li", has_text="cerrado").count() == 0:
                    page.locator("#preset-name").fill("cerrado")
                    page.locator("#preset-save").click()
                    page.wait_for_timeout(500)
                page.locator("#presets li", has_text="cerrado").locator("button", has_text="Borrar").focus()
                page.keyboard.press("Enter")
                page.wait_for_timeout(300)
                r["undo"]["focus_after_delete"] = page.evaluate("() => document.activeElement === document.body ? 'body' : window.__A.desc(document.activeElement).sel + ' ' + window.__A.desc(document.activeElement).text")
                r["undo"]["undo_in_tab_order_after"] = page.evaluate("""() => { const all = window.__A.interactive().filter(e => !e.closest('#cards')); const u = document.querySelector('#undo button');
                    const i = all.indexOf(document.activeElement), j = all.indexOf(u); return { from: i, undo: j, total: all.length }; }""")
                if page.locator("#undo:not([hidden]) button").count():
                    page.locator("#undo button").click()
                log("undo done")
            # 6. Escape on the (?) popups, both themes.
            go(page, "ajustes")
            btn = page.locator("[data-view=ajustes] .help-btn").first
            pop = page.locator("[data-view=ajustes] .help-pop").first
            vis = lambda pop=pop: pop.evaluate("(e) => getComputedStyle(e).visibility")  # noqa: E731
            btn.scroll_into_view_if_needed()
            btn.hover()
            page.wait_for_timeout(400)
            esc = {"hover_visible": vis()}
            page.keyboard.press("Escape")
            page.wait_for_timeout(300)
            esc["after_escape_hover"] = vis()
            pb = pop.bounding_box()
            if pb:
                page.mouse.move(pb["x"] + 20, pb["y"] + 10, steps=10)
                page.wait_for_timeout(300)
                esc["hoverable"] = vis()
            page.mouse.move(5, 5)
            page.wait_for_timeout(300)
            page.keyboard.press("Shift")
            btn.focus()
            page.wait_for_timeout(400)
            esc["focus_visible_popup"] = vis()
            page.keyboard.press("Escape")
            page.wait_for_timeout(300)
            esc["after_escape_focus"] = vis()
            esc["popup_contrast_text"] = pop.evaluate("(e) => { const c = window.__A.rgba(getComputedStyle(e).color), b = window.__A.bgOf(e); return window.__A.ratio(c, b).toFixed(2); }")
            esc["popup_width"] = pb["width"] if pb else None
            r["help_popup"] = esc
            # Cadena (?) buttons.
            go(page, "cadena")
            q = page.locator("[data-view=cadena] .help-btn, [data-view=cadena] button[aria-label^='Ayuda'], [data-view=cadena] button:text-is('?')").locator("visible=true").first
            if q.count():
                q.scroll_into_view_if_needed()
                page.keyboard.press("Shift")
                q.focus()
                before = q.evaluate("(e) => ({ expanded: e.getAttribute('aria-expanded'), label: e.getAttribute('aria-label'), controls: e.getAttribute('aria-controls') })")
                page.keyboard.press("Enter")
                page.wait_for_timeout(400)
                mid = q.evaluate("(e) => e.getAttribute('aria-expanded')")
                page.keyboard.press("Escape")
                page.wait_for_timeout(300)
                r["cadena_help"] = {"before": before, "after_enter": mid, "after_escape": q.evaluate("(e) => e.getAttribute('aria-expanded')"),
                                    "focus_after": page.evaluate("() => window.__A.desc(document.activeElement).sel")}
                if r["cadena_help"]["after_escape"] == "true":
                    page.keyboard.press("Enter")
            # details + Escape
            go(page, "calibrar")
            det = page.locator("#est-explain summary")
            if det.count():
                det.scroll_into_view_if_needed()
                det.focus()
                page.keyboard.press("Enter")
                o1 = page.locator("#est-explain").evaluate("(e) => e.open")
                page.keyboard.press("Escape")
                o2 = page.locator("#est-explain").evaluate("(e) => e.open")
                r["details_escape"] = {"opened": o1, "open_after_escape": o2}
                if o2:
                    det.press("Enter")
            # 7. Focus on every primary button, measured in pixels (both themes, playing and stopped).
            prim = []
            for v in TAB_IDS:
                go(page, v)
                page.wait_for_timeout(400)
                for i in range(page.locator(f"[data-view={v}] .btn-primary, #topbar .btn-primary").count()):
                    loc = page.locator(f"[data-view={v}] .btn-primary, #topbar .btn-primary").nth(i)
                    if not loc.is_visible() or loc.is_disabled():
                        continue
                    prim.append({"view": v, "text": loc.inner_text().strip()[:30], **focus_diff(page, loc)})
            r["primary_focus"] = prim
            log(f"{scheme} primary focus done")
            ctx.close()
        # Stopped state: the Iniciar button (primary) focus, light and dark.
        svc.command("stop")
        for scheme in ("light", "dark"):
            ctx = browser.new_context(viewport={"width": 1440, "height": 900}, color_scheme=scheme, device_scale_factor=1)
            page = ctx.new_page()
            open_panel(page, svc)
            inject(page)
            page.wait_for_timeout(800)
            res[scheme]["run_stopped_focus"] = {"text": page.locator("#run").inner_text(), **focus_diff(page, page.locator("#run"))}
            ctx.close()
        browser.close()
finally:
    OUT.write_text(json.dumps(res, indent=1, ensure_ascii=False))
    svc.stop()
    tmp.cleanup()
    log("service stopped")
