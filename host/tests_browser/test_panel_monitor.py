"""The panel's "Monitor (audífonos)" card (spec 2026-10-04-headphone-monitor-design.md §2):
the destination list, the mode, and what PipeWire really did. SIMULATED."""

import time

from playwright.sync_api import Page, expect

from .test_panel import TOKEN, Running


def _open(page: Page, svc: Running) -> None:
    page.goto(f"{svc.url}/?t={TOKEN}")
    expect(page.locator("body[data-ready='1']")).to_be_attached()
    assert page.evaluate("(s) => window.aurasyncShow(s)", '[data-card="monitor"]')
    expect(page.locator('[data-card="monitor"]')).to_be_visible()


def _wait(cond, timeout=5.0):
    end = time.monotonic() + timeout
    while not cond() and time.monotonic() < end:
        time.sleep(0.05)
    assert cond()


def test_the_monitor_lists_outputs_but_never_a_speaker(page: Page, svc: Running):
    _open(page, svc)
    options = page.locator("#monitor-target option")
    expect(options.filter(has_text="Audífonos (simulados)")).to_have_count(1)
    sinks = {p.sink for p in svc.service.installation.parlantes}
    values = set(page.locator("#monitor-target option").evaluate_all("(os) => os.map((o) => o.value)"))
    assert not values & sinks


def test_choosing_binaural_while_playing_says_where_it_arrives(page: Page, svc: Running):
    svc.command("start")
    _open(page, svc)
    page.locator("#monitor-target").select_option("simulated_headphones")
    page.locator("#monitor-mode").select_option("binaural")
    _wait(lambda: svc.service.monitor.settings.mode == "binaural")
    state = page.locator("#monitor-state")
    expect(state).to_have_attribute("data-state", "on", timeout=5000)
    expect(state).to_contain_text("Llega a Audífonos (simulados)")
    page.locator("#monitor-mode").select_option("off")
    expect(state).to_have_attribute("data-state", "off", timeout=5000)


def test_a_mode_without_an_output_asks_for_one(page: Page, svc: Running):
    _open(page, svc)
    page.locator("#monitor-mode").select_option("stereo")
    expect(page.locator("#monitor-state")).to_contain_text("Elegí una salida")
    assert svc.service.monitor.settings.mode == "off"
