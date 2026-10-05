"""What the usability flows found and fixed (probes/18-usabilidad, 2026-10-04), held by a test each:
the person doing the flow on a phone, as probes/18-usabilidad/flujos.py does it. SIMULADO."""

import time

from playwright.sync_api import Browser, expect

from .test_panel import Running, go
from .test_panel_usability import open_panel


def _wait(cond, timeout=5.0):
    end = time.monotonic() + timeout
    while not cond() and time.monotonic() < end:
        time.sleep(0.05)
    assert cond()


def test_the_mode_is_at_hand_on_every_tab(browser: Browser, svc: Running):
    page = open_panel(browser, svc, 390, 844)
    try:
        for tab in ("Escuchar", "Calibrar", "Ajustes"):
            go(page, tab)
            expect(page.locator("#now-render")).to_be_visible()
        page.locator("#now-render").select_option("front")
        _wait(lambda: svc.service.settings.chain.algorithm("spatial") == "front")
        go(page, "Parlantes")
        expect(page.locator("#spatial-render")).to_have_value("front")  # the two stay in step
    finally:
        page.context.close()


def test_on_a_phone_each_speaker_s_name_is_whole(browser: Browser, svc: Running):
    page = open_panel(browser, svc, 390, 844)
    try:
        names = page.locator("[data-quick] .speaker-name")
        expect(names).to_have_count(3)
        texts = names.all_inner_texts()
        assert len({t.split(" · ")[0] for t in texts}) == 3, texts  # the part that tells them apart is there
        assert not any(names.evaluate_all("(ns) => ns.map((n) => n.scrollWidth > n.clientWidth)")), texts
    finally:
        page.context.close()


def test_on_a_phone_a_device_s_buttons_are_on_screen(browser: Browser, svc: Running):
    page = open_panel(browser, svc, 390, 844)
    try:
        go(page, "Parlantes")
        button = page.locator("#devices tr", has_text="JBL Flip 7").get_by_role("button", name="Emparejar y conectar")
        expect(button).to_be_visible()
        box = button.bounding_box()
        assert box is not None
        assert box["x"] + box["width"] <= 390, box
    finally:
        page.context.close()


def test_a_speaker_without_a_place_is_said_and_automatic_is_offered(browser: Browser, svc: Running):
    svc.command("set", changes={"layout": "quad"})
    # A pan and an ambience that are no role of the quad: that speaker has no place in this room.
    svc.command("set", speaker=svc.service.installation.parlantes[2].nombre, changes={"pan": 0.0, "ambience": 0.3})
    assert sum(1 for sp in svc.state()["speakers"] if not sp.get("role")) == 1
    page = open_panel(browser, svc, 390, 844)
    try:
        go(page, "Parlantes")
        hint = page.locator("#room-hint")
        expect(hint).to_be_visible()
        expect(hint).to_contain_text("1 parlante quedó sin lugar")
        hint.locator("[data-room-auto]").click()
        _wait(lambda: svc.state()["global"]["layout"] == "auto")
        expect(hint).to_be_hidden()
    finally:
        page.context.close()


def test_the_ab_says_why_it_cannot_start(browser: Browser, svc: Running):
    page = open_panel(browser, svc, 390, 844)
    try:
        expect(page.locator("#ab-start")).to_be_disabled()
        expect(page.locator("#ab-why")).to_contain_text("Hacen falta dos presets")
    finally:
        page.context.close()
