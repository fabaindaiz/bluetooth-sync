"""Servidor del panel: HTTP para la interfaz y un WebSocket para el estado, los logs y
las órdenes (docs/research/09 §3 y §7.2).

Un solo publicador arma el snapshot en cada tick, el mismo JSON para todos. Cada
cliente tiene su propio cursor de logs y una señal de "hay novedades": si es lento,
se salta snapshots y recupera los logs desde su cursor; si se cae, se lo saca. Nada
de eso puede detener al motor (tarjeta best-effort-side-channels).

Premisa: un motor por proceso. El lock de órdenes vale solo dentro de este proceso
(tarjeta in-process-guarantees).
"""

import asyncio
import contextlib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from aiohttp import WSMsgType, web

from aurasync.engine.base import CommandError, Engine, parse_command
from aurasync.logbuffer import LogBuffer
from aurasync.panel.auth import COOKIE, host_allowed, origin_allowed, token_matches
from aurasync.panel.pairing import qr_svg

log = logging.getLogger("aurasync.panel")

STATIC = Path(__file__).parent / "static"
STATIC_FILES = {"app.js": "text/javascript", "styles.css": "text/css"}
LOG_BACKLOG = 500

ENGINE_KEY = web.AppKey[Engine]("engine")
TOKEN_KEY = web.AppKey("token", str)
ALLOWED_KEY = web.AppKey[frozenset[str]]("allowed_hosts")
PAIRING_KEY = web.AppKey[str | None]("pairing_url")
INTERVAL_KEY = web.AppKey("publish_interval", float)
CLIENTS_KEY = web.AppKey[set["_Client"]]("clients")
LOCK_KEY = web.AppKey("lock", asyncio.Lock)
LOGS_KEY = web.AppKey("logs", LogBuffer)
TASKS_KEY = web.AppKey[list[asyncio.Task[None]]]("tasks")

_UNAUTHORIZED = """<!doctype html><html lang="es"><meta charset="utf-8">
<title>aurasync</title><body>
<h1>Falta el token</h1>
<p>Abre la URL con el token que imprimió <code>aurasync panel</code> en la terminal,
o escanea su QR. El token cambia cada vez que se reinicia el panel.</p></body></html>"""


@dataclass(eq=False)
class _Client:
    ws: web.WebSocketResponse
    log_seq: int
    snapshot: str | None = None
    wake: asyncio.Event = field(default_factory=asyncio.Event)


def _security_headers(response: web.StreamResponse, host: str) -> None:
    response.headers["Content-Security-Policy"] = (
        f"default-src 'self'; img-src 'self' data: blob:; connect-src 'self' ws://{host}; "
        "frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
    )
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "no-store"


@web.middleware
async def _guard(request: web.Request, handler):
    if not host_allowed(request.headers.get("Host"), request.app[ALLOWED_KEY]):
        return web.Response(status=403, text="Host no permitido")
    try:
        response = await handler(request)
    except web.HTTPException as raised:
        _security_headers(raised, request.host)
        raise
    if not isinstance(response, web.WebSocketResponse):
        _security_headers(response, request.host)
    return response


def _authorized(request: web.Request) -> bool:
    return token_matches(request.app[TOKEN_KEY], request.cookies.get(COOKIE))


def _unauthorized() -> web.Response:
    return web.Response(status=401, text=_UNAUTHORIZED, content_type="text/html")


async def _index(request: web.Request) -> web.StreamResponse:
    given = request.query.get("t")
    if given is not None:
        if not token_matches(request.app[TOKEN_KEY], given):
            return _unauthorized()
        redirect = web.HTTPFound("/")
        redirect.set_cookie(COOKIE, given, httponly=True, samesite="Strict", path="/")
        raise redirect
    if not _authorized(request):
        return _unauthorized()
    return web.FileResponse(STATIC / "index.html")


async def _static(request: web.Request) -> web.StreamResponse:
    if not _authorized(request):
        return _unauthorized()
    name = request.match_info["name"]
    if name not in STATIC_FILES:
        raise web.HTTPNotFound
    return web.FileResponse(STATIC / name, headers={"Content-Type": STATIC_FILES[name]})


async def _pairing(request: web.Request) -> web.StreamResponse:
    if not _authorized(request):
        return _unauthorized()
    url = request.app[PAIRING_KEY]
    if url is None:
        raise web.HTTPNotFound
    return web.Response(text=qr_svg(url), content_type="image/svg+xml")


async def _reply(ws: web.WebSocketResponse, request_id, error: str | None = None) -> None:
    message = {"type": "reply", "id": request_id, "ok": error is None}
    if error is not None:
        message["error"] = error
    await ws.send_json(message)


async def _handle_message(app: web.Application, ws: web.WebSocketResponse, text: str) -> None:
    try:
        raw = json.loads(text)
    except json.JSONDecodeError:
        log.warning("mensaje que no es JSON")
        await _reply(ws, None, "el mensaje no es JSON")
        return
    if not isinstance(raw, dict):
        await _reply(ws, None, "el mensaje tiene que ser un objeto")
        return
    request_id = raw.get("id")
    shown = json.dumps(raw.get("args", {}), ensure_ascii=False)
    try:
        command = parse_command(raw)
        async with app[LOCK_KEY]:
            await app[ENGINE_KEY].apply(command)
    except CommandError as error:
        log.warning("orden %s %s rechazada: %s", raw.get("cmd"), shown, error)
        await _reply(ws, request_id, str(error))
    except Exception:
        log.exception("el motor falló al aplicar %s %s", raw.get("cmd"), shown)
        await _reply(ws, request_id, "error interno del motor; revisa el log")
    else:
        log.info("orden %s %s", command.name, shown)
        await _reply(ws, request_id)


async def _send_updates(app: web.Application, client: _Client) -> None:
    buffer = app[LOGS_KEY]
    try:
        while True:
            await client.wake.wait()
            client.wake.clear()
            records = buffer.since(client.log_seq)
            if records:
                gap = records[0]["seq"] > client.log_seq + 1
                client.log_seq = records[-1]["seq"]
                await client.ws.send_json({"type": "logs", "records": records, "gap": gap})
            if client.snapshot is not None:
                payload, client.snapshot = client.snapshot, None
                await client.ws.send_str(payload)
    except (ConnectionError, RuntimeError):
        log.info("un cliente del panel se desconectó mientras recibía")


async def _websocket(request: web.Request) -> web.StreamResponse:
    if not _authorized(request):
        return _unauthorized()
    if not origin_allowed(request.headers.get("Origin"), request.headers.get("Host")):
        return web.Response(status=403, text="Origin no permitido")

    ws = web.WebSocketResponse(heartbeat=10)
    await ws.prepare(request)
    app = request.app
    buffer = app[LOGS_KEY]
    backlog = buffer.tail(LOG_BACKLOG)
    client = _Client(ws=ws, log_seq=backlog[-1]["seq"] if backlog else buffer.last_seq)
    sender = None
    try:
        await ws.send_json({"type": "hello", "engine": app[ENGINE_KEY].kind, "pairing": app[PAIRING_KEY] is not None})
        await ws.send_json({"type": "logs", "records": backlog, "reset": True})
        app[CLIENTS_KEY].add(client)
        sender = asyncio.create_task(_send_updates(app, client))
        log.info("cliente conectado desde %s", request.remote)
        async for message in ws:
            if message.type == WSMsgType.TEXT:
                await _handle_message(app, ws, message.data)
    except (ConnectionError, RuntimeError):
        pass
    finally:
        app[CLIENTS_KEY].discard(client)
        if sender is not None:
            sender.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await sender
        log.info("cliente desconectado (%s)", request.remote)
    return ws


async def _publish(app: web.Application) -> None:
    while True:
        try:
            payload = '{"type":"snapshot","data":' + app[ENGINE_KEY].snapshot().to_json() + "}"
        except Exception:
            log.exception("no se pudo armar el snapshot")
            await asyncio.sleep(1)
            continue
        for client in list(app[CLIENTS_KEY]):
            client.snapshot = payload
            client.wake.set()
        await asyncio.sleep(app[INTERVAL_KEY])


async def _start(app: web.Application) -> None:
    app[LOGS_KEY].attach()  # no hace nada si quien creó el búfer ya lo enganchó
    tasks = [asyncio.create_task(_publish(app))]
    run = getattr(app[ENGINE_KEY], "run", None)
    if run is not None:
        tasks.append(asyncio.create_task(run()))
    app[TASKS_KEY] = tasks


async def _stop(app: web.Application) -> None:
    for task in app[TASKS_KEY]:
        task.cancel()
    for task in app[TASKS_KEY]:
        with contextlib.suppress(asyncio.CancelledError):
            await task
    app[LOGS_KEY].detach()


def create_app(
    engine: Engine,
    *,
    token: str,
    allowed_hosts: frozenset[str],
    pairing_url: str | None = None,
    publish_interval: float = 0.1,
    logs: LogBuffer | None = None,
) -> web.Application:
    app = web.Application(middlewares=[_guard])
    app[ENGINE_KEY] = engine
    app[TOKEN_KEY] = token
    app[ALLOWED_KEY] = allowed_hosts
    app[PAIRING_KEY] = pairing_url
    app[INTERVAL_KEY] = publish_interval
    app[CLIENTS_KEY] = set()
    app[LOCK_KEY] = asyncio.Lock()
    app[LOGS_KEY] = logs if logs is not None else LogBuffer()
    app.router.add_get("/", _index)
    app.router.add_get("/static/{name}", _static)
    app.router.add_get("/pairing.svg", _pairing)
    app.router.add_get("/ws", _websocket)
    app.on_startup.append(_start)
    app.on_cleanup.append(_stop)
    return app
