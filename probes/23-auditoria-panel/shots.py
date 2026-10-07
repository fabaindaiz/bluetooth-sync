"""Screenshots of every tab, both themes, three viewports; plus page heights."""

import json
import sys

from playwright.sync_api import sync_playwright

from harness import AUDIT, TAB_IDS, go, open_panel, realistic, service

VIEWPORTS = {"pc1440": (1440, 900), "pc1920": (1920, 1080), "phone390": (390, 844)}
only = sys.argv[1:] or list(VIEWPORTS)
svc, tmp = service()
out = {}
try:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        print("chromium", browser.version)
        for name in only:
            w, h = VIEWPORTS[name]
            for scheme in ("light", "dark"):
                ctx = browser.new_context(viewport={"width": w, "height": h}, color_scheme=scheme,
                                          is_mobile=name.startswith("phone"), has_touch=name.startswith("phone"),
                                          device_scale_factor=1)
                page = ctx.new_page()
                open_panel(page, svc)
                if not out:
                    realistic(page, svc)
                    out["setup"] = True
                for v in TAB_IDS:
                    go(page, v)
                    page.wait_for_timeout(800)
                    page.screenshot(path=str(AUDIT / "shots" / f"{name}-{scheme}-{v}-fold.png"))
                    page.screenshot(path=str(AUDIT / "shots" / f"{name}-{scheme}-{v}-full.png"), full_page=True)
                    out[f"{name}-{scheme}-{v}"] = page.evaluate(
                        "() => ({scrollH: document.documentElement.scrollHeight, header: document.getElementById('topbar').getBoundingClientRect().height})")
                ctx.close()
        browser.close()
finally:
    svc.stop()
    tmp.cleanup()
print(json.dumps(out, indent=1))
