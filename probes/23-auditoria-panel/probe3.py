"""Slider track widths (px per step) at PC widths, and the services table overflow."""

import json

from playwright.sync_api import sync_playwright

from harness import AUDIT, go, open_panel, service

OUT = AUDIT / "data" / "probe3.json"
res = {}
svc, tmp = service()
try:
    svc.command("speaker_add_virtual")
    svc.command("start")
    with sync_playwright() as p:
        b = p.chromium.launch()
        for w, h in ((1440, 900), (1920, 1080), (390, 844)):
            ctx = b.new_context(viewport={"width": w, "height": h}, device_scale_factor=1, is_mobile=w < 500, has_touch=w < 500)
            page = ctx.new_page()
            open_panel(page, svc)
            r = res.setdefault(str(w), {})
            for v in ("escuchar", "parlantes", "cadena"):
                go(page, v)
                page.wait_for_timeout(800)
                r[v] = page.evaluate("""() => [...document.querySelectorAll('[data-view]:not([hidden]) input[type=range], #volume')]
                  .filter(e => e.getBoundingClientRect().width > 0).map(e => { const r = e.getBoundingClientRect();
                    const steps = (parseFloat(e.max) - parseFloat(e.min)) / (parseFloat(e.step) || 1);
                    const card = e.closest('[data-card]');
                    return { name: e.getAttribute('aria-label') || (e.labels && e.labels[0] ? e.labels[0].textContent.trim().slice(0, 30) : e.id), card: card ? card.dataset.card : 'cabecera',
                      w: Math.round(r.width), h: Math.round(r.height), steps: Math.round(steps), pxPerStep: +(r.width / steps).toFixed(2) }; })""")
            go(page, "diagnostico")
            page.wait_for_timeout(800)
            r["services_overflow"] = page.evaluate("""() => { const t = document.querySelector('.services-table'); if (!t) return null;
                const wrap = t.closest('.table-wrap') || t.parentElement; const cs = getComputedStyle(wrap);
                return { tableW: Math.round(t.scrollWidth), wrapW: Math.round(wrap.clientWidth), overflowX: cs.overflowX,
                  clippedButtons: [...t.querySelectorAll('button')].filter(bn => { const a = bn.getBoundingClientRect(), c = wrap.getBoundingClientRect(); return a.right > c.right + 1; }).map(bn => bn.textContent.trim()) }; }""")
            page.locator("[data-card=services]").screenshot(path=str(AUDIT / "shots" / f"w{w}-diagnostico-servicios.png"))
            ctx.close()
        b.close()
finally:
    OUT.write_text(json.dumps(res, indent=1, ensure_ascii=False))
    svc.stop()
    tmp.cleanup()
    print("stopped")
