"""Fixtures shared by the browser tests: Playwright, each browser, the simulated service, a page."""

from __future__ import annotations

import re
from collections.abc import Iterator

import pytest
from playwright.sync_api import Browser, BrowserContext, Page, Playwright, expect, sync_playwright

from tests_browser.test_panel import BROWSERS, TOKEN, Running

DIALOGS: list[str] = []
"""Every alert/confirm/prompt any page opened. The panel uses none (research/11 §4.3: undo
instead of confirm()); each test checks that this stays empty."""


@pytest.fixture(scope="session")
def playwright() -> Iterator[Playwright]:
    # Every context of every browser (the PWA's own Chromium too) reports its dialogs here, and
    # dismisses them so a stray one cannot hang a test.
    original = Browser.new_context

    def new_context(self: Browser, *args, **kwargs) -> BrowserContext:
        context = original(self, *args, **kwargs)
        context.on("dialog", lambda d: (DIALOGS.append(f"{d.type}: {d.message}"), d.dismiss()))
        return context

    Browser.new_context = new_context
    try:
        with sync_playwright() as p:
            yield p
    finally:
        Browser.new_context = original


@pytest.fixture(autouse=True)
def no_dialogs() -> Iterator[None]:
    DIALOGS.clear()
    yield
    assert DIALOGS == [], f"the panel opened a dialog: {DIALOGS}"


@pytest.fixture(scope="session", params=BROWSERS)
def browser(request, playwright: Playwright) -> Iterator[Browser]:
    browser = getattr(playwright, request.param).launch()
    yield browser
    browser.close()


@pytest.fixture
def svc(tmp_path) -> Iterator[Running]:
    running = Running(tmp_path)
    yield running
    running.stop()


@pytest.fixture
def page(browser: Browser, svc: Running) -> Iterator[Page]:
    context = browser.new_context(viewport={"width": 1366, "height": 900})
    page = context.new_page()
    page.goto(f"{svc.url}/?t={TOKEN}")
    expect(page.locator("#connection")).to_have_text(re.compile("En vivo|Consultando"))
    expect(page.locator("body[data-ready='1']")).to_be_attached()
    yield page
    context.close()


@pytest.fixture
def browser_page_without_stream(browser: Browser, svc: Running) -> Iterator[Page]:
    """A browser where `/v1/stream` never connects, as behind a proxy that buffers it."""
    context = browser.new_context(viewport={"width": 1366, "height": 900})
    page = context.new_page()
    page.route("**/v1/stream*", lambda route: route.abort())
    page.goto(f"{svc.url}/?t={TOKEN}")
    yield page
    context.close()
