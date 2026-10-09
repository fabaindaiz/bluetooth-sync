"""A Bluetooth microphone of a device that is an output in use is never opened (2026-10-09,
HP-O16): opening the WH-CH520's own microphone switched the headphones to the headset profile and
the monitor went silent. SIMULATED: no PipeWire."""

import threading
import time

import pytest

from aurasync import microphones
from aurasync.config import Instalacion, Parlante
from aurasync.logbuffer import LogBuffer
from aurasync.service import Service
from aurasync.session import SessionError, SessionOptions
from aurasync.simulated import SimulatedMonitor, SimulatedObserver, SimulatedSession

PHONES_MIC = "bluez_input.14:06:A7:6B:E3:F0"
"""As WirePlumber lists the headphones' microphone while they play A2DP (VERIFICADO, HP-O16)."""
PHONES_MIC_HFP = "bluez_input.14_06_A7_6B_E3_F0.0"
"""As it is named once the headset profile is on (the journal, HP-O16)."""
PHONES = "bluez_output.14_06_A7_6B_E3_F0.1"
SPEAKER = "bluez_output.AA_BB_CC_DD_EE_01.1"
SPEAKER_MIC = "bluez_input.AA:BB:CC:DD:EE:01"


# -- the rule ------------------------------------------------------------------------------------


def test_the_address_is_read_from_both_spellings_of_a_bluetooth_node():
    assert microphones.bluetooth_address(PHONES_MIC) == "14:06:A7:6B:E3:F0"
    assert microphones.bluetooth_address(PHONES_MIC_HFP) == "14:06:A7:6B:E3:F0"
    assert microphones.bluetooth_address(PHONES) == "14:06:A7:6B:E3:F0"
    assert microphones.bluetooth_address("bluez_output.14_06_a7_6b_e3_f0.1") == "14:06:A7:6B:E3:F0"
    assert microphones.bluetooth_address("alsa_input.pci-0000_00_1f.3.HiFi__Mic1__source") is None
    assert microphones.bluetooth_address(None) is None


def test_a_name_without_the_address_falls_back_to_the_bluez_property():
    """Older PipeWire names (`bluez_source.*`) or a renamed node: `api.bluez5.address` says the device."""
    assert microphones.bluetooth_address("bluez_source.14_06_A7_6B_E3_F0.headset_head_unit") == "14:06:A7:6B:E3:F0"
    assert microphones.bluetooth_address("headset-mic", "14:06:A7:6B:E3:F0") == "14:06:A7:6B:E3:F0"
    assert microphones.bluetooth_address("headset-mic", "not an address") is None
    used = [microphones.OutputInUse("phones", "monitor", "WH-CH520", "14:06:a7:6b:e3:f0")]
    assert microphones.blocked_reason("headset-mic", used, "14:06:A7:6B:E3:F0") is not None
    assert microphones.blocked_reason("headset-mic", used) is None


def test_the_monitors_own_microphone_is_blocked_with_the_reason():
    used = [microphones.OutputInUse(PHONES, "monitor", "WH-CH520")]
    for node in (PHONES_MIC, PHONES_MIC_HFP):
        reason = microphones.blocked_reason(node, used)
        assert reason is not None
        assert "WH-CH520" in reason
        assert "monitor" in reason
        assert "otro micrófono" in reason
    assert microphones.blocked_reason("alsa_input.mic", used) is None
    assert microphones.blocked_reason(SPEAKER_MIC, used) is None


def test_a_speakers_microphone_is_blocked_too():
    used = [microphones.OutputInUse(SPEAKER, "speaker", "Red")]
    reason = microphones.blocked_reason(SPEAKER_MIC, used)
    assert reason is not None
    assert "Red" in reason
    assert "parlante" in reason


def test_marking_adds_the_reason_to_each_microphone():
    used = [microphones.OutputInUse(PHONES, "monitor", "WH-CH520")]
    marked = microphones.mark(
        [{"node": PHONES_MIC, "description": "WH-CH520"}, {"node": "m", "description": "M"}], used
    )
    assert marked[0]["blocked_reason"] is not None
    assert marked[1] == {"node": "m", "description": "M", "blocked_reason": None}


# -- the session never opens it, whoever asks (the loop's own restarts included) ----------------


def _session(guard):
    inst = Instalacion(parlantes=[Parlante("a", "sink_a", pan=-0.7), Parlante("b", "sink_b", pan=0.7)])
    session = SimulatedSession(inst, _motor(inst), SessionOptions(block=1024), lambda *_a, **_k: None)
    session.microphone_guard = guard
    return session


def _motor(inst):
    from aurasync.motor import Motor

    return Motor(inst, 48000)


def test_the_session_refuses_a_blocked_microphone_for_the_check_and_the_calibration():
    session = _session(lambda node: "blocked" if node == PHONES_MIC else None)
    session.open()
    try:
        with pytest.raises(SessionError) as check:
            session.check_microphone(PHONES_MIC)
        assert check.value.code == "unavailable"
        with pytest.raises(SessionError):
            session.start_calibration(2.0, 0.1, PHONES_MIC)
        assert session.calibration is None
        assert session.pids()["microphone"] is None
        assert session.mic_check is False
    finally:
        session.close()


def test_the_loop_never_opens_a_blocked_microphone_even_when_it_restarts_by_itself():
    session = _session(lambda node: "blocked" if node == PHONES_MIC else None)
    session.open()
    try:
        session.enable_recalibration(PHONES_MIC)  # no error: the loop is a side channel
        assert session.loop is None
        assert session.pids()["microphone"] is None
    finally:
        session.close()


# -- the service: a clear refusal, the choice untouched, the state says why --------------------


def _service(tmp_path, microphone=PHONES_MIC):
    inst = Instalacion(
        parlantes=[Parlante("Red", SPEAKER, pan=-0.7, ambiente=0.2), Parlante("Blue", "sink1", pan=0.7, ambiente=0.2)]
    )
    inst.guardar(tmp_path / "i.json")
    observer = SimulatedObserver(inst)
    observer.view["sinks"] = [*observer.view["sinks"], {"node": PHONES, "description": "WH-CH520"}]
    observer.view["devices"] = [*observer.view["devices"], {"address": "14:06:A7:6B:E3:F0", "name": "WH-CH520"}]
    observer.view["microphones"] = [
        {"node": PHONES_MIC, "description": "WH-CH520"},
        {"node": SPEAKER_MIC, "description": "Red"},
        {"node": "simulado", "description": "Micrófono simulado"},
    ]
    return Service(
        tmp_path / "i.json",
        tmp_path / "p.json",
        options=SessionOptions(microphone=microphone, block=1024),
        session_factory=SimulatedSession,
        observer=observer,
        simulated=True,
        measurements_path=tmp_path / "m",
        log=lambda _: None,
        logs=LogBuffer(),
        monitor_factory=SimulatedMonitor,
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


def call(service, **message):
    return service.handle({"v": 1, **message})


def ok(service, **message):
    reply = call(service, **message)
    assert reply["ok"], reply
    return reply["result"]


def _state(service, until, timeout=5.0):
    deadline = time.monotonic() + timeout
    while True:
        state = ok(service, op="state")
        if until(state) or time.monotonic() > deadline:
            return state
        time.sleep(0.05)


def _monitor_on_the_phones(service):
    ok(service, op="start", recalibrate=False)
    ok(service, op="monitor_set", mode="mix", target=PHONES)
    _state(service, lambda s: s["monitor"]["state"] == "on")


def test_mic_check_refuses_the_monitors_own_microphone_and_never_opens_it(svc):
    _monitor_on_the_phones(svc)
    reply = call(svc, op="mic_check")
    assert reply["error"]["code"] == "unavailable"
    assert "WH-CH520" in reply["error"]["message"]
    assert "monitor" in reply["error"]["message"]
    assert svc.session.pids()["microphone"] is None
    assert svc.options.microphone == PHONES_MIC  # the choice stays: the panel asks for another one


def test_calibrate_and_the_loop_refuse_it_too(svc):
    _monitor_on_the_phones(svc)
    assert call(svc, op="calibrate", seconds=5)["error"]["code"] == "unavailable"
    assert svc.session.calibration is None
    assert call(svc, op="recalibrate", active=True)["error"]["code"] == "unavailable"
    assert svc.session.loop is None
    assert svc.session.pids()["microphone"] is None


def test_choosing_it_is_refused_and_another_one_is_taken(svc):
    _monitor_on_the_phones(svc)
    ok(svc, op="microphone_set", node="simulado")
    reply = call(svc, op="microphone_set", node=PHONES_MIC)
    assert reply["error"]["code"] == "unavailable"
    assert svc.options.microphone == "simulado"
    assert ok(svc, op="mic_check") == {"opened": True}


def test_the_state_marks_each_blocked_microphone_with_why(svc):
    ok(svc, op="monitor_set", mode="mix", target=PHONES)
    state = _state(svc, lambda s: any(m.get("blocked_reason") for m in s["microphones"]))
    by_node = {m["node"]: m for m in state["microphones"]}
    assert "monitor" in by_node[PHONES_MIC]["blocked_reason"]
    assert "WH-CH520" in by_node[PHONES_MIC]["blocked_reason"]
    assert "Red" in by_node[SPEAKER_MIC]["blocked_reason"]  # a speaker of the installation
    assert by_node["simulado"]["blocked_reason"] is None
    ok(svc, op="monitor_set", mode="off")
    state = _state(svc, lambda s: not any(m["node"] == PHONES_MIC and m["blocked_reason"] for m in s["microphones"]))
    assert {m["node"]: m["blocked_reason"] for m in state["microphones"]}[PHONES_MIC] is None


def test_a_session_asked_with_the_loop_plays_without_it(tmp_path):
    service = _service(tmp_path, microphone=SPEAKER_MIC)  # Red's own microphone
    thread = threading.Thread(target=service.run, daemon=True)
    thread.start()
    try:
        ok(service, op="start", recalibrate=True)
        assert service.session.loop is None
        assert service.session.pids()["microphone"] is None
        assert service.options.microphone == SPEAKER_MIC
    finally:
        service.handle({"v": 1, "op": "shutdown"})
        thread.join(timeout=5)
        service.close()
