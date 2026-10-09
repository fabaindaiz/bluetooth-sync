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


# -- the headphones' own microphone, and a target that goes away (2026-10-09, HP-O16) -----------
# Entering Calibrar opened the WH-CH520's microphone: the headphones went to hands-free, their sink
# was rebuilt, and the monitor fell silent while the card said it reached them.

PHONES = "bluez_output.14_06_A7_6B_E3_F0.1"
PHONES_MIC = "bluez_input.14:06:A7:6B:E3:F0"


def _phones(svc: Running) -> None:
    """The headphones next to the simulated room: their sink and their microphone, as PipeWire
    lists them while they play A2DP; their microphone is the one chosen."""
    observer = svc.service.observer
    observer.view = {
        **observer.view,
        "sinks": [*observer.view["sinks"], {"node": PHONES, "description": "WH-CH520"}],
        "microphones": [{"node": PHONES_MIC, "description": "WH-CH520"}, *observer.view["microphones"]],
        "at": time.time(),
    }
    svc.command("microphone_set", node=PHONES_MIC)


def test_calibrar_never_opens_the_monitors_own_microphone_and_says_why(page: Page, svc: Running):
    _phones(svc)
    svc.command("start")
    svc.command("monitor_set", mode="mix", target=PHONES)
    _wait(lambda: svc.service.monitor.state == "on")
    asked: list[str] = []
    page.on(
        "request",
        lambda r: asked.append((r.post_data_json or {}).get("op")) if r.url.endswith("/v1/command") else None,
    )
    page.locator("[data-goto]:visible", has_text="Calibrar").first.click()
    expect(page.locator("#mic-note")).to_contain_text("salida del monitor", timeout=5000)
    expect(page.locator("#mic-note")).to_contain_text("WH-CH520")
    page.wait_for_timeout(1500)
    assert "mic_check" not in asked
    assert svc.service.session.pids()["microphone"] is None
    expect(page.locator("#mic-recheck")).to_be_hidden()
    option = page.locator(f'#cal-mic option[value="{PHONES_MIC}"]')
    expect(option).to_have_attribute("disabled", "")
    expect(option).to_have_attribute("title", re.compile("salida del monitor"))
    assert svc.service.options.microphone == PHONES_MIC  # the choice stays: the panel asks for another


def test_a_target_that_goes_away_says_the_monitor_waits_for_it(page: Page, svc: Running):
    svc.command("start")
    _open(page, svc)
    svc.command("monitor_set", mode="mix", target="simulated_headphones")
    state = page.locator("#monitor-state")
    expect(state).to_contain_text("Llega a Audífonos (simulados)", timeout=5000)
    observer = svc.service.observer
    sinks = observer.view["sinks"]
    observer.view = {
        **observer.view,
        "sinks": [s for s in sinks if s["node"] != "simulated_headphones"],
        "at": time.time(),
    }
    expect(state).to_contain_text("desapareció de PipeWire", timeout=5000)
    observer.view = {**observer.view, "sinks": sinks, "at": time.time()}
    expect(state).to_contain_text("Llega a", timeout=5000)
