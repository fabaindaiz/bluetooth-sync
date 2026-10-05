"""The REST adapter: each route is a shortcut to one contract message.

Standard library only (`http.server`, `ssl`). The routes are in
`docs/superpowers/specs/2026-09-29-control-service-design.md` §10 and `host/docs/control-api.md`.

`POST /v1/command` takes the raw message exactly as it would travel over serial; the
tests check that every shortcut replies the same as its raw message.

**Who may ask** (`access.py`, d-7c8794-37f9bc). A request proves itself with the master token of
`service.json` or a client's token, as `Authorization: Bearer <token>` (any route), `?token=`
(a link opened on a phone), or the panel's `HttpOnly` cookie (the panel this service serves);
`GET /v1/stream` also takes a one-use `?ticket=`. Each operation needs a scope (`read`,
`control`, `admin`; the master token is `admin`). Wrong credentials count against the address
and block it for a while. Unauthenticated: `GET /v1/hello`, the root certificate under
`/v1/tls/`, and pairing (`POST /v1/pair/request`, `GET /v1/pair/{id}`).

**Which pages may read the replies.** Every request passes the `Host` check (DNS rebinding).
A request with an `Origin` must come from this server or from `panel_origins` (the PWA on
GitHub Pages); those get CORS headers, without credentials (no cookie crosses sites), and
`OPTIONS` answers their preflight.

The same handler serves HTTP and, with a `ssl_context`, HTTPS.
"""

from __future__ import annotations

import contextlib
import io
import json
import re
import sys
import threading
import time
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, unquote, urlsplit

import segno

from aurasync import __version__, control
from aurasync.access import Access
from aurasync.control import ContractError
from aurasync.lan import LocalNames, host_name

if TYPE_CHECKING:
    import ssl

    from aurasync.clients import Principal
    from aurasync.service import Service
    from aurasync.tls import Certificates

__all__ = ["host_name", "make_server", "route"]


def route(method: str, path: str, body: Any) -> dict:
    """The contract message a request stands for. Raises `ContractError` for an unknown route."""
    parts = [unquote(p) for p in path.strip("/").split("/")]
    if parts[:1] != ["v1"]:
        raise ContractError("not_found", f"no route {method} {path}")
    rest = parts[1:]
    v = control.VERSION
    extra = body if isinstance(body, dict) else {}
    match method, rest:
        case "GET", ["state"]:
            return {"v": v, "op": "state"}
        case "POST", ["session", "start"]:
            return {**extra, "v": v, "op": "start"}
        case "POST", ["session", "stop"]:
            return {"v": v, "op": "stop"}
        case "PATCH", ["speakers", name]:
            return {"v": v, "op": "set", "speaker": name, "changes": body}
        case "POST", ["speakers", name, "join"]:
            return {"v": v, "op": "speaker_join", "speaker": name}
        case "POST", ["speakers", name, "leave"]:
            return {"v": v, "op": "speaker_leave", "speaker": name}
        case "PATCH", ["global"]:
            return {"v": v, "op": "set", "changes": body}
        case "GET", ["presets"]:
            return {"v": v, "op": "presets"}
        case "GET", ["logs"]:
            return {"v": v, "op": "logs"}
        case "PUT", ["presets", name]:
            return {"v": v, "op": "preset_save", "name": name}
        case "DELETE", ["presets", name]:
            return {"v": v, "op": "preset_delete", "name": name}
        case "POST", ["presets", name, "load"]:
            return {"v": v, "op": "preset_load", "name": name}
        case "POST", ["installation", "save"]:
            return {"v": v, "op": "save"}
        case "POST", ["shutdown"]:
            return {"v": v, "op": "shutdown"}
        # Clients and pairing (access.py):
        case "GET", ["clients"]:
            return {"v": v, "op": "clients"}
        case "DELETE", ["clients", client]:
            return {"v": v, "op": "client_revoke", "client": client}
        case "PATCH", ["clients", client]:
            return {**extra, "v": v, "op": "client_rename", "client": client}
        case "GET", ["pair"]:
            return {"v": v, "op": "pair_status"}
        case "POST", ["pair", "code"]:
            return {**extra, "v": v, "op": "pair_start"}
        case "POST", ["pair", request, "approve"]:
            return {**extra, "v": v, "op": "pair_approve", "request": request}
        case "POST", ["pair", request, "deny"]:
            return {"v": v, "op": "pair_deny", "request": request}
        # The sync estimator (spec 2026-10-03 §6.1):
        case "GET", ["sync"]:
            return {"v": v, "op": "sync_state"}
        case "PATCH", ["sync"]:
            return {"v": v, "op": "sync_set", "changes": body}
        case "POST", ["sync", "apply"]:
            return {**extra, "v": v, "op": "sync_apply"}
        case "GET", ["sync", "explain"]:
            return {"v": v, "op": "sync_explain"}
        case "GET", ["spatial", "explain"]:
            return {"v": v, "op": "spatial_explain"}
        case "POST", ["command"]:
            return body
    raise ContractError("not_found", f"no route {method} {path}")


COOKIE = "aurasync_token"
PANEL_DIR = Path(__file__).parent / "panel"
STATIC_TYPES = {".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml"}
TLS_TIMEOUT_S = 30.0
"""A TLS connection that stalls in its handshake (or anywhere) gives its thread back after this."""
CORS_MAX_AGE_S = 600
PAIR_REQUEST_FIELDS = {"name", "scope", "code"}
CODE_RE = re.compile(r"^[0-9]{6}$")


def parse_pair_request(raw: bytes) -> tuple[str, str, str | None]:
    """`POST /v1/pair/request`'s body: `{"name", "scope"?, "code"?}`, checked like a contract message."""
    body = control.decode(raw) if raw else None
    if not isinstance(body, dict):
        raise ContractError("bad_request", 'send {"name": "…"}')
    unknown = set(body) - PAIR_REQUEST_FIELDS
    if unknown:
        raise ContractError("unknown_field", f"a pairing request takes no field {sorted(unknown)[0]!r}")
    name = control.check_value("name", control.NAME, body.get("name"))
    scope = control.check_value("scope", control.SCOPE, body.get("scope", "control"))
    code = body.get("code")
    if code is not None and (not isinstance(code, str) or not CODE_RE.match(code)):
        raise ContractError("type", "code must be 6 digits, as a string")
    return name, scope, code


def allowed_hosts(bind: str) -> frozenset[str]:
    """The names a browser may use to reach this server, now. Anything else is DNS rebinding."""
    return LocalNames(bind).names()


PWA_URL = "https://fabaindaiz.github.io/bluetooth-sync/"
"""The panel as a PWA on GitHub Pages (d-7c8794-37f9bc; `.github/workflows/pages.yml`)."""


def pairing_link(urls: list[str], tls: dict | None) -> str | None:
    """What the panel's QR carries: the PWA, with this device's HTTPS address and the SHA-256 of
    its root in the fragment, which the browser never sends to GitHub Pages. No token: the phone
    pairs, and an admin (or the first-client window) approves it. None without HTTPS or without
    an address on the local network."""
    lan = [u for u in urls if "127.0.0.1" not in u]
    if not lan or not tls or not tls.get("root_sha256"):
        return None
    host = urlsplit(lan[0]).hostname or ""
    if ":" in host:
        host = f"[{host}]"
    fingerprint = str(tls["root_sha256"]).replace(":", "")
    return f"{PWA_URL}#d={host}:{tls['port']}&fp={fingerprint}"


def qr_svg(url: str) -> str:
    """A complete SVG with its namespace: an <img> does not draw one without it."""
    out = io.BytesIO()
    segno.make(url, error="m").save(out, kind="svg", scale=5, border=2, dark="#111", light="#fff", xmldecl=False)
    return out.getvalue().decode()


def qr_terminal(url: str) -> str:
    out = io.StringIO()
    try:
        segno.make(url, error="m").terminal(out=out, compact=True)
    except Exception:  # noqa: BLE001 - a terminal without the characters just gets no QR
        return ""
    return out.getvalue()


STREAM_MAX = 8
"""Streams open at once. Each holds one server thread for as long as it lives."""
STREAM_TICK_S = 0.05
"""Meters at 20 Hz."""
STREAM_INPUT_S = 0.1
STREAM_STATE_S = 1.0
"""The snapshot also carries what changes without `sequence` (health, the observer)."""
STREAM_PING_S = 5.0
STREAM_QUALITY_S = 0.5
"""`quality` at 2 Hz (spec 2026-10-02 §6.3)."""
STREAM_CHAIN_S = 0.2
"""`chain` (each stage's metrics) at 5 Hz."""
STREAM_RADIO_S = 1.0
"""`radio` at 1 Hz, and at once when a drop arrives."""


def sse(event: str, data: Any) -> bytes:
    """One Server-Sent Event."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, separators=(',', ':'))}\n\n".encode()


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request: Any, client_address: Any) -> None:
        # A phone that refuses the certificate, or a client gone mid-request: not worth a
        # traceback in the service's terminal.
        if isinstance(sys.exc_info()[1], OSError):
            return
        super().handle_error(request, client_address)


def make_server(
    service: Service,
    bind: str,
    port: int,
    token: str,
    *,
    access: Access | None = None,
    names: LocalNames | None = None,
    ssl_context: ssl.SSLContext | None = None,
    certificates: Certificates | None = None,
) -> ThreadingHTTPServer:
    """An HTTP server (HTTPS with `ssl_context`) over `service`.

    `access` holds the master token, the clients and pairing; without one, a store in memory is
    made around `token` (and given to the service, so the access operations answer)."""
    if access is None:
        access = getattr(service, "access", None) or Access(token)
    if getattr(service, "access", None) is None:
        service.access = access
    names = names or LocalNames(bind)
    scheme = "https" if ssl_context is not None else "http"
    streams = getattr(service, "streams", None) or {"open": 0}
    """Shared with the service: `state.health.streams_open` reports it."""
    streams_lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        server_version = "aurasync"
        protocol_version = "HTTP/1.1"
        timeout = TLS_TIMEOUT_S if ssl_context is not None else None
        _cors: str | None = None

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - the base class names it so
            pass  # the service logs commands itself; and a URL can carry a token or a ticket

        @property
        def ip(self) -> str:
            return str(self.client_address[0])

        def _cookie_token(self) -> str:
            jar = cookies.SimpleCookie()
            with contextlib.suppress(cookies.CookieError):
                jar.load(self.headers.get("Cookie", ""))
            morsel = jar.get(COOKIE)
            return morsel.value if morsel else ""

        def _auth(self, query: dict, *, ticket_ok: bool = False) -> tuple[Principal | None, str | None, bool]:
            """Who the request is, how it proved it ('bearer', 'query', 'ticket', 'cookie'), and
            whether it presented a credential that was wrong."""
            header = self.headers.get("Authorization", "")
            offered = {
                "bearer": header[7:].strip() if header.startswith("Bearer ") else "",
                "query": (query.get("token") or [""])[0],
                "cookie": self._cookie_token(),
            }
            wrong = False
            for how, value in offered.items():
                if not value:
                    continue
                principal = access.verify(value, self.ip)
                if principal is not None:
                    return principal, how, False
                wrong = True
            ticket = (query.get("ticket") or [""])[0]
            if ticket_ok and ticket:
                principal = access.tickets.redeem(ticket)
                if principal is not None and access.alive(principal):
                    return principal, "ticket", False
                wrong = True
            return None, None, wrong

        def _send(self, status: int, body: bytes, content_type: str, extra: dict | None = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self._cors_headers()
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

        def _cors_headers(self) -> None:
            if self._cors:
                self.send_header("Access-Control-Allow-Origin", self._cors)
                self.send_header("Access-Control-Expose-Headers", "Retry-After")
                self.send_header("Vary", "Origin")

        def _reply(self, reply: dict, extra: dict | None = None) -> None:
            status = 200 if reply.get("ok") else control.HTTP_STATUS[reply["error"]["code"]]
            body = json.dumps(reply, ensure_ascii=False).encode()
            self._send(status, body, "application/json; charset=utf-8", extra)

        def _error(self, code: str, message: str, extra: dict | None = None) -> None:
            self._reply(control.error(None, code, message), extra)

        def _rate_limited(self) -> bool:
            wait = access.limiter.blocked_for(self.ip)
            if wait <= 0:
                return False
            self._error(
                "rate_limited", "too many failed attempts from this address", {"Retry-After": str(int(wait) + 1)}
            )
            return True

        def _stream(self, query: dict, principal: Principal) -> None:
            """`GET /v1/stream`: state, meters, input and logs as Server-Sent Events (spec §17).

            The engine never waits for this thread: it reads the snapshot and the telemetry
            ring, which the engine replaces or appends to without blocking. A client that
            stops reading fills only its own socket, and a failed write ends only this stream
            (card `best-effort-side-channels`). A revoked client's stream ends within a tick
            (card `kill-switch-reaches-every-path`).
            """
            with streams_lock:
                if streams["open"] >= STREAM_MAX:
                    self._error("busy", f"{STREAM_MAX} streams are open already")
                    return
                streams["open"] += 1
            try:
                self.close_connection = True
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Connection", "close")
                self._cors_headers()
                self.end_headers()
                try:
                    log_seq = int((query.get("since") or ["0"])[0])
                except ValueError:
                    log_seq = 0
                self._stream_loop(log_seq, principal)
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass  # the client left: only this stream ends
            finally:
                with streams_lock:
                    streams["open"] -= 1

        def _stream_loop(self, log_seq: int, principal: Principal) -> None:
            last_sequence, last_state, last_input, last_ping = None, 0.0, 0.0, time.monotonic()
            last_quality = last_chain = last_radio = 0.0
            drops_seen = None
            while not service.stopping and access.alive(principal):
                now = time.monotonic()
                out = []
                snapshot = service.snapshot
                if snapshot and (snapshot.get("sequence") != last_sequence or now - last_state >= STREAM_STATE_S):
                    out.append(sse("state", snapshot))
                    last_sequence, last_state = snapshot.get("sequence"), now
                telemetry = getattr(service.session, "telemetry", None)
                if telemetry is not None:
                    meters = telemetry.meters_at()
                    if meters is not None:
                        out.append(sse("meters", meters))
                    if now - last_input >= STREAM_INPUT_S:
                        frame = telemetry.input_at()
                        if frame is not None:
                            out.append(sse("input", frame))
                        last_input = now
                if now - last_quality >= STREAM_QUALITY_S:
                    last_quality = now
                    quality = service.quality
                    if quality is not None:
                        out.append(sse("quality", quality))
                if now - last_chain >= STREAM_CHAIN_S:
                    last_chain = now
                    metrics = service.chain_metrics
                    if metrics is not None:
                        out.append(sse("chain", metrics))
                if now - last_radio >= STREAM_RADIO_S or service.radio_drops != drops_seen:
                    last_radio, drops_seen = now, service.radio_drops
                    out.append(sse("radio", service.radio_view()))
                logs = service.logs.since(log_seq, 200)
                if logs["records"] or logs["gap"]:
                    out.append(sse("log", logs))
                log_seq = logs["last"]
                if not out and now - last_ping >= STREAM_PING_S:
                    out.append(b": ping\n\n")
                if out:
                    self.wfile.write(b"".join(out))
                    self.wfile.flush()
                    last_ping = now
                time.sleep(max(0.0, STREAM_TICK_S - (time.monotonic() - now)))

        def _forbidden(self, why: str) -> None:
            self._send(403, why.encode(), "text/plain; charset=utf-8")

        def _web(self, path: str, query: dict, how: str | None) -> None:
            """The panel: `/`, `/static/*` and `/pairing.svg` (the PWA's pairing link, never a token)."""
            if path == "/" and (query.get("t") or [""])[0]:
                if self._rate_limited():
                    return
                given = query["t"][0]
                if access.verify(given, self.ip) is None:
                    access.limiter.fail(self.ip)
                    self._send(401, b"wrong token", "text/plain; charset=utf-8")
                    return
                # The token travels once in the URL; from here on it is an HttpOnly cookie,
                # and the redirect drops it from the address bar and the history.
                secure = "; Secure" if scheme == "https" else ""
                cookie = f"{COOKIE}={given}; HttpOnly; SameSite=Strict; Path=/; Max-Age=31536000{secure}"
                self._send(303, b"", "text/plain", {"Location": "/", "Set-Cookie": cookie})
                return
            if how is None:
                page = (
                    "<!doctype html><meta charset=utf-8><title>aurasync</title>"
                    "<p>Abrí el link con el token que imprime <code>aurasync service</code> al arrancar."
                )
                self._send(401, page.encode(), "text/html; charset=utf-8")
                return
            if path == "/pairing.svg":
                link = pairing_link(service.pairing.get("urls", []), access.tls)
                if link is None:
                    why = b"the PWA needs HTTPS (tls true in service.json) and an address on the local network"
                    self._send(404, why, "text/plain; charset=utf-8")
                    return
                self._send(200, qr_svg(link).encode(), "image/svg+xml")
                return
            name = "index.html" if path == "/" else path.removeprefix("/static/")
            file = (PANEL_DIR / name).resolve()
            if file.parent != PANEL_DIR.resolve() or not file.is_file() or file.suffix not in STATIC_TYPES:
                self._send(404, b"not found", "text/plain; charset=utf-8")
                return
            self._send(200, file.read_bytes(), f"{STATIC_TYPES[file.suffix]}; charset=utf-8")

        # -- routes without credentials -------------------------------------------------

        def _open_route(self, method: str, path: str, raw: bytes) -> bool:
            """`hello`, the root certificate and pairing. True when it answered."""
            if method == "GET" and path == "/v1/hello":
                self._reply(control.ok(None, access.hello(names.hostname or "aurasync", __version__)))
                return True
            if method == "GET" and path.startswith("/v1/tls/root."):
                self._root(path.removeprefix("/v1/tls/root."))
                return True
            parts = path.strip("/").split("/")
            if method == "POST" and parts == ["v1", "pair", "request"]:
                if not self._rate_limited():
                    self._pair_request(raw)
                return True
            if method == "GET" and len(parts) == 3 and parts[:2] == ["v1", "pair"]:  # noqa: PLR2004
                if not self._rate_limited():
                    found = access.desk.poll(unquote(parts[2]))
                    if found is None:
                        access.limiter.fail(self.ip)
                        self._error("not_found", "no such pairing request (or it expired)")
                    else:
                        self._reply(control.ok(None, found))
                return True
            return False

        def _root(self, kind: str) -> None:
            if certificates is None or not certificates.root_pem.exists():
                self._error("not_found", "this service has no certificate: set tls true in service.json")
                return
            if kind == "pem":
                self._send(200, certificates.root_pem.read_bytes(), "application/x-pem-file")
            elif kind == "crt":
                # DER: what Android's "Install a certificate → CA certificate" expects.
                disposition = {"Content-Disposition": 'attachment; filename="aurasync-root.crt"'}
                self._send(200, certificates.root_der(), "application/x-x509-ca-cert", disposition)
            elif kind == "mobileconfig":
                disposition = {"Content-Disposition": 'attachment; filename="aurasync.mobileconfig"'}
                body = certificates.mobileconfig(names.hostname)
                self._send(200, body, "application/x-apple-aspen-config", disposition)
            else:
                self._error("not_found", "the root is served as root.pem, root.crt or root.mobileconfig")

        def _pair_request(self, raw: bytes) -> None:
            try:
                name, scope, code = parse_pair_request(raw)
                view, wrong_code = access.desk.request(name, self.ip, scope, code)
            except ContractError as exc:
                self._error(exc.code, exc.message)
                return
            if wrong_code:
                access.limiter.fail(self.ip)
            self._reply(control.ok(None, view))

        # -- every request ---------------------------------------------------------------

        def _origin_ok(self) -> bool:
            """No Origin (curl, a same-origin GET), this server, or an allowed panel."""
            origin = self.headers.get("Origin")
            self._cors = access.cors_origin(origin)
            return origin is None or origin == f"{scheme}://{self.headers.get('Host')}" or self._cors is not None

        def _handle(self, method: str) -> None:
            url = urlsplit(self.path)
            query = parse_qs(url.query)
            self._cors = None
            # DNS rebinding: a page on another name that resolves to this machine.
            if not names.allows(host_name(self.headers.get("Host"))):
                self._forbidden("unknown Host")
                return
            if not self._origin_ok():
                self._forbidden("foreign Origin")
                return
            length = int(self.headers.get("Content-Length") or 0)
            if length > control.MAX_BYTES:
                # Do not read it: answer and drop the connection.
                self.close_connection = True
                self._error("bad_request", f"the body is over {control.MAX_BYTES // 1024} KiB")
                return
            raw = self.rfile.read(length) if length else b""
            if url.path.startswith("/v1/") and self._open_route(method, url.path, raw):
                return
            stream = method == "GET" and url.path == "/v1/stream"
            if self._rate_limited():
                return
            principal, how, wrong = self._auth(query, ticket_ok=stream)
            if wrong and principal is None:
                access.limiter.fail(self.ip)
            elif principal is not None:
                access.limiter.succeed(self.ip)
            if not url.path.startswith("/v1/"):
                if method == "GET":
                    self._web(url.path, query, how)
                else:
                    self._send(405, b"", "text/plain")
                return
            if principal is None:
                self._error("unauthorized", "missing or wrong token")
                return
            # A browser always sends Origin with a cookie on these; it must be this very server.
            if (
                how == "cookie"
                and method != "GET"
                and self.headers.get("Origin") != f"{scheme}://{self.headers.get('Host')}"
            ):
                self._forbidden("foreign Origin")
                return
            if stream:
                self._stream(query, principal)
                return
            if method == "POST" and url.path == "/v1/stream/ticket":
                try:
                    self._reply(control.ok(None, access.tickets.issue(principal)))
                except ContractError as exc:
                    self._error(exc.code, exc.message)
                return
            try:
                body = control.decode(raw) if raw else None
                message = route(method, url.path, body)
            except ContractError as exc:
                self._error(exc.code, exc.message)
                return
            op = message.get("op") if isinstance(message, dict) else None
            needed = access.needs(op)
            if not access.allows(principal, needed):
                self._reply(control.error(control.message_id(message), "forbidden", f"{op} needs the {needed} scope"))
                return
            self._reply(service.handle(message, actor=principal.name if principal else None))

        def do_OPTIONS(self) -> None:
            """The CORS preflight of a panel on another origin (the PWA)."""
            self._cors = None
            if not names.allows(host_name(self.headers.get("Host"))):
                self._forbidden("unknown Host")
                return
            self._cors = access.cors_origin(self.headers.get("Origin"))
            if self._cors is None or not urlsplit(self.path).path.startswith("/v1/"):
                self._forbidden("foreign Origin")
                return
            extra = {
                "Access-Control-Allow-Methods": "GET, POST, PUT, PATCH, DELETE",
                "Access-Control-Allow-Headers": "Authorization, Content-Type",
                "Access-Control-Max-Age": str(CORS_MAX_AGE_S),
            }
            # Private Network Access is paused in Chrome and Local Network Access (a permission
            # prompt) replaced it; older Chromium builds still send this preflight header, and
            # answering it costs nothing (research H §1.2).
            if self.headers.get("Access-Control-Request-Private-Network") == "true":
                extra["Access-Control-Allow-Private-Network"] = "true"
            self._send(204, b"", "text/plain", extra)

        def do_GET(self) -> None:
            self._handle("GET")

        def do_POST(self) -> None:
            self._handle("POST")

        def do_PUT(self) -> None:
            self._handle("PUT")

        def do_PATCH(self) -> None:
            self._handle("PATCH")

        def do_DELETE(self) -> None:
            self._handle("DELETE")

    server = _Server((bind, port), Handler)
    if ssl_context is not None:
        # The handshake happens in the request's thread (on its first read), not in the accept
        # loop: a client that connects and says nothing cannot stall the server.
        server.socket = ssl_context.wrap_socket(server.socket, server_side=True, do_handshake_on_connect=False)
    return server
