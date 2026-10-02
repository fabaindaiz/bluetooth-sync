"""Clients, pairing, CORS and stream tickets over a real HTTP server (d-7c8794-37f9bc)."""

import http.client
import json
import os
import threading
import time

import pytest

from aurasync import access as access_module
from aurasync import rest as rest_module
from aurasync import service as service_module
from aurasync.access import Access
from aurasync.clients import ClientStore
from aurasync.config import Instalacion, Parlante
from aurasync.rest import make_server
from aurasync.service import ConfigError, Service, load_config
from tests.test_service import FakeSession

TOKEN = "t" * 43
PAGES = "https://fabaindaiz.github.io"


@pytest.fixture
def svc_with_fake(tmp_path):
    FakeSession.instances = []
    FakeSession.fail_open = None
    FakeSession.fail_after = None
    Instalacion(parlantes=[Parlante("Go 4 Red", "s0"), Parlante("Go 4 Blue", "s1")]).guardar(tmp_path / "i.json")
    svc = Service(tmp_path / "i.json", tmp_path / "p.json", session_factory=FakeSession, log=lambda _: None)
    engine = threading.Thread(target=svc.run, daemon=True)
    engine.start()
    yield svc
    svc.handle({"v": 1, "op": "shutdown"})
    engine.join(timeout=5)
    svc.close()


class Served:
    def __init__(self, svc, tmp_path, window_s=0.0, master=TOKEN):
        self.store = ClientStore(tmp_path / "clients.json")
        self.access = Access(master, self.store, window_s=window_s)
        svc.access = self.access
        self.httpd = make_server(svc, "127.0.0.1", 0, master, access=self.access)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.port = self.httpd.server_address[1]

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def call(self, method, path, body=None, token=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h = dict(headers or {})
        if token:
            h["Authorization"] = f"Bearer {token}"
        data = json.dumps(body).encode() if body is not None else None
        conn.request(method, path, body=data, headers=h)
        response = conn.getresponse()
        raw = response.read()
        conn.close()
        try:
            reply = json.loads(raw) if raw else None
        except ValueError:
            reply = raw
        return response, reply


@pytest.fixture
def served(svc_with_fake, tmp_path):
    s = Served(svc_with_fake, tmp_path)
    yield s
    s.close()


def client(served, scope):
    return served.store.add(f"{scope} phone", scope)[1]


# -- hello -------------------------------------------------------------------------------


def test_hello_needs_nothing_and_says_nothing_secret(served):
    response, reply = served.call("GET", "/v1/hello")
    assert response.status == 200
    result = reply["result"]
    assert result["service"] == "aurasync"
    assert result["contract"] == 1
    assert result["tls"] == {"enabled": False}
    assert set(result["pairing"]) == {"accepting", "first_window_s"}
    assert TOKEN not in json.dumps(reply)


# -- scopes ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("scope", "method", "path", "body", "status"),
    [
        ("read", "GET", "/v1/state", None, 200),
        ("read", "PATCH", "/v1/global", {"volume_db": -30}, 403),
        ("control", "PATCH", "/v1/global", {"volume_db": -30}, 200),
        ("control", "POST", "/v1/command", {"v": 1, "op": "clients"}, 403),
        ("control", "GET", "/v1/clients", None, 403),
        ("admin", "GET", "/v1/clients", None, 200),
        ("read", "POST", "/v1/command", {"v": 1, "op": "some_future_op"}, 403),
    ],
)
def test_each_operation_needs_its_scope(served, scope, method, path, body, status):
    response, reply = served.call(method, path, body, token=client(served, scope))
    assert response.status == status, reply
    if status == 403:
        assert reply["error"]["code"] == "forbidden"


def test_the_master_token_is_admin_and_rotating_it_keeps_the_clients(svc_with_fake, tmp_path):
    first = Served(svc_with_fake, tmp_path)
    phone = client(first, "control")
    assert first.call("GET", "/v1/clients", token=TOKEN)[0].status == 200
    first.close()
    svc_with_fake.access = None
    rotated = Served(svc_with_fake, tmp_path, master="r" * 43)
    try:
        assert rotated.call("PATCH", "/v1/global", {"volume_db": -25}, token=phone)[0].status == 200
        assert rotated.call("GET", "/v1/state", token=TOKEN)[0].status == 401
    finally:
        rotated.close()


def test_a_revoked_client_is_refused(served):
    phone = client(served, "control")
    cid = phone.split("_")[1]
    assert served.call("DELETE", f"/v1/clients/{cid}", token=TOKEN)[1]["result"] == {"revoked": cid}
    assert served.call("GET", "/v1/state", token=phone)[0].status == 401


def test_failed_attempts_block_the_address_even_for_the_right_token(served):
    for _ in range(access_module.FREE_FAILURES):
        assert served.call("GET", "/v1/state", token="x" * 43)[0].status == 401
    assert served.call("GET", "/v1/state", token=TOKEN)[0].status == 200, "free failures"
    for _ in range(access_module.FREE_FAILURES + 1):
        served.call("GET", "/v1/state", token="x" * 43)
    response, reply = served.call("GET", "/v1/state", token=TOKEN)
    assert response.status == 429
    assert reply["error"]["code"] == "rate_limited"
    assert int(response.getheader("Retry-After")) >= 1


def test_a_missing_credential_is_not_a_failed_attempt(served):
    for _ in range(access_module.FREE_FAILURES * 3):
        assert served.call("GET", "/v1/state")[0].status == 401
    assert served.call("GET", "/v1/state", token=TOKEN)[0].status == 200


# -- pairing over HTTP -------------------------------------------------------------------


def test_pairing_in_the_first_client_window(svc_with_fake, tmp_path):
    s = Served(svc_with_fake, tmp_path, window_s=600)
    try:
        assert s.call("GET", "/v1/hello")[1]["result"]["pairing"]["accepting"]
        response, reply = s.call("POST", "/v1/pair/request", {"name": "Fabián's Pixel"})
        assert response.status == 200
        request_id = reply["result"]["id"]
        _, got = s.call("GET", f"/v1/pair/{request_id}")
        token = got["result"]["token"]
        assert got["result"]["client"]["scope"] == "admin"
        assert "token" not in s.call("GET", f"/v1/pair/{request_id}")[1]["result"], "once"
        assert s.call("GET", "/v1/clients", token=token)[0].status == 200
        _, second = s.call("POST", "/v1/pair/request", {"name": "Guest"})
        assert second["result"]["status"] == "pending"
    finally:
        s.close()


def test_an_admin_approves_over_rest(served):
    _, reply = served.call("POST", "/v1/pair/request", {"name": "Tablet", "scope": "control"})
    request_id = reply["result"]["id"]
    assert served.call("GET", f"/v1/pair/{request_id}")[1]["result"] == {"status": "pending"}
    pending = served.call("GET", "/v1/pair", token=TOKEN)[1]["result"]["requests"]
    assert pending[0]["check"] == reply["result"]["check"]
    response, approved = served.call("POST", f"/v1/pair/{request_id}/approve", {"scope": "read"}, token=TOKEN)
    assert response.status == 200, approved
    got = served.call("GET", f"/v1/pair/{request_id}")[1]["result"]
    assert got["client"]["scope"] == "read"
    assert served.call("GET", "/v1/state", token=got["token"])[0].status == 200


def test_a_denied_request_gets_no_token(served):
    _, reply = served.call("POST", "/v1/pair/request", {"name": "Stranger"})
    request_id = reply["result"]["id"]
    assert served.call("POST", f"/v1/pair/{request_id}/deny", token=TOKEN)[0].status == 200
    assert served.call("GET", f"/v1/pair/{request_id}")[1]["result"] == {"status": "denied"}


def test_a_control_client_cannot_approve(served):
    _, reply = served.call("POST", "/v1/pair/request", {"name": "Stranger"})
    phone = client(served, "control")
    response, _ = served.call("POST", f"/v1/pair/{reply['result']['id']}/approve", token=phone)
    assert response.status == 403


def test_the_code_from_the_terminal(served):
    code = served.call("POST", "/v1/pair/code", token=TOKEN)[1]["result"]["code"]
    _, reply = served.call("POST", "/v1/pair/request", {"name": "New phone", "scope": "admin", "code": code})
    assert reply["result"]["status"] == "approved"


@pytest.mark.parametrize(
    "body",
    [None, {"name": ""}, {"name": "x", "token": "y"}, {"name": "x", "code": 123456}, {"name": "x", "scope": "root"}],
)
def test_a_malformed_pairing_request_is_400(served, body):
    assert served.call("POST", "/v1/pair/request", body)[0].status == 400


def test_guessing_request_ids_counts_as_failed_attempts(served):
    for _ in range(access_module.FREE_FAILURES + 1):
        assert served.call("GET", "/v1/pair/" + "z" * 24)[0].status == 404
    assert served.call("GET", "/v1/pair/" + "z" * 24)[0].status == 429


# -- CORS --------------------------------------------------------------------------------


def test_the_preflight_of_the_pwa(served):
    headers = {
        "Origin": PAGES,
        "Access-Control-Request-Method": "PATCH",
        "Access-Control-Request-Headers": "authorization, content-type",
        "Access-Control-Request-Private-Network": "true",
    }
    response, _ = served.call("OPTIONS", "/v1/global", headers=headers)
    assert response.status == 204
    assert response.getheader("Access-Control-Allow-Origin") == PAGES
    assert "PATCH" in response.getheader("Access-Control-Allow-Methods")
    assert response.getheader("Access-Control-Allow-Headers") == "Authorization, Content-Type"
    assert response.getheader("Access-Control-Allow-Private-Network") == "true"
    assert response.getheader("Access-Control-Allow-Credentials") is None
    assert response.getheader("Vary") == "Origin"


def test_another_origin_gets_no_cors_and_cannot_order(served):
    evil = {"Origin": "https://evil.example"}
    response, _ = served.call("OPTIONS", "/v1/global", headers=evil)
    assert response.status == 403
    assert response.getheader("Access-Control-Allow-Origin") is None
    response, _ = served.call("PATCH", "/v1/global", {"volume_db": -30}, token=TOKEN, headers=evil)
    assert response.status == 403


def test_the_pwa_reads_replies_with_its_bearer(served):
    phone = client(served, "control")
    response, _ = served.call("GET", "/v1/state", token=phone, headers={"Origin": PAGES})
    assert response.status == 200
    assert response.getheader("Access-Control-Allow-Origin") == PAGES
    assert response.getheader("Access-Control-Allow-Credentials") is None
    response, _ = served.call("GET", "/v1/state", headers={"Origin": PAGES})
    assert response.status == 401
    assert response.getheader("Access-Control-Allow-Origin") == PAGES, "the PWA can read the error"


def test_vite_in_development_is_allowed(served):
    response, _ = served.call("GET", "/v1/hello", headers={"Origin": "http://localhost:5173"})
    assert response.getheader("Access-Control-Allow-Origin") == "http://localhost:5173"


def test_the_host_check_stays(served):
    response, _ = served.call("GET", "/v1/hello", headers={"Host": f"evil.example:{served.port}"})
    assert response.status == 403


# -- stream tickets ----------------------------------------------------------------------


def open_stream(served, path, token=None):
    conn = http.client.HTTPConnection("127.0.0.1", served.port, timeout=10)
    conn.request("GET", path, headers={"Authorization": f"Bearer {token}"} if token else {})
    return conn, conn.getresponse()


def ticket(served, token):
    response, reply = served.call("POST", "/v1/stream/ticket", token=token)
    assert response.status == 200, reply
    return reply["result"]["ticket"]


def test_a_ticket_opens_the_stream_once(served):
    phone = client(served, "read")
    t = ticket(served, phone)
    conn, response = open_stream(served, f"/v1/stream?ticket={t}")
    assert response.status == 200
    assert response.getheader("Content-Type").startswith("text/event-stream")
    conn.close()
    conn, response = open_stream(served, f"/v1/stream?ticket={t}")
    assert response.status == 401
    conn.close()


def test_an_expired_ticket_and_no_ticket_are_refused(served, monkeypatch):
    phone = client(served, "read")
    monkeypatch.setattr(access_module, "TICKET_TTL_S", 0.05)
    t = ticket(served, phone)
    time.sleep(0.1)
    conn, response = open_stream(served, f"/v1/stream?ticket={t}")
    assert response.status == 401
    conn.close()
    conn, response = open_stream(served, "/v1/stream")
    assert response.status == 401
    conn.close()


def test_a_ticket_only_opens_the_stream(served):
    t = ticket(served, TOKEN)
    assert served.call("GET", f"/v1/state?ticket={t}")[0].status == 401


def test_the_stream_also_takes_a_bearer(served):
    conn, response = open_stream(served, "/v1/stream", token=client(served, "read"))
    assert response.status == 200
    conn.close()


def test_revoking_a_client_ends_its_open_stream(served):
    phone = client(served, "read")
    conn, response = open_stream(served, "/v1/stream", token=phone)
    assert response.status == 200
    response.fp.readline()
    served.store.revoke(phone.split("_")[1])
    end = time.monotonic() + 3
    ended = False
    while time.monotonic() < end:
        if response.fp.readline() == b"":
            ended = True
            break
    conn.close()
    assert ended, "the stream outlived the revocation"


def test_a_revoked_clients_ticket_is_refused(served):
    phone = client(served, "read")
    t = ticket(served, phone)
    served.store.revoke(phone.split("_")[1])
    conn, response = open_stream(served, f"/v1/stream?ticket={t}")
    assert response.status == 401
    conn.close()


# -- service.json ------------------------------------------------------------------------


def test_a_new_config_turns_tls_on_and_an_old_one_keeps_it_off(tmp_path):
    assert load_config(tmp_path / "new" / "service.json").tls is True
    old = tmp_path / "service.json"
    old.write_text(json.dumps({"token": "o" * 43}))
    os.chmod(old, 0o600)
    config = load_config(old)
    assert config.tls is False
    assert config.https_port == 8443
    assert PAGES in config.panel_origins


@pytest.mark.parametrize(
    "extra",
    [
        {"tls": "yes"},
        {"https_port": 8731},
        {"https_port": 70000},
        {"panel_origins": "https://x.github.io"},
        {"panel_origins": ["http://evil.example"]},
        {"panel_origins": ["https://x.github.io/aurasync/"]},
        {"pair_window_s": -1},
        {"mdns": 1},
    ],
)
def test_bad_remote_keys_refuse_to_start(tmp_path, extra):
    path = tmp_path / "service.json"
    path.write_text(json.dumps({"token": "o" * 43, **extra}))
    os.chmod(path, 0o600)
    with pytest.raises(ConfigError):
        load_config(path)


def test_the_service_answers_access_ops_only_with_a_store(tmp_path):
    Instalacion(parlantes=[Parlante("A", "s0")]).guardar(tmp_path / "i.json")
    svc = Service(tmp_path / "i.json", tmp_path / "p.json", session_factory=FakeSession, log=lambda _: None)
    try:
        assert svc.handle({"v": 1, "op": "clients"})["error"]["code"] == "unavailable"
        svc.access = Access(TOKEN)
        assert svc.handle({"v": 1, "op": "clients"})["ok"]
    finally:
        svc.close()


def test_serve_with_tls_listens_on_both_and_closes_both(tmp_path):
    import socket
    import ssl

    Instalacion(parlantes=[Parlante("A", "s0")]).guardar(tmp_path / "i.json")
    svc_with_fake = Service(tmp_path / "i.json", tmp_path / "p.json", session_factory=FakeSession, log=lambda _: None)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        https_port = probe.getsockname()[1]
    config = service_module.ServiceConfig(bind="127.0.0.1", port=0, token=TOKEN, tls=True, https_port=https_port)
    seen = {}

    def poke():
        try:
            end = time.monotonic() + 10
            while svc_with_fake.access is None or not svc_with_fake.access.tls:
                if time.monotonic() > end:
                    return
                time.sleep(0.05)
            root = tmp_path / "cfg" / "tls" / "root.pem"
            context = ssl.create_default_context(cafile=str(root))
            conn = http.client.HTTPSConnection("127.0.0.1", https_port, context=context, timeout=5)
            conn.request("GET", "/v1/hello")
            seen["hello"] = json.loads(conn.getresponse().read())["result"]
            conn.close()
        finally:
            svc_with_fake.handle({"v": 1, "op": "shutdown"})

    threading.Thread(target=poke, daemon=True).start()
    lines = []
    try:
        code = service_module.serve(
            svc_with_fake, config, announce=lines.append, show_token=False, directory=tmp_path / "cfg"
        )
    finally:
        svc_with_fake.close()
    assert code == 0
    assert seen["hello"]["tls"]["enabled"]
    assert seen["hello"]["tls"]["port"] == https_port
    assert any("root certificate sha256" in line for line in lines)
    assert (tmp_path / "cfg" / "clients.json").exists()
    with socket.socket() as again:
        again.bind(("127.0.0.1", https_port))  # the HTTPS port was closed


# -- the panel's pairing QR (the PWA's link, never a token) -----------------------------------


def test_the_pairing_link_carries_the_device_and_its_root_not_a_token():
    tls = {"enabled": True, "port": 8443, "root_sha256": "AB:CD:" + "EF:" * 29 + "01"}
    link = rest_module.pairing_link(["http://127.0.0.1:8731", "http://192.0.2.7:8731"], tls)
    assert link == f"{rest_module.PWA_URL}#d=192.0.2.7:8443&fp=ABCD{'EF' * 29}01"
    assert rest_module.pairing_link(["http://127.0.0.1:8731"], tls) is None
    assert rest_module.pairing_link(["http://192.0.2.7:8731"], None) is None
    assert rest_module.pairing_link(["http://[fd00::7]:8731"], tls).startswith(
        f"{rest_module.PWA_URL}#d=[fd00::7]:8443&"
    )


def test_the_pairing_svg_is_the_pwa_link_and_needs_https(served, svc_with_fake):
    svc_with_fake.pairing = {"urls": ["http://192.0.2.7:8731"]}
    response, reply = served.call("GET", "/pairing.svg", token=TOKEN)
    assert response.status == 404
    served.access.tls = {"enabled": True, "port": 8443, "root_sha256": "AB:CD"}
    response, reply = served.call("GET", "/pairing.svg", token=TOKEN)
    assert response.status == 200
    assert reply == rest_module.qr_svg(f"{rest_module.PWA_URL}#d=192.0.2.7:8443&fp=ABCD").encode()
    assert reply != rest_module.qr_svg(f"http://192.0.2.7:8731/?t={TOKEN}").encode()
