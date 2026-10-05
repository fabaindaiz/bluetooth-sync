"""A phone's point measurement, server side (spec 2026-10-03 §6, step 3): the server clock, the probe
each speaker sent, and the measurement the phone sends back. The phone is played here by Python:
its "recording" is the reference delayed by known amounts. SIMULADO."""

import base64
import threading
import time

import numpy as np

from aurasync import probe_measure
from aurasync.config import Instalacion, Parlante
from aurasync.logbuffer import LogBuffer
from aurasync.service import Service
from aurasync.session import SessionOptions
from aurasync.simulated import SimulatedObserver, SimulatedSession

SR = 48000


def _service(tmp_path):
    inst = Instalacion(
        parlantes=[Parlante(f"s{i}", f"sink{i}", pan=p, ambiente=0.2) for i, p in enumerate((-0.7, 0.7, 0.0))]
    )
    inst.guardar(tmp_path / "i.json")
    return Service(
        tmp_path / "i.json",
        tmp_path / "p.json",
        options=SessionOptions(microphone="sim", block=1024),
        session_factory=SimulatedSession,
        observer=SimulatedObserver(inst),
        simulated=True,
        log=lambda _: None,
        logs=LogBuffer(),
    )


def call(svc, **message):
    return svc.handle({"v": 1, **message})


def ok(svc, **message):
    reply = call(svc, **message)
    assert reply["ok"], reply
    return reply["result"]


def test_a_phone_measures_against_the_probe_the_server_sent(tmp_path):
    svc = _service(tmp_path)
    thread = threading.Thread(target=svc.run, daemon=True)
    thread.start()
    try:
        ok(svc, op="start")
        ok(svc, op="set", changes={"probe": True})
        time.sleep(4.0)  # the probe ramps up and the ring fills
        now = ok(svc, op="sync_time")["t"]
        span_from, seconds = now - 3.0, 2.0
        ref = ok(svc, op="probe_reference", **{"from": span_from, "seconds": seconds})
        assert ref["sr"] == SR
        probes = {
            name: np.frombuffer(base64.b64decode(b64), dtype="<i2").astype(float) * ref["scale"]
            for name, b64 in ref["speakers"].items()
        }
        assert set(probes) == {"s0", "s1", "s2"}
        assert all(np.sqrt(np.mean(p**2)) > 0 for p in probes.values())
        # The phone's recording: each speaker's probe, delayed as the room would, and summed.
        delays_ms = {"s0": 12.0, "s1": 19.5, "s2": 15.25}
        n = len(probes["s0"]) + SR // 2
        mic = np.zeros(n)
        for name, p in probes.items():
            k = round(delays_ms[name] * SR / 1000)
            mic[k : k + len(p)] += p
        mic += np.random.default_rng(4).standard_normal(n) * 1e-5
        m = probe_measure.measure(mic, probes, SR)
        assert m.valid == frozenset(delays_ms)
        reply = ok(
            svc,
            op="sync_measure",
            measurement={
                "source_id": "phone-1",
                "position_id": "sofa",
                "kind": "point",
                "role": "target",
                "t": span_from + seconds / 2,
                "arrivals_ms": {name: m.arrivals_ms[name] for name in m.valid},
                "halves_ms": {name: list(m.halves_ms[name]) for name in m.valid},
                "quality": {"echo_cancellation": False, "noise_suppression": False, "auto_gain_control": False},
                "origin": "browser",
            },
        )
        assert reply["accepted"] is True
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not any(
            s.get("source_id") == "phone-1" for s in ok(svc, op="sync_state")["sources"]
        ):
            time.sleep(0.1)
        assert any(s.get("source_id") == "phone-1" for s in ok(svc, op="sync_state")["sources"])
        # Errors say what happened.
        assert call(svc, op="probe_reference", **{"from": now - 600, "seconds": 2.0})["error"]["code"] == "out_of_range"
        assert call(svc, op="sync_measure", measurement={"kind": "point"})["error"]["code"] in {
            "bad_request",
            "unknown_field",
        }
    finally:
        call(svc, op="shutdown")
        thread.join(timeout=5)
        svc.close()
