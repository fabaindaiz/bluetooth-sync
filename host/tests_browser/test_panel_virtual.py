"""Virtual speakers in the panel (spec 2026-10-05-virtual-speakers-and-hot-join-design.md §6, §7).
SIMULATED."""

from __future__ import annotations

import copy

from playwright.sync_api import Browser, Page, expect

from .test_panel import TOKEN, Running, go, speaker_row, start


def _routed_page(browser: Browser, svc: Running, edit) -> tuple[Page, list[str], object]:
    """A page whose snapshot is the service's own, edited by `edit(state)`; the stream never connects."""
    state = copy.deepcopy(svc.state())
    edit(state)
    errors: list[str] = []
    context = browser.new_context(viewport={"width": 1366, "height": 900})
    page = context.new_page()
    # The aborted stream (this helper's own route) logs a network error: not the panel's.
    noise = ("ERR_FAILED", "/v1/stream")
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on(
        "console",
        lambda m: errors.append(m.text) if m.type == "error" and not any(n in m.text for n in noise) else None,
    )
    page.route("**/v1/stream*", lambda route: route.abort())
    page.route("**/v1/state", lambda route: route.fulfill(status=200, json={"v": 1, "ok": True, "result": state}))
    page.goto(f"{svc.url}/?t={TOKEN}")
    expect(page.locator("body[data-ready='1']")).to_be_attached()
    return page, errors, context


def _virtualize(state: dict) -> None:
    sp = state["speakers"][0]
    sp.update(
        sink=None,
        address=None,
        battery_pct=None,
        codec=None,
        rssi_dbm=None,
        modalias=None,
        connected=None,
        output="virtual",
        output_kind="virtual",
    )
    sp["name"] = "Virtual 1"


def test_new_snapshot_on_the_old_panel(browser: Browser, svc: Running):
    """Step 0: the published PWA (old reader) meets a virtual speaker: no console error, the row renders."""
    page, errors, context = _routed_page(browser, svc, _virtualize)
    try:
        go(page, "Parlantes")
        expect(speaker_row(page, "Virtual 1")).to_have_count(1)
        page.wait_for_timeout(1200)  # a few polls
        assert errors == []
    finally:
        context.close()


def test_add_virtual_speaker_shows_virtual_while_stopped_and_playing(page: Page, svc: Running):
    go(page, "Parlantes")
    page.get_by_role("button", name="Agregar parlante virtual").click()
    row = speaker_row(page, "Virtual 1")
    expect(row).to_have_count(1, timeout=5000)
    expect(row.locator(".status")).to_contain_text("virtual")
    start(page)
    expect(speaker_row(page, "Virtual 1").locator(".status")).to_contain_text("virtual", timeout=5000)
    # The real speakers still say "sonando".
    expect(speaker_row(page, "JBL Go 4 Red").locator(".status")).to_contain_text("sonando", timeout=5000)


def test_old_snapshot_without_output_still_renders(browser: Browser, svc: Running):
    def edit(state: dict) -> None:
        for sp, connected in zip(state["speakers"], (True, False, None), strict=False):
            sp.pop("output", None)
            sp.pop("output_kind", None)
            sp["connected"] = connected
            sp["playing"] = False

    page, errors, context = _routed_page(browser, svc, edit)
    try:
        go(page, "Parlantes")
        expect(speaker_row(page, "JBL Go 4 Red").locator(".status")).to_contain_text("conectado")
        expect(speaker_row(page, "JBL Go 4 Black").locator(".status")).to_contain_text("desconectado")
        expect(speaker_row(page, "JBL Go 4 Blue").locator(".status")).to_contain_text("sin observar")
        assert errors == []
    finally:
        context.close()


def test_a_virtual_speaker_is_never_reported_lost_while_playing(page: Page, svc: Running):
    go(page, "Parlantes")
    page.get_by_role("button", name="Agregar parlante virtual").click()
    expect(speaker_row(page, "Virtual 1")).to_have_count(1, timeout=5000)
    start(page)
    expect(speaker_row(page, "Virtual 1").locator(".status")).to_contain_text("virtual", timeout=5000)
    go(page, "Escuchar")
    page.wait_for_timeout(1500)
    assert "perdido" not in (page.locator("#now-alerts").text_content() or "")
    assert "perdido" not in (page.locator("#chip-alert").text_content() or "")


# -- joining and leaving a running session (phase 2, spec §5, §6). SIMULATED -----------


def _status(page: Page, name: str):
    return speaker_row(page, name).locator(".status")


def test_leave_and_join_a_speaker_while_playing(page: Page, svc: Running):
    go(page, "Parlantes")
    start(page)
    expect(_status(page, "JBL Go 4 Red")).to_contain_text("sonando", timeout=5000)
    speaker_row(page, "JBL Go 4 Red").get_by_role("button", name="Sacar").click()
    expect(_status(page, "JBL Go 4 Red")).to_contain_text("sin conectar", timeout=10000)
    expect(_status(page, "JBL Go 4 Black")).to_contain_text("sonando")
    speaker_row(page, "JBL Go 4 Red").get_by_role("button", name="Hacer entrar").click()
    expect(_status(page, "JBL Go 4 Red")).to_contain_text("sonando", timeout=10000)


def test_join_with_the_loop_off_warns_the_alignment_may_have_changed(page: Page, svc: Running):
    svc.command("set", changes={"recalibrate": False})
    go(page, "Parlantes")
    start(page)
    expect(_status(page, "JBL Go 4 Red")).to_contain_text("sonando", timeout=5000)
    speaker_row(page, "JBL Go 4 Red").get_by_role("button", name="Sacar").click()
    expect(_status(page, "JBL Go 4 Red")).to_contain_text("sin conectar", timeout=10000)
    expect(page.locator("#join-warning")).to_be_hidden()
    speaker_row(page, "JBL Go 4 Red").get_by_role("button", name="Hacer entrar").click()
    expect(_status(page, "JBL Go 4 Red")).to_contain_text("sonando", timeout=10000)
    expect(page.locator("#join-warning")).to_contain_text("recalibr", timeout=5000)


def test_a_lost_speaker_returns_by_itself_and_the_log_says_so(page: Page, svc: Running):
    go(page, "Parlantes")
    start(page)
    expect(_status(page, "JBL Go 4 Red")).to_contain_text("sonando", timeout=5000)
    sink = next(s["sink"] for s in svc.state()["speakers"] if s["name"] == "JBL Go 4 Red")
    address = sink.removeprefix("bluez_output.").split(".")[0].replace("_", ":")
    # The link drops: the observer sees it go and the playing stream dies (the simulator keeps them apart).
    svc.service.observer.disconnect(address)
    svc.service.session.outputs.player.soltar(sink)
    expect(_status(page, "JBL Go 4 Red")).to_contain_text("perdido", timeout=10000)
    svc.service.observer.connect(address)
    expect(_status(page, "JBL Go 4 Red")).to_contain_text("sonando", timeout=30000)
    go(page, "Diagnóstico")
    expect(page.locator("#logs")).to_contain_text("volvió JBL Go 4 Red", timeout=5000)


def test_a_speaker_that_gave_up_offers_retry(browser: Browser, svc: Running):
    def edit(state: dict) -> None:
        state["session"]["status"] = "playing"
        state["speakers"][0].update(output="lost", playing=False, rejoin="gave_up")

    page, errors, context = _routed_page(browser, svc, edit)
    try:
        go(page, "Parlantes")
        row = speaker_row(page, "JBL Go 4 Red")
        expect(row.get_by_role("button", name="Reintentar")).to_be_visible()
        expect(row.get_by_role("button", name="Hacer entrar")).to_have_count(0)
        assert errors == []
    finally:
        context.close()


def test_calibration_lists_who_is_left_out(browser: Browser, svc: Running):
    def edit(state: dict) -> None:
        state["session"]["status"] = "playing"
        state["speakers"][0].update(output="absent", playing=False, rejoin=None)

    page, errors, context = _routed_page(browser, svc, edit)
    try:
        go(page, "Calibrar")
        expect(page.locator("#cal-left-out")).to_contain_text("JBL Go 4 Red")
        assert errors == []
    finally:
        context.close()
