"""A real HTTP server on 127.0.0.1 and a free port, over the service with a fake session."""

import http.client
import json
import threading
from urllib.parse import quote

import pytest

from aurasync.config import Instalacion, Parlante
from aurasync.rest import make_server
from aurasync.service import Service
from tests.test_service import FakeSession

TOKEN = "t" * 43


@pytest.fixture
def server(tmp_path):
    FakeSession.instances = []
    FakeSession.fail_open = None
    FakeSession.fail_after = None
    Instalacion(parlantes=[Parlante("Go 4 Red", "s0"), Parlante("Go 4 Blue", "s1")]).guardar(tmp_path / "i.json")
    svc = Service(tmp_path / "i.json", tmp_path / "p.json", session_factory=FakeSession, log=lambda _: None)
    engine = threading.Thread(target=svc.run, daemon=True)
    engine.start()
    httpd = make_server(svc, "127.0.0.1", 0, TOKEN)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield httpd.server_address[1]
    svc.handle({"v": 1, "op": "shutdown"})
    engine.join(timeout=5)
    httpd.shutdown()
    httpd.server_close()


def call(port, method, path, body=None, token=TOKEN, raw=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    conn.request(method, path, body=data, headers=headers)
    response = conn.getresponse()
    reply = json.loads(response.read())
    conn.close()
    return response.status, reply


def test_no_token_or_wrong_token_is_401(server):
    assert call(server, "GET", "/v1/state", token=None)[0] == 401
    assert call(server, "GET", "/v1/state", token="x" * 43)[0] == 401


def test_token_in_the_query_for_a_link_opened_on_a_phone(server):
    status, reply = call(server, "GET", f"/v1/state?token={TOKEN}", token=None)
    assert status == 200
    assert reply["ok"]


def test_speaker_names_with_spaces_are_url_decoded(server):
    status, reply = call(server, "PATCH", f"/v1/speakers/{quote('Go 4 Red')}", {"pan": 0.4})
    assert status == 200, reply
    assert call(server, "GET", "/v1/state")[1]["result"]["speakers"][0]["pan"] == 0.4


@pytest.mark.parametrize(
    ("method", "path", "body", "status"),
    [
        ("PATCH", "/v1/speakers/Go%204%20Red", {"pan": 3}, 400),
        ("PATCH", "/v1/speakers/Nope", {"pan": 0}, 404),
        ("POST", "/v1/presets/nope/load", None, 404),
        ("GET", "/v1/nothing", None, 404),
    ],
)
def test_http_statuses(server, method, path, body, status):
    assert call(server, method, path, body)[0] == status


def test_malformed_json_is_400(server):
    status, reply = call(server, "PATCH", "/v1/global", raw=b"{nope")
    assert status == 400
    assert reply["error"]["code"] == "bad_request"


def test_start_twice_is_409(server):
    assert call(server, "POST", "/v1/session/start")[0] == 200
    assert call(server, "POST", "/v1/session/start")[0] == 409


@pytest.mark.parametrize(
    ("method", "path", "body", "message"),
    [
        ("PATCH", "/v1/global", {"volume_db": -25}, {"op": "set", "changes": {"volume_db": -25}}),
        (
            "PATCH",
            "/v1/speakers/Go%204%20Blue",
            {"gain_db": -2},
            {"op": "set", "speaker": "Go 4 Blue", "changes": {"gain_db": -2}},
        ),
        ("GET", "/v1/presets", None, {"op": "presets"}),
        ("PATCH", "/v1/presets/nope", {"new_name": "x"}, {"op": "preset_rename", "name": "nope", "new_name": "x"}),
        ("PATCH", "/v1/global", {"volume_db": 9}, {"op": "set", "changes": {"volume_db": 9}}),
        ("PATCH", "/v1/speakers/Ghost", {"pan": 0}, {"op": "set", "speaker": "Ghost", "changes": {"pan": 0}}),
    ],
)
def test_every_shortcut_replies_like_its_raw_message(server, method, path, body, message):
    """Parity: what REST does is exactly what serial will do with the same message."""
    status_a, a = call(server, method, path, body)
    status_b, b = call(server, "POST", "/v1/command", {"v": 1, **message})
    assert status_a == status_b
    a.get("result", {}).pop("sequence", None)
    b.get("result", {}).pop("sequence", None)
    assert a == b


def test_raw_command_echoes_the_id(server):
    status, reply = call(server, "POST", "/v1/command", {"v": 1, "id": 42, "op": "presets"})
    assert status == 200
    assert reply["id"] == 42


def test_join_and_leave_routes_need_a_session(server):
    quoted = quote("Go 4 Red")
    assert call(server, "POST", f"/v1/speakers/{quoted}/join", None)[0] == 409
    assert call(server, "POST", f"/v1/speakers/{quoted}/leave", None)[0] == 409
