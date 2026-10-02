"""`GET /v1/stream` (spec §17): live state, meters, input and logs as Server-Sent Events."""

import http.client
import json
import threading
import time

import pytest

from aurasync import rest
from aurasync.rest import make_server

from .test_panel_ops import ok, svc  # noqa: F401 - the fixture

TOKEN = "s" * 43


@pytest.fixture
def served(svc):  # noqa: F811
    s = svc()
    httpd = make_server(s, "127.0.0.1", 0, TOKEN)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield s, httpd.server_address[1]
    httpd.shutdown()


def open_stream(port, token=TOKEN):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request("GET", "/v1/stream", headers={"Authorization": f"Bearer {token}"})
    return conn, conn.getresponse()


def events(response, seconds):
    """The events read in `seconds`, as (name, data)."""
    found, name, end = [], None, time.monotonic() + seconds
    while time.monotonic() < end:
        line = response.fp.readline().decode().rstrip("\n")
        if line.startswith("event: "):
            name = line[7:]
        elif line.startswith("data: "):
            found.append((name, json.loads(line[6:])))
    return found


def test_the_stream_sends_state_meters_input_and_logs(served):
    s, port = served
    ok(s, op="start")
    conn, response = open_stream(port)
    assert response.status == 200
    assert response.getheader("Content-Type").startswith("text/event-stream")
    got = events(response, 2.5)
    names = [n for n, _ in got]
    assert "state" in names
    assert names.count("meters") >= 15  # ~20 Hz
    assert "input" in names
    meters = next(d for n, d in got if n == "meters")
    assert {"in L", "in R"} <= set(meters["meters"])
    ok(s, op="set", changes={"volume_db": -21})
    later = events(response, 1.5)
    assert any(n == "state" and d["global"]["volume_db"] == -21 for n, d in later)
    assert any(n == "log" for n, _ in got + later)
    conn.close()


def test_the_stream_needs_the_token(served):
    _, port = served
    conn, response = open_stream(port, token="wrong" * 9)
    assert response.status == 401
    conn.close()


def test_a_ninth_stream_is_refused_and_a_closed_one_frees_its_place(served):
    _, port = served
    opened = [open_stream(port) for _ in range(rest.STREAM_MAX)]
    conn, response = open_stream(port)
    assert response.status == 503
    assert json.loads(response.read())["error"]["code"] == "busy"
    conn.close()
    opened[0][0].close()
    opened[0][1].close()
    # The server notices when a write fails: with no session it writes the state once a
    # second, and the first write after the close can still land in the socket buffer.
    end = time.monotonic() + 5
    while True:
        conn, response = open_stream(port)
        if response.status == 200 or time.monotonic() > end:
            break
        conn.close()
        time.sleep(0.2)
    assert response.status == 200
    conn.close()
    for c, r in opened[1:]:
        c.close()
        r.close()


def test_a_client_that_leaves_does_not_disturb_the_session(served):
    s, port = served
    ok(s, op="start")
    conn, response = open_stream(port)
    events(response, 0.3)
    conn.close()
    response.close()
    blocks = s.session.blocks
    time.sleep(0.6)
    assert s.session.blocks > blocks
    assert ok(s, op="state")["session"]["status"] == "playing"
