"""The sync estimator inside the service: settings, ops, REST, snapshot, apply (spec 2026-10-03 §6.1)."""

import dataclasses
import json
import threading
import time

import pytest

from aurasync import rest, sync_sim
from aurasync.clients import required_scope
from aurasync.config import Instalacion, Parlante
from aurasync.logbuffer import LogBuffer
from aurasync.service import Service
from aurasync.session import SessionOptions
from aurasync.simulated import SimulatedObserver, SimulatedSession

NAMES = ["s0", "s1", "s2"]


def _installation():
    return Instalacion(parlantes=[Parlante(n, f"sink{i}", pan=(-0.7, 0.7, 0.0)[i]) for i, n in enumerate(NAMES)])


def _service(tmp_path):
    inst = _installation()
    inst.guardar(tmp_path / "i.json")
    return Service(
        tmp_path / "i.json",
        tmp_path / "p.json",
        options=SessionOptions(microphone="sim", block=1024),
        session_factory=SimulatedSession,
        observer=SimulatedObserver(inst),
        simulated=True,
        measurements_path=tmp_path / "mediciones",
        log=lambda _: None,
        logs=LogBuffer(),
    )


@pytest.fixture
def svc(tmp_path):
    service = _service(tmp_path)
    thread = threading.Thread(target=service.run, daemon=True)
    thread.start()
    yield service
    service.handle({"v": 1, "op": "shutdown"})
    thread.join(timeout=5)
    service.close()


def ok(service, **message):
    reply = service.handle({"v": 1, **message})
    assert reply["ok"], reply
    return reply["result"]


def _feed(service, minutes=10.0):
    """Simulated measurements of the server's microphone, on the service's clock."""
    sc = sync_sim.standard("drift")
    sc.duration_s = minutes * 60
    base = time.monotonic() - sc.duration_s
    for m in sync_sim.measurements(sc, 0):
        service.sync_estimator.submit(dataclasses.replace(m, t=m.t + base))
    assert service.sync_estimator.wait_idle()
    return sc


def test_a_suggestion_appears_in_the_state_and_the_snapshot(svc):
    _feed(svc)
    state = ok(svc, op="sync_state")
    assert state["suggestion"]["anchor"] == "server"
    assert set(state["suggestion"]["delays_ms"]) == set(NAMES)
    assert state["settings"]["method"] == "robust_ls"
    deadline = time.monotonic() + 3
    while ok(svc, op="state").get("sync_suggestion") is None and time.monotonic() < deadline:
        time.sleep(0.05)
    brief = ok(svc, op="state")["sync_suggestion"]
    assert brief["id"] == state["suggestion"]["id"]
    assert "figure" not in json.dumps(brief)


def test_apply_sets_absolute_delays(svc):
    _feed(svc)
    suggestion = ok(svc, op="sync_state")["suggestion"]
    ok(svc, op="sync_apply", suggestion_id=suggestion["id"])
    first = {p.nombre: p.retardo_ms for p in svc.installation.parlantes}
    assert first == pytest.approx(suggestion["delays_ms"], abs=1e-3)
    _feed(svc)  # a new suggestion, from the same truth: the same delays, not doubled
    ok(svc, op="sync_apply")
    second = {p.nombre: p.retardo_ms for p in svc.installation.parlantes}
    assert second == pytest.approx(first, abs=0.05)


def test_apply_leaves_unsuggested_speakers(svc):
    sc = sync_sim.standard("drift")
    sc.hears["server"] = {"s0", "s1"}
    sc.duration_s = 600
    base = time.monotonic() - 600
    svc.installation.por_nombre("s2").retardo_ms = 7.25
    for m in sync_sim.measurements(sc, 0):
        svc.sync_estimator.submit(dataclasses.replace(m, t=m.t + base))
    assert svc.sync_estimator.wait_idle()
    ok(svc, op="sync_apply")
    assert svc.installation.por_nombre("s2").retardo_ms == 7.25


def test_apply_without_suggestion_is_a_conflict(svc):
    reply = svc.handle({"v": 1, "op": "sync_apply"})
    assert reply["error"]["code"] == "conflict"


def test_stale_id_is_a_conflict(svc):
    _feed(svc)
    current = ok(svc, op="sync_state")["suggestion"]["id"]
    reply = svc.handle({"v": 1, "op": "sync_apply", "suggestion_id": current - 1})
    assert reply["error"]["code"] == "conflict"
    ok(svc, op="sync_apply", suggestion_id=current)
    again = svc.handle({"v": 1, "op": "sync_apply", "suggestion_id": current})
    assert again["error"]["code"] == "conflict"  # already applied


def test_settings_persist(tmp_path):
    first = _service(tmp_path)
    try:
        first.sync_set({"window_min": 20, "jump_repeats": 3})
    finally:
        first.sync_estimator.close()
        first.close()
    second = _service(tmp_path)
    try:
        assert second.sync_estimator.settings.window_min == 20
        assert second.sync_estimator.settings.jump_repeats == 3
    finally:
        second.sync_estimator.close()
        second.close()


def test_bad_settings_are_out_of_range(svc):
    reply = svc.handle({"v": 1, "op": "sync_set", "changes": {"window_min": 0.1}})
    assert reply["error"]["code"] == "out_of_range"
    reply = svc.handle({"v": 1, "op": "sync_set", "changes": {"nonsense": 1}})
    assert reply["error"]["code"] == "out_of_range"


def test_corrupt_sync_json_falls_back_to_defaults(tmp_path):
    (tmp_path / "sync.json").write_text("{not json", encoding="utf-8")
    service = _service(tmp_path)
    try:
        assert service.sync_estimator.settings.window_min == 10
    finally:
        service.sync_estimator.close()
        service.close()


def test_explain_is_pending_then_ready(svc):
    first = ok(svc, op="sync_explain")
    assert first.get("pending") or "docs" in first
    deadline = time.monotonic() + 30
    while "docs" not in ok(svc, op="sync_explain") and time.monotonic() < deadline:
        time.sleep(0.1)
    ready = ok(svc, op="sync_explain")
    assert "together" in ready
    assert ready["docs"]["window_min"]["figure"]["evidence"] == "SIMULADO"


def test_a_new_session_starts_a_new_history(svc):
    """Review 2026-10-03: a new session reopens every stream; the old latencies no longer hold."""
    _feed(svc)
    assert ok(svc, op="sync_state")["suggestion"] is not None
    ok(svc, op="start", recalibrate=False)
    assert svc.sync_estimator.wait_idle()
    state = ok(svc, op="sync_state")
    assert state["suggestion"] is None
    assert state["sources"] == []
    ok(svc, op="stop")


def test_apply_refuses_speakers_not_in_the_installation(svc):
    sc = sync_sim.standard("drift", 4)  # s3 is not in the installation
    sc.duration_s = 600
    base = time.monotonic() - 600
    for m in sync_sim.measurements(sc, 0):
        svc.sync_estimator.submit(dataclasses.replace(m, t=m.t + base))
    assert svc.sync_estimator.wait_idle()
    before = {p.nombre: p.retardo_ms for p in svc.installation.parlantes}
    reply = svc.handle({"v": 1, "op": "sync_apply"})
    assert reply["error"]["code"] == "conflict"
    assert "s3" in reply["error"]["message"]
    assert {p.nombre: p.retardo_ms for p in svc.installation.parlantes} == before


def test_the_state_reports_an_estimator_error(svc, monkeypatch):
    from aurasync import sync_estimator

    def broken(*_args, **_kwargs):
        msg = "boom"
        raise RuntimeError(msg)

    monkeypatch.setattr(sync_estimator.sync_methods, "fit", broken)
    _feed(svc)
    state = ok(svc, op="sync_state")
    assert "boom" in state["error"]
    assert svc.sync_estimator.thread.is_alive()


def test_scopes():
    assert required_scope("sync_state") == "read"
    assert required_scope("sync_explain") == "read"
    assert required_scope("sync_set") == "control"
    assert required_scope("sync_apply") == "control"


def test_rest_routes():
    assert rest.route("GET", "/v1/sync", None)["op"] == "sync_state"
    assert rest.route("PATCH", "/v1/sync", {"window_min": 5})["changes"] == {"window_min": 5}
    assert rest.route("POST", "/v1/sync/apply", {"suggestion_id": 3}) == {
        "v": 1,
        "op": "sync_apply",
        "suggestion_id": 3,
    }
    assert rest.route("GET", "/v1/sync/explain", None)["op"] == "sync_explain"


def test_the_loop_feeds_the_estimator(svc):
    """The session hands the server microphone's measurement to the estimator."""
    seen = []
    svc.sync_estimator.submit = seen.append
    ok(svc, op="start", recalibrate=False)
    session = svc.session
    session.on_measurement({"s0": 500.0, "s1": 502.0, "s2": 501.0}, frozenset({"s0", "s1"}), time.monotonic())
    assert len(seen) == 1
    m = seen[0]
    assert m.position_id == "server"
    assert m.kind == "continuous"
    assert m.heard == frozenset({"s0", "s1"})
    ok(svc, op="stop")
