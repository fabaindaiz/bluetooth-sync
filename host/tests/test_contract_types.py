"""The panel's TypeScript types (d-7c8794-6da524): generated from the contract, checked
against the real producers, and never stale.

`contract_types.py` writes `web/src/contract.gen.ts`. These tests run what the service really
sends (the chain's description, a `chain_set` reply, the snapshot, the stream's `quality`,
`chain` and `radio` events) through `conforms()`, so a field the producer adds, drops or retypes
fails here before the web application reads it wrong.
"""

import threading
import time

import pytest

from aurasync import chain, contract_types
from aurasync.config import Instalacion, Parlante
from aurasync.contract_types import (
    ChainChangeReply,
    ChainDescription,
    QualityEvent,
    RadioEvent,
    RadioLogStatus,
    StageMetrics,
    StateView,
    conforms,
)
from aurasync.dsp import profiles
from aurasync.logbuffer import LogBuffer
from aurasync.service import Service
from aurasync.session import SessionOptions
from aurasync.simulated import SimulatedObserver, SimulatedRadio, SimulatedSession, simulated_log_level

SINKS = ("bluez_output.90_F2_60_75_4A_83.1", "bluez_output.90_F2_60_DA_66_6D.1", "bluez_output.90_F2_60_E3_07_39.1")


def test_the_generated_file_is_up_to_date():
    current = contract_types.GENERATED.read_text(encoding="utf-8")
    assert current == contract_types.render(), "web/src/contract.gen.ts is stale: run `hatch run gen-types`"


def test_the_check_mode_says_stale(tmp_path, monkeypatch, capsys):
    stale = tmp_path / "contract.gen.ts"
    stale.write_text(contract_types.render().replace("ChainParam", "ChainKnob"), encoding="utf-8")
    monkeypatch.setattr(contract_types, "GENERATED", stale)
    assert contract_types.main(["--check"]) == 1
    assert "stale" in capsys.readouterr().err
    stale.write_text(contract_types.render(), encoding="utf-8")
    assert contract_types.main(["--check"]) == 0


@pytest.mark.parametrize("speakers", [("JBL Go 4 Red", "JBL Go 4 Blue"), ("JBL Go 4 Red", "JBL Charge 6")])
def test_the_chain_description_conforms(speakers):
    """With and without a bass-capable speaker: `crossover` unavailable, then available."""
    context = chain.ChainContext(tuple((n, profiles.guess(n)) for n in speakers))
    values = chain.ChainValues().with_algorithm("limiter", "true_peak")
    described = chain.describe(values, context)
    assert conforms(described, ChainDescription) == []
    assert conforms(chain.describe(chain.ChainValues()), ChainDescription) == []


def test_conforms_sees_a_missing_an_extra_and_a_wrong_field():
    described = chain.describe(chain.ChainValues())
    param = described["stages"][0]["algorithms"][0]["params"][0]
    param["surprise"] = 1
    del param["unit"]
    param["kind"] = "slider"
    problems = conforms(described, ChainDescription)
    assert any("surprise" in p for p in problems), problems
    assert any(p.endswith("unit: missing (ChainParam)") for p in problems), problems
    assert any("'slider' is not one of" in p for p in problems), problems
    # A bool is not a number, and an int field takes no fraction.
    assert conforms(True, float) != []
    assert conforms(2.5, int) != []
    assert conforms(2.0, int) == []


def _wait(predicate, timeout):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        value = predicate()
        if value:
            return value
        time.sleep(0.05)
    pytest.fail("timed out")


@pytest.fixture
def simulated(tmp_path):
    """The service as `aurasync service --simular` builds it, with its radio and log level."""
    inst = Instalacion(
        parlantes=[
            Parlante("JBL Go 4 Red", SINKS[0], pan=-0.7, ambiente=0.15),
            Parlante("JBL Go 4 Black", SINKS[1], pan=0.7, ambiente=0.15),
            Parlante("JBL Go 4 Blue", SINKS[2], ambiente=0.55),
        ]
    )
    inst.guardar(tmp_path / "inst.json")
    level = simulated_log_level(tmp_path / "cambios-de-sistema.txt")
    radio = SimulatedRadio(lambda: list(SINKS), lambda: level.mode is not None, drop_every_s=1.0, seed=3)
    svc = Service(
        tmp_path / "inst.json",
        tmp_path / "presets.json",
        options=SessionOptions(block=1024, microphone="simulado"),
        session_factory=SimulatedSession,
        observer=SimulatedObserver(inst),
        simulated=True,
        log=lambda _: None,
        logs=LogBuffer(),
        radio=radio,
        log_level=level,
    )
    thread = threading.Thread(target=svc.run, daemon=True)
    thread.start()
    yield svc
    svc.handle({"v": 1, "op": "shutdown"})
    thread.join(timeout=10)
    svc.close()


def _ok(svc, **message):
    reply = svc.handle({"v": 1, **message})
    assert reply["ok"], reply
    return reply["result"]


def test_what_the_simulated_service_sends_conforms(simulated):
    svc = simulated
    assert conforms(_ok(svc, op="state"), StateView) == []  # stopped: quality and ab are null
    _ok(svc, op="start")
    _ok(svc, op="source", kind="tone")
    radio_log = _ok(svc, op="radio_log", active=True)
    assert conforms(radio_log, RadioLogStatus) == []
    quality = _wait(lambda: svc.quality, 10)
    metrics = _wait(lambda: svc.chain_metrics, 10)
    _wait(lambda: svc.radio_view()["speakers"], 10)
    assert conforms(quality, QualityEvent) == []
    assert conforms(metrics, dict[str, StageMetrics]) == []
    assert set(metrics) == {s.id for s in chain.CHAIN}
    assert conforms(svc.radio_view(), RadioEvent) == []
    reply = _ok(svc, op="chain_set", stage="limiter", params={"release_ms": 400})
    assert conforms(reply, ChainChangeReply) == []
    assert conforms(_ok(svc, op="chain_reset", stage="limiter", param="release_ms"), ChainChangeReply) == []
    assert conforms(_ok(svc, op="chain"), ChainDescription) == []
    # The loop's residual, as the snapshot carries it once the loop measured.
    svc.session.last_residual = {"residual_ms": 0.42, "measured_at": "2026-10-02T12:00:00-03:00", "t": 0.0,
                                 "speakers": ["JBL Go 4 Black", "JBL Go 4 Red"]}  # fmt: skip
    state = _wait(lambda: (s := _ok(svc, op="state"))["sync"]["residual_ms"] and s, 5)
    assert conforms(state, StateView) == []
    assert state["sync"]["residual_ms"] == 0.42
    assert state["sync"]["age_s"] > 0
    assert [sp["battery_pct"] for sp in state["speakers"]] == [90, 75, 60]
