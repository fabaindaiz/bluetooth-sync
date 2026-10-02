"""The REST adapter: each route is a shortcut to one contract message.

Standard library only (`http.server`). Every request carries the token, as
`Authorization: Bearer <token>` or `?token=<token>` for a link opened on a phone, compared
in constant time. The routes are in
`docs/superpowers/specs/2026-09-29-control-service-design.md` §10 and `host/docs/control-api.md`.

`POST /v1/command` takes the raw message exactly as it would travel over serial; the
tests check that every shortcut replies the same as its raw message.
"""

from __future__ import annotations

import contextlib
import hmac
import io
import json
import socket
import threading
import time
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, unquote, urlsplit

import segno

from aurasync import control
from aurasync.control import ContractError

if TYPE_CHECKING:
    from aurasync.service import Service


def route(method: str, path: str, body: Any) -> dict:
    """The contract message a request stands for. Raises `ContractError` for an unknown route."""
    parts = [unquote(p) for p in path.strip("/").split("/")]
    if parts[:1] != ["v1"]:
        raise ContractError("not_found", f"no route {method} {path}")
    rest = parts[1:]
    v = control.VERSION
    match method, rest:
        case "GET", ["state"]:
            return {"v": v, "op": "state"}
        case "POST", ["session", "start"]:
            extra = body if isinstance(body, dict) else {}
            return {**extra, "v": v, "op": "start"}
        case "POST", ["session", "stop"]:
            return {"v": v, "op": "stop"}
        case "PATCH", ["speakers", name]:
            return {"v": v, "op": "set", "speaker": name, "changes": body}
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
        case "POST", ["command"]:
            return body
    raise ContractError("not_found", f"no route {method} {path}")


COOKIE = "aurasync_token"
PANEL_DIR = Path(__file__).parent / "panel"
STATIC_TYPES = {".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml"}


def allowed_hosts(bind: str) -> frozenset[str]:
    """The names a browser may use to reach this server. Anything else is DNS rebinding."""
    names = {"127.0.0.1", "localhost"}
    if bind not in {"0.0.0.0", ""}:  # noqa: S104
        names.add(bind)
    else:
        from aurasync.service import local_addresses  # noqa: PLC0415 - service imports this module

        names.update(local_addresses())
        host = socket.gethostname()
        names.update({host, f"{host}.local"})
    return frozenset(names)


def host_name(header: str | None) -> str | None:
    if not header:
        return None
    return header.rsplit(":", 1)[0] if ":" in header else header


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


def sse(event: str, data: Any) -> bytes:
    """One Server-Sent Event."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, separators=(',', ':'))}\n\n".encode()


def make_server(service: Service, bind: str, port: int, token: str) -> ThreadingHTTPServer:
    expected = token.encode()
    hosts = allowed_hosts(bind)
    streams = {"open": 0}
    streams_lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        server_version = "aurasync"
        protocol_version = "HTTP/1.1"

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - the base class names it so
            pass  # the service logs commands itself; per-request lines would bury them

        def _cookie_token(self) -> str:
            jar = cookies.SimpleCookie()
            with contextlib.suppress(cookies.CookieError):
                jar.load(self.headers.get("Cookie", ""))
            morsel = jar.get(COOKIE)
            return morsel.value if morsel else ""

        def _auth(self, query: dict) -> str | None:
            """How the request proved it knows the token: 'bearer', 'query', 'cookie', or None."""
            header = self.headers.get("Authorization", "")
            if header.startswith("Bearer ") and hmac.compare_digest(header[7:].strip().encode(), expected):
                return "bearer"
            if hmac.compare_digest(((query.get("token") or [""])[0]).encode(), expected):
                return "query"
            if hmac.compare_digest(self._cookie_token().encode(), expected):
                return "cookie"
            return None

        def _send(self, status: int, body: bytes, content_type: str, extra: dict | None = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

        def _reply(self, reply: dict) -> None:
            status = 200 if reply.get("ok") else control.HTTP_STATUS[reply["error"]["code"]]
            self._send(status, json.dumps(reply, ensure_ascii=False).encode(), "application/json; charset=utf-8")

        def _stream(self, query: dict) -> None:
            """`GET /v1/stream`: state, meters, input and logs as Server-Sent Events (spec §17).

            The engine never waits for this thread: it reads the snapshot and the telemetry
            ring, which the engine replaces or appends to without blocking. A client that
            stops reading fills only its own socket, and a failed write ends only this stream
            (card `best-effort-side-channels`).
            """
            with streams_lock:
                if streams["open"] >= STREAM_MAX:
                    self._reply(control.error(None, "busy", f"{STREAM_MAX} streams are open already"))
                    return
                streams["open"] += 1
            try:
                self.close_connection = True
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Connection", "close")
                self.end_headers()
                try:
                    log_seq = int((query.get("since") or ["0"])[0])
                except ValueError:
                    log_seq = 0
                self._stream_loop(log_seq)
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass  # the client left: only this stream ends
            finally:
                with streams_lock:
                    streams["open"] -= 1

        def _stream_loop(self, log_seq: int) -> None:
            last_sequence, last_state, last_input, last_ping = None, 0.0, 0.0, time.monotonic()
            while not service.stopping:
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
            """The panel: `/`, `/static/*` and `/pairing.svg`."""
            if path == "/" and (query.get("t") or [""])[0]:
                if not hmac.compare_digest(query["t"][0].encode(), expected):
                    self._send(401, b"wrong token", "text/plain; charset=utf-8")
                    return
                # The token travels once in the URL; from here on it is an HttpOnly cookie,
                # and the redirect drops it from the address bar and the history.
                cookie = f"{COOKIE}={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=31536000"
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
                urls = [u for u in service.pairing.get("urls", []) if "127.0.0.1" not in u]
                if not urls:
                    self._send(404, b"not listening on the local network", "text/plain; charset=utf-8")
                    return
                self._send(200, qr_svg(f"{urls[0]}/?t={token}").encode(), "image/svg+xml")
                return
            name = "index.html" if path == "/" else path.removeprefix("/static/")
            file = (PANEL_DIR / name).resolve()
            if file.parent != PANEL_DIR.resolve() or not file.is_file() or file.suffix not in STATIC_TYPES:
                self._send(404, b"not found", "text/plain; charset=utf-8")
                return
            self._send(200, file.read_bytes(), f"{STATIC_TYPES[file.suffix]}; charset=utf-8")

        def _handle(self, method: str) -> None:
            url = urlsplit(self.path)
            query = parse_qs(url.query)
            # DNS rebinding: a page on another name that resolves to this machine.
            if host_name(self.headers.get("Host")) not in hosts:
                self._forbidden("unknown Host")
                return
            length = int(self.headers.get("Content-Length") or 0)
            if length > control.MAX_BYTES:
                # Do not read it: answer and drop the connection.
                self.close_connection = True
                self._reply(control.error(None, "bad_request", f"the body is over {control.MAX_BYTES // 1024} KiB"))
                return
            raw = self.rfile.read(length) if length else b""
            how = self._auth(query)
            if not url.path.startswith("/v1/"):
                if method == "GET":
                    self._web(url.path, query, how)
                else:
                    self._send(405, b"", "text/plain")
                return
            if how is None:
                self._reply(control.error(None, "unauthorized", "missing or wrong token"))
                return
            if method == "GET" and url.path == "/v1/stream":
                self._stream(query)
                return
            if how == "cookie" and method != "GET":
                # A browser always sends Origin on these; it must be this very server.
                origin = self.headers.get("Origin")
                if origin != f"http://{self.headers.get('Host')}":
                    self._forbidden("foreign Origin")
                    return
            try:
                body = control.decode(raw) if raw else None
                message = route(method, url.path, body)
            except ContractError as exc:
                self._reply(control.error(None, exc.code, exc.message))
                return
            self._reply(service.handle(message))

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

    server = ThreadingHTTPServer((bind, port), Handler)
    server.daemon_threads = True
    return server
