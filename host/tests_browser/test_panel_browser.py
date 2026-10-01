"""El panel en navegadores reales (docs/research/09 §9): `hatch run browser:test`.

Usa Playwright 1.60.0 con el caché de navegadores de Playwright (Chromium y WebKit;
el Firefox de Playwright no arranca en el Mac de desarrollo). El panel corre con el
motor simulado en un hilo propio. Fuera de scripts/check.sh porque necesita esos
navegadores instalados.
"""

import asyncio
import threading
import time
from collections.abc import Iterator

import pytest
from aiohttp import web
from playwright.sync_api import Browser, Page, Playwright, expect, sync_playwright

from aurasync.engine.simulated import CALIBRATION_SECONDS, SimulatedEngine
from aurasync.logbuffer import LogBuffer
from aurasync.panel.server import create_app

TOKEN = "prueba-de-navegador-0123456789"
BROWSERS = ("chromium", "webkit")
QUAD = ("FL", "FR", "RL", "RR")


class PanelThread(threading.Thread):
    """El panel con el motor simulado, en su propio bucle de asyncio."""

    def __init__(self) -> None:
        super().__init__(daemon=True)
        self.ready = threading.Event()
        self.port = 0
        self._loop: asyncio.AbstractEventLoop | None = None
        self._shutdown: asyncio.Event | None = None

    def run(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._loop.run_until_complete(self._serve())

    async def _serve(self) -> None:
        self._shutdown = asyncio.Event()
        logs = LogBuffer()
        logs.attach()
        app = create_app(
            SimulatedEngine(now=time.monotonic),
            token=TOKEN,
            allowed_hosts=frozenset({"127.0.0.1"}),
            logs=logs,
        )
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        self.port = site._server.sockets[0].getsockname()[1]  # noqa: SLF001
        self.ready.set()
        await self._shutdown.wait()
        await runner.cleanup()
        logs.detach()

    def stop(self) -> None:
        if self._loop and self._shutdown:
            self._loop.call_soon_threadsafe(self._shutdown.set)
        self.join(timeout=5)


@pytest.fixture(scope="session")
def playwright() -> Iterator[Playwright]:
    with sync_playwright() as p:
        yield p


@pytest.fixture(scope="session", params=BROWSERS)
def browser(request, playwright: Playwright) -> Iterator[Browser]:
    browser = getattr(playwright, request.param).launch()
    yield browser
    browser.close()


@pytest.fixture
def panel_url() -> Iterator[str]:
    server = PanelThread()
    server.start()
    assert server.ready.wait(10)
    yield f"http://127.0.0.1:{server.port}/?t={TOKEN}"
    server.stop()


@pytest.fixture
def page(browser: Browser, panel_url: str) -> Iterator[Page]:
    context = browser.new_context(viewport={"width": 1366, "height": 1400})
    page = context.new_page()
    page.goto(panel_url)
    expect(page.locator("#connection")).to_have_text("Conectado")
    expect(page.locator("#speakers tr")).to_have_count(4)
    yield page
    context.close()


def speaker_select(page: Page, index: int):
    return page.locator("#speakers tr").nth(index).locator("select")


def service_row(page: Page, label: str):
    return page.locator("#services tr", has=page.get_by_role("button", name=label, exact=True))


def assign_quad(page: Page) -> None:
    for index, channel in enumerate(QUAD):
        speaker_select(page, index).select_option(channel)
        expect(page.locator(".slot.filled")).to_have_count(index + 1)


def start(page: Page) -> None:
    page.locator("#run").click()
    expect(page.locator("#run")).to_have_text("Detener", timeout=5000)


# -- canales y controles ----------------------------------------------------------


def test_assigning_a_channel_reaches_the_engine_and_stays(page: Page):
    speaker_select(page, 0).select_option("FL")
    expect(page.locator(".slot.filled")).to_have_count(1)
    expect(page.locator(".slot.filled .slot-name")).to_have_text("Go 4 (simulado 1)")
    page.wait_for_timeout(600)
    expect(speaker_select(page, 0)).to_have_value("FL")


def test_a_control_in_use_without_focus_is_not_overwritten(page: Page):
    # Firefox en macOS: un clic en un <select> no le da el foco (09 §7.4).
    page.evaluate(
        """(() => { const s = document.querySelector('#speakers select');
        s.dispatchEvent(new PointerEvent('pointerdown', {bubbles: true}));
        document.activeElement.blur(); s.value = 'FR'; })()"""
    )
    page.wait_for_timeout(700)
    expect(speaker_select(page, 0)).to_have_value("FR")


def test_a_refused_assignment_goes_back_to_the_engine_value(page: Page):
    speaker_select(page, 0).select_option("FL")
    expect(page.locator(".slot.filled")).to_have_count(1)
    speaker_select(page, 1).select_option("FL")  # ocupado: el motor lo rechaza
    expect(page.locator("#toast")).to_contain_text("ocupado")
    expect(speaker_select(page, 1)).to_have_value("")


def test_trim_and_volume_controls_reach_the_engine(page: Page):
    row = page.locator("#speakers tr").nth(2)
    row.locator('input[type="number"]').first.fill("12.5")
    row.locator('input[type="number"]').first.press("Enter")
    page.wait_for_timeout(400)
    assert page.evaluate("latest.speakers[2].delay_ms") == 12.5


# -- transmisión y servicios -------------------------------------------------------


def test_start_brings_every_pipeline_service_up(page: Page):
    start(page)
    for label in ("Controlador", "Captura", "DSP y reloj", "Emisor"):
        expect(service_row(page, label).locator(".status")).to_contain_text("corriendo")
    expect(page.locator("#t-big")).to_contain_text("activo")


def test_stopping_the_controller_stops_the_emitter(page: Page):
    start(page)
    service_row(page, "Controlador").get_by_role("button", name="Detener").click()
    expect(service_row(page, "Emisor").locator(".status")).to_contain_text("detenido")
    expect(service_row(page, "Captura").locator(".status")).to_contain_text("corriendo")
    expect(page.locator("#run")).to_have_text("Transmitir")


def test_a_simulated_failure_shows_its_error_and_can_be_recovered(page: Page):
    assign_quad(page)
    start(page)
    page.wait_for_timeout(1700)
    emitter = service_row(page, "Emisor")
    emitter.get_by_role("button", name="Simular falla").click()
    expect(emitter.locator(".status")).to_contain_text("falló")
    expect(emitter.locator(".service-error")).to_contain_text("Command Disallowed")
    expect(page.locator(".slot.lost")).to_have_count(4)
    emitter.get_by_role("button", name="Iniciar").click()
    expect(emitter.locator(".status")).to_contain_text("corriendo", timeout=5000)


def test_system_services_offer_no_actions(page: Page):
    rows = page.locator("#services tr", has_text="solo se observa")
    expect(rows.first).to_be_visible()
    expect(rows.first.locator(".cell-actions button")).to_have_count(0)


# -- logs ----------------------------------------------------------------------


def test_logs_show_transitions_and_filter_by_service(page: Page):
    start(page)
    expect(page.locator("#logs .log", has_text="→ running").first).to_be_visible(timeout=5000)
    page.get_by_role("button", name="Emisor", exact=True).click()
    expect(page.locator("#log-service")).to_have_value("emitter")
    services = page.locator("#logs .log-service").all_text_contents()
    assert services
    assert set(services) == {"emitter"}


def test_refused_orders_appear_as_warnings(page: Page):
    page.locator("#log-level").select_option("warning")
    speaker_select(page, 0).select_option("FL")
    speaker_select(page, 1).select_option("FL")
    expect(page.locator("#logs .log-warning", has_text="ocupado")).to_be_visible()


# -- configuración y calibración ---------------------------------------------------------


def test_config_changes_reach_the_engine(page: Page):
    page.locator('[data-config="upmix"]').select_option("surround")
    expect(service_row(page, "DSP y reloj").locator(".service-detail")).to_contain_text("upmix surround")
    page.locator('[data-config="presentation_delay_us"]').select_option("80000")
    # 21,3 de captura + 12,5 de LC3 + 65 de transporte + 80 de presentation delay
    expect(page.locator("#latency-total")).to_contain_text("179")


def test_calibration_runs_and_applies_to_the_speakers(page: Page):
    assign_quad(page)
    start(page)
    page.locator("#cal-run").click()
    expect(page.locator("#cal-apply")).to_be_enabled(timeout=(CALIBRATION_SECONDS + 3) * 1000)
    expect(page.locator("#cal-save")).to_be_disabled()
    page.locator("#cal-apply").click()
    expect(page.locator("#speakers tr").nth(3).locator('input[type="number"]').first).to_have_value("2.4")


# -- teléfono ------------------------------------------------------------------------


def test_phone_width_has_no_horizontal_scroll(browser: Browser, panel_url: str):
    context = browser.new_context(viewport={"width": 390, "height": 900}, is_mobile=True, has_touch=True)
    page = context.new_page()
    page.goto(panel_url)
    expect(page.locator("#speakers tr")).to_have_count(4)
    overflow = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
    context.close()
    assert overflow <= 1
