"""The panel's "Monitor (audífonos)" card (spec 2026-10-04-headphone-monitor-design.md §2):
the destination list, the mode, and what PipeWire really did. SIMULATED."""

import re
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


def test_the_card_says_the_modes_are_matched_in_loudness(page: Page, svc: Running):
    svc.command("start")
    _open(page, svc)
    page.locator("#monitor-target").select_option("simulated_headphones")
    page.locator("#monitor-mode").select_option("mix")
    _wait(lambda: svc.service.monitor.settings.mode == "mix")
    state = page.locator("#monitor-state")
    expect(state).to_have_attribute("data-state", "on", timeout=5000)
    expect(state).to_contain_text(re.compile(r"Igualando…|Nivel igualado: -?\d+,\d dB"), timeout=5000)


# -- the volume by the device (fix round 1, review 2026-10-05) -----------------------------------------


def test_the_volume_selector_sends_only_who_controls_it(page: Page, svc: Running):
    """The slider is in % for the device and in dB for software: the selector must not send its
    value in the old unit under the new field (it was refused as out of range either way)."""
    _open(page, svc)
    page.locator("#monitor-volume-control").select_option("software")
    _wait(lambda: svc.service.monitor.settings.volume_control == "software")
    settings = svc.service.monitor.settings
    assert (settings.gain_db, settings.device_volume_pct) == (-12.0, None)
    expect(page.locator("#monitor-gain-value")).to_have_text("-12 dB")
    page.locator("#monitor-volume-control").select_option("device")
    _wait(lambda: svc.service.monitor.settings.volume_control == "device")
    settings = svc.service.monitor.settings
    assert (settings.gain_db, settings.device_volume_pct) == (-12.0, None)


def test_changing_the_output_never_carries_the_level_read_back(page: Page, svc: Running):
    """The review's repro: the headphones at 80 % (their buttons), the ceiling 30, the output
    changed to one at 20 % -> the panel sent the 80 it read back and the new output went to 80."""
    backend = svc.service.monitor.backend
    backend.volumes.update({"simulated_headphones": 20.0, "simulated_pc_output": 20.0})
    _open(page, svc)
    page.locator("#monitor-target").select_option("simulated_headphones")
    page.locator("#monitor-mode").select_option("stereo")
    _wait(lambda: svc.service.monitor.settings.mode == "stereo")
    svc.service.monitor.wait()
    backend.volumes["simulated_headphones"] = 80.0  # the headphone buttons
    expect(page.locator("#monitor-gain")).to_have_value("80", timeout=8000)
    page.locator("#monitor-target").select_option("simulated_pc_output")
    _wait(lambda: svc.service.monitor.settings.target == "simulated_pc_output")
    page.wait_for_timeout(300)
    svc.service.monitor.wait()
    assert backend.volumes["simulated_pc_output"] == 20.0
    assert svc.service.monitor.settings.device_volume_pct is None


def test_moving_the_level_sets_the_headphones_volume(page: Page, svc: Running):
    backend = svc.service.monitor.backend
    backend.volumes["simulated_headphones"] = 20.0
    _open(page, svc)
    page.locator("#monitor-target").select_option("simulated_headphones")
    page.locator("#monitor-mode").select_option("stereo")
    _wait(lambda: svc.service.monitor.settings.mode == "stereo")
    page.locator("#monitor-gain").evaluate(
        "(n) => { n.value = '25'; n.dispatchEvent(new Event('input')); n.dispatchEvent(new Event('change')); }"
    )
    _wait(lambda: backend.volumes["simulated_headphones"] == 25.0)
    assert svc.service.monitor.settings.device_volume_pct == 25.0
