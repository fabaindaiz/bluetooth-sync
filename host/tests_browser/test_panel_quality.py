"""The panel's usability fixes (spec 2026-10-02 §7.3), each one seen to fail without its change.

Same service and fixtures as `test_panel.py`: the real service in simulated mode, in real
browsers. A few tests need Chromium's DevTools protocol (layout counts, the navigation-cost
model of `probes/10-panel-organizacion/medir.py`) and skip on Firefox.
"""

from __future__ import annotations

import importlib.util
import json
import math
import re
from pathlib import Path

import pytest
from playwright.sync_api import Browser, Page, expect

from tests_browser.test_panel import NAMES, TOKEN, Running, go, start

PROBE = Path(__file__).resolve().parents[2] / "probes/10-panel-organizacion/medir.py"
RED = NAMES[0]
RED_ADDRESS = "90:F2:60:75:4A:83"


def chromium_only(browser: Browser) -> None:
    if browser.browser_type.name != "chromium":
        pytest.skip("needs Chromium's DevTools protocol")


def phone(browser: Browser, svc: Running, **options) -> Page:
    """A phone-sized page (390x844, touch). The caller closes `page.context`."""
    mobile = {"is_mobile": True} if browser.browser_type.name == "chromium" else {}
    context = browser.new_context(viewport={"width": 390, "height": 844}, has_touch=True, **mobile, **options)
    page = context.new_page()
    page.goto(f"{svc.url}/?t={TOKEN}")
    expect(page.locator("body[data-ready='1']")).to_be_attached()
    return page


def play_tone(page: Page) -> None:
    start(page)
    page.locator("#source-kind").select_option("tone")
    expect(page.locator(".meter")).to_have_count(6, timeout=5000)
    expect(page.locator(".meter", has_text="Entrada L").locator(".meter-value")).not_to_have_text("—", timeout=5000)


def calibrate(page: Page) -> None:
    go(page, "Calibrar")
    page.locator("#cal-seconds").fill("5")
    page.locator("#cal-run").click()
    expect(page.locator("#cal-note")).to_contain_text("Emitiendo", timeout=5000)
    expect(page.locator("#cal-note")).to_contain_text("Medido", timeout=30000)


# -- 1. meters without forced layout ---------------------------------------------------------


def test_the_meters_move_without_forcing_a_layout_per_meter(browser: Browser, svc: Running):
    """Before: `clientWidth` read after writing each meter's style, 7 layouts per frame (420/s)."""
    chromium_only(browser)
    page = phone(browser, svc)
    try:
        play_tone(page)
        page.locator("[data-card=levels]").scroll_into_view_if_needed()
        cdp = page.context.new_cdp_session(page)
        cdp.send("Performance.enable")

        def layouts() -> float:
            return next(m["value"] for m in cdp.send("Performance.getMetrics")["metrics"] if m["name"] == "LayoutCount")

        before = layouts()
        page.wait_for_timeout(3000)
        per_s = (layouts() - before) / 3
        assert per_s <= 65, f"{per_s:.0f} layouts/s"
        # The bar is a transform (compositor), not a width.
        mask = page.locator(".meter-mask").first
        expect(mask).to_have_attribute("style", re.compile(r"transform: translateX"))
    finally:
        page.context.close()


# -- 2. the stream closes with the tab hidden ------------------------------------------------


def open_streams(svc: Running) -> int | None:
    """The service's own count of open streams (`rest.make_server`'s closure), if it is there.

    The contract does not expose it; when the service publishes one (work package E), this reads
    that instead.
    """
    fn = getattr(svc.httpd.RequestHandlerClass, "_stream", None)
    if fn is None or fn.__closure__ is None:
        return None
    cells = dict(zip(fn.__code__.co_freevars, fn.__closure__, strict=True))
    cell = cells.get("streams")
    return cell.cell_contents.get("open") if cell is not None else None


SET_VISIBILITY = """(hidden) => {
  Object.defineProperty(document, 'hidden', { configurable: true, get: () => hidden });
  Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => (hidden ? 'hidden' : 'visible') });
  document.dispatchEvent(new Event('visibilitychange'));
}"""


def test_a_hidden_tab_closes_the_stream_and_comes_back_live(page: Page, svc: Running):
    expect(page.locator("#connection")).to_have_text("En vivo", timeout=10000)
    assert page.evaluate("window.aurasyncStream().open")
    polls: list[str] = []
    page.on("request", lambda r: polls.append(r.url) if "/v1/state" in r.url or "/v1/command" in r.url else None)

    page.evaluate(SET_VISIBILITY, True)
    assert page.evaluate("window.aurasyncStream()") == {"open": False, "readyState": None, "alive": False}
    if open_streams(svc) is not None:
        # The server notices on its next write: the state goes out at least once a second.
        for _ in range(25):
            if open_streams(svc) == 0:
                break
            page.wait_for_timeout(100)
        assert open_streams(svc) == 0, "the service still holds the stream 2.5 s after hiding"
    page.wait_for_timeout(1500)
    assert polls == [], f"a hidden tab kept polling: {polls[:3]}"

    page.evaluate(SET_VISIBILITY, False)
    expect(page.locator("#connection")).to_have_text("En vivo", timeout=2000)
    assert page.evaluate("window.aurasyncStream().open")
    if open_streams(svc) is not None:
        assert open_streams(svc) == 1


# -- 3. warnings where one looks -------------------------------------------------------------


def test_low_battery_and_a_lost_speaker_show_in_now_and_the_header(page: Page, svc: Running):
    start(page)
    observer = svc.service.observer
    observer._devices[RED_ADDRESS]["battery_pct"] = 15  # noqa: SLF001 - the simulation has no battery knob
    observer.refresh()
    sinks = svc.service.session._player._pids  # noqa: SLF001 - a stream dies, as a speaker lost
    sinks.pop(next(s for s in sinks if "75_4A_83" in s))
    go(page, "Escuchar")
    alerts = page.locator("#now-alerts")
    expect(alerts).to_be_visible(timeout=5000)
    expect(alerts).to_contain_text(f"{RED}: batería 15 %")
    expect(alerts).to_contain_text(f"{RED}: perdido")
    # Icon and text, not only colour.
    expect(alerts.locator("li svg")).to_have_count(2)
    chip = page.locator("#chip-alert")
    expect(chip).to_be_visible()
    expect(chip).to_contain_text("Go 4 Red")
    expect(chip.locator("svg")).to_have_count(1)


# -- 4. "en camino" for the measured latency -------------------------------------------------

IN_FLIGHT = """() => new Promise((resolve) => {
  const out = document.getElementById('volume-out');
  const slider = document.getElementById('volume');
  const t0 = performance.now();
  let on = null;
  let text = null;
  setTimeout(() => resolve({ on, off: null, text }), 3000); // never set, or never cleared
  new MutationObserver(() => {
    if (out.hasAttribute('data-pending')) {
      if (on === null) { on = performance.now() - t0; text = getComputedStyle(out, '::after').content; }
    } else if (on !== null) resolve({ on, off: performance.now() - t0, text });
  }).observe(out, { attributes: true, attributeFilter: ['data-pending'] });
  slider.value = '-25';
  slider.dispatchEvent(new Event('input', { bubbles: true }));
  slider.dispatchEvent(new Event('change', { bubbles: true }));
})"""


def test_a_changed_control_is_marked_in_flight_for_the_latency(page: Page, svc: Running):
    latency = svc.state()["latency"]
    expected = latency["measured_ms"] if latency["measured_ms"] is not None else latency["known_ms"]
    timing = page.evaluate(IN_FLIGHT)
    assert timing["on"] is not None, "no data-pending after the change"
    assert timing["on"] <= 100
    assert timing["off"] is not None, "data-pending never went away"
    assert expected - 150 <= timing["off"] <= expected + 300, (timing, expected)
    assert "en camino" in timing["text"]
    page.wait_for_timeout(300)
    assert svc.state()["global"]["volume_db"] == -25


# -- 5. accessibility --------------------------------------------------------------------------


def test_meters_say_their_value_in_words(page: Page):
    play_tone(page)
    track = page.locator(".meter", has_text="Entrada L").locator("[role=meter]")
    expect(track).to_have_attribute("aria-valuetext", re.compile(r"^-?\d+,\d dB, pico -?\d+,\d dB"), timeout=5000)


def test_saturation_says_sat_in_text(browser: Browser, svc: Running):
    """A meters frame at 0 dBFS through a stream served by the test (the simulation never clips)."""
    state = svc.state()
    meters = {
        "meters": {"in L": {"rms_db": -3.0, "peak_db": 0.0}, "in R": {"rms_db": -20.0, "peak_db": -12.0}},
        "synced": False,
        "limiter_db": {},
    }
    # The served stream ends, and the page reconnects every second: the first answer clips, the
    # next ones are quiet (with quiet meters in the state too), so a cleared SAT stays cleared.
    quiet = {**meters, "meters": {k: {"rms_db": -20.0, "peak_db": -12.0} for k in meters["meters"]}}
    state = {**state, "meters": quiet["meters"]}
    bodies = [
        f"event: state\ndata: {json.dumps(state)}\n\nevent: meters\ndata: {json.dumps(m)}\n\n" for m in (meters, quiet)
    ]
    served = []

    def serve(route) -> None:
        route.fulfill(status=200, body=bodies[min(len(served), 1)], headers={"Content-Type": "text/event-stream"})
        served.append(1)

    context = browser.new_context(viewport={"width": 1366, "height": 900})
    page = context.new_page()
    page.route("**/v1/stream*", serve)
    # Between two connections the page polls: it gets the same state, so the meters are not rebuilt
    # (a rebuilt meter starts without SAT, and the click would not be what cleared it).
    page.route(
        "**/v1/state",
        lambda route: route.fulfill(status=200, json={"v": 1, "ok": True, "result": state}),
    )
    page.goto(f"{svc.url}/?t={TOKEN}")
    try:
        left = page.locator(".meter", has_text="Entrada L").locator(".meter-clip")
        expect(left).to_have_text("SAT", timeout=5000)
        expect(left).to_have_attribute("aria-pressed", "true")
        expect(page.locator(".meter", has_text="Entrada R").locator(".meter-clip")).to_have_text("")
        # The peak is held 1.5 s at 0 dBFS (and falls after): while it is shown, SAT is lit again.
        page.wait_for_timeout(2000)
        left.click()
        expect(left).to_have_text("")
    finally:
        context.close()


def test_each_kind_of_cut_has_its_own_shape(page: Page, svc: Running):
    start(page)
    cuts = svc.service.session.cuts
    for kind in ("underrun", "xrun", "input_gap", "fade"):
        cuts.add(kind, RED, "prueba")
    go(page, "Diagnóstico")
    dots = page.locator("#cuts-timeline .cut-dot")
    # At least the four added; a loaded machine may add real ones (all of a kind already there).
    expect(dots.nth(3)).to_be_attached(timeout=5000)
    shapes = dots.evaluate_all("""(nodes) => nodes.map((n) => {
      const c = getComputedStyle(n);
      return [n.dataset.shape, c.borderTopLeftRadius, c.clipPath, c.borderTopWidth].join('|');
    })""")
    kinds = {s.split("|")[0] for s in shapes}
    assert {"círculo", "cuadrado", "triángulo", "anillo"} <= kinds, shapes
    # Without the colour: the geometry alone tells them apart.
    assert len({s.split("|", 1)[1] for s in shapes}) == len(kinds), shapes


def test_levels_and_input_can_be_paused(page: Page):
    play_tone(page)
    expect(page.locator("#input-bars .input-bar").first).to_be_attached(timeout=5000)

    def frames(selector: str) -> set[str]:
        seen = set()
        for _ in range(8):
            seen.add(page.locator(selector).evaluate_all("(ns) => ns.map((n) => n.style.transform).join(',')"))
            page.wait_for_timeout(120)
        return seen

    assert len(frames(".meter-mask")) > 1
    page.locator("#levels-pause").click()
    expect(page.locator("#levels-pause")).to_have_attribute("aria-pressed", "true")
    page.wait_for_timeout(100)
    assert len(frames(".meter-mask")) == 1
    page.locator("#input-pause").click()
    page.wait_for_timeout(150)
    assert len(frames("#input-bars .input-bar")) == 1
    page.locator("#levels-pause").click()
    expect(page.locator("#levels-pause")).to_have_text("Pausar")
    assert len(frames(".meter-mask")) > 1


COUNT_STYLE_CHANGES = """(args) => new Promise((resolve) => {
  const [selector, ms] = args;
  const nodes = [...document.querySelectorAll(selector)];
  const counts = new Map(nodes.map((n) => [n, 0]));
  const mo = new MutationObserver((records) => { for (const r of records) counts.set(r.target, counts.get(r.target) + 1); });
  for (const n of nodes) mo.observe(n, { attributes: true, attributeFilter: ['style'] });
  setTimeout(() => { mo.disconnect(); resolve([...counts.values()].map((c) => c / (ms / 1000))); }, ms);
})"""


def test_reduced_motion_updates_at_most_five_times_a_second_without_transitions(browser: Browser, svc: Running):
    context = browser.new_context(viewport={"width": 1366, "height": 900}, reduced_motion="reduce")
    page = context.new_page()
    page.goto(f"{svc.url}/?t={TOKEN}")
    try:
        play_tone(page)
        expect(page.locator("#input-bars .input-bar").first).to_be_attached(timeout=5000)
        rates = page.evaluate(COUNT_STYLE_CHANGES, [".meter-mask", 3000])
        assert max(rates) <= 5.4, rates  # 5 Hz, with one change of slack at the window's edge
        assert max(rates) > 0, "the meters stopped instead of slowing down"
        bars = page.evaluate(COUNT_STYLE_CHANGES, ["#input-bars .input-bar", 3000])
        assert max(bars) <= 5.4, bars
        durations = page.evaluate("""() => ['.btn', '.help-pop', '.input-bar'].map((s) => {
          const n = document.querySelector(s); return n ? getComputedStyle(n).transitionDuration : '0s'; })""")
        assert all(d.split(",")[0].strip() == "0s" for d in durations), durations
    finally:
        context.close()


# The spacing exception of WCAG 2.5.8, computed: a target under 44x44 passes if it is at least
# 24x24 and a 24 px circle centred on it does not touch another target (nor another circle).
TARGETS = """() => {
  const nodes = [...document.querySelectorAll('button, input:not([type=hidden]), select, textarea, a[href], [role=button]')];
  const boxes = [];
  for (const n of nodes) {
    // A checkbox inside its <label> is hit through the whole label.
    const hit = n.matches('input[type=checkbox], input[type=radio]') && n.closest('label') ? n.closest('label') : n;
    if (!hit.checkVisibility({ visibilityProperty: true, opacityProperty: true })) continue;
    const r = hit.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) continue;
    const name = n.id ? `#${n.id}` : `${n.tagName.toLowerCase()}.${String(n.className).split(' ')[0]} "${(n.getAttribute('aria-label') || n.textContent || '').trim().slice(0, 30)}"`;
    // Bars that stay fixed while the page scrolls are their own layer: a button scrolled under
    // the bottom bar is not next to it.
    const fixed = hit.closest('#topbar, #bottom-nav, #side-nav');
    const layer = fixed ? fixed.id : 'page';
    boxes.push({ name, layer, x: r.left, y: r.top + (fixed ? 0 : window.scrollY), w: r.width, h: r.height, hit });
  }
  const unique = boxes.filter((b, i) => boxes.findIndex((o) => o.hit === b.hit) === i);
  return unique.map(({ hit, ...b }) => b);
}"""


def circle_hits_box(cx: float, cy: float, r: float, box: dict) -> bool:
    nx = min(max(cx, box["x"]), box["x"] + box["w"])
    ny = min(max(cy, box["y"]), box["y"] + box["h"])
    return math.hypot(cx - nx, cy - ny) < r


def target_failures(boxes: list[dict]) -> list[str]:
    failures = []
    for b in boxes:
        if b["w"] >= 44 and b["h"] >= 44:
            continue
        if b["w"] < 24 or b["h"] < 24:
            failures.append(f"{b['name']}: {b['w']:.0f}x{b['h']:.0f} px")
            continue
        cx, cy = b["x"] + b["w"] / 2, b["y"] + b["h"] / 2
        for o in boxes:
            if o is b or o["layer"] != b["layer"]:
                continue
            if circle_hits_box(cx, cy, 12, o):
                failures.append(f"{b['name']}: {b['w']:.0f}x{b['h']:.0f} px touches {o['name']}")
                break
    return failures


def test_touch_targets_on_a_phone(browser: Browser, svc: Running):
    page = phone(browser, svc)
    try:
        start(page)
        page.wait_for_timeout(600)
        failures = []
        for view in page.evaluate("[...document.querySelectorAll('[data-view]')].map((v) => v.dataset.view)"):
            page.evaluate('(v) => window.aurasyncShow(`[data-view="${v}"] *`)', view)
            page.wait_for_timeout(200)
            failures += [f"{view}: {f}" for f in target_failures(page.evaluate(TARGETS))]
        assert failures == [], "\n".join(failures)
    finally:
        page.context.close()


def test_a_visible_reset_button_when_a_slider_is_off_its_neutral(browser: Browser, svc: Running):
    page = phone(browser, svc)
    try:
        card = page.locator(f'[data-quick="{RED}"]')
        reset = card.locator(".reset-btn")
        expect(reset).to_be_hidden()
        card.locator(".q-gain").fill("-6")
        expect(reset).to_be_visible()
        box = reset.bounding_box()
        assert box["width"] >= 44, box
        assert box["height"] >= 44, box
        page.wait_for_timeout(500)
        assert next(s for s in svc.state()["speakers"] if s["name"] == RED)["gain_db"] == -6
        reset.click()
        expect(reset).to_be_hidden()
        page.wait_for_timeout(500)
        assert next(s for s in svc.state()["speakers"] if s["name"] == RED)["gain_db"] == 0
    finally:
        page.context.close()


def test_native_controls_follow_the_colour_scheme(page: Page):
    assert page.evaluate("getComputedStyle(document.documentElement).colorScheme") == "light dark"


# -- 6. numbers with a decimal comma -----------------------------------------------------------

VALUES = """() => [...document.querySelectorAll('.num, output, .tile-value, .sync-value, #latency-total, .chip')]
  .filter((n) => n.checkVisibility() && !n.closest('#logs, #services, .speaker-sub, input'))
  .map((n) => n.textContent.trim()).filter(Boolean)"""


def test_visible_numbers_use_a_decimal_comma(page: Page, svc: Running):
    play_tone(page)
    calibrate(page)
    seen = []
    for label in ("Escuchar", "Cadena", "Parlantes", "Calibrar", "Diagnóstico", "Ajustes"):
        go(page, label)
        page.wait_for_timeout(300)
        seen += page.evaluate(VALUES)
    assert any(re.search(r"\d,\d", v) for v in seen), seen  # it did look at decimals
    dotted = [v for v in seen if re.search(r"\d\.\d", v)]
    assert dotted == [], dotted


# -- 7. the navigation cost does not regress ----------------------------------------------------

# `pestanas`, measured with probes/10-panel-organizacion/medir.py. Round 3 (2026-10-01) measured
# 18.4 (phone) and 6.5 (PC); the panel at f44efb2 already measured 28.0 and 10.2 (the "Ahora"
# card pushed the compact card and the A/B down). After the fixes of 2026-10-02 (among them
# `.inline` -> `.hstack`: Tailwind's `inline` utility had been stacking each speaker's buttons):
# 24.6 and 9.9. With the Cadena screen (package F) the six scenarios measure 26.1 and 10.0
# (+1.0: the decorrelation moved from Ajustes to Cadena, two cards down; +0.3: the radio box in
# Cortes; +0.2: the A/B's "Igualar la sonoridad"), and the seventh, "Afinar la cadena", brings
# the totals to 31.4 and 13.7 (docs/research/10-panel-de-control.md §5). The guard holds these;
# a change that has to raise them says so here.
MAX_COST = {"teléfono": 31.4, "PC": 13.7}
MAX_COST_SIX = {"teléfono": 26.1, "PC": 10.0}


def test_the_navigation_cost_of_the_tabs_does_not_grow(browser: Browser):
    chromium_only(browser)
    spec = importlib.util.spec_from_file_location("medir", PROBE)
    medir = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(medir)
    results = medir.run(["pestanas"], medir.VIEWPORTS, browser=browser)
    totals = {vp: by["pestanas"]["total"] for vp, by in results.items()}
    for vp, limit in MAX_COST.items():
        assert totals[vp] <= limit, (vp, totals[vp], results[vp]["pestanas"]["escenarios"])
        six = results[vp]["pestanas"]["total_seis"]
        assert six <= MAX_COST_SIX[vp], (vp, six, results[vp]["pestanas"]["escenarios"])


# -- 8. sync for the listener -------------------------------------------------------------------


def test_now_shows_the_measured_misalignment_on_the_perceptual_ruler(page: Page, svc: Running):
    start(page)
    sync = page.locator("#now-sync")
    expect(sync).to_have_attribute("data-zone", "none")
    expect(page.locator("#sync-value")).to_have_text("sin medición")
    # The simulated room: 3, 7.5 and 12 ms. Measured, before any correction: 9 ms apart.
    calibrate(page)
    go(page, "Escuchar")
    expect(sync).to_have_attribute("data-zone", "double", timeout=5000)
    expect(page.locator("#sync-value")).to_have_text(re.compile(r"^(8,[7-9]|9,[0-3]) ms$"))
    expect(page.locator("#sync-phrase")).to_contain_text("puede oírse doble")
    # Applying moves the delays: that number no longer holds, and the panel does not guess.
    go(page, "Calibrar")
    page.locator("#cal-apply").click()
    go(page, "Escuchar")
    expect(sync).to_have_attribute("data-zone", "none", timeout=5000)
    expect(page.locator("#sync-note")).to_contain_text("calibrá otra vez")
    calibrate(page)
    go(page, "Escuchar")
    expect(sync).to_have_attribute("data-zone", "fused", timeout=10000)
    expect(page.locator("#sync-phrase")).to_contain_text("un solo sonido")
    value = float(page.locator("#sync-value").inner_text().split()[0].replace(",", "."))
    assert value < 2
