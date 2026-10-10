"""The Cadena screen, the A/B with matched loudness and the radio in Cortes (spec 2026-10-02 §7).

The screen is Preact (`host/web/`, compiled to `panel/cadena.js`, d-7c8794-6da524) and is drawn
from the `chain` operation alone. Same service and fixtures as `test_panel.py`: the real service
in simulated mode (with the simulated radio and log level, as `aurasync service --simular`).
"""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import Browser, Page, expect

from aurasync import chain
from tests_browser.test_panel import NAMES, TOKEN, Running, go, start
from tests_browser.test_panel_quality import TARGETS, phone, target_failures

RED = NAMES[0]


def stage(page: Page, stage_id: str):
    return page.locator(f'[data-stage="{stage_id}"]')


def knobs(page: Page, stage_id: str):
    """The stage's own knobs, opened (they are folded unless one is off its default)."""
    box = stage(page, stage_id).locator("details.knobs")
    if box.get_attribute("open") is None:
        box.locator("summary").click()
    expect(box).to_have_attribute("open", "")
    return box


def wait_for(svc: Running, predicate, page: Page, timeout_ms: int = 5000):
    """Poll the service until `predicate(svc)` holds (what the panel asked reached it)."""
    for _ in range(timeout_ms // 100):
        value = predicate(svc)
        if value:
            return value
        page.wait_for_timeout(100)
    pytest.fail("the service never showed the change")


# -- drawn from `chain` -----------------------------------------------------------------------


def test_the_tab_sits_between_listen_and_speakers(page: Page):
    labels = page.locator("#nav [data-goto]").all_text_contents()
    assert labels[:3] == ["Escuchar", "Cadena", "Parlantes"], labels


def test_every_stage_of_chain_is_drawn_in_order(page: Page, svc: Running):
    described = svc.command("chain")
    go(page, "Cadena")
    cards = page.locator("[data-stage]")
    expect(cards).to_have_count(len(described["stages"]))
    assert cards.evaluate_all("(ns) => ns.map((n) => n.dataset.stage)") == [s["id"] for s in described["stages"]]
    flow = page.locator(".stage-flow")
    expect(flow).to_contain_text("Entrada")
    expect(flow).to_contain_text("Parlantes")
    titles = [s["title"] for s in described["stages"]]
    order = r"\s*→\s*".join(map(re.escape, ["Entrada", *titles, "Parlantes"]))
    assert re.search(order, flow.inner_text()), flow.inner_text()
    for s in described["stages"]:
        card = stage(page, s["id"])
        expect(card.locator(".stage-title")).to_contain_text(s["title"])
        options = card.locator("[data-algorithm]")
        expect(options).to_have_count(len(s["algorithms"]) if len(s["algorithms"]) > 1 else 0)
        for a in s["algorithms"] if len(s["algorithms"]) > 1 else []:
            option = card.locator(f'[data-algorithm="{a["id"]}"]')
            expect(option).to_contain_text(a["title"])
            if a["available"]:
                expect(option.locator("input")).to_be_enabled()
            else:
                expect(option.locator("input")).to_be_disabled()
                expect(option).to_contain_text(a["unavailable_reason"])
        current = next(a for a in s["algorithms"] if a["id"] == s["value"]["algorithm"])
        for p in current["params"]:
            if p["scope"] == "global":
                expect(card.locator(f'.param-grid [data-param="{p["id"]}"]')).to_have_count(1)
            else:  # one cell per speaker
                expect(card.locator(f'.spk-cell [data-param="{p["id"]}"]')).to_have_count(len(NAMES))


def test_choosing_an_algorithm_and_moving_a_knob_reach_the_service(page: Page, svc: Running):
    go(page, "Cadena")
    limiter = stage(page, "limiter")
    limiter.locator('[data-algorithm="true_peak"]').click()
    wait_for(svc, lambda s: s.stage("limiter")["value"]["algorithm"] == "true_peak", page)
    # The new algorithm's own knob appears, from the descriptor.
    knobs(page, "limiter")
    expect(limiter.locator('[data-param="lookahead_ms"]')).to_be_visible()
    expect(limiter.locator('[data-param="lookahead_ms"] .apply-badge')).to_have_text("con un corte breve")
    limiter.locator('[data-param="release_ms"] input[type=range]').fill("400")
    wait_for(svc, lambda s: s.stage("limiter")["value"]["params"]["release_ms"] == 400, page)
    number = limiter.locator('[data-param="ceiling_db"] input[type=number]')
    number.fill("-2")
    number.press("Enter")
    wait_for(svc, lambda s: s.stage("limiter")["value"]["params"]["ceiling_db"] == -2, page)
    assert svc.stage("limiter")["chosen"] == {
        "algorithm": "true_peak",
        "params": {"release_ms": 400.0, "ceiling_db": -2.0},
    }
    # A per-speaker knob, in its speaker's cell.
    amb = stage(page, "ambience")
    amb.locator(f'.spk-cell [data-param="ambience"][data-speaker="{RED}"] input[type=range]').fill("0.4")
    wait_for(svc, lambda s: s.stage("ambience")["value"]["speakers"][RED]["ambience"] == pytest.approx(0.4), page)


def test_the_reset_button_goes_back_to_the_default(page: Page, svc: Running):
    svc.command("chain_set", stage="limiter", params={"release_ms": 400})
    go(page, "Cadena")
    # A knob off its default opens its stage's knobs from the start: a change is never hidden.
    expect(stage(page, "limiter").locator("details.knobs")).to_have_attribute("open", "")
    expect(stage(page, "limiter").locator(".knobs-count")).to_contain_text("1 fuera de su valor por defecto")
    knob = stage(page, "limiter").locator('[data-param="release_ms"]')
    reset = knob.locator(".reset-btn")
    expect(reset).to_be_visible()
    expect(knob.locator("input[type=number]")).to_have_value("400")
    reset.click()
    wait_for(svc, lambda s: not s.stage("limiter")["chosen"], page)
    assert svc.stage("limiter")["value"]["params"]["release_ms"] == chain.default("limiter", "release_ms")
    expect(reset).to_be_hidden()
    expect(knob.locator("input[type=number]")).to_have_value("250")


def test_a_change_is_marked_in_flight(page: Page):
    go(page, "Cadena")
    knob = knobs(page, "limiter").locator('[data-param="release_ms"]')
    knob.locator("input[type=range]").fill("500")
    expect(knob.locator("output")).to_have_attribute("data-pending", "dots", timeout=1000)
    expect(knob.locator("output")).not_to_have_attribute("data-pending", "dots", timeout=3000)


SHIMMER = chain.Stage(
    "shimmer",
    "Brillo de prueba",
    "Una etapa que solo existe en este test.",
    "Agregada a los descriptores en Python; el panel no la conoce.",
    (
        chain.Algorithm("off", "Sin brillo", "Nada.", ""),
        chain.Algorithm(
            "sparkle",
            "Destellos",
            "Una perilla nueva.",
            "",
            (chain.Param("amount_db", "Cantidad", "Cuánto brilla.", "", "float", -6.0, -12.0, 0.0, 0.5, "dB"),),
            cost="nada",
            implemented=False,
        ),
    ),
    "off",
)


def test_a_stage_added_in_python_appears_without_touching_the_front(page: Page, svc: Running, monkeypatch):
    # It goes last, after whatever stage is last today (`Transiciones` since 2026-10-08).
    last = chain.CHAIN[-1].title
    monkeypatch.setattr(chain, "CHAIN", (*chain.CHAIN, SHIMMER))
    monkeypatch.setitem(chain.STAGES, SHIMMER.id, SHIMMER)
    go(page, "Cadena")
    card = stage(page, "shimmer")
    expect(card).to_be_visible()
    expect(card).to_contain_text("Brillo de prueba")
    flow = re.compile(rf"{re.escape(last)}\s*→\s*Brillo de prueba\s*→\s*Parlantes")
    expect(page.locator(".stage-flow")).to_contain_text(flow)
    card.locator('[data-algorithm="sparkle"]').click()
    wait_for(svc, lambda s: s.stage("shimmer")["value"]["algorithm"] == "sparkle", page)
    expect(card).to_contain_text("elegido, todavía no corre")
    knobs(page, "shimmer").locator('[data-param="amount_db"] input[type=range]').fill("-3")
    wait_for(svc, lambda s: s.stage("shimmer")["value"]["params"]["amount_db"] == -3, page)


def test_live_metrics_come_from_the_chain_event(page: Page):
    start(page)
    page.locator("#source-kind").select_option("tone")
    go(page, "Cadena")
    limiter = stage(page, "limiter").locator(".stage-metrics")
    expect(limiter).to_contain_text("En vivo", timeout=5000)
    expect(limiter.locator('[data-metric="reduction_db"] .metric-bar')).to_have_count(len(NAMES), timeout=5000)
    expect(stage(page, "align").locator('[data-metric="delay_now_ms"]')).to_contain_text("Go 4 Red")


def test_sound_settings_moved_out_of_ajustes(page: Page):
    go(page, "Ajustes")
    config = page.locator("[data-card=config]")
    for key in ("extract_ambience", "decorrelate", "eq_active", "rear_delay_ms"):
        expect(config.locator(f'[data-global="{key}"]')).to_have_count(0)
    config.locator('[data-goto-card="chain"]').click()
    expect(stage(page, "align")).to_be_visible()
    expect(knobs(page, "align").locator('[data-param="rear_delay_ms"]')).to_be_visible()


# -- the quality strip ---------------------------------------------------------------------------


def test_the_quality_strip_shows_the_live_loudness(page: Page):
    go(page, "Cadena")
    expect(page.locator("#chain-quality")).to_contain_text("se mide mientras suena")
    start(page)
    page.locator("#source-kind").select_option("tone")
    go(page, "Cadena")
    expect(page.locator('[data-q="in"] .q-value')).to_have_text(re.compile(r"^-?\d+,\d LUFS$"), timeout=8000)
    expect(page.locator('[data-q="out"] .q-value')).to_have_text(re.compile(r"^-?\d+,\d LUFS$"))
    expect(page.locator('[data-q="gain"] .q-value')).to_have_text(re.compile(r"LU$"))


QUALITY = {
    "input": {"m": -23.0, "s": -23.0, "i": -23.0, "tp": -4.0, "psr": 15.0},
    "outputs": {
        "JBL Go 4 Red": {"m": -26.0, "s": -26.0, "tp": -6.0, "psr": 14.5, "limiter_pct": 0.0, "flattening": False},
        "JBL Go 4 Blue": {"m": -25.0, "s": -25.0, "tp": -9.5, "psr": 11.0, "limiter_pct": 12.0, "flattening": True},
    },
    "sum": {"m": -27.0, "s": -27.4},
    "net_gain_lu": -24.4,
    "chain_gain_lu": -4.4,
    "flattening": True,
    "flattening_outputs": ["JBL Go 4 Blue"],
    "tp_max": -6.0,
    "limiter_pct_max": 12.0,
    "cost_ms": 0.3,
}


def test_the_quality_strip_warns_in_words_and_shapes(browser: Browser, svc: Running):
    """A `quality` event with a flattened output and lost gain, handed to the screen as app.js
    hands the stream's (the stream itself is cut, so the simulation's own does not replace it)."""
    context = browser.new_context(viewport={"width": 1366, "height": 900})
    page = context.new_page()
    page.route("**/v1/stream*", lambda route: route.abort())
    page.goto(f"{svc.url}/?t={TOKEN}")
    try:
        start(page)
        go(page, "Cadena")
        page.evaluate(
            "(q) => { window.__q = setInterval(() => document.dispatchEvent(new CustomEvent('aurasync:quality', { detail: q })), 200); }",
            QUALITY,
        )
        strip = page.locator("#chain-quality")
        expect(strip.locator('[data-q="in"] .q-value')).to_have_text("-23,0 LUFS", timeout=5000)
        expect(strip.locator('[data-q="out"] .q-value')).to_have_text("-27,4 LUFS")
        expect(strip.locator('[data-q="gain"] .q-value')).to_have_text("-4,4 LU")
        expect(strip.locator('[data-q="psr"] .q-value')).to_have_text("15,0 / 11,0 dB")
        expect(strip.locator('[data-q="tp"] .q-value')).to_have_text("-6,0 dBTP")
        expect(strip.locator('[data-q="limiter"] .q-value')).to_have_text("12 %")
        flat = strip.locator('[data-warning="flattening"]')
        expect(flat).to_contain_text("La cadena aplana")
        expect(flat).to_contain_text("Go 4 Blue")
        lost = strip.locator('[data-warning="gain_lost"]')
        expect(lost).to_contain_text("Ganancia perdida")
        # Text and a shape, not only a colour (WCAG 1.4.1): each warning its own geometry.
        shapes = strip.locator(".q-shape").evaluate_all("(ns) => ns.map((n) => getComputedStyle(n).clipPath)")
        assert len(set(shapes)) == 2, shapes
        assert all(s != "none" for s in shapes), shapes
    finally:
        context.close()


# -- the A/B with matched loudness ---------------------------------------------------------------


def _two_presets_apart(page: Page, svc: Running) -> None:
    start(page)
    page.locator("#source-kind").select_option("tone")
    svc.command("preset_save", name="fuerte")
    for name in NAMES:
        svc.command("set", speaker=name, changes={"gain_db": -10.0})
    svc.command("preset_save", name="suave")
    page.locator("#ab-a").select_option("fuerte")
    page.locator("#ab-b").select_option("suave")


def test_the_ab_shows_the_loudness_difference_and_warns(page: Page, svc: Running):
    _two_presets_apart(page, svc)
    page.locator("#ab-match").uncheck()
    page.locator("#ab-start").click()
    expect(page.locator("#ab-live")).to_be_visible()
    loud = page.locator("#ab-loudness")
    expect(loud).to_contain_text(re.compile(r"A -?\d+,\d LUFS"), timeout=10000)
    page.locator('[data-ab="b"]').click()
    expect(loud).to_contain_text("diferencia", timeout=10000)
    ab = svc.state()["ab"]
    assert ab["match_loudness"] is False
    assert ab["loudness_lu"]["diff"] < -5  # B is the quiet one
    expect(loud.locator(".ab-diff")).to_have_attribute("data-warn", "1")
    expect(loud).to_contain_text("el más fuerte suele parecer mejor")
    expect(loud.locator(".q-shape")).to_have_count(1)
    page.locator("#ab-stop").click()
    expect(page.locator("#ab-result")).to_contain_text("Sonoridad: A")


def test_the_ab_can_match_the_loudness(page: Page, svc: Running):
    _two_presets_apart(page, svc)
    expect(page.locator("#ab-match")).to_be_checked()  # the fair comparison is the default
    page.locator("#ab-start").click()
    expect(page.locator("#ab-live")).to_be_visible()
    assert svc.state()["ab"]["match_loudness"] is True
    expect(page.locator("#ab-loudness")).to_contain_text("igualada")


# -- Cortes: the radio ------------------------------------------------------------------------------


def test_the_radio_lanes_and_the_radio_log(page: Page, svc: Running):
    start(page)
    go(page, "Diagnóstico")
    button = page.locator("#radio-log")
    expect(button).to_have_text("Activar registro de radio")
    expect(page.locator("#radio-log-note")).to_contain_text("cambio de sistema")
    expect(page.locator("#radio-log-note")).to_contain_text("revert")
    expect(page.locator("#radio-lanes")).to_be_hidden()  # nothing to show yet
    button.click()
    expect(button).to_have_text("Desactivar registro de radio", timeout=5000)
    expect(page.locator("[data-radio-speaker]")).to_have_count(len(NAMES))
    assert svc.log_level.mode == "light"
    assert "revertir" in (svc.service.log_level.changes_file.read_text())
    lane = page.locator(f'[data-radio-speaker="{RED}"]')
    expect(lane.locator(".radio-pool")).to_contain_text(re.compile(r"bitpool \d+"), timeout=8000)
    # The simulated radio drops a packet every ~3 s per speaker: one shows as a diamond in a lane.
    expect(page.locator("#radio-lanes .cut-dot.cut-radio").first).to_be_attached(timeout=15000)
    expect(page.locator("#radio-note")).to_contain_text("SIMULADO")
    button.click()
    expect(button).to_have_text("Activar registro de radio", timeout=5000)
    assert svc.log_level.mode is None


# -- on a phone ------------------------------------------------------------------------------------


def test_the_chain_screen_on_a_phone(browser: Browser, svc: Running):
    page = phone(browser, svc)
    try:
        start(page)
        go(page, "Cadena")
        expect(page.locator("[data-stage]").first).to_be_visible()
        page.wait_for_timeout(500)
        assert page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth") <= 0
        # One block per speaker, not columns: each speaker's cells stack under its name.
        cols = page.locator('[data-stage="ambience"] [data-speaker-col]')
        expect(cols).to_have_count(len(NAMES))
        boxes = [cols.nth(i).bounding_box() for i in range(len(NAMES))]
        assert boxes[0]["y"] + boxes[0]["height"] <= boxes[1]["y"], boxes
        # Every control says what it is to a screen reader.
        unnamed = page.evaluate("""() => [...document.querySelectorAll('#chain-root input, #chain-root select')]
          .filter((n) => !n.getAttribute('aria-label') && !n.closest('label')).map((n) => n.outerHTML.slice(0, 80))""")
        assert unnamed == [], unnamed
        failures = target_failures(page.evaluate(TARGETS))
        assert failures == [], "\n".join(failures)
        # The help opens on touch (focus).
        knobs(page, "limiter")
        help_btn = page.locator('[data-stage="limiter"] [data-param="release_ms"] .help-btn')
        help_btn.click()
        expect(page.locator('[data-stage="limiter"] [data-param="release_ms"] .help-pop')).to_be_visible()
    finally:
        page.context.close()


def test_the_chain_screen_in_dark_mode_keeps_its_contrast(browser: Browser, svc: Running):
    context = browser.new_context(viewport={"width": 1366, "height": 900}, color_scheme="dark")
    page = context.new_page()
    page.goto(f"{svc.url}/?t={TOKEN}")
    try:
        go(page, "Cadena")
        card = page.locator('[data-stage="limiter"]')
        expect(card).to_be_visible()
        colors = card.evaluate(
            "(n) => [getComputedStyle(n.closest('.card')).backgroundColor, getComputedStyle(n).color]"
        )
        assert colors[0] != "rgb(255, 255, 255)", colors
    finally:
        context.close()


def test_the_screen_does_not_rerender_while_hidden(page: Page):
    """Metrics at 5 Hz and quality at 2 Hz are not drawn into a hidden tab."""
    start(page)
    page.locator("#source-kind").select_option("tone")
    go(page, "Cadena")
    expect(stage(page, "limiter").locator(".stage-metrics")).to_contain_text("En vivo", timeout=5000)
    go(page, "Escuchar")
    page.wait_for_timeout(500)  # an answer already on its way may still land
    count = page.evaluate("""() => new Promise((resolve) => {
      let n = 0;
      const mo = new MutationObserver((rs) => { n += rs.length; });
      mo.observe(document.getElementById('chain-root'), { subtree: true, childList: true, characterData: true, attributes: true });
      setTimeout(() => { mo.disconnect(); resolve(n); }, 1500);
    })""")
    assert count == 0, count
