"""The panel in real browsers, over the real service in simulated mode: `hatch run browser:test`.

Playwright 1.60.0 with Chromium and Firefox (`hatch run browser:install` once). The
service runs with `SimulatedSession` and `SimulatedObserver`: the real motor, contract,
loop and calibration over a simulated room. Outside `scripts/check.sh` because it needs the
browsers installed.
"""

from __future__ import annotations

import json
import re
import threading
import urllib.request
from pathlib import Path

import pytest
from playwright.sync_api import Browser, Page, expect

from aurasync.bt_volume import BluetoothVolume
from aurasync.config import Instalacion, Parlante
from aurasync.logbuffer import LogBuffer
from aurasync.rest import make_server
from aurasync.service import Service
from aurasync.session import SessionOptions
from aurasync.simulated import (
    SimulatedObserver,
    SimulatedRadio,
    SimulatedSession,
    SimulatedVolumes,
    simulated_log_level,
)

TOKEN = "prueba-de-navegador-0123456789-abcdefghijk"
BROWSERS = ("chromium", "firefox")
NAMES = ("JBL Go 4 Red", "JBL Go 4 Black", "JBL Go 4 Blue")
SINKS = ("bluez_output.90_F2_60_75_4A_83.1", "bluez_output.90_F2_60_DA_66_6D.1", "bluez_output.90_F2_60_E3_07_39.1")
THREE = ((NAMES[0], -0.7, 0.15), (NAMES[1], 0.7, 0.15), (NAMES[2], 0.0, 0.55))
EIGHT = (
    *THREE,
    ("JBL Go 4 Green", -0.7, 0.55),
    ("JBL Go 4 White", 0.7, 0.55),
    ("JBL Go 4 Pink", 0.0, 0.1),
    ("JBL Charge 6", -1.0, 0.3),
    ("JBL Go 4 Gray", 1.0, 0.4),
)
"""Eight speakers (experimentos/16): the three of always, the four quad roles filled, and three
without a role. In front (role F…, or ambience under 0.35): Red, Black, Pink, Charge 6."""


class Running:
    def __init__(self, tmp: Path, speakers: tuple[tuple[str, float, float], ...] = THREE) -> None:
        inst = Instalacion(
            parlantes=[
                Parlante(
                    name,
                    SINKS[i] if i < len(SINKS) else f"bluez_output.90_F2_60_00_00_{i:02X}.1",
                    pan=pan,
                    ambiente=amb,
                )
                for i, (name, pan, amb) in enumerate(speakers)
            ]
        )
        inst.guardar(tmp / "instalacion.json")
        # As `aurasync service --simular` builds it (cli.py): the radio log and the speakers'
        # volume are simulated too, so the panel's radio lanes and AVRCP volume can be tried.
        self.log_level = simulated_log_level(tmp / "cambios-de-sistema.txt")
        self.radio = SimulatedRadio(
            lambda: [p.sink for p in inst.parlantes], lambda: self.log_level.mode is not None, drop_every_s=3.0, seed=7
        )
        self.service = Service(
            tmp / "instalacion.json",
            tmp / "presets.json",
            options=SessionOptions(microphone="simulado"),
            session_factory=SimulatedSession,
            observer=SimulatedObserver(inst),
            simulated=True,
            log=lambda _: None,
            logs=LogBuffer(),
            log_level=self.log_level,
            radio=self.radio,
            bt_volume=BluetoothVolume(SimulatedVolumes()),
        )
        self.engine = threading.Thread(target=self.service.run, daemon=True)
        self.engine.start()
        self.httpd = make_server(self.service, "127.0.0.1", 0, TOKEN)
        self.port = self.httpd.server_address[1]
        self.service.pairing = {"urls": [f"http://127.0.0.1:{self.port}"]}
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def command(self, op: str, **args) -> dict:
        """One raw message, as the panel sends it; the reply's `result` (asserts `ok`)."""
        body = json.dumps({"v": 1, "op": op, **args}).encode()
        request = urllib.request.Request(
            f"{self.url}/v1/command", data=body, headers={"Authorization": f"Bearer {TOKEN}"}, method="POST"
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            reply = json.loads(response.read())
        assert reply["ok"], reply
        return reply["result"]

    def stage(self, stage_id: str) -> dict:
        """One stage of the `chain` reply."""
        return next(s for s in self.command("chain")["stages"] if s["id"] == stage_id)

    def state(self) -> dict:
        request = urllib.request.Request(f"{self.url}/v1/state", headers={"Authorization": f"Bearer {TOKEN}"})
        with urllib.request.urlopen(request, timeout=5) as response:
            return json.loads(response.read())["result"]

    def stop(self) -> None:
        self.service.handle({"v": 1, "op": "shutdown"})
        self.engine.join(timeout=10)
        self.httpd.shutdown()
        self.httpd.server_close()
        self.service.close()


def start(page: Page) -> None:
    page.locator("#run").click()
    expect(page.locator("#run")).to_have_text("Detener", timeout=10000)


def go(page: Page, label: str) -> None:
    """Open a section the way a person would: its tab (top on a PC, bottom on a phone)."""
    page.locator("[data-goto]:visible", has_text=label).first.click()


def speaker_row(page: Page, name: str):
    return page.locator("#speakers tr", has=page.locator(".speaker-name", has_text=name))


# -- access ------------------------------------------------------------------------


def test_without_the_token_the_panel_says_how_to_get_in(browser: Browser, svc: Running):
    context = browser.new_context()
    page = context.new_page()
    response = page.goto(svc.url + "/")
    assert response.status == 401
    expect(page.locator("body")).to_contain_text("aurasync service")
    context.close()


def test_the_token_leaves_the_address_bar(page: Page, svc: Running):
    assert page.url == f"{svc.url}/"
    expect(page.locator("#engine-badge")).to_be_visible()


# -- session, speakers and levels ----------------------------------------------------


def test_start_shows_levels_and_stop_clears_them(page: Page, svc: Running):
    start(page)
    page.locator("#source-kind").select_option("tone")
    # Entrada L y R, los tres parlantes y el micrófono (el lazo va encendido por defecto).
    expect(page.locator(".meter")).to_have_count(6, timeout=5000)
    go(page, "Diagnóstico")
    expect(page.locator("#t-input")).to_have_text("recibe audio", timeout=5000)
    page.locator("#run").click()
    expect(page.locator("#run")).to_have_text("Iniciar", timeout=5000)
    assert svc.state()["session"]["status"] == "stopped"


def test_assign_a_role_and_it_is_not_overwritten_while_polling(page: Page, svc: Running):
    """The panel-demo bug: a control rewritten by the next snapshot while it is being used."""
    go(page, "Parlantes")
    row = speaker_row(page, "JBL Go 4 Blue")
    select = row.locator(".cell-role select")
    expect(select).to_have_value("")  # pan 0, ambience 0.55: custom in quad
    select.select_option("RR")
    page.wait_for_timeout(1500)  # three polls
    expect(select).to_have_value("RR")
    blue = next(s for s in svc.state()["speakers"] if s["name"] == "JBL Go 4 Blue")
    assert (blue["pan"], blue["ambience"], blue["role"]) == (0.7, 0.55, "RR")
    expect(page.locator(".slot", has_text="RR")).to_contain_text("JBL Go 4 Blue")


def test_a_taken_role_is_refused_and_the_control_goes_back(page: Page):
    go(page, "Parlantes")
    select = speaker_row(page, "JBL Go 4 Blue").locator(".cell-role select")
    select.select_option("FL")  # Red has it
    expect(page.locator("#toast")).to_contain_text("taken by JBL Go 4 Red")
    expect(select).to_have_value("", timeout=3000)


def test_moving_pan_and_gain_reaches_the_service(page: Page, svc: Running):
    go(page, "Parlantes")
    row = speaker_row(page, "JBL Go 4 Red")
    row.locator(".cell-pan input").fill("0.3")
    row.locator(".cell-volume input").fill("-6")
    page.wait_for_timeout(800)
    red = next(s for s in svc.state()["speakers"] if s["name"] == "JBL Go 4 Red")
    assert red["pan"] == pytest.approx(0.3)
    assert red["gain_db"] == pytest.approx(-6.0)
    expect(page.locator("#dirty")).to_be_visible()
    page.locator("#save").click()
    expect(page.locator("#toast")).to_contain_text("Instalación guardada")
    expect(page.locator("#dirty")).to_be_hidden()


def test_mute_and_tone(page: Page, svc: Running):
    start(page)
    card = page.locator('[data-quick="JBL Go 4 Black"]')
    card.locator(".q-mute").click()
    expect(card).to_contain_text("mudo")
    card.locator(".q-tone").click()
    go(page, "Diagnóstico")
    expect(page.locator("#logs")).to_contain_text('tone: ok {"speaker": "JBL Go 4 Black"', timeout=5000)
    go(page, "Escuchar")
    card.locator(".q-mute").click()
    expect(card).not_to_contain_text("mudo")


def test_volume_slider(page: Page, svc: Running):
    page.locator("#volume").fill("-30")
    page.wait_for_timeout(600)
    assert svc.state()["global"]["volume_db"] == -30
    expect(page.locator("#volume-out")).to_have_text("-30 dB")


def test_layout_changes_the_room(page: Page):
    go(page, "Parlantes")
    page.get_by_role("button", name=re.compile("Películas")).click()
    expect(page.locator(".slot")).to_have_count(4)
    expect(page.locator(".slot", has_text="FC")).to_be_visible()


# -- source, devices, services -------------------------------------------------------


def test_source_app_lists_what_is_playing(page: Page, svc: Running):
    start(page)
    page.locator("#source-kind").select_option("app")
    page.locator("#source-app").select_option("Spotify")
    page.wait_for_timeout(1200)
    assert svc.state()["source"]["kind"] == "app"
    assert svc.state()["source"]["name"] == "Spotify"


def test_connect_a_nearby_speaker_and_add_it(page: Page, svc: Running):
    go(page, "Parlantes")
    flip = page.locator("#devices tr", has_text="JBL Flip 7")
    flip.get_by_role("button", name="Emparejar y conectar").click()
    flip.get_by_role("button", name="Agregar a la instalación").click()
    expect(page.locator("#speakers tr")).to_have_count(4)
    assert "JBL Flip 7" in [s["name"] for s in svc.state()["speakers"]]


def test_services_start_and_stop_the_recalibration_loop(page: Page, svc: Running):
    start(page)
    go(page, "Diagnóstico")
    row = page.locator("#services tr", has_text="Lazo de recalibración")
    row.get_by_role("button", name="Iniciar").click()
    expect(row.locator(".cell-status")).to_contain_text("corriendo")
    assert svc.state()["recalibration"]["active"]
    go(page, "Parlantes")
    expect(speaker_row(page, "JBL Go 4 Red").locator(".cell-delay input")).to_be_disabled()
    go(page, "Diagnóstico")
    row.get_by_role("button", name="Detener").click()
    expect(row.locator(".cell-status")).to_contain_text("detenido")
    expect(page.locator("#services tr", has_text="JBL Go 4 Red (pw-play)")).to_contain_text("corriendo")


def test_a_service_name_filters_the_logs(page: Page):
    start(page)
    go(page, "Diagnóstico")
    page.locator("#services tr", has_text="Sesión de audio").locator(".link-btn").click()
    expect(page.locator("#log-service")).to_have_value("session")
    expect(page.locator("#logs .log").first).to_be_visible()
    for service in page.locator("#logs .log-service").all_text_contents():
        assert service == "session"


# -- configuration ------------------------------------------------------------------


def test_decorrelation_off_warns(page: Page, svc: Running):
    # Ajustes → Sonido moved into Cadena (spec 2026-10-02 §7.1): the decorrelation's algorithm.
    go(page, "Cadena")
    page.locator('[data-stage="decorrelate"] [data-algorithm="off"]').click()
    expect(page.locator("#warnings")).to_contain_text("decorrelación encendida")
    assert svc.state()["global"]["decorrelate"] is False


def test_a_restart_setting_is_marked_pending(page: Page, svc: Running):
    start(page)
    go(page, "Ajustes")
    page.locator('[data-card=config] [data-global="block_size"]').select_option("2048")
    expect(page.locator('[data-restart="block_size"]')).to_have_text("pendiente: reiniciá la sesión")
    go(page, "Diagnóstico")
    row = page.locator("#services tr", has_text="Sesión de audio")
    row.get_by_role("button", name="Reiniciar").click()
    go(page, "Ajustes")
    expect(page.locator('[data-restart="block_size"]')).to_have_text("al reiniciar", timeout=10000)


def test_the_masked_probe_switches_from_ajustes(page: Page, svc: Running):
    """The probe (dsp/probe.py) is off by default; Ajustes → Sincronía switches it live and the
    playing session carries it (state.recalibration.probe)."""
    start(page)
    go(page, "Ajustes")
    box = page.locator('[data-card=config] [data-global="probe"]')
    expect(box).not_to_be_checked()
    box.check()
    for _ in range(50):
        if svc.state()["global"]["probe"]:
            break
        page.wait_for_timeout(100)
    state = svc.state()
    assert state["global"]["probe"] is True
    assert state["recalibration"]["probe"]["active"] is True


# -- calibration ----------------------------------------------------------------------


def test_calibrate_apply_and_saving_a_simulated_one_is_refused(page: Page, svc: Running):
    start(page)
    go(page, "Calibrar")
    page.locator("#cal-seconds").fill("5")
    page.locator("#cal-run").click()
    expect(page.locator("#cal-results tr")).to_have_count(3, timeout=20000)
    expect(page.locator("#cal-note")).to_contain_text("SIMULADO")
    expect(page.locator("#cal-save")).to_be_disabled()
    page.locator("#cal-apply").click()
    page.wait_for_timeout(800)
    delays = {s["name"]: s["delay_ms"] for s in svc.state()["speakers"]}
    # The simulated room: 3, 7.5 and 12 ms. The calibration aligns everyone to the latest.
    assert delays == pytest.approx({"JBL Go 4 Red": 9.0, "JBL Go 4 Black": 4.5, "JBL Go 4 Blue": 0.0}, abs=0.1)


# -- presets and blind A/B --------------------------------------------------------------


def test_presets_and_a_blind_ab_round(page: Page, svc: Running):
    start(page)
    page.locator("#preset-name").fill("cerrado")
    page.locator("#preset-save").click()
    page.locator('[data-quick="JBL Go 4 Blue"] .q-ambience').fill("0.9")
    page.wait_for_timeout(500)
    page.locator("#preset-name").fill("amplio")
    page.locator("#preset-save").click()
    expect(page.locator("#presets li")).to_have_count(2)
    page.locator("#ab-a").select_option("cerrado")
    page.locator("#ab-b").select_option("amplio")
    page.locator("#ab-start").click()
    expect(page.locator("#ab-live")).to_be_visible()
    page.locator('[data-ab="x"]').click()
    expect(page.locator('[data-ab="x"]')).to_have_attribute("aria-pressed", "true")
    # The state must not say which preset X is.
    assert svc.state()["preset"] is None
    page.locator('[data-answer="a"]').click()
    expect(page.locator("#ab-score")).to_contain_text("de 1 aciertos")
    page.locator("#ab-stop").click()
    expect(page.locator("#ab-result")).to_contain_text("No alcanza")


# -- the phone ----------------------------------------------------------------------------


def test_phone_width_has_no_horizontal_scroll(browser: Browser, svc: Running):
    context = browser.new_context(
        viewport={"width": 390, "height": 844}, is_mobile=browser.browser_type.name != "firefox"
    )
    page = context.new_page()
    page.goto(f"{svc.url}/?t={TOKEN}")
    expect(page.locator("#connection")).to_have_text(re.compile("En vivo|Consultando"))
    start(page)
    page.wait_for_timeout(500)
    overflow = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
    assert overflow <= 0
    context.close()


def test_the_pairing_qr_is_an_svg(page: Page, svc: Running):
    svc.service.pairing = {
        "urls": [f"http://127.0.0.1:{svc.port}", f"http://127.0.0.1:{svc.port}".replace("127.0.0.1", "localhost")]
    }
    # The QR is the PWA's pairing link (rest.pairing_link), which needs HTTPS: say it is on.
    svc.service.access.tls = {"enabled": True, "port": 8443, "root_sha256": "AB:CD"}
    expect(page.locator("#pair-open")).to_be_visible()
    page.locator("#pair-open").click()
    expect(page.locator("#pair-qr")).to_have_js_property("complete", True)
    assert page.locator("#pair-qr").evaluate("img => img.naturalWidth") > 0


# -- the organisations and the compact card -------------------------------------------------


def test_the_compact_card_moves_ambience_and_mutes(page: Page, svc: Running):
    card = page.locator('[data-quick="JBL Go 4 Red"]')
    card.locator(".q-ambience").fill("0.4")
    page.wait_for_timeout(600)
    assert next(s for s in svc.state()["speakers"] if s["name"] == "JBL Go 4 Red")["ambience"] == pytest.approx(0.4)
    card.locator(".q-mute").click()
    expect(card).to_contain_text("mudo")


@pytest.mark.parametrize("layout", ["pagina", "pestanas", "inicio", "lateral"])
def test_every_organisation_reaches_every_card(browser: Browser, svc: Running, layout: str):
    context = browser.new_context(viewport={"width": 1366, "height": 900})
    page = context.new_page()
    page.goto(f"{svc.url}/?t={TOKEN}")
    page.goto(f"{svc.url}/?layout={layout}")
    expect(page.locator("body[data-ready='1']")).to_be_attached()
    for card in (
        "presets",
        "quick",
        "ab",
        "chain",
        "room",
        "speakers",
        "devices",
        "calibration",
        "estimator",
        "response",
        "health",
        "levels",
        "services",
        "logs",
        "config",
    ):
        assert page.evaluate("(s) => window.aurasyncShow(s)", f'[data-card="{card}"]'), card
        expect(page.locator(f'[data-card="{card}"]')).to_be_visible()
    context.close()


def test_the_hub_goes_to_a_detail_and_back(browser: Browser, svc: Running):
    context = browser.new_context(viewport={"width": 390, "height": 844})
    page = context.new_page()
    page.goto(f"{svc.url}/?t={TOKEN}")
    page.goto(f"{svc.url}/?layout=inicio")
    expect(page.locator("body[data-ready='1']")).to_be_attached()
    page.locator('[data-card="hub"] [data-goto="calibrar"]').click()
    expect(page.locator("#cal-run")).to_be_visible()
    page.locator('[data-view="calibrar"] [data-back]').click()
    expect(page.locator('[data-card="presets"]')).to_be_visible()
    context.close()


def test_the_input_card_describes_the_music_and_the_microphone_can_be_chosen(page: Page, svc: Running):
    start(page)
    expect(page.locator("#input-facts")).to_contain_text("Correlación L/R", timeout=10000)
    go(page, "Calibrar")
    mic = page.locator("#cal-mic")
    expect(mic.locator("option")).to_have_count(3)
    mic.select_option("simulado-2")
    expect(mic).to_have_value("simulado-2", timeout=5000)
    page.wait_for_timeout(1200)
    expect(mic).to_have_value("simulado-2")


def test_the_panel_goes_live_over_the_stream_and_meters_move(page: Page, svc: Running):
    expect(page.locator("#connection")).to_have_text("En vivo", timeout=10000)
    start(page)
    page.locator("#source-kind").select_option("tone")
    value = page.locator(".meter", has_text="Entrada L").locator(".meter-value")
    expect(value).not_to_have_text("—", timeout=5000)
    seen = {value.text_content() for _ in range(10) if page.wait_for_timeout(100) is None}
    assert len(seen) > 1  # it moves between state snapshots, so it comes from the stream
    expect(page.locator("#input-bars .input-bar").first).to_be_attached(timeout=5000)


def test_without_the_stream_the_panel_polls_and_says_so(browser_page_without_stream: Page):
    page = browser_page_without_stream
    expect(page.locator("#connection")).to_have_text("Consultando", timeout=15000)


def test_clicking_around_never_changes_the_room_layout(page: Page, svc: Running):
    """The <body> carries the panel's own layout; it used to be caught as a room-layout button."""
    for label in ("Parlantes", "Calibrar", "Escuchar"):
        go(page, label)
        page.locator("body").click(position={"x": 5, "y": 300})
    page.wait_for_timeout(700)
    assert svc.service.snapshot["global"]["layout"] == "quad"


def test_cuts_are_listed_with_their_reason(page: Page, svc: Running):
    start(page)
    svc.service.session.cuts.context["bt_discovering"] = True
    svc.service.session.cuts.add("xrun", "JBL Go 4 Red", "2 en el nodo Bluetooth")
    go(page, "Diagnóstico")
    expect(page.locator("#cuts-list")).to_contain_text("PipeWire registró un corte", timeout=5000)
    expect(page.locator("#cuts-list")).to_contain_text("Bluetooth buscaba dispositivos")
    expect(page.locator("#cuts-timeline .cut-dot")).to_have_count(1)
    expect(page.locator("#cuts-likely")).to_contain_text("Bluetooth buscaba")


def test_diagnostics_has_health_left_services_right_and_logs_below(page: Page, svc: Running):
    go(page, "Diagnóstico")
    health = page.locator("[data-card=health]").bounding_box()
    services = page.locator("[data-card=services]").bounding_box()
    logs = page.locator("[data-card=logs]").bounding_box()
    assert health["x"] < services["x"]
    assert logs["y"] > max(health["y"], services["y"])


def test_the_main_screen_has_the_everyday_controls(page: Page, svc: Running):
    go(page, "Escuchar")
    now = page.locator("[data-card=now]")
    expect(now).to_be_visible()
    for label in ("Ambiente", "Separar parlantes", "Ecualización", "Mantener sincronía"):
        expect(now.locator(".toggle", has_text=label)).to_be_visible()
    start(page)
    now.locator(".toggle", has_text="Separar parlantes").locator("input").uncheck()
    # The same knob as the chain's decorrelation: Cadena follows it.
    go(page, "Cadena")
    expect(page.locator('[data-stage="decorrelate"] [data-algorithm="off"] input')).to_be_checked(timeout=5000)
    go(page, "Escuchar")
    expect(page.locator("#now-facts")).to_contain_text("sonando")


def test_every_setting_explains_itself(page: Page, svc: Running):
    go(page, "Ajustes")
    settings = page.locator("[data-card=config] .setting")
    for i in range(settings.count()):
        expect(settings.nth(i).locator(".setting-desc")).not_to_be_empty()
    first = settings.nth(0)
    pop = first.locator(".help-pop")
    expect(pop).to_be_hidden()
    first.locator(".help-btn").hover()
    expect(pop).to_be_visible()


def test_devices_are_grouped_and_a_stranger_can_be_forgotten(page: Page, svc: Running):
    go(page, "Parlantes")
    devices = page.locator("#devices")
    expect(devices).to_contain_text("Conectados")
    row = devices.locator("tr", has_text="JBL Flip 7")
    expect(row).to_be_visible()
    row.get_by_role("button", name="Emparejar y conectar").click()
    # Forgetting cannot be undone by the service: the panel shows it done and sends it when the
    # "Deshacer" notice ends (no confirm(), research/11 §4.3).
    page.evaluate("window.aurasync.undo.setDuration(1500)")
    row.get_by_role("button", name="Olvidar").click()
    expect(row).to_have_count(0)
    expect(page.locator("#undo")).to_contain_text("olvidado")
    flip = lambda: next(d for d in svc.state()["devices"] if d["name"] == "JBL Flip 7")  # noqa: E731
    assert flip()["paired"]
    expect(page.locator("#undo")).to_be_hidden(timeout=5000)
    page.wait_for_timeout(500)
    assert not flip()["paired"]
