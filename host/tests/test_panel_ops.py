"""The panel's operations (spec §15) over the real service with the simulated session."""

import http.client
import json
import threading
import time
from pathlib import Path

import numpy as np
import pytest

from aurasync.config import Instalacion, Parlante
from aurasync.logbuffer import LogBuffer
from aurasync.rest import COOKIE, make_server
from aurasync.service import Service
from aurasync.session import SessionOptions
from aurasync.simulated import SimulatedObserver, SimulatedSession

NAMES = ("Red", "Black", "Blue")


@pytest.fixture
def svc(tmp_path):
    inst = Instalacion(
        parlantes=[
            Parlante("Red", "bluez_output.90_F2_60_75_4A_83.1", pan=-0.7, ambiente=0.15),
            Parlante("Black", "bluez_output.90_F2_60_DA_66_6D.1", pan=0.7, ambiente=0.15),
            Parlante("Blue", "bluez_output.90_F2_60_E3_07_39.1", pan=0.0, ambiente=0.55),
        ]
    )
    inst.guardar(tmp_path / "i.json")

    def make(*, simulated=True):
        service = Service(
            tmp_path / "i.json",
            tmp_path / "p.json",
            options=SessionOptions(microphone="sim", block=1024),
            session_factory=SimulatedSession,
            observer=SimulatedObserver(inst),
            simulated=simulated,
            measurements_path=tmp_path / "mediciones",
            log=lambda _: None,
            logs=LogBuffer(),
        )
        thread = threading.Thread(target=service.run, daemon=True)
        thread.start()
        made.append((service, thread))
        return service

    made = []
    yield make
    for service, thread in made:
        service.handle({"v": 1, "op": "shutdown"})
        thread.join(timeout=5)
        service.close()


def ok(service, **message):
    reply = service.handle({"v": 1, **message})
    assert reply["ok"], reply
    return reply["result"]


def code(service, **message):
    reply = service.handle({"v": 1, **message})
    assert not reply["ok"], reply
    return reply["error"]["code"]


def wait(predicate, timeout=15.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(0.05)
    pytest.fail("timed out")


def test_assign_sets_the_role_values_and_a_role_has_one_speaker(svc):
    s = svc()
    ok(s, op="assign", speaker="Blue", role="RR")
    blue = next(p for p in ok(s, op="state")["speakers"] if p["name"] == "Blue")
    assert (blue["pan"], blue["ambience"], blue["role"]) == (0.7, 0.55, "RR")
    assert code(s, op="assign", speaker="Blue", role="FL") == "conflict"
    assert code(s, op="assign", speaker="Blue", role="FC") == "out_of_range"  # not a quad role


def test_delay_by_hand_only_while_the_loop_is_off(svc):
    s = svc()
    ok(s, op="start", recalibrate=False)
    ok(s, op="set", speaker="Red", changes={"delay_ms": 2.5})
    ok(s, op="recalibrate", active=True)
    assert code(s, op="set", speaker="Red", changes={"delay_ms": 3.0}) == "conflict"


def test_mute_is_not_an_installation_change(svc):
    s = svc()
    ok(s, op="set", speaker="Red", changes={"muted": True})
    state = ok(s, op="state")
    assert state["speakers"][0]["muted"]
    assert not state["dirty"]


def test_operations_that_need_a_session_say_so(svc):
    s = svc()
    for message in (
        {"op": "tone", "speaker": "Red"},
        {"op": "source", "kind": "tone"},
        {"op": "recalibrate", "active": True},
        {"op": "calibrate"},
        {"op": "ab_start", "a": "x", "b": "y"},
    ):
        assert code(s, **message) == "conflict", message


def test_speakers_cannot_be_added_or_removed_while_playing(svc):
    s = svc()
    ok(s, op="start")
    assert code(s, op="speaker_remove", speaker="Blue") == "conflict"
    ok(s, op="stop")
    ok(s, op="speaker_remove", speaker="Blue")
    assert ok(s, op="state")["dirty"]


def test_add_a_connected_speaker(svc):
    s = svc()
    assert code(s, op="speaker_add", address="F8:5C:7D:00:11:22") == "unavailable"
    s.observer.connect("F8:5C:7D:00:11:22")
    ok(s, op="speaker_add", address="F8:5C:7D:00:11:22")
    names = [p["name"] for p in ok(s, op="state")["speakers"]]
    assert names == ["Red", "Black", "Blue", "JBL Flip 7"]
    assert code(s, op="speaker_add", address="F8:5C:7D:00:11:22") == "conflict"


def test_blind_ab_never_tells_which_preset_x_is(svc):
    s = svc()
    ok(s, op="preset_save", name="a")
    ok(s, op="set", speaker="Blue", changes={"ambience": 0.9})
    ok(s, op="preset_save", name="b")
    ok(s, op="start")
    ok(s, op="ab_start", a="a", b="b")
    ok(s, op="ab_play", which="x")
    state = ok(s, op="state")
    assert state["preset"] is None
    assert "x" not in json.dumps(state["ab"]).replace('"playing": "x"', "")
    answer = ok(s, op="ab_answer", x_is="a")
    assert answer["truth"] in {"a", "b"}
    assert code(s, op="preset_delete", name="a") == "conflict"
    result = ok(s, op="ab_stop")
    assert len(result["trials"]) == 1


def test_calibration_and_saving_it(svc):
    s = svc(simulated=False)
    ok(s, op="start")
    ok(s, op="calibrate", seconds=5.0)
    wait(lambda: (ok(s, op="state")["calibration"] or {}).get("state") == "done", 30)
    cal = ok(s, op="state")["calibration"]
    delays = {r["speaker"]: r["delay_ms"] for r in cal["results"]}
    assert delays == pytest.approx({"Red": 9.0, "Black": 4.5, "Blue": 0.0}, abs=0.1)
    path = ok(s, op="measurement_save", note="prueba")["path"]
    record = json.loads(Path(path).read_text())
    assert record["mark"] == "MEDIDO"
    assert record["note"] == "prueba"
    assert record["environment"]["kernel"]
    ok(s, op="calibration_apply")
    wait(lambda: ok(s, op="state")["speakers"][0]["delay_ms"] == pytest.approx(9.0, abs=0.1))


def test_a_simulated_calibration_is_never_saved_as_a_measurement(svc):
    s = svc()
    ok(s, op="start")
    ok(s, op="calibrate", seconds=5.0)
    wait(lambda: (ok(s, op="state")["calibration"] or {}).get("state") == "done", 30)
    assert code(s, op="measurement_save") == "conflict"


def test_logs_since(svc):
    s = svc()
    ok(s, op="start")
    first = ok(s, op="logs", since=0)
    assert any("session open" in r["message"] for r in first["records"])
    later = ok(s, op="logs", since=first["last"])
    assert later["records"] == [] or later["records"][0]["seq"] > first["last"]


def test_services_session_restart_and_observed_ones(svc):
    s = svc()
    ok(s, op="service_start", name="session")
    ok(s, op="service_restart", name="session")
    state = ok(s, op="state")
    session = next(x for x in state["services"] if x["name"] == "session")
    assert (session["state"], session["restarts"]) == ("running", 1)
    assert code(s, op="service_stop", name="pipewire") == "conflict"
    assert code(s, op="service_stop", name="nope") == "not_found"


def test_restart_settings_wait_for_the_next_session(svc):
    s = svc()
    ok(s, op="start")
    ok(s, op="set", changes={"block_size": 2048})
    assert ok(s, op="state")["config"]["pending_restart"] == ["block_size"]
    ok(s, op="service_restart", name="session")
    assert ok(s, op="state")["config"]["pending_restart"] == []


# -- the browser's way in -------------------------------------------------------------


def request(port, method, path, headers=None, body=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request(method, path, body=body, headers=headers or {})
    response = conn.getresponse()
    data = response.read()
    conn.close()
    return response, data


def test_web_access_cookie_host_and_origin(svc):
    s = svc()
    token = "t" * 43
    httpd = make_server(s, "127.0.0.1", 0, token)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    port = httpd.server_address[1]
    try:
        response, _ = request(port, "GET", "/")
        assert response.status == 401
        response, _ = request(port, "GET", f"/?t={token}")
        assert response.status == 303
        assert response.getheader("Location") == "/"
        cookie = response.getheader("Set-Cookie")
        assert "HttpOnly" in cookie
        assert "SameSite=Strict" in cookie
        jar = {"Cookie": f"{COOKIE}={token}"}
        response, page = request(port, "GET", "/", jar)
        assert response.status == 200
        assert b"aurasync" in page
        response, _ = request(port, "GET", "/static/app.js", jar)
        assert response.status == 200
        response, _ = request(port, "GET", "/static/../service.py", jar)
        assert response.status == 404
        # DNS rebinding: another name that resolves here.
        response, _ = request(port, "GET", "/v1/state", {**jar, "Host": f"evil.example:{port}"})
        assert response.status == 403
        body = json.dumps({"v": 1, "op": "presets"})
        # A cookie alone, from another origin, cannot order anything.
        response, _ = request(port, "POST", "/v1/command", {**jar, "Origin": "http://evil.example"}, body)
        assert response.status == 403
        response, _ = request(port, "POST", "/v1/command", jar, body)
        assert response.status == 403, "a browser always sends Origin; its absence is refused"
        response, _ = request(port, "POST", "/v1/command", {**jar, "Origin": f"http://127.0.0.1:{port}"}, body)
        assert response.status == 200
        # curl with the bearer token needs no Origin.
        response, _ = request(port, "POST", "/v1/command", {"Authorization": f"Bearer {token}"}, body)
        assert response.status == 200
    finally:
        httpd.shutdown()
        httpd.server_close()


def _calibrate(s):
    ok(s, op="calibrate", seconds=5.0)
    wait(lambda: (ok(s, op="state")["calibration"] or {}).get("state") == "done", 30)
    return {r["speaker"]: r for r in ok(s, op="state")["calibration"]["results"]}


def test_closure_a_second_calibration_after_applying_finds_nothing_left(svc):
    s = svc()
    ok(s, op="start")
    first = _calibrate(s)
    assert first["Red"]["delay_ms"] == pytest.approx(9.0, abs=0.1)
    ok(s, op="calibration_apply")
    wait(lambda: ok(s, op="state")["speakers"][0]["delay_ms"] == pytest.approx(9.0, abs=0.1))
    second = _calibrate(s)
    for r in second.values():
        assert r["delay_ms"] == pytest.approx(0.0, abs=0.1), second
        assert r["gain_db"] == pytest.approx(0.0, abs=0.5), second


def test_a_delay_added_on_purpose_is_found_by_the_calibration(svc):
    """The injection test: 4 ms more on Black must show up as Black 4 ms ahead of the rest."""
    s = svc()
    ok(s, op="start", recalibrate=False)  # the delay is set by hand, as in the test
    _calibrate(s)
    ok(s, op="calibration_apply")
    wait(lambda: ok(s, op="state")["speakers"][0]["delay_ms"] == pytest.approx(9.0, abs=0.1))
    black = next(p for p in ok(s, op="state")["speakers"] if p["name"] == "Black")
    ok(s, op="set", speaker="Black", changes={"delay_ms": black["delay_ms"] + 4.0})
    found = _calibrate(s)
    assert found["Black"]["delay_ms"] - found["Red"]["delay_ms"] == pytest.approx(-4.0, abs=0.1), found
    ok(s, op="calibration_apply")
    wait(
        lambda: next(p for p in ok(s, op="state")["speakers"] if p["name"] == "Black")["delay_ms"]
        == pytest.approx(black["delay_ms"], abs=0.15)
    )


def test_apply_works_with_nothing_playing(svc):
    """With the music paused the motor still has to run, or a pending fade never ends."""
    s = svc()
    ok(s, op="start")
    ok(s, op="source", kind="system")  # the simulated input delivers nothing now
    wait(lambda: ok(s, op="state")["source"]["kind"] == "system", 5)
    ok(s, op="preset_save", name="p")
    ok(s, op="set", speaker="Red", changes={"pan": 0.5})
    ok(s, op="preset_load", name="p")
    wait(lambda: ok(s, op="state")["speakers"][0]["pan"] == -0.7, 5)


def test_the_calibration_measures_each_speakers_frequency_response(svc):
    """The simulated speakers have a known colouring: the measurement must find it."""
    from aurasync.simulated import ROOM_COLOUR_DB

    s = svc()
    ok(s, op="start")
    results = _calibrate(s)
    hz = ok(s, op="state")["calibration"]["response_hz"]
    known = ROOM_COLOUR_DB - np.median(ROOM_COLOUR_DB[(np.array(hz) > 400) & (np.array(hz) < 2500)])
    for r in results.values():
        assert r["band_hz"][0] == pytest.approx(99, abs=1)
        assert round(r["band_hz"][1]) in {10079, 8000}
        # From 100 Hz to 16 kHz. Below, the thirds have a handful of FFT bins and with 5 s and
        # the other two noises acting as noise they vary several dB: the method's own limit.
        errors = [
            abs(v - k)
            for f, v, k in zip(hz, r["response_db"], known, strict=True)
            if 100 <= f <= 16000 and v is not None
        ]
        assert max(errors) < 2.5, errors


def test_apply_keeps_a_doubtful_speaker_where_it_was(svc):
    s = svc()
    ok(s, op="start")
    _calibrate(s)
    cal = s.session.calibration
    cal.results = [dict(r) for r in cal.results]
    blue = next(r for r in cal.results if r["speaker"] == "Blue")
    blue["doubtful"] = True
    blue["delay_ms"] = 99.0  # an untrustworthy number that must not be used
    result = ok(s, op="calibration_apply")
    assert result["skipped"] == ["Blue"]
    wait(lambda: ok(s, op="state")["speakers"][0]["delay_ms"] == pytest.approx(4.5, abs=0.15))
    speakers = {p["name"]: p["delay_ms"] for p in ok(s, op="state")["speakers"]}
    # Red and Black aligned between themselves (9.0 and 4.5 relative to Blue before; Red
    # arrives 4.5 ms before Black), Blue untouched at 0.
    assert speakers["Blue"] == pytest.approx(0.0, abs=0.01)
    assert speakers["Red"] - speakers["Black"] == pytest.approx(4.5, abs=0.15)


def test_closure_for_the_spectrum_equalising_flattens_the_response(svc):
    """The simulated speakers are coloured like the Go 4. Calibrate, equalise, calibrate again:
    the second response must be flatter in the speaker's usable band."""
    s = svc()
    ok(s, op="start")
    hz = None

    def spread(results):
        nonlocal hz
        hz = ok(s, op="state")["calibration"]["response_hz"]
        worst = 0.0
        for r in results.values():
            band = [v for f, v in zip(hz, r["response_db"], strict=True) if 160 <= f <= 8000 and v is not None]
            worst = max(worst, max(band) - min(band))
        return worst

    before = spread(_calibrate(s))
    result = ok(s, op="eq_apply")
    assert result["skipped"] == []
    wait(lambda: ok(s, op="state")["speakers"][0]["eq_db"] is not None)
    time.sleep(0.5)  # the fade that swaps the filters
    after = spread(_calibrate(s))
    assert after < before - 2.0, (before, after)


def test_the_loop_is_off_by_default(svc):
    s = svc()
    ok(s, op="start")
    assert not ok(s, op="state")["recalibration"]["active"]
    assert not ok(s, op="state")["global"]["recalibrate"]


def test_a_calibration_pauses_and_resumes_the_loop(svc):
    s = svc()
    ok(s, op="start", recalibrate=True)
    assert ok(s, op="state")["recalibration"]["active"]
    ok(s, op="calibrate", seconds=5)
    session = s.session
    assert session.loop is None
    deadline = time.monotonic() + 60
    while session.calibration.state in {"running", "measuring"} and time.monotonic() < deadline:
        time.sleep(0.2)
    time.sleep(0.5)
    assert session.loop is not None


def test_the_microphone_check_opens_on_demand_and_closes_by_itself(svc, monkeypatch):
    """With the loop off (the default) the panel's microphone check has no reading: it asks for
    one, and the microphone opens for a few seconds only (it is the laptop's own: privacy)."""
    from aurasync import session as session_module

    monkeypatch.setattr(session_module, "MIC_CHECK_S", 1.0)
    s = svc()
    ok(s, op="start")
    time.sleep(0.5)
    state = ok(s, op="state")
    assert "mic" not in state["meters"]
    assert state["recalibration"]["mic_check"] is False
    assert s.session.pids()["microphone"] is None
    ok(s, op="mic_check")
    wait(lambda: "mic" in ok(s, op="state")["meters"], timeout=5)
    assert ok(s, op="state")["recalibration"]["mic_check"] is True
    assert s.session.pids()["microphone"] is not None
    # It closes alone, and its reading goes with it.
    wait(lambda: not ok(s, op="state")["recalibration"]["mic_check"], timeout=5)
    assert "mic" not in ok(s, op="state")["meters"]
    assert s.session.pids()["microphone"] is None


def test_asking_again_never_keeps_the_microphone_open_longer(svc, monkeypatch):
    """Privacy: a panel asking every so often must not keep the microphone open for good. One
    check lasts MIC_CHECK_S from its start; a call while it is open does not extend it; a new
    check opens only after the previous one closed."""
    from aurasync import session as session_module

    monkeypatch.setattr(session_module, "MIC_CHECK_S", 1.0)
    s = svc()
    ok(s, op="start")
    opened_at: dict[int, float] = {}
    last_seen: dict[int, float] = {}
    end = time.monotonic() + 3.5
    while time.monotonic() < end:
        ok(s, op="mic_check")
        pid = s.session.pids()["microphone"]
        now = time.monotonic()
        if pid is not None:
            opened_at.setdefault(pid, now)
            last_seen[pid] = now
        time.sleep(0.1)
    assert len(opened_at) >= 2, opened_at  # it closed, and a later call opened a new one
    spans = {pid: last_seen[pid] - opened_at[pid] for pid in opened_at}
    assert max(spans.values()) <= 1.0 + 0.4, spans


def test_the_microphone_check_gives_way_to_the_loop_and_to_a_calibration(svc):
    s = svc()
    ok(s, op="start")
    ok(s, op="mic_check")
    wait(lambda: ok(s, op="state")["recalibration"]["mic_check"], timeout=5)
    ok(s, op="calibrate", seconds=5)
    wait(lambda: not ok(s, op="state")["recalibration"]["mic_check"], timeout=5)
    ok(s, op="calibrate_cancel")
    ok(s, op="set", changes={"recalibrate": True})
    ok(s, op="mic_check")  # the loop already reads the microphone: nothing else opens
    time.sleep(0.3)
    assert ok(s, op="state")["recalibration"]["mic_check"] is False


def test_the_microphone_check_needs_a_session(svc):
    s = svc()
    assert code(s, op="mic_check") == "conflict"


def test_switching_the_loop_off_is_a_live_setting(svc):
    s = svc()
    ok(s, op="start")
    ok(s, op="set", changes={"recalibrate": False})
    assert s.session.loop is None
    assert not ok(s, op="state")["global"]["recalibrate"]
    ok(s, op="set", changes={"recalibrate": True})
    assert s.session.loop is not None


def test_the_microphone_can_be_changed_live_and_the_loop_follows(svc):
    s = svc()
    ok(s, op="start", recalibrate=True)
    ok(s, op="microphone_set", node="simulado-2")
    state = ok(s, op="state")
    assert state["recalibration"]["microphone"] == "simulado-2"
    assert state["recalibration"]["active"]
    assert s.session.microphone == "simulado-2"
    assert code(s, op="microphone_set", node="not-a-mic") == "out_of_range"


def test_the_input_is_described(svc):
    s = svc()
    ok(s, op="start")
    # Wait for what is checked, not for any analysis: under load the first one can come before
    # the simulated music does (failed once in a full run with the machine busy, 2026-10-02).
    deadline = time.monotonic() + 15

    def described():
        a = ok(s, op="state")["input"]["analysis"]
        return a and a.get("kind") in {"estéreo", "mono"}

    while time.monotonic() < deadline and not described():
        time.sleep(0.2)
    analysis = ok(s, op="state")["input"]["analysis"]
    assert analysis["kind"] in {"estéreo", "mono"}
    assert -1 <= analysis["correlation"] <= 1


def test_forget_refuses_a_speaker_of_the_installation_and_forgets_another(svc):
    s = svc()
    assert code(s, op="forget", address="90:F2:60:75:4A:83") == "conflict"
    ok(s, op="forget", address="F8:5C:7D:00:11:22")
    device = next(d for d in ok(s, op="state")["devices"] if d["address"] == "F8:5C:7D:00:11:22")
    assert not device["paired"]


def test_a_loop_measurement_that_cannot_start_falls_back_and_the_audio_goes_on(svc):
    """The loop is a side channel (card best-effort-side-channels): a measuring process that
    cannot start (2026-10-02: forkserver and an unguarded main script) never ends the session."""
    s = svc()
    ok(s, op="set", changes={"recalibrate_every_s": 5, "recalibrate_measure_s": 10})
    ok(s, op="start", recalibrate=True)
    session = s.session

    def broken(*_args, **_kwargs):
        msg = "an attempt has been made to start a new process before bootstrapping"
        raise RuntimeError(msg)

    session._measurer.lanzar = broken  # noqa: SLF001
    wait(lambda: session.last_recalibration and session.last_recalibration["kind"] == "error", timeout=40)
    assert "próxima vuelta" in session.last_recalibration["reason"]
    assert ok(s, op="state")["session"]["status"] == "playing"
    assert session.loop is not None


def test_a_speaker_s_eq_curve_can_be_written_back(svc):
    """research/10 §7.1: undoing the EQ, or adding a speaker back, has to write its curve again."""
    from aurasync.dsp.response import THIRDS

    s = svc()
    curve = [0.0] * len(THIRDS)
    curve[5] = 3.5
    ok(s, op="set", speaker="Red", changes={"eq_db": curve})
    assert s.installation.por_nombre("Red").ecualizacion_db == curve
    assert next(sp for sp in ok(s, op="state")["speakers"] if sp["name"] == "Red")["eq_db"] == curve
    ok(s, op="set", speaker="Red", changes={"eq_db": None})
    assert s.installation.por_nombre("Red").ecualizacion_db is None


@pytest.mark.parametrize(
    ("value", "error"),
    [
        ([0.0] * 3, "out_of_range"),  # not one value per third
        ("flat", "type"),
        (["a"] * 27, "type"),
        ([9.0] * 27, "out_of_range"),  # the EQ only lifts, up to MAX_BOOST_DB
        ([-1.0] * 27, "out_of_range"),
    ],
)
def test_a_wrong_eq_curve_is_refused(svc, value, error):
    s = svc()
    assert code(s, op="set", speaker="Red", changes={"eq_db": value}) == error
