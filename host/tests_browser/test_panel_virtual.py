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
