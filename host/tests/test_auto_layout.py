"""The `auto` layout, speaker angles and principal/ambient roles (spec 2026-10-04 §2, §5)."""

import json
import threading

import pytest

from aurasync import control
from aurasync.config import Instalacion, Parlante
from aurasync.logbuffer import LogBuffer
from aurasync.service import Service
from aurasync.session import SessionOptions
from aurasync.simulated import SimulatedObserver, SimulatedSession


@pytest.mark.parametrize(
    ("n", "want"),
    [
        (2, [-90.0, 90.0]),
        (3, [-60.0, 60.0, 180.0]),
        (4, [-45.0, 45.0, 135.0, -135.0]),
        (5, [-36.0, 36.0, 108.0, 180.0, -108.0]),
        (8, [-22.5, 22.5, 67.5, 112.5, 157.5, -157.5, -112.5, -67.5]),
    ],
)
def test_auto_angles_for_two_to_eight(n, want):
    assert control.auto_angles(n) == pytest.approx(want)


def test_angle_of_inverts_role_from_angle():
    for layout, angles in control.LAYOUT_ANGLES.items():
        for role, (angle, lift) in angles.items():
            if lift:
                continue
            pan, ambience = control.ROLES[layout][role]
            got = control.angle_of(pan, ambience)
            assert (got - angle + 180) % 360 - 180 == pytest.approx(0, abs=1.0), (layout, role, got)


def test_auto_roles_follow_the_principals():
    three = control.layout_roles("auto", 3)
    assert list(three) == ["P1", "P2", "P3"]
    assert three["P3"] == control.role_from_angle(180)
    assert control.layout_roles("quad", 3) == control.ROLES["quad"]
    assert "auto" in control.LAYOUTS
    assert {"P1", "P8"} <= set(control.ALL_ROLES)


def test_old_installation_loads_as_principal(tmp_path):
    path = tmp_path / "i.json"
    path.write_text(json.dumps({"parlantes": [{"nombre": "a", "sink": "s"}]}), encoding="utf-8")
    assert Instalacion.cargar(path).parlantes[0].role_kind == "principal"


def _service(tmp_path, n=3):
    inst = Instalacion(parlantes=[Parlante(f"s{i}", f"sink{i}") for i in range(n)])
    inst.guardar(tmp_path / "i.json")
    return Service(
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


def test_role_kind_is_a_speaker_field(svc):
    ok(svc, op="set", speaker="s2", changes={"role_kind": "ambient"})
    assert svc.installation.por_nombre("s2").role_kind == "ambient"
    bad = svc.handle({"v": 1, "op": "set", "speaker": "s2", "changes": {"role_kind": "sideways"}})
    assert bad["error"]["code"] == "out_of_range"


def test_assign_in_auto_counts_only_the_principals(svc):
    ok(svc, op="set", changes={"layout": "auto"})
    ok(svc, op="assign", speaker="s0", role="P3")
    assert (svc.installation.por_nombre("s0").pan, svc.installation.por_nombre("s0").ambiente) == control.layout_roles(
        "auto", 3
    )["P3"]
    ok(svc, op="set", speaker="s2", changes={"role_kind": "ambient"})
    reply = svc.handle({"v": 1, "op": "assign", "speaker": "s1", "role": "P3"})
    assert reply["error"]["code"] == "out_of_range"  # two principals now: P1, P2


def test_in_auto_an_ambient_speaker_re_places_the_principals(svc):
    """Review 2026-10-04: making one speaker ambient left the other two at ±60° (custom), not on
    the two-principal `auto` (±90°)."""
    ok(svc, op="set", changes={"layout": "auto"})
    for i, role in enumerate(("P1", "P2", "P3")):
        ok(svc, op="assign", speaker=f"s{i}", role=role)
    ok(svc, op="set", speaker="s2", changes={"role_kind": "ambient"})
    state = ok(svc, op="state")
    roles = {s["name"]: s["role"] for s in state["speakers"]}
    assert roles["s0"] == "P1"
    assert roles["s1"] == "P2"
    two = control.layout_roles("auto", 2)
    assert (svc.installation.por_nombre("s0").pan, svc.installation.por_nombre("s0").ambiente) == two["P1"]
    ok(svc, op="set", speaker="s2", changes={"role_kind": "principal"})
    assert {s["name"]: s["role"] for s in ok(svc, op="state")["speakers"]} == {"s0": "P1", "s1": "P2", "s2": "P3"}


def test_the_snapshot_names_auto_roles(svc):
    ok(svc, op="set", changes={"layout": "auto"})
    ok(svc, op="assign", speaker="s1", role="P2")
    state = ok(svc, op="state")
    assert state["roles"]["auto"] == ["P1", "P2", "P3"]
    assert next(s for s in state["speakers"] if s["name"] == "s1")["role"] == "P2"
    assert state["role_places"]["auto"]["P3"]["angle_deg"] == pytest.approx(180)
