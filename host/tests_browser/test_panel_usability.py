"""The panel's usability pending items (research/11 §4, report D §8 items 5, 10, 11; experimentos/16
§7), each one seen to fail with a mutation of its change.

- **Undo instead of confirm()**: loading a preset, removing a speaker, applying a calibration and
  clearing the EQ get a "Deshacer" notice; no test opens a dialog (conftest.no_dialogs).
- **The microphone's level before calibrating**, with a target window, as Dirac Live does.
- **Coherence in the frequency response**: a third with low coherence is drawn faint.
- **Eight speakers**: folded cards, group actions, nothing overflowing, on a phone and on a PC.
"""

from __future__ import annotations

import contextlib
import re
import time
from collections.abc import Iterator

import numpy as np
import pytest
from playwright.sync_api import Browser, Page, expect

from tests_browser.test_panel import EIGHT, NAMES, TOKEN, Running, go, speaker_row, start
from tests_browser.test_panel_quality import calibrate, play_tone

ARTISTIC_SPEAKER = ("pan", "ambience", "gain_db", "delay_ms", "muted", "kind")
ARTISTIC_GLOBAL = ("rear_delay_ms", "extract_ambience", "decorrelate", "eq_active")


def wait_until(predicate, timeout: float = 5.0):
    """The first truthy value of `predicate()`: a delay moves at the bottom of a fade, not at once."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        value = predicate()
        if value:
            return value
        time.sleep(0.1)
    return predicate()


def artistic(state: dict) -> dict:
    return {
        "speakers": {sp["name"]: {k: sp[k] for k in ARTISTIC_SPEAKER} for sp in state["speakers"]},
        "global": {k: state["global"][k] for k in ARTISTIC_GLOBAL},
    }


def undo(page: Page) -> None:
    notice = page.locator("#undo")
    expect(notice).to_be_visible()
    notice.get_by_role("button", name="Deshacer").click()
    expect(notice).to_contain_text("Deshecho", timeout=10000)


# -- 1. undo instead of confirm() -----------------------------------------------------------------


def test_loading_a_preset_can_be_undone(page: Page, svc: Running):
    page.locator("#preset-name").fill("amplio")
    svc.command("set", speaker=NAMES[2], changes={"ambience": 0.9, "pan": 0.3})
    svc.command("set", changes={"decorrelate": False})
    page.locator("#preset-save").click()
    expect(page.locator("#presets li")).to_have_count(1)
    # Back to something else, which the preset will overwrite.
    svc.command("set", speaker=NAMES[2], changes={"ambience": 0.4, "pan": -0.2})
    svc.command("set", speaker=NAMES[0], changes={"gain_db": -4.5})
    svc.command("set", changes={"decorrelate": True})
    before = artistic(svc.state())
    page.locator("#presets li", has_text="amplio").get_by_role("button", name="Cargar").click()
    expect(page.locator("#undo")).to_contain_text("Preset «amplio» cargado")
    loaded = artistic(svc.state())
    assert loaded != before
    assert loaded["speakers"][NAMES[2]]["ambience"] == pytest.approx(0.9)
    undo(page)
    assert artistic(svc.state()) == before


def test_removing_a_speaker_can_be_undone_with_what_it_had(page: Page, svc: Running):
    svc.command("set", speaker=NAMES[1], changes={"gain_db": -3.0, "muted": True, "ambience": 0.25})
    before = artistic(svc.state())
    go(page, "Parlantes")
    speaker_row(page, NAMES[1]).get_by_role("button", name="Quitar").click()
    expect(page.locator("#speakers tr")).to_have_count(2)
    assert NAMES[1] not in [s["name"] for s in svc.state()["speakers"]]
    undo(page)
    expect(page.locator("#speakers tr")).to_have_count(3)
    assert artistic(svc.state()) == before


def test_applying_a_calibration_can_be_undone(page: Page, svc: Running):
    start(page)
    calibrate(page)
    before = artistic(svc.state())
    page.locator("#cal-apply").click()
    expect(page.locator("#undo")).to_contain_text("Alineación aplicada")
    red = lambda: next(s for s in svc.state()["speakers"] if s["name"] == NAMES[0])["delay_ms"]  # noqa: E731
    assert wait_until(lambda: abs(red() - 9.0) < 0.1), red()
    undo(page)
    assert wait_until(lambda: artistic(svc.state()) == before), (artistic(svc.state()), before)
    # The loop owns the delays while it runs: undo switched it off and on again around them.
    assert svc.state()["recalibration"]["active"]


def test_clearing_the_eq_is_heard_at_once_and_done_when_the_notice_ends(page: Page, svc: Running):
    start(page)
    calibrate(page)
    page.locator("#eq-apply").click()
    expect(page.locator("#eq-reset")).to_be_enabled(timeout=5000)
    curves = {s["name"]: s["eq_db"] for s in svc.state()["speakers"]}
    assert any(curves.values())
    # Undo: the EQ comes back on, with its curves.
    page.locator("#eq-reset").click()
    expect(page.locator("#undo")).to_contain_text("Ecualización quitada")
    assert svc.state()["global"]["eq_active"] is False
    undo(page)
    state = svc.state()
    assert state["global"]["eq_active"] is True
    assert {s["name"]: s["eq_db"] for s in state["speakers"]} == curves
    # Let it run out: the curves go, and the EQ is on again (flat), as eq_reset leaves it.
    page.evaluate("window.aurasync.undo.setDuration(1000)")
    page.locator("#eq-reset").click()
    expect(page.locator("#undo")).to_be_hidden(timeout=5000)
    page.wait_for_timeout(800)
    state = svc.state()
    assert all(s["eq_db"] is None for s in state["speakers"])
    assert state["global"]["eq_active"] is True


def test_deleting_a_preset_waits_for_the_notice_and_undo_keeps_it(page: Page, svc: Running):
    svc.command("preset_save", name="cine")
    expect(page.locator("#presets li", has_text="cine")).to_have_count(1)
    page.locator("#presets li", has_text="cine").get_by_role("button", name="Borrar").click()
    expect(page.locator("#presets li", has_text="cine")).to_have_count(0)
    assert "cine" in svc.state()["presets"]
    undo(page)
    expect(page.locator("#presets li", has_text="cine")).to_have_count(1)
    page.wait_for_timeout(500)
    assert "cine" in svc.state()["presets"]
    page.evaluate("window.aurasync.undo.setDuration(800)")
    page.locator("#presets li", has_text="cine").get_by_role("button", name="Borrar").click()
    expect(page.locator("#undo")).to_be_hidden(timeout=5000)
    page.wait_for_timeout(500)
    assert "cine" not in svc.state()["presets"]


# -- 2. the microphone's level before calibrating ---------------------------------------------------


@contextlib.contextmanager
def quiet_microphone(svc: Running) -> Iterator[None]:
    """The simulated microphone 60 dB lower: what a microphone far away or with its gain down
    hears. Over the running session (the meters and the stream's level both see it)."""
    session = svc.service.session
    record, update = session.telemetry.record_microphone, session.meters.update
    session.telemetry.record_microphone = lambda x: record(np.asarray(x) * 1e-3)
    session.meters.update = lambda name, x, *a: update(name, np.asarray(x) * 1e-3 if name == "mic" else x, *a)
    try:
        yield
    finally:
        session.telemetry.record_microphone, session.meters.update = record, update


def test_a_microphone_in_the_window_calibrates_at_once(page: Page, svc: Running):
    play_tone(page)
    go(page, "Calibrar")
    check = page.locator("#mic-check")
    expect(check).to_have_attribute("data-state", "ok", timeout=5000)
    expect(page.locator("#mic-state-text")).to_have_text("en la ventana")
    expect(page.locator("#mic-value")).to_have_text(re.compile(r"^-\d+ dBFS$"))
    page.locator("#cal-seconds").fill("5")
    page.locator("#cal-run").click()
    expect(page.locator("#cal-gate")).to_be_hidden()
    expect(page.locator("#cal-note")).to_contain_text("Emitiendo", timeout=5000)


def test_a_quiet_microphone_asks_before_calibrating(page: Page, svc: Running):
    play_tone(page)
    go(page, "Calibrar")
    with quiet_microphone(svc):
        expect(page.locator("#mic-check")).to_have_attribute("data-state", "low", timeout=5000)
        expect(page.locator("#mic-state-text")).to_have_text("demasiado bajo")
        page.locator("#cal-seconds").fill("5")
        page.locator("#cal-run").click()
        gate = page.locator("#cal-gate")
        expect(gate).to_be_visible()
        expect(gate).to_contain_text("demasiado bajo")
        page.wait_for_timeout(600)
        assert svc.state()["calibration"] is None  # it did not start
        gate.get_by_role("button", name="Cancelar").click()
        expect(gate).to_be_hidden()
        page.locator("#cal-run").click()
        gate.get_by_role("button", name="Calibrar igual").click()
        expect(page.locator("#cal-note")).to_contain_text("Emitiendo", timeout=5000)
        # The shortcut in Escuchar asks the same.
        expect(page.locator("#cal-note")).to_contain_text("Medido", timeout=30000)
        go(page, "Escuchar")
        page.locator("#now-calibrate").click()
        expect(page.locator("#now-gate")).to_contain_text("demasiado bajo")


def test_in_silence_the_check_says_it_cannot_tell_and_does_not_stop_the_calibration(page: Page, svc: Running):
    start(page)
    svc.command("source", kind="system")  # nothing plays in the simulated room: the microphone hears its floor
    go(page, "Calibrar")
    expect(page.locator("#mic-check")).to_have_attribute("data-state", "quiet", timeout=5000)
    expect(page.locator("#mic-note")).to_contain_text("Poné música")
    page.locator("#cal-run").click()
    expect(page.locator("#cal-gate")).to_be_hidden()
    expect(page.locator("#cal-note")).to_contain_text("Emitiendo", timeout=5000)


# -- 3. coherence in the frequency response -----------------------------------------------------------


def test_thirds_with_low_coherence_are_drawn_faint(page: Page, svc: Running):
    start(page)
    calibrate(page)
    chart = page.locator("#resp-chart")
    # The calibration's error per third (`response_error_db`, session.py, dsp/response.py), set
    # here so the test knows which thirds are unreliable: all within 1 dB but two of Red's, one
    # over 1 dB and one that could not be estimated. The γ² is set low everywhere on purpose: with
    # N speakers it falls to ~1/N even when the curve is well measured, so it must not decide.
    results = svc.service.session.calibration.results
    n = len(results[0]["response_db"])
    for r in results:
        r["response_error_db"] = [0.3] * n
        r["coherence"] = [0.2] * n
    results[0]["response_error_db"][17] = 1.5
    results[0]["response_error_db"][18] = None
    # One stretch per third, so a third can be faint alone.
    drawn = sum(v is not None for v in results[0]["response_db"])
    expect(chart.locator(f'polyline[data-speaker="{NAMES[0]}"][data-band]')).to_have_count(drawn, timeout=5000)
    expect(chart.locator(".low-coherence")).to_have_count(2, timeout=5000)
    expect(chart.locator(f'.low-coherence[data-speaker="{NAMES[0]}"][data-band="17"]')).to_have_count(1)
    expect(chart.locator(f'.low-coherence[data-speaker="{NAMES[0]}"][data-band="18"]')).to_have_count(1)
    expect(chart.locator(f'[data-speaker="{NAMES[1]}"].low-coherence')).to_have_count(0)
    assert float(chart.locator(".low-coherence").first.evaluate("n => getComputedStyle(n).opacity")) < 0.5
    expect(page.locator("#resp-legend")).to_contain_text("coherencia baja")


def test_without_the_error_estimate_the_coherence_decides(page: Page, svc: Running):
    """A calibration that only carries γ² (no `response_error_db`) still gets the faint thirds."""
    start(page)
    calibrate(page)
    chart = page.locator("#resp-chart")
    results = svc.service.session.calibration.results
    n = len(results[0]["response_db"])
    for r in results:
        r.pop("response_error_db", None)
        r["coherence"] = [0.95] * n
    results[0]["coherence"][17] = 0.2
    expect(chart.locator(f'.low-coherence[data-speaker="{NAMES[0]}"][data-band="17"]')).to_have_count(1, timeout=5000)
    expect(chart.locator(".low-coherence")).to_have_count(1)


def test_the_legend_hides_and_shows_a_speakers_curve(page: Page, svc: Running):
    start(page)
    calibrate(page)
    chart = page.locator("#resp-chart")
    black = chart.locator(f'polyline.measured[data-speaker="{NAMES[1]}"]')
    expect(black.first).to_be_attached()
    page.locator("#resp-legend button", has_text=NAMES[1]).click()
    expect(black).to_have_count(0)
    expect(chart.locator(f'polyline.measured[data-speaker="{NAMES[0]}"]').first).to_be_attached()
    expect(page.locator("#resp-legend button", has_text=NAMES[1])).to_have_attribute("aria-pressed", "false")
    page.locator("#resp-legend button", has_text=NAMES[1]).click()
    expect(black.first).to_be_attached()


# -- 4. eight speakers ----------------------------------------------------------------------------------


@pytest.fixture
def svc8(tmp_path) -> Iterator[Running]:
    running = Running(tmp_path, EIGHT)
    # With 7 or more, the fixed decorrelation bank does not build (experimentos/16 §2): off, so the
    # session starts whatever the motor does about it.
    running.command("set", changes={"decorrelate": False})
    yield running
    running.stop()


def open_panel(browser: Browser, svc: Running, width: int, height: int) -> Page:
    phone = width < 640
    mobile = {"is_mobile": True, "has_touch": True} if phone and browser.browser_type.name == "chromium" else {}
    context = browser.new_context(viewport={"width": width, "height": height}, **mobile)
    page = context.new_page()
    page.goto(f"{svc.url}/?t={TOKEN}")
    expect(page.locator("body[data-ready='1']")).to_be_attached()
    return page


# What sticks out past the screen's right edge, leaving aside what an ancestor clips or scrolls
# (the meters' moving layers, a wide table in its own scroller).
OVERFLOW = """() => {
  const doc = document.documentElement;
  const clipped = (n) => {
    for (let a = n.parentElement; a && a !== doc; a = a.parentElement) {
      if (getComputedStyle(a).overflowX !== 'visible') return true;
    }
    return false;
  };
  const wide = [...document.querySelectorAll('[data-view]:not([hidden]) *, #bottom-nav *, #topbar *')]
    .filter((n) => n.checkVisibility() && n.getBoundingClientRect().right > doc.clientWidth + 1
      && !n.closest('.help-pop') && !clipped(n))
    .map((n) => (n.id || n.className || n.tagName).toString().slice(0, 60));
  return { page: doc.scrollWidth - doc.clientWidth, wide: [...new Set(wide)].slice(0, 8) };
}"""

VIEW_HEIGHT = "() => document.querySelector('[data-view]:not([hidden])').getBoundingClientRect().height"


@pytest.mark.parametrize("size", [(390, 844), (1366, 900)], ids=["phone", "pc"])
def test_eight_speakers_fit_every_screen(browser: Browser, svc8: Running, size):
    page = open_panel(browser, svc8, *size)
    try:
        start(page)
        page.locator("#source-kind").select_option("tone")
        # Entrada L y R, ocho parlantes y el micrófono.
        expect(page.locator(".meter")).to_have_count(11, timeout=10000)
        svc8.command("radio_log", active=True)
        for label in ("Escuchar", "Cadena", "Parlantes", "Calibrar", "Diagnóstico", "Ajustes"):
            go(page, label)
            page.wait_for_timeout(400)
            found = page.evaluate(OVERFLOW)
            assert found == {"page": 0, "wide": []}, (label, found)
        go(page, "Diagnóstico")
        expect(page.locator("#radio-lanes .radio-lane")).to_have_count(8, timeout=5000)
        go(page, "Parlantes")
        # The room draws all eight: four roles and the three without one, where pan and ambience put
        # them (before, they were a line of text under the plan).
        expect(page.locator("#room .slot.filled")).to_have_count(4)
        expect(page.locator("#room .slot.custom")).to_have_count(4)
    finally:
        page.context.close()


def test_eight_speakers_fold_on_a_phone_and_open_one_by_one(browser: Browser, svc8: Running):
    page = open_panel(browser, svc8, 390, 844)
    try:
        cards = page.locator("[data-quick]")
        expect(cards).to_have_count(8)
        # Folded: name, state, Tono and Silenciar in one line; the sliders hidden.
        for i in range(8):
            assert cards.nth(i).bounding_box()["height"] <= 72, cards.nth(i).bounding_box()
        green = page.locator('[data-quick="JBL Go 4 Green"]')
        expect(green.locator(".q-ambience")).to_be_hidden()
        green.locator(".fold-btn").click()
        expect(green.locator(".q-ambience")).to_be_visible()
        green.locator(".q-ambience").fill("0.8")
        page.wait_for_timeout(500)
        green_now = next(s for s in svc8.state()["speakers"] if s["name"] == "JBL Go 4 Green")
        assert green_now["ambience"] == pytest.approx(0.8)
        expect(green.locator(".q-ambience")).to_be_visible()  # stays open across renders
        # Parlantes en detalle: one card per speaker, folded; the pan after opening it.
        go(page, "Parlantes")
        row = speaker_row(page, "JBL Go 4 Pink")
        expect(row.locator(".cell-pan input")).to_be_hidden()
        row.locator(".fold-btn").click()
        expect(row.locator(".cell-pan input")).to_be_visible()
        # Cadena: each speaker's block folded too, with a tap on its name to open it.
        go(page, "Cadena")
        col = page.locator('[data-stage="ambience"] [data-speaker-col="JBL Go 4 White"]').first
        expect(col.locator(".spk-cell").first).to_be_hidden()
        col.locator(".spk-toggle").click()
        expect(col.locator(".spk-cell").first).to_be_visible()
    finally:
        page.context.close()


def test_the_chain_keeps_one_open_column_per_speaker_on_a_pc(browser: Browser, svc8: Running):
    page = open_panel(browser, svc8, 1366, 900)
    try:
        go(page, "Cadena")
        cells = page.locator('[data-stage="ambience"] [data-speaker-col] .spk-cell')
        expect(cells.first).to_be_visible()
        widths = [cells.nth(i).bounding_box()["width"] for i in range(cells.count())]
        assert min(widths) >= 90, widths  # eight columns still leave a usable slider
    finally:
        page.context.close()


def test_group_actions_mute_the_rear_and_bring_it_back(browser: Browser, svc8: Running):
    page = open_panel(browser, svc8, 1366, 900)
    group = page.locator("#quick-group")
    expect(group).to_be_visible()
    group.locator('[data-group="rear"]').click()
    expect(group).to_contain_text("4 de 8")
    group.locator('[data-group-action="mute"]').click()
    expect(page.locator('[data-quick="JBL Go 4 Gray"]')).to_contain_text("mudo", timeout=5000)
    muted = {s["name"] for s in svc8.state()["speakers"] if s["muted"]}
    assert muted == {"JBL Go 4 Blue", "JBL Go 4 Green", "JBL Go 4 White", "JBL Go 4 Gray"}
    group.locator('[data-group="all"]').click()
    group.locator('[data-group-action="unmute"]').click()
    expect(page.locator('[data-quick="JBL Go 4 Gray"]')).not_to_contain_text("mudo", timeout=5000)
    assert not any(s["muted"] for s in svc8.state()["speakers"])
    page.context.close()


def test_three_speakers_look_as_always(page: Page):
    expect(page.locator("#quick-group")).to_be_hidden()
    expect(page.locator("[data-quick] .fold-btn:visible")).to_have_count(0)
    expect(page.locator('[data-quick="JBL Go 4 Red"] .q-ambience')).to_be_visible()
