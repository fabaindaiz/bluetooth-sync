"""The spatial mode's knobs explained: recommendation, how it sounds, a SIMULATED figure, and the
room seen from above (spec 2026-10-04 §4, d-7c8794-0a4586)."""

import pytest

from aurasync import chain, spatial_docs
from aurasync.chain import ChainValues
from aurasync.knob_docs import Doc, Figure

KNOBS = ("render", "character", "manual", "arc_deg", "ambience", "ambient_level_db", "haas_ms")
RING = {"s0": (-60.0, "principal"), "s1": (60.0, "principal"), "s2": (180.0, "principal"), "a": (None, "ambient")}


def test_every_knob_has_a_doc_with_its_recommendation():
    assert set(spatial_docs.DOCS) == set(KNOBS)
    stage = chain.STAGES["spatial"]
    for name, doc in spatial_docs.DOCS.items():
        assert isinstance(doc, Doc)
        assert doc.why_recommended
        assert doc.sounds_choices or (doc.sounds_low and doc.sounds_high)
        if name == "render":
            assert doc.recommended == stage.default_algorithm
        else:
            assert doc.recommended == stage.find_param(name, "spatial").default


def test_every_render_is_explained_and_drawn():
    stage = chain.STAGES["spatial"]
    assert set(spatial_docs.DOCS["render"].sounds_choices) == {a.id for a in stage.algorithms}
    front = ChainValues.from_json({"spatial": {"algorithm": "front"}})
    assert spatial_docs.params_of(front).front_intact
    fig = spatial_docs.figure("render", front, RING)
    styles = {s["label"]: s["style"] for s in fig.series}
    assert styles["frente intacto"] == "current"
    assert styles["espacial"] == "other"


def test_figures_compare_current_with_recommended():
    values = ChainValues.from_json({"spatial": {"algorithm": "spatial", "params": {"character": 0.9}}})
    for name in KNOBS:
        fig = spatial_docs.figure(name, values, RING)
        assert isinstance(fig, Figure), name
        assert fig.evidence == "SIMULADO"
        assert "recommended" in {s["style"] for s in fig.series}, name


def test_a_wider_arc_separates_less_at_the_front_and_more_ambience_with_character():
    low = spatial_docs.metrics(
        spatial_docs.params_of(ChainValues.from_json({"spatial": {"params": {"character": 0.0}}})), RING
    )
    high = spatial_docs.metrics(
        spatial_docs.params_of(ChainValues.from_json({"spatial": {"params": {"character": 1.0}}})), RING
    )
    assert high["ambient_pct"] > low["ambient_pct"]
    assert low["separation_db"] >= 10
    assert high["separation_db"] >= 10


def test_the_room_is_drawn_from_above():
    fig = spatial_docs.room(RING)
    assert fig.kind == "room"
    angles = {s["label"]: s["points"][0][0] for s in fig.series}
    assert angles["s2"] == pytest.approx(180)
    assert {s["label"]: s["style"] for s in fig.series}["a"] == "other"


def test_the_service_explains_off_the_engine_thread(tmp_path):
    import threading
    import time

    from aurasync import rest
    from aurasync.clients import required_scope
    from aurasync.config import Instalacion, Parlante
    from aurasync.logbuffer import LogBuffer
    from aurasync.service import Service
    from aurasync.session import SessionOptions
    from aurasync.simulated import SimulatedObserver, SimulatedSession

    inst = Instalacion(parlantes=[Parlante(f"s{i}", f"sink{i}") for i in range(3)])
    inst.guardar(tmp_path / "i.json")
    svc = Service(
        tmp_path / "i.json",
        tmp_path / "p.json",
        options=SessionOptions(microphone="sim", block=1024),
        session_factory=SimulatedSession,
        observer=SimulatedObserver(inst),
        simulated=True,
        measurements_path=tmp_path / "m",
        log=lambda _: None,
        logs=LogBuffer(),
    )
    thread = threading.Thread(target=svc.run, daemon=True)
    thread.start()
    try:
        first = svc.handle({"v": 1, "op": "spatial_explain"})
        assert first["ok"]
        deadline = time.monotonic() + 30
        reply = first
        while "docs" not in reply["result"] and time.monotonic() < deadline:
            time.sleep(0.1)
            reply = svc.handle({"v": 1, "op": "spatial_explain"})
        assert set(reply["result"]["docs"]) == set(KNOBS)
        assert reply["result"]["room"]["kind"] == "room"
        assert required_scope("spatial_explain") == "read"
        assert rest.route("GET", "/v1/spatial/explain", None)["op"] == "spatial_explain"
    finally:
        svc.handle({"v": 1, "op": "shutdown"})
        thread.join(timeout=5)
        svc.close()


def test_explain_has_docs_figures_and_the_room():
    out = spatial_docs.explain(ChainValues(), RING)
    assert set(out["docs"]) == set(KNOBS)
    assert out["room"]["kind"] == "room"
    assert out["docs"]["character"]["figure"]["evidence"] == "SIMULADO"
