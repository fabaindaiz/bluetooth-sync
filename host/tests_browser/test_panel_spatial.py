"""The panel's "Espacial" card: principal/ambient per speaker, the spatial mode and its knobs
explained, the room from above, and the `auto` layout (spec 2026-10-04 §5)."""

import time

from playwright.sync_api import Page, expect

from .test_panel import TOKEN, Running


def _open(page: Page, svc: Running) -> None:
    page.goto(f"{svc.url}/?t={TOKEN}")
    expect(page.locator("body[data-ready='1']")).to_be_attached()
    assert page.evaluate("(s) => window.aurasyncShow(s)", '[data-card="spatial"]')
    expect(page.locator('[data-card="spatial"]')).to_be_visible()


def _wait(cond, timeout=5.0):
    end = time.monotonic() + timeout
    while not cond() and time.monotonic() < end:
        time.sleep(0.05)
    assert cond()


def test_each_speaker_can_be_principal_or_ambient(page: Page, svc: Running):
    _open(page, svc)
    names = [p.nombre for p in svc.service.installation.parlantes]
    rows = page.locator("#spatial-speakers [data-speaker]")
    expect(rows).to_have_count(len(names))
    page.locator(f'#spatial-speakers [data-speaker="{names[-1]}"] [data-role-kind="ambient"]').click()
    _wait(lambda: svc.service.installation.por_nombre(names[-1]).role_kind == "ambient")
    page.locator("#spatial-all-principal").click()
    _wait(lambda: all(p.role_kind == "principal" for p in svc.service.installation.parlantes))


def test_the_spatial_mode_is_chosen_here(page: Page, svc: Running):
    _open(page, svc)
    page.locator("#spatial-render").select_option("spatial")
    _wait(lambda: svc.service.settings.chain.algorithm("spatial") == "spatial")


def test_front_intact_is_chosen_here(page: Page, svc: Running):
    _open(page, svc)
    page.locator("#spatial-render").select_option("front")
    _wait(lambda: svc.service.settings.chain.algorithm("spatial") == "front")


def test_every_knob_is_explained_and_the_room_is_drawn(page: Page, svc: Running):
    _open(page, svc)
    page.locator("#spatial-explain summary").click()
    opened = page.evaluate("() => document.getElementById('spatial-explain').open")
    assert opened, "the summary click did not open the details"
    knobs = page.locator("#spatial-docs [data-setting]")
    try:
        expect(knobs.first).to_be_visible(timeout=30_000)
    except AssertionError:
        state = page.evaluate(
            """() => ({open: document.getElementById('spatial-explain').open,
            card: getComputedStyle(document.querySelector('[data-card=spatial]')).display,
            hidden: document.querySelector('[data-card=spatial]').hidden,
            rect: document.querySelector('[data-card=spatial]').getBoundingClientRect().height,
            details: document.getElementById('spatial-explain').getBoundingClientRect().height,
            docs: document.getElementById('spatial-docs').getBoundingClientRect().height,
            first: (() => { const f = document.querySelector('#spatial-docs [data-setting]');
              return f ? [f.getBoundingClientRect().height, getComputedStyle(f).display, f.children.length] : null; })(),
            view: document.body.dataset.view || null})"""
        )
        raise AssertionError(state) from None
    assert knobs.count() == 7
    expect(page.locator('#spatial-docs [data-setting="character"] .est-recommended')).to_contain_text("Recomendado")
    expect(page.locator('#spatial-docs [data-setting="character"] .est-evidence')).to_have_text("SIMULADO")
    room = page.locator("#spatial-room svg")
    expect(room).to_be_visible()
    assert room.locator("circle[data-speaker]").count() == len(svc.service.installation.parlantes)


def test_auto_is_a_layout(page: Page, svc: Running):
    page.goto(f"{svc.url}/?t={TOKEN}")
    expect(page.locator("body[data-ready='1']")).to_be_attached()
    assert page.evaluate("(s) => window.aurasyncShow(s)", '[data-card="room"]')
    expect(page.locator('[data-room-layout="auto"]')).to_contain_text("Automático")
