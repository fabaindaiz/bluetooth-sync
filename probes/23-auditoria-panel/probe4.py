"""Why the Calibrar button's focus ring turns white when reached with Tab."""
import json
from playwright.sync_api import sync_playwright
from harness import AUDIT, go, open_panel, service
svc, tmp = service()
out = {}
try:
    svc.command("start")
    with sync_playwright() as p:
        b = p.chromium.launch()
        ctx = b.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=1)
        page = ctx.new_page()
        open_panel(page, svc)
        go(page, "calibrar")
        page.wait_for_timeout(800)
        page.mouse.click(3, 3)
        for i in range(80):
            page.keyboard.press("Tab")
            if page.evaluate("() => document.activeElement.id") == "cal-run":
                break
        q = """() => { const e = document.activeElement, c = getComputedStyle(e); return { id: e.id, fv: e.matches(':focus-visible'), hover: e.matches(':hover'),
              outline: c.outlineStyle + ' ' + c.outlineWidth + ' ' + c.outlineColor, cls: e.className, title: e.title, disabled: e.disabled }; }"""
        out["tab"] = page.evaluate(q)
        page.locator("#cal-run").screenshot(path=str(AUDIT / "shots" / "pc1440-light-calibrar-foco-tab.png"))
        cdp = ctx.new_cdp_session(page)
        cdp.send("DOM.enable"); cdp.send("CSS.enable")
        doc = cdp.send("DOM.getDocument")
        nid = cdp.send("DOM.querySelector", {"nodeId": doc["root"]["nodeId"], "selector": "#cal-run"})["nodeId"]
        cdp.send("CSS.forcePseudoState", {"nodeId": nid, "forcedPseudoClasses": ["focus", "focus-visible"]})
        m = cdp.send("CSS.getMatchedStylesForNode", {"nodeId": nid})
        rules = []
        for r in m.get("matchedCSSRules", []):
            props = [f"{pp['name']}:{pp['value']}" for pp in r["rule"]["style"]["cssProperties"] if "outline" in pp["name"]]
            if props:
                rules.append({"sel": r["rule"]["selectorList"]["text"][:120], "props": props})
        out["rules"] = rules
        page.mouse.move(700, 300)
        page.locator("#cal-run").focus()
        out["after_mouse_focus_call"] = page.evaluate(q)
        ctx.close(); b.close()
finally:
    (AUDIT / "data" / "probe4.json").write_text(json.dumps(out, indent=1, ensure_ascii=False))
    svc.stop(); tmp.cleanup(); print(json.dumps(out, indent=1, ensure_ascii=False)); print("stopped")
