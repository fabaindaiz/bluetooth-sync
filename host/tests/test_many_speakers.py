"""Up to eight speakers (d-7c8794-3b7793, experiment 16): the engine, the chain, the roles, the
simulated room and the service.

What must hold, and why:

1. **Seven or eight speakers do not stop the service.** Until 2026-10-02 the engine raised a
   `ValueError` past `decorrelate.MAXIMO_FIJOS` and the service could not start; now the bank
   is built and the chain and the engine's metrics say how far apart it is.
2. **`decorrelate.assignment: mix`** puts the least alike filters on the most alike mixes, and
   a pan that changes the best assignment waits for a cut (moving a slider must not cut).
   The default stays `order` (experiment 16 §9: through the real engine the gain did not repeat).
3. **Roles by angle** reproduce today's `quad` and `lcrs` exactly, and give 5-8 speakers roles.
4. **The simulated room has eight distinct speakers**, and the service plays with eight.
"""

import threading

import numpy as np
import pytest

from aurasync import chain, control, motor
from aurasync.chain import ChainContext, ChainValues
from aurasync.config import Instalacion, Parlante
from aurasync.dsp import decorrelate
from aurasync.dsp import decorrelation_bank as bank_mod
from aurasync.logbuffer import LogBuffer
from aurasync.service import Service
from aurasync.session import SessionOptions
from aurasync.simulated import ROOM_DELAYS_MS, ROOM_GAINS, Room, SimulatedObserver, SimulatedSession

SR = 48000


def octagon(n: int = 8) -> Instalacion:
    roles = list(control.ROLES["octagon"].values())
    return Instalacion(
        parlantes=[
            Parlante(f"JBL Go 4 #{k}", f"bluez_output.90_F2_60_00_00_{k:02X}.1", pan=p, ambiente=a)
            for k, (p, a) in enumerate(roles[:n])
        ]
    )


def mix_chain() -> ChainValues:
    return ChainValues().with_change(chain.validate_set("decorrelate", params={"assignment": "mix"}))


def through_the_cut(m: motor.Motor) -> None:
    for _ in range(4):
        m.procesar(np.zeros(4096), np.zeros(4096))


# -- 1. the engine and the chain with 7 and 8 ---------------------------------------------------


@pytest.mark.parametrize("n", [7, 8])
def test_the_engine_builds_and_plays_with_more_speakers_than_fixed_filters(n):
    inst = octagon(n)
    m = motor.Motor(inst, SR, ecualizar=True)
    rng = np.random.default_rng(0)
    out = m.procesar(rng.standard_normal(4096) * 0.1, rng.standard_normal(4096) * 0.1)
    assert sorted(out) == sorted(p.nombre for p in inst.parlantes)
    assert all(np.isfinite(x).all() for x in out.values())
    metrics = m.metricas_cadena()["decorrelate"]
    assert metrics["notice"].startswith(f"con {n} parlantes la separación es menor")
    assert metrics["assignment_mode"] == "order"
    assert list(metrics["assignment"].values()) == list(range(n))
    assert 0 < metrics["worst_above_500"] < 0.6


def test_the_chain_offers_the_decorrelator_with_eight_and_says_how_far_apart():
    eight = ChainContext.of(octagon(8))
    six = ChainContext.of(octagon(6))
    change = chain.validate_set("decorrelate", "group_delay", context=eight)
    assert change.algorithm == "group_delay"
    for context, expected in ((eight, "con 8 parlantes"), (six, None)):
        stage = next(s for s in chain.describe(ChainValues(), context)["stages"] if s["id"] == "decorrelate")
        algo = next(a for a in stage["algorithms"] if a["id"] == "group_delay")
        assert algo["available"]
        assert algo["unavailable_reason"] is None
        if expected is None:
            assert algo["notice"] is None
        else:
            assert algo["notice"].startswith(expected)
    # The chain and the engine quote the same number: they share the bank.
    engine = motor.Motor(octagon(8), SR).metricas_cadena()["decorrelate"]["notice"]
    stage = next(s for s in chain.describe(ChainValues(), eight)["stages"] if s["id"] == "decorrelate")
    assert next(a for a in stage["algorithms"] if a["id"] == "group_delay")["notice"] == engine


# -- 2. which filter goes to which speaker ----------------------------------------------------


def test_mix_assignment_follows_the_model_and_order_is_the_default():
    inst = octagon(8)
    b = bank_mod.bank(8)
    expected = bank_mod.assign(b, [(p.pan, p.ambiente) for p in inst.parlantes])
    assert expected != list(range(8))  # otherwise this test would prove nothing
    by_mix = motor.Motor(inst, SR, chain=mix_chain()).metricas_cadena()["decorrelate"]
    assert list(by_mix["assignment"].values()) == expected
    assert by_mix["assignment_mode"] == "mix"
    assert by_mix["worst_feeds"] < motor.Motor(inst, SR).metricas_cadena()["decorrelate"]["worst_feeds"]


def test_choosing_mix_goes_through_the_cut():
    """With `transition=cut`; with the crossfade (the default) the new assignment crossfades."""
    for mode, how in (("cut", "cut"), ("crossfade", "crossfade")):
        m = motor.Motor(octagon(8), SR, chain=ChainValues().with_algorithm("transition", mode))
        assert m.aplicar_cadena(mix_chain().with_algorithm("transition", mode)) == how
        assert list(m.metricas_cadena()["decorrelate"]["assignment"].values()) == list(range(8))
        through_the_cut(m)
        assert list(m.metricas_cadena()["decorrelate"]["assignment"].values()) != list(range(8))


def test_a_pan_that_changes_the_best_assignment_waits_for_a_cut():
    inst = octagon(8)
    m = motor.Motor(inst, SR, chain=mix_chain())
    before = list(m.metricas_cadena()["decorrelate"]["assignment"].values())
    # FL takes FR's pan (the ambience, and so the Haas delay, stays): the best assignment changes.
    inst.parlantes[0].pan = inst.parlantes[1].pan
    wanted = bank_mod.assign(bank_mod.bank(8), [(p.pan, p.ambiente) for p in inst.parlantes])
    assert wanted != before
    m.actualizar_desde_control()
    through_the_cut(m)
    metrics = m.metricas_cadena()["decorrelate"]
    assert metrics["reassign_pending"]
    assert list(metrics["assignment"].values()) == before  # nothing cut on its own
    m.cortar()
    through_the_cut(m)
    metrics = m.metricas_cadena()["decorrelate"]
    assert not metrics["reassign_pending"]
    assert list(metrics["assignment"].values()) == wanted


def test_a_pending_assignment_does_not_undo_a_new_bank_in_the_same_cut():
    inst = octagon(8)
    m = motor.Motor(inst, SR, chain=mix_chain())
    inst.parlantes[0].pan = inst.parlantes[1].pan
    m.actualizar_desde_control()
    assert m.metricas_cadena()["decorrelate"]["reassign_pending"]
    other_seed = mix_chain().with_change(chain.validate_set("decorrelate", params={"seed": 7}))

    def load_preset() -> None:  # as `Service.preset_load` does, inside a cut of its own
        m.aplicar_cadena(other_seed, en_corte=True)

    m.cortar(load_preset)
    through_the_cut(m)
    new_bank = bank_mod.bank(8, seed=7)
    order = bank_mod.assign(new_bank, [(p.pan, p.ambiente) for p in inst.parlantes])
    assert list(m.metricas_cadena()["decorrelate"]["assignment"].values()) == order
    playing = m._filtros  # noqa: SLF001
    for p, k in zip(inst.parlantes, order, strict=True):
        assert np.array_equal(playing[p.nombre], new_bank.filters[k])


def test_with_the_default_order_a_pan_never_asks_for_a_reassignment():
    inst = octagon(8)
    m = motor.Motor(inst, SR)
    inst.parlantes[0].pan = inst.parlantes[7].pan
    m.actualizar_desde_control()
    assert not m.metricas_cadena()["decorrelate"]["reassign_pending"]


# -- 3. roles by angle -------------------------------------------------------------------------


def test_the_formula_gives_exactly_todays_roles():
    assert control.ROLES["quad"] == {"FL": (-0.7, 0.15), "FR": (0.7, 0.15), "RL": (-0.7, 0.55), "RR": (0.7, 0.55)}
    assert control.ROLES["lcrs"] == {"FL": (-0.7, 0.15), "FC": (0.0, 0.1), "FR": (0.7, 0.15), "RC": (0.0, 0.55)}


@pytest.mark.parametrize(("layout", "count"), [("5.0", 5), ("hex", 6), ("7.0", 7), ("octagon", 8), ("rings", 8)])
def test_every_layout_has_distinct_roles_in_range(layout, count):
    roles = control.ROLES[layout]
    assert len(roles) == count
    assert len(set(roles.values())) == count  # `role_of` could not tell two apart otherwise
    for pan, ambience in roles.values():
        assert -1 <= pan <= 1
        assert 0 <= ambience <= 1
    for role, (pan, ambience) in roles.items():
        assert control.role_of(pan, ambience, layout) == role


def test_the_contract_takes_the_new_layouts_and_roles():
    for layout in ("5.0", "hex", "7.0", "octagon", "rings"):
        command = control.parse({"v": 1, "op": "set", "changes": {"layout": layout}})
        assert command.args["changes"]["layout"] == layout
    for role in ("SL", "SR", "WL", "WR", "OF", "OL", "OR", "OB"):
        assert control.parse({"v": 1, "op": "assign", "speaker": "A", "role": role}).args["role"] == role
    with pytest.raises(control.ContractError) as caught:
        control.parse({"v": 1, "op": "assign", "speaker": "A", "role": "XL"})
    assert caught.value.code == "out_of_range"


# -- 4. the simulated room and the service with eight ----------------------------------------


def test_the_simulated_room_has_eight_distinct_speakers():
    names = [p.nombre for p in octagon(8).parlantes]
    room = Room(names, SR)
    assert len(set(room.delays.values())) == 8
    assert len(set(room.gains.values())) == 8
    assert len(ROOM_DELAYS_MS) == len(ROOM_GAINS) == 8


@pytest.fixture
def service8(tmp_path):
    inst = octagon(8)
    inst.guardar(tmp_path / "i.json")
    service = Service(
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


def test_the_simulated_service_plays_with_eight_speakers(service8):
    """What `aurasync service --simular` does with an installation of 8: until 2026-10-02 the
    `start` failed (`conflict`, the engine's `ValueError`)."""
    assert ok(service8, op="state")["chain_summary"]["decorrelate"] == "group_delay"
    ok(service8, op="start", recalibrate=False)
    state = ok(service8, op="state")
    assert state["session"]["status"] == "playing"
    assert len(state["speakers"]) == 8
    stage = next(s for s in ok(service8, op="chain")["stages"] if s["id"] == "decorrelate")
    assert next(a for a in stage["algorithms"] if a["id"] == "group_delay")["notice"].startswith("con 8 parlantes")
    ok(service8, op="stop")


def test_the_service_assigns_octagon_roles(service8):
    ok(service8, op="set", changes={"layout": "octagon"})
    state = ok(service8, op="state")
    assert [p["role"] for p in state["speakers"]] == list(control.ROLES["octagon"])
    assert state["roles"]["octagon"] == list(control.ROLES["octagon"])
    ok(service8, op="set", changes={"layout": "quad"})
    reply = service8.handle({"v": 1, "op": "assign", "speaker": "JBL Go 4 #0", "role": "WL"})
    assert reply["error"]["code"] == "out_of_range"  # WL is an octagon role, not a quad one


def test_more_than_the_fixed_limit_is_not_special_below_it():
    """Up to `MAXIMO_FIJOS` nothing is said: the notice is only for what experiment 16 changed."""
    assert decorrelate.MAXIMO_FIJOS == 6
    assert motor.Motor(octagon(6), SR).metricas_cadena()["decorrelate"]["notice"] is None
