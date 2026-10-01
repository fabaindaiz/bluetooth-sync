"""Servidor del panel (docs/research/09 §6, §8 y §9). Corre en 127.0.0.1, dentro del
mismo proceso del test."""

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import pytest
from aiohttp.test_utils import TestClient, TestServer

from aurasync.engine.base import Command
from aurasync.engine.simulated import SimulatedEngine
from aurasync.logbuffer import LogBuffer
from aurasync.panel.auth import COOKIE, new_token, token_matches
from aurasync.panel.pairing import pairing_url, qr_svg, qr_terminal
from aurasync.panel.server import create_app

TOKEN = "token-de-prueba-0123456789"
ALLOWED = frozenset({"127.0.0.1", "localhost"})


def engine() -> SimulatedEngine:
    return SimulatedEngine(now=lambda: 1000.0, wallclock=lambda: datetime(2026, 10, 1, tzinfo=UTC))


@asynccontextmanager
async def panel(the_engine=None, *, pairing=None):
    app = create_app(
        the_engine or engine(),
        token=TOKEN,
        allowed_hosts=ALLOWED,
        pairing_url=pairing,
        publish_interval=0.01,
    )
    async with TestClient(TestServer(app, host="127.0.0.1")) as client:
        yield client


def cookie() -> dict[str, str]:
    return {"Cookie": f"{COOKIE}={TOKEN}"}


def origin(client: TestClient) -> dict[str, str]:
    return {"Origin": f"http://127.0.0.1:{client.port}", **cookie()}


# -- auth -----------------------------------------------------------------------


def test_tokens_are_long_and_random():
    a, b = new_token(), new_token()
    assert a != b
    assert len(a) >= 22  # 16 bytes en base64 urlsafe


def test_token_comparison():
    assert token_matches(TOKEN, TOKEN)
    assert not token_matches(TOKEN, TOKEN[:-1])
    assert not token_matches(TOKEN, None)
    assert not token_matches(TOKEN, "")


# -- emparejamiento -------------------------------------------------------------


def test_pairing_url_and_qr():
    url = pairing_url("192.168.1.20", 8737, TOKEN)
    assert url == f"http://192.168.1.20:8737/?t={TOKEN}"
    svg_text = qr_svg(url)
    assert svg_text.lstrip().startswith("<svg")
    # Se sirve como imagen suelta (<img src="/pairing.svg">): sin el namespace no se dibuja.
    assert 'xmlns="http://www.w3.org/2000/svg"' in svg_text
    assert qr_terminal(url).strip()


# -- HTTP -----------------------------------------------------------------------


def test_index_without_token_is_refused():
    async def go():
        async with panel() as client:
            resp = await client.get("/")
            assert resp.status == 401
            assert "token" in (await resp.text())

    asyncio.run(go())


def test_wrong_token_in_url_is_refused():
    async def go():
        async with panel() as client:
            resp = await client.get("/?t=equivocado", allow_redirects=False)
            assert resp.status == 401

    asyncio.run(go())


def test_token_in_url_becomes_a_strict_cookie_and_disappears_from_the_url():
    async def go():
        async with panel() as client:
            resp = await client.get(f"/?t={TOKEN}", allow_redirects=False)
            assert resp.status == 302
            assert resp.headers["Location"] == "/"
            set_cookie = resp.headers["Set-Cookie"]
            assert f"{COOKIE}={TOKEN}" in set_cookie
            assert "HttpOnly" in set_cookie
            assert "SameSite=Strict" in set_cookie

    asyncio.run(go())


def test_index_with_cookie_serves_the_panel_with_security_headers():
    async def go():
        async with panel() as client:
            resp = await client.get("/", headers=cookie())
            assert resp.status == 200
            assert "aurasync" in await resp.text()
            assert "default-src 'self'" in resp.headers["Content-Security-Policy"]
            assert resp.headers["X-Frame-Options"] == "DENY"
            assert resp.headers["Referrer-Policy"] == "no-referrer"

    asyncio.run(go())


def test_static_files_need_the_cookie():
    async def go():
        async with panel() as client:
            assert (await client.get("/static/app.js")).status == 401
            assert (await client.get("/static/app.js", headers=cookie())).status == 200
            assert (await client.get("/static/../server.py", headers=cookie())).status == 404

    asyncio.run(go())


def test_foreign_host_header_is_refused_even_with_the_cookie():
    async def go():
        async with panel() as client:
            resp = await client.get("/", headers={"Host": "evil.example", **cookie()})
            assert resp.status == 403

    asyncio.run(go())


def test_pairing_qr_exists_only_with_lan_enabled():
    async def go():
        async with panel() as client:
            assert (await client.get("/pairing.svg", headers=cookie())).status == 404
        async with panel(pairing=f"http://192.168.1.20:8737/?t={TOKEN}") as client:
            resp = await client.get("/pairing.svg", headers=cookie())
            assert resp.status == 200
            assert resp.content_type == "image/svg+xml"

    asyncio.run(go())


# -- WebSocket ------------------------------------------------------------------


def test_websocket_without_origin_is_refused():
    async def go():
        async with panel() as client:
            resp = await client.get("/ws", headers=cookie())
            assert resp.status == 403

    asyncio.run(go())


def test_websocket_from_a_foreign_origin_is_refused():
    async def go():
        async with panel() as client:
            resp = await client.get("/ws", headers={"Origin": "http://evil.example", **cookie()})
            assert resp.status == 403

    asyncio.run(go())


async def receive(ws, kind: str, *, limit: int = 300) -> dict:
    """El siguiente mensaje de un tipo. Falla, en vez de colgarse, si no llega."""
    for _ in range(limit):
        message = json.loads((await ws.receive(timeout=2)).data)
        if message["type"] == kind:
            return message
    msg = f"no llegó ningún mensaje {kind!r} en {limit} mensajes"
    raise AssertionError(msg)


def test_websocket_says_hello_then_streams_snapshots():
    async def go():
        async with panel() as client, client.ws_connect("/ws", headers=origin(client)) as ws:
            hello = await receive(ws, "hello")
            assert hello["engine"] == "simulated"
            assert hello["pairing"] is False
            first = await receive(ws, "snapshot")
            second = await receive(ws, "snapshot")
            assert second["data"]["seq"] > first["data"]["seq"]

    asyncio.run(go())


def test_websocket_command_ok_and_refused():
    async def go():
        async with panel() as client, client.ws_connect("/ws", headers=origin(client)) as ws:
            await ws.send_json({"id": 1, "cmd": "start"})
            assert await receive(ws, "reply") == {"type": "reply", "id": 1, "ok": True}
            await ws.send_json({"id": 2, "cmd": "tone", "args": {"channel": "FC", "seconds": 1}})
            reply = await receive(ws, "reply")
            assert reply["id"] == 2
            assert reply["ok"] is False
            assert "FC" in reply["error"]
            await ws.send_str("esto no es json")
            assert (await receive(ws, "reply"))["ok"] is False

    asyncio.run(go())


def test_a_client_that_drops_does_not_stop_the_others():
    async def go():
        async with panel() as client:
            alive = await client.ws_connect("/ws", headers=origin(client))
            dying = await client.ws_connect("/ws", headers=origin(client))
            await dying.close()
            await receive(alive, "snapshot")
            seq = (await receive(alive, "snapshot"))["data"]["seq"]
            assert (await receive(alive, "snapshot"))["data"]["seq"] > seq
            await alive.close()

    asyncio.run(go())


class ExplodingEngine(SimulatedEngine):
    async def apply(self, command: Command) -> None:
        if command.name == "start":
            msg = "fallo inesperado"
            raise RuntimeError(msg)
        await super().apply(command)


def test_an_engine_failure_is_reported_and_the_panel_keeps_serving():
    async def go():
        broken = ExplodingEngine(now=lambda: 1000.0)
        async with panel(broken) as client, client.ws_connect("/ws", headers=origin(client)) as ws:
            await ws.send_json({"id": 1, "cmd": "start"})
            reply = await receive(ws, "reply")
            assert reply["ok"] is False
            assert "interno" in reply["error"]
            await ws.send_json({"id": 2, "cmd": "set_master", "args": {"db": -30}})
            assert (await receive(ws, "reply"))["ok"] is True

    asyncio.run(go())


@pytest.mark.parametrize("path", ["/", "/ws", "/pairing.svg", "/static/app.js"])
def test_every_route_checks_the_host(path):
    async def go():
        async with panel() as client:
            resp = await client.get(path, headers={"Host": "rebind.example", **origin(client)})
            assert resp.status == 403

    asyncio.run(go())


# -- logs -----------------------------------------------------------------------


def test_logs_arrive_as_a_backlog_and_then_as_new_lines():
    async def go():
        async with panel() as client, client.ws_connect("/ws", headers=origin(client)) as ws:
            backlog = await receive(ws, "logs")
            assert backlog["reset"] is True
            await ws.send_json({"id": 1, "cmd": "set_mode", "args": {"mode": "lcrs"}})
            await receive(ws, "reply")
            async with client.ws_connect("/ws", headers=origin(client)) as late:
                late_backlog = (await receive(late, "logs"))["records"]
                assert any("set_mode" in r["message"] for r in late_backlog)
            await ws.send_json({"id": 2, "cmd": "set_master", "args": {"db": -33}})
            await receive(ws, "reply")
            seen = []
            for _ in range(20):
                message = await receive(ws, "logs")
                seen += message["records"]
                if any("set_master" in r["message"] for r in seen):
                    break
            line = next(r for r in seen if "set_master" in r["message"])
            assert line["service"] == "panel"
            assert line["seq"] > max((r["seq"] for r in backlog["records"]), default=0)

    asyncio.run(go())


def test_refused_commands_are_logged_as_warnings():
    async def go():
        async with panel() as client, client.ws_connect("/ws", headers=origin(client)) as ws:
            await receive(ws, "logs")
            await ws.send_json({"id": 1, "cmd": "service_stop", "args": {"name": "panel"}})
            assert (await receive(ws, "reply"))["ok"] is False
            for _ in range(20):
                records = (await receive(ws, "logs"))["records"]
                warning = [r for r in records if r["level"] == "warning" and "service_stop" in r["message"]]
                if warning:
                    break
            assert warning

    asyncio.run(go())


def test_the_panel_detaches_its_log_handler_on_cleanup():
    async def go():
        before = list(logging.getLogger("aurasync").handlers)
        async with panel():
            assert len(logging.getLogger("aurasync").handlers) == len(before) + 1
        assert logging.getLogger("aurasync").handlers == before

    asyncio.run(go())


def test_lines_logged_before_the_panel_starts_reach_the_backlog():
    async def go():
        buffer = LogBuffer()
        buffer.attach()
        try:
            the_engine = engine()  # anota "motor simulado listo" al crearse
            app = create_app(the_engine, token=TOKEN, allowed_hosts=ALLOWED, logs=buffer, publish_interval=0.01)
            async with (
                TestClient(TestServer(app, host="127.0.0.1")) as client,
                client.ws_connect("/ws", headers=origin(client)) as ws,
            ):
                records = (await receive(ws, "logs"))["records"]
                assert any("motor simulado listo" in r["message"] for r in records)
        finally:
            buffer.detach()

    asyncio.run(go())
