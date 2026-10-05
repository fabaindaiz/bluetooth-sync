"""The PWA (d-7c8794-37f9bc) in Chromium: built, served from another origin, against the service by HTTPS.

`npm run build:pwa` (host/web) first: these tests serve `host/web/dist-pwa/` as GitHub Pages
would, under `/bluetooth-sync/`, from a static server on `http://localhost:5173` — an origin the
service lets in by default (`panel_origins`) — and talk to the simulated service over HTTPS with
the root it generated.

**How Chromium trusts the root.** Not with `ignore_https_errors`: Chromium is launched with
`--ignore-certificate-errors-spki-list=<SHA-256 of the root's public key>`, which accepts only a
chain that contains that key. For the root to be in the chain, the test's HTTPS server sends it
after the server certificate (the service sends only its own certificate; with the root installed
on a phone, that is enough). Installing it in the system's store instead would change this
machine, which a test must not do. The test of the untrusted certificate uses a Chromium without
the flag.

Chromium only: the PWA tests need service workers, Cache Storage and the SPKI flag.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import shutil
import ssl
import threading
from collections.abc import Iterator
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from playwright.sync_api import Browser, BrowserContext, Page, Playwright, expect

from aurasync.rest import make_server
from aurasync.tls import Certificates
from tests_browser.test_panel import TOKEN, Running

DIST = Path(__file__).resolve().parents[1] / "web" / "dist-pwa"
PAGES = "/bluetooth-sync/"
ORIGIN = "http://localhost:5173"
APP = f"{ORIGIN}{PAGES}"


# -- the static site, as GitHub Pages serves it -------------------------------------------------


class Site:
    """`dist-pwa/` copied to a directory a test may change (a new version), served under PAGES."""

    def __init__(self) -> None:
        self.root = DIST
        self.served: list[str] = []
        """Every path the network was asked for (what a service worker answers never gets here)."""
        site = self

        class Handler(SimpleHTTPRequestHandler):
            extensions_map = {  # noqa: RUF012 - the base class declares it so
                **SimpleHTTPRequestHandler.extensions_map,
                ".js": "text/javascript",
                ".webmanifest": "application/manifest+json",
                ".json": "application/json",
            }

            def do_GET(self) -> None:
                site.served.append(self.path.split("?", 1)[0])
                super().do_GET()

            def translate_path(self, path: str) -> str:
                path = path.split("?", 1)[0].split("#", 1)[0]
                if not path.startswith(PAGES):
                    return str(site.root / "__nothing__")
                rest = path[len(PAGES) :] or "index.html"
                return str(site.root / rest)

            def end_headers(self) -> None:
                # What GitHub Pages sends: a 10-minute HTTP cache the worker has to see past.
                self.send_header("Cache-Control", "max-age=600")
                super().end_headers()

            def log_message(self, format: str, *args) -> None:  # noqa: A002
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 5173), Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def use(self, root: Path) -> None:
        self.root = root

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture(scope="session")
def site() -> Iterator[Site]:
    if not (DIST / "sw.js").exists():
        pytest.fail("no PWA build: run `npm ci && npm run build:pwa` in host/web")
    s = Site()
    yield s
    s.close()


@pytest.fixture
def site_dir(site: Site, tmp_path: Path) -> Path:
    root = tmp_path / "site"
    shutil.copytree(DIST, root)
    site.use(root)
    return root


# -- the service, by HTTPS ------------------------------------------------------------------------


@pytest.fixture(scope="session")
def certificates(tmp_path_factory) -> Certificates:
    certs = Certificates(tmp_path_factory.mktemp("tls") / "tls")
    certs.ensure_server(["localhost"], ["127.0.0.1"], hostname="")
    return certs


def spki_sha256(pem: bytes) -> str:
    key = x509.load_pem_x509_certificate(pem).public_key()
    der = key.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    return base64.b64encode(hashlib.sha256(der).digest()).decode()


class TlsService(Running):
    """The simulated service, with HTTPS next to its HTTP, as `serve` opens them."""

    def __init__(self, tmp: Path, certs: Certificates) -> None:
        super().__init__(tmp)
        chain = tmp / "chain.pem"
        chain.write_bytes(certs.server_pem.read_bytes() + certs.root_pem.read_bytes())
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(chain, certs.server_key)
        self.access = self.service.access
        self.https = make_server(
            self.service, "127.0.0.1", 0, TOKEN, access=self.access, ssl_context=context, certificates=certs
        )
        self.https_port = self.https.server_address[1]
        info = certs.info()
        self.root_sha256 = info.root_sha256
        self.access.tls = {
            "enabled": True,
            "port": self.https_port,
            "root_sha256": info.root_sha256,
            "cert_sha256": info.cert_sha256,
        }
        threading.Thread(target=self.https.serve_forever, daemon=True).start()

    @property
    def address(self) -> str:
        return f"localhost:{self.https_port}"

    def stop(self) -> None:
        self.https.shutdown()
        self.https.server_close()
        super().stop()


@pytest.fixture
def tsvc(tmp_path: Path, certificates: Certificates) -> Iterator[TlsService]:
    running = TlsService(tmp_path, certificates)
    yield running
    running.stop()


# -- Chromium ---------------------------------------------------------------------------------------


@pytest.fixture(scope="session")
def chromium(playwright: Playwright, certificates: Certificates) -> Iterator[Browser]:
    """Chromium that trusts the service's root (see the module's docstring)."""
    spki = spki_sha256(certificates.root_pem.read_bytes())
    browser = playwright.chromium.launch(args=[f"--ignore-certificate-errors-spki-list={spki}"])
    yield browser
    browser.close()


@pytest.fixture
def context(chromium: Browser, site_dir: Path) -> Iterator[BrowserContext]:
    ctx = chromium.new_context(viewport={"width": 1280, "height": 900})
    yield ctx
    ctx.close()


def open_app(context: BrowserContext, fragment: str = "") -> Page:
    page = context.new_page()
    page.goto(f"{APP}{fragment}")
    return page


def find(page: Page, address: str) -> None:
    page.get_by_label("Código de conexión o dirección").fill(address)
    page.get_by_role("button", name="Conectar").click()


def pair_in_window(page: Page, tsvc: TlsService) -> None:
    """Add the device by its address and pair while its first-client window is open."""
    expect(page.locator(".connect-screen")).to_be_visible()
    find(page, tsvc.address)
    expect(page.locator("[data-found-name]")).not_to_be_empty()
    page.get_by_role("button", name="Pedir acceso").click()
    expect(page.locator(".connect-screen")).to_be_hidden(timeout=10000)
    expect(page.locator("#connection")).to_have_text("En vivo", timeout=10000)


def wait_cached(page: Page) -> None:
    """The worker holds every file of the build."""
    expect(page.locator("#pwa-status")).to_have_text(re.compile(r"sw active (\d+)/\1 "), timeout=15000)


# -- adding a device, trusting it, pairing ----------------------------------------------------------


def test_add_by_address_pair_in_the_first_window_and_go_live_with_bearer_and_ticket(context, tsvc):
    page = open_app(context)
    requests: list[tuple[str, dict]] = []
    page.on("request", lambda r: requests.append((r.url, r.headers)))
    find(page, tsvc.address)
    expect(page.locator("[data-found] .fingerprint code")).to_have_text(tsvc.root_sha256)
    name = page.locator("[data-found-name]").inner_text()
    assert name
    expect(page.get_by_text("queda como administrador", exact=False)).to_be_visible()
    page.get_by_role("button", name="Pedir acceso").click()
    expect(page.locator("#connection")).to_have_text("En vivo", timeout=10000)
    clients = tsvc.access.store.list()
    assert [c["scope"] for c in clients] == ["admin"]
    expect(page.locator("#device-open")).to_have_text(name)
    # An order goes with the bearer, and the stream opened with a one-use ticket, never the token.
    page.locator("#run").click()
    expect(page.locator("#run")).to_have_text("Detener", timeout=10000)
    api = [(u, h) for u, h in requests if f":{tsvc.https_port}/v1/" in u]
    commands = [h for u, h in api if u.endswith("/v1/command")]
    assert commands
    assert all(h.get("authorization", "").startswith("Bearer asc_") for h in commands)
    assert any("/v1/stream?ticket=" in u for u, _ in api)
    assert not any("token=" in u for u, _ in api)
    assert not any("cookie" in h for _, h in api)


def test_add_from_the_qr_fragment_checks_the_root(context, tsvc):
    fp = tsvc.root_sha256.replace(":", "")
    page = open_app(context, f"#d={tsvc.address}&fp={fp}")
    expect(page.locator("[data-fp-match='1']")).to_be_visible()
    assert "fp=" not in page.url  # the fragment leaves the address bar
    expect(page.get_by_role("button", name="Pedir acceso")).to_be_enabled()
    # A link pasted into the open app (only the fragment changes): another root, refused.
    page.evaluate(f"location.hash = '#d={tsvc.address}&fp={'0' * 64}'")
    expect(page.locator("[data-fp-match='0']")).to_be_visible()
    expect(page.get_by_role("button", name="Pedir acceso")).to_be_disabled()


def test_a_connection_code_finds_checks_and_pairs_in_one_step(context, tsvc):
    """One code (connection_code.py): the address, the pairing code and part of the root's
    fingerprint. Typed once, the device is found, checked and paired; no other field."""
    tsvc.access.where = lambda: "127.0.0.1"
    code = tsvc.command("pair_start")["connection_code"]
    assert code
    page = open_app(context)
    page.get_by_label("Código de conexión o dirección").fill(code.lower().replace("-", " "))
    page.get_by_role("button", name="Conectar").click()
    expect(page.locator(".connect-screen")).to_be_hidden(timeout=10000)
    expect(page.locator("#connection")).to_have_text("En vivo", timeout=10000)
    assert len(tsvc.access.store.list()) == 1


def test_a_connection_code_for_another_device_is_refused(context, tsvc):
    from aurasync import connection_code

    tsvc.access.where = lambda: "127.0.0.1"
    started = tsvc.command("pair_start")
    other = connection_code.encode("127.0.0.1", tsvc.https_port, started["code"], "0" * 64)
    page = open_app(context)
    page.get_by_label("Código de conexión o dirección").fill(other)
    page.get_by_role("button", name="Conectar").click()
    expect(page.locator("[data-fp-match='0']")).to_be_visible(timeout=10000)
    expect(page.get_by_role("button", name="Pedir acceso")).to_be_disabled()
    assert tsvc.access.store.list() == []


def test_the_demo_loads_the_panel_with_no_device_and_says_so(context):
    """The PWA's demo (web/src/demo/api.ts): no service at all. The warning stays at the top, what is
    changed is kept in the page, what needs hardware says so, and leaving goes back to the start."""
    page = open_app(context)
    requests: list[str] = []
    page.on("request", lambda r: requests.append(r.url))
    page.locator("[data-demo-open]").click()
    banner = page.locator("[data-demo]")
    expect(banner).to_be_visible(timeout=10000)
    expect(banner).to_contain_text("no hay ningún equipo conectado")
    expect(page.locator("#engine-badge")).to_have_text("DEMO")
    expect(page.locator(".connect-screen")).to_be_hidden()
    expect(page.locator("[data-quick]")).to_have_count(3)
    page.locator("#now-render").select_option("spatial")
    page.wait_for_timeout(1200)  # the next poll of the state
    expect(page.locator("#now-render")).to_have_value("spatial")
    page.get_by_role("button", name="Calibrar", exact=True).first.click()
    page.locator("#cal-run").click()
    expect(page.locator("#toast, .toast").first).to_contain_text("demo", timeout=5000)
    assert not any(":8443/" in u or "/v1/" in u for u in requests), [u for u in requests if "/v1/" in u]
    banner.get_by_role("button", name="Salir de la demo").click()
    expect(page.locator("[data-demo]")).to_have_count(0)
    expect(page.locator(".connect-screen")).to_be_visible()


def test_the_qr_button_says_what_to_do_where_the_page_cannot_read_one(context, tsvc):
    page = open_app(context)
    page.evaluate("delete window.BarcodeDetector")
    page.get_by_role("button", name="Escanear QR").click()
    expect(page.locator("[data-qr-note]")).to_contain_text("cámara del teléfono")


def test_an_untrusted_certificate_is_explained_in_two_steps(playwright: Playwright, site_dir, tsvc):
    browser = playwright.chromium.launch()  # without the root
    try:
        page = browser.new_page()
        page.goto(APP)
        find(page, tsvc.address)
        box = page.locator(".connect-untrusted")
        expect(box).to_be_visible()
        base = f"https://{tsvc.address}"
        expect(box.locator(f"a[href='{base}/v1/tls/root.mobileconfig']")).to_be_visible()
        expect(box.locator(f"a[href='{base}/v1/tls/root.crt']")).to_be_visible()
        expect(box.locator(f"a[href='{base}/v1/hello']")).to_be_visible()
        expect(box).to_contain_text("aceptá el aviso")
    finally:
        browser.close()


def test_a_second_device_is_approved_by_the_admin_after_matching_the_check(chromium, context, tsvc):
    admin = open_app(context)
    pair_in_window(admin, tsvc)
    other_context = chromium.new_context()
    try:
        other = open_app(other_context)
        find(other, tsvc.address)
        other.get_by_label("Nombre de este dispositivo").fill("Teléfono de prueba")
        other.get_by_role("button", name="Pedir acceso").click()
        check = other.locator("[data-check]")
        expect(check).to_have_text(re.compile(r"^\d{4}$"))
        # The admin sees the request, with the same check, and approves it.
        admin.locator("#device-open").click()
        row = admin.locator("[data-request]")
        expect(row).to_contain_text("Teléfono de prueba")
        expect(row).to_contain_text(check.inner_text())
        row.get_by_role("button", name="Aprobar").click()
        expect(other.locator("#connection")).to_have_text("En vivo", timeout=10000)
        scopes = sorted(c["scope"] for c in tsvc.access.store.list())
        assert scopes == ["admin", "control"]
        # And renames it.
        phone = next(c["id"] for c in tsvc.access.store.list() if c["scope"] == "control")
        client = admin.locator(f"[data-client='{phone}']")
        client.get_by_role("button", name="Renombrar").click()
        client.get_by_label("Nombre nuevo para Teléfono de prueba").fill("Teléfono del living")
        client.get_by_role("button", name="Guardar").click()
        expect(admin.locator("[data-client]", has_text="Teléfono del living")).to_be_visible()
        assert "Teléfono del living" in [c["name"] for c in tsvc.access.store.list()]
    finally:
        other_context.close()


def test_a_revoked_client_is_asked_to_pair_again(context, tsvc):
    page = open_app(context)
    pair_in_window(page, tsvc)
    client = tsvc.access.store.list()[0]["id"]
    tsvc.access.store.revoke(client)
    expect(page.locator("[data-revoked='1']")).to_be_visible(timeout=10000)
    expect(page.locator(".connect-screen")).to_be_visible()
    expect(page.locator("#disconnected")).to_contain_text("volvé a emparejar")
    # And it is remembered: reopened, the app does not use the dead token.
    page.reload()
    expect(page.locator(".connect-screen")).to_be_visible()
    expect(page.locator("[data-device] .chip-warn")).to_have_text("sin emparejar")


# -- offline and updates ---------------------------------------------------------------------------


def cached_urls(page: Page) -> list[str]:
    return page.evaluate(
        """async () => {
          const out = [];
          for (const key of await caches.keys()) {
            const cache = await caches.open(key);
            for (const request of await cache.keys()) out.push(key + ' ' + request.url);
          }
          return out;
        }"""
    )


def test_opens_offline_from_the_service_worker_and_never_caches_the_api(context, tsvc):
    page = open_app(context)
    pair_in_window(page, tsvc)
    wait_cached(page)
    urls = cached_urls(page)
    assert urls
    assert all(f"{APP}" in u.split(" ", 1)[1] for u in urls)
    assert not any("/v1/" in u for u in urls)
    name = page.locator("#device-open").inner_text()
    context.set_offline(True)
    page.reload()
    expect(page.locator("#disconnected")).to_contain_text(f"Sin conexión con {name}", timeout=15000)
    expect(page.locator(".connect-screen")).to_be_hidden()
    expect(page.locator("#cards, #views").first).to_be_attached()
    context.set_offline(False)
    expect(page.locator("#connection")).to_have_text(re.compile("En vivo|Consultando"), timeout=15000)


def publish_new_version(root: Path, mark: str) -> str:
    """What the next deploy does: a changed file, its new hash in sw.js, and a new version."""
    app = root / "app.js"
    app.write_text(app.read_text(encoding="utf-8") + f"\n// {mark}\n", encoding="utf-8")
    build = json.loads((root / "build.json").read_text(encoding="utf-8"))
    build["commit"] = mark
    (root / "build.json").write_text(json.dumps(build), encoding="utf-8")
    sw = (root / "sw.js").read_text(encoding="utf-8")
    entries = json.loads(re.search(r"^const ENTRIES = (.*);$", sw, re.MULTILINE).group(1))
    entries = [[n, hashlib.sha256((root / n).read_bytes()).hexdigest()] for n, _ in entries]
    version = f"0.0.0-{mark}"
    sw = re.sub(r"^const ENTRIES = .*;$", lambda _: f"const ENTRIES = {json.dumps(entries)};", sw, flags=re.MULTILINE)
    sw = re.sub(r'^const VERSION = ".*";$', f'const VERSION = "{version}";', sw, flags=re.MULTILINE)
    (root / "sw.js").write_text(sw, encoding="utf-8")
    return version


def check_for_update(page: Page) -> None:
    page.evaluate("navigator.serviceWorker.getRegistration().then((r) => r.update())")


def test_an_update_waits_while_a_blind_ab_runs(context, site_dir, tsvc):
    page = open_app(context)
    pair_in_window(page, tsvc)
    wait_cached(page)
    tsvc.command("preset_save", name="uno")
    tsvc.command("preset_save", name="dos")
    tsvc.command("start")
    tsvc.command("ab_start", a="uno", b="dos")
    expect(page.locator("#ab-live")).to_be_visible(timeout=10000)
    page.evaluate("window.__oldPage = true")
    version = publish_new_version(site_dir, "v2test")
    check_for_update(page)
    expect(page.locator("#pwa-status")).to_contain_text("upd ready", timeout=15000)
    page.wait_for_timeout(1500)
    assert page.evaluate("window.__oldPage === true"), "the page reloaded in the middle of the A/B"
    tsvc.command("ab_stop")
    page.wait_for_function("window.__oldPage === undefined", timeout=15000)
    expect(page.locator("#pwa-status")).to_contain_text("v2test", timeout=15000)
    assert "v=" not in page.url
    keys = {u.split(" ", 1)[0] for u in cached_urls(page)}
    assert keys == {f"aurasync-{version}"}


def test_an_update_reloads_an_idle_page_and_downloads_only_what_changed(context, site, site_dir, tsvc):
    page = open_app(context)
    pair_in_window(page, tsvc)
    wait_cached(page)
    page.evaluate("window.__oldPage = true")
    site.served.clear()
    publish_new_version(site_dir, "v3test")
    check_for_update(page)
    page.wait_for_function("window.__oldPage === undefined", timeout=15000)
    expect(page.locator("#pwa-status")).to_contain_text("v3test", timeout=15000)
    wait_cached(page)
    downloaded = {p.removeprefix(PAGES) for p in site.served}
    # The worker fetched the two files that changed (and itself); the rest came from the old cache.
    assert downloaded == {"app.js", "build.json", "sw.js"}


# -- the public build ------------------------------------------------------------------------------

PRIVATE = [
    ("a MAC address", re.compile(r"\b[0-9A-F]{2}([:_-])[0-9A-F]{2}(?:\1[0-9A-F]{2}){4}\b", re.IGNORECASE)),
    ("a private IPv4 address", re.compile(r"\b(?:10\.\d{1,3}|192\.168|172\.(?:1[6-9]|2\d|3[01]))\.\d{1,3}\b")),
    ("a client token", re.compile(r"\basc_[0-9a-z]", re.IGNORECASE)),
    ("a Bluetooth sink", re.compile(r"bluez_(?:output|input|card)\.[0-9A-F]{2}", re.IGNORECASE)),
    ("a machine of this project", re.compile(r"\b(?:pc-ryzen5|hp-o16)\b", re.IGNORECASE)),
]


def private_hits(root: Path) -> list[str]:
    hits = []
    for path in sorted(root.iterdir()):
        if path.suffix == ".png":
            continue
        text = path.read_text(encoding="utf-8")
        hits += [f"{path.name}: {what} {m.group(0)}" for what, rx in PRIVATE for m in rx.finditer(text)]
    return hits


def test_the_public_build_carries_nothing_private_and_lists_every_file():
    assert private_hits(DIST) == []
    manifest = json.loads((DIST / "manifest.webmanifest").read_text(encoding="utf-8"))
    assert manifest["start_url"] == "./"
    assert manifest["scope"] == "./"
    assert manifest["display"] == "standalone"
    assert {i["sizes"] for i in manifest["icons"]} >= {"192x192", "512x512"}
    sw = (DIST / "sw.js").read_text(encoding="utf-8")
    entries = json.loads(re.search(r"^const ENTRIES = (.*);$", sw, re.MULTILINE).group(1))
    assert entries[0][0] == "index.html"
    files = {p.name for p in DIST.iterdir()} - {"sw.js"}
    assert {n for n, _ in entries} == files
    for name, digest in entries:
        assert hashlib.sha256((DIST / name).read_bytes()).hexdigest() == digest, name
    build = json.loads((DIST / "build.json").read_text(encoding="utf-8"))
    assert build["version"]
    assert build["commit"]


def test_the_privacy_check_sees_what_it_looks_for(tmp_path: Path):
    for leak in ("90:F2:60:75:4A:83", "192.168.1.50", "10.0.0.7", "asc_942e018c_x", "bluez_output.90_F2", "PC-Ryzen5"):
        (tmp_path / "leak.js").write_text(f"const x = '{leak}';", encoding="utf-8")
        assert private_hits(tmp_path), leak
    (tmp_path / "leak.js").write_text(
        "fetch('https://127.0.0.1:8443/v1/hello'); 'aurasync.local:8443'", encoding="utf-8"
    )
    assert private_hits(tmp_path) == []
