"""Re-runs only the keyboard pass (PC 1440, light and dark) and merges it into results.json."""
import json
import time
from playwright.sync_api import sync_playwright
from harness import TAB_IDS, go, open_panel, service
from measure import LONGTASKS, OUT, inject, keyboard, setup_state

results = json.loads(OUT.read_text())
svc, tmp = service()
t0 = time.time()
log = lambda m: print(f"[{time.time() - t0:6.1f}s] {m}", flush=True)  # noqa: E731
try:
    with sync_playwright() as p:
        b = p.chromium.launch()
        ready = False
        for scheme in ("light", "dark"):
            ctx = b.new_context(viewport={"width": 1440, "height": 900}, color_scheme=scheme, device_scale_factor=1)
            ctx.add_init_script(LONGTASKS)
            page = ctx.new_page()
            open_panel(page, svc)
            if not ready:
                setup_state(page, svc, log)
                ready = True
            inject(page)
            for v in TAB_IDS:
                go(page, v)
                page.wait_for_timeout(1000)
                results[f"pc1440-{scheme}"].setdefault(v, {})["keyboard"] = keyboard(page, v, pixel_diff=True)
                log(f"{scheme} {v} kb done")
            ctx.close()
        b.close()
finally:
    OUT.write_text(json.dumps(results, indent=1, ensure_ascii=False, default=list))
    svc.stop()
    tmp.cleanup()
    log("service stopped")
