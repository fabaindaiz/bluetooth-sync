"""The panel's "Sincronía sugerida" card: the estimator's suggestion, Aplicar, and every setting
explained with its recommendation, how it sounds and a SIMULADO figure (spec 2026-10-03 §5)."""

import dataclasses
import re
import time

from playwright.sync_api import Page, expect

from aurasync import sync_sim

from .test_panel import TOKEN, Running


def _feed(svc: Running) -> None:
    names = [p.nombre for p in svc.service.installation.parlantes]
    sc = sync_sim.standard("drift")
    rename = dict(zip(sc.speakers, names, strict=True))
    sc.duration_s = 600
    base = time.monotonic() - sc.duration_s
    for m in sync_sim.measurements(sc, 0):
        arrivals = {rename[s]: a for s, a in m.arrivals_ms.items()}
        svc.service.sync_estimator.submit(dataclasses.replace(m, t=m.t + base, arrivals_ms=arrivals, halves_ms={}))
    assert svc.service.sync_estimator.wait_idle()


def _open(page: Page, svc: Running) -> None:
    page.goto(f"{svc.url}/?t={TOKEN}")
    expect(page.locator("body[data-ready='1']")).to_be_attached()
    assert page.evaluate("(s) => window.aurasyncShow(s)", '[data-card="estimator"]')
    expect(page.locator('[data-card="estimator"]')).to_be_visible()


def test_without_measurements_apply_is_disabled(page: Page, svc: Running):
    _open(page, svc)
    expect(page.locator("#est-apply")).to_be_disabled()
    expect(page.locator("#est-reason")).to_contain_text(re.compile("Todavía no|no hay", re.IGNORECASE))


def test_the_suggestion_is_shown_and_applied(page: Page, svc: Running):
    _feed(svc)
    _open(page, svc)
    rows = page.locator("#est-rows tr")
    expect(rows).to_have_count(len(svc.service.installation.parlantes))
    expect(page.locator("#est-apply")).to_be_enabled()
    suggested = svc.service.sync_estimator.suggestion.delays_ms
    page.locator("#est-apply").click()

    def applied() -> bool:
        now = {p.nombre: p.retardo_ms for p in svc.service.installation.parlantes}
        return all(abs(now[n] - v) < 1e-3 for n, v in suggested.items())

    deadline = time.monotonic() + 5
    while not applied() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert applied(), suggested
    expect(page.locator("#est-applied")).to_be_visible()


def test_every_setting_says_its_recommendation_and_draws_a_simulated_figure(page: Page, svc: Running):
    _open(page, svc)
    page.locator("#est-explain-open").click()
    settings = page.locator("#est-settings [data-setting]")
    expect(settings.first).to_be_visible(timeout=30_000)
    assert settings.count() >= 7
    expect(page.locator('#est-settings [data-setting="window_min"] .est-recommended')).to_contain_text("Recomendado")
    fig = page.locator('#est-settings [data-setting="window_min"] svg')
    expect(fig).to_be_visible()
    expect(page.locator('#est-settings [data-setting="window_min"] .est-evidence')).to_have_text("SIMULADO")
    assert fig.locator("title").count() == 1  # accessible: the caption is the SVG's title
    expect(page.locator("#est-together-text")).to_contain_text("min")


def test_the_room_figure_draws_every_bar_inside_with_its_place(page: Page, svc: Running):
    """Review 2026-10-03: the bars of the second place were drawn off the canvas."""
    _open(page, svc)
    page.locator("#est-explain-open").click()
    fig = page.locator('#est-settings [data-setting="point_vote_weight"] svg')
    expect(fig).to_be_visible(timeout=30_000)
    bars = fig.locator("rect")
    assert bars.count() >= 4
    for i in range(bars.count()):
        x = float(bars.nth(i).get_attribute("x"))
        w = float(bars.nth(i).get_attribute("width"))
        assert x >= 0
        assert x + w <= 320, (i, x, w)
    labels = fig.locator("text").all_text_contents()
    assert any("ancla" in t for t in labels), labels
    assert any("puntual" in t for t in labels), labels


def test_changing_a_setting_updates_the_together_text(page: Page, svc: Running):
    _open(page, svc)
    page.locator("#est-explain-open").click()
    box = page.locator('#est-settings [data-setting="jump_repeats"] input')
    expect(box).to_be_visible(timeout=30_000)
    box.fill("1")
    box.dispatch_event("change")
    expect(page.locator("#est-together-text")).to_contain_text("1 medición", timeout=30_000)
    assert svc.service.sync_estimator.settings.jump_repeats == 1
