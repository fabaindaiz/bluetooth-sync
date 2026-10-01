"""Motor simulado (docs/research/09 §3, §7 y §9). El estado se lleva a instantes
exactos con un reloj falso, sin dormir."""

import asyncio
import json
import logging
from datetime import datetime

import pytest

from aurasync.engine.base import CommandError, parse_command
from aurasync.engine.simulated import (
    CALIBRATION_SECONDS,
    SCAN_SECONDS,
    SETTLE_SECONDS,
    SYNC_SECONDS,
    SimulatedEngine,
)
from aurasync.logbuffer import LogBuffer
from aurasync.state import MODES, SILENCE_DB

PIPELINE = ("controller", "capture", "dsp", "emitter")


class FakeClock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def engine(clock: FakeClock) -> SimulatedEngine:
    return SimulatedEngine(now=clock, wallclock=lambda: datetime(2026, 10, 1, 12, 0).astimezone(), platform="darwin")


def run(engine: SimulatedEngine, cmd: str, **args) -> None:
    asyncio.run(engine.apply(parse_command({"cmd": cmd, "args": args})))


def states(engine: SimulatedEngine) -> dict[str, str]:
    return {s.name: s.state for s in engine.snapshot().services}


def start_and_settle(engine: SimulatedEngine, clock: FakeClock) -> None:
    run(engine, "start")
    clock.t += SETTLE_SECONDS


def assign_all(engine: SimulatedEngine) -> None:
    snap = engine.snapshot()
    for speaker, channel in zip(snap.speakers, MODES[snap.engine.mode], strict=True):
        run(engine, "assign", address=speaker.address, channel=channel)


# -- arranque y servicios ----------------------------------------------------------------


def test_starts_idle_with_its_services_listed(engine):
    snap = engine.snapshot()
    assert not snap.engine.running
    assert snap.controller.big.state == "idle"
    got = {s.name: (s.state, s.managed) for s in snap.services}
    for name in PIPELINE:
        assert got[name] == ("stopped", True)
    assert got["bass"] == ("unavailable", False)
    assert got["panel"] == ("running", False)
    assert got["coreaudiod"] == ("running", False)
    assert all(m.rms_db == SILENCE_DB for m in snap.meters.values())


def test_linux_lists_its_own_system_services(clock):
    names = {s.name for s in SimulatedEngine(now=clock, platform="linux").snapshot().services}
    assert {"bluetoothd", "pipewire", "wireplumber"} <= names
    assert "coreaudiod" not in names


def test_start_brings_the_pipeline_up_in_dependency_order(engine, clock):
    run(engine, "start")
    first = states(engine)
    assert first["emitter"] == "starting"
    assert not engine.snapshot().engine.running
    assert engine.snapshot().controller.big.state == "creating"
    clock.t += SETTLE_SECONDS
    assert all(states(engine)[name] == "running" for name in PIPELINE)
    snap = engine.snapshot()
    assert snap.engine.running
    assert snap.controller.big.state == "active"
    assert snap.controller.big.num_bis == len(MODES["quad"])


def test_running_services_report_pid_and_uptime(engine, clock):
    start_and_settle(engine, clock)
    clock.t += 5
    capture = engine.snapshot().service("capture")
    assert capture.pid is not None
    assert capture.uptime_s == pytest.approx(5 + SETTLE_SECONDS, abs=1)
    assert capture.started_at is not None


def test_stop_cuts_every_service_the_tone_and_the_calibration(engine, clock):
    assign_all(engine)
    start_and_settle(engine, clock)
    run(engine, "tone", channel="FL", seconds=5)
    run(engine, "calibrate")
    run(engine, "stop")
    clock.t += 1
    snap = engine.snapshot()
    assert all(snap.service(name).state == "stopped" for name in PIPELINE)
    assert snap.calibration.state == "idle"
    assert all(m.rms_db == SILENCE_DB for m in snap.meters.values())
    assert all(s.state == "seen" for s in snap.speakers)  # detener a propósito no es perderlos


def test_stopping_a_dependency_stops_its_dependents(engine, clock):
    start_and_settle(engine, clock)
    run(engine, "service_stop", name="controller")
    clock.t += 1
    got = states(engine)
    assert got["controller"] == "stopped"
    assert got["emitter"] == "stopped"
    assert got["capture"] == "running"
    assert got["dsp"] == "running"
    snap = engine.snapshot()
    assert not snap.engine.running
    assert snap.controller.iso_broadcaster is None  # sin enlace, no observado
    assert any(m.rms_db > SILENCE_DB for m in snap.meters.values())  # el audio sigue llegando


def test_starting_without_dependencies_is_refused(engine):
    with pytest.raises(CommandError, match="primero"):
        run(engine, "service_start", name="emitter")


def test_starting_a_running_service_changes_nothing(engine, clock):
    start_and_settle(engine, clock)
    before = engine.snapshot().service("capture")
    run(engine, "service_start", name="capture")
    after = engine.snapshot().service("capture")
    assert (after.state, after.pid, after.restarts) == ("running", before.pid, before.restarts)


def test_restart_counts_and_changes_the_pid(engine, clock):
    start_and_settle(engine, clock)
    pid = engine.snapshot().service("capture").pid
    run(engine, "service_restart", name="capture")
    clock.t += SETTLE_SECONDS
    capture = engine.snapshot().service("capture")
    assert capture.restarts == 1
    assert capture.state == "running"
    assert capture.pid != pid


def test_a_failed_emitter_loses_the_speakers_and_recovers(engine, clock):
    assign_all(engine)
    start_and_settle(engine, clock)
    clock.t += SYNC_SECONDS
    run(engine, "service_fail", name="emitter")
    snap = engine.snapshot()
    assert snap.service("emitter").state == "failed"
    assert snap.service("emitter").last_error
    assert snap.controller.big.state == "error"
    assert all(s.state == "lost" for s in snap.speakers if s.channel)
    run(engine, "service_start", name="emitter")
    clock.t += SETTLE_SECONDS + SYNC_SECONDS
    snap = engine.snapshot()
    assert snap.service("emitter").state == "running"
    assert snap.service("emitter").last_error is None
    assert all(s.state == "synced" for s in snap.speakers if s.channel)


def test_a_failed_capture_stops_everything_downstream(engine, clock):
    start_and_settle(engine, clock)
    run(engine, "service_fail", name="capture")
    clock.t += 1
    got = states(engine)
    assert got["capture"] == "failed"
    assert got["dsp"] == "stopped"
    assert got["emitter"] == "stopped"
    assert got["controller"] == "running"


# -- parlantes ------------------------------------------------------------------


def test_simulated_speakers_use_locally_administered_addresses(engine):
    for speaker in engine.snapshot().speakers:
        assert int(speaker.address.split(":")[0], 16) & 0x02


def test_assigned_speakers_sync_after_the_emitter_settles(engine, clock):
    first = engine.snapshot().speakers[0]
    run(engine, "assign", address=first.address, channel="FL")
    start_and_settle(engine, clock)
    assert engine.snapshot().speaker(first.address).state == "seen"
    clock.t += SYNC_SECONDS
    speaker = engine.snapshot().speaker(first.address)
    assert speaker.state == "synced"
    assert speaker.bis_index == 1


def test_scan_finds_one_more_speaker(engine, clock):
    before = len(engine.snapshot().speakers)
    run(engine, "scan")
    assert engine.snapshot().engine.scanning
    clock.t += SCAN_SECONDS
    snap = engine.snapshot()
    assert not snap.engine.scanning
    assert len(snap.speakers) == before + 1


def test_trim_updates_one_speaker(engine):
    address = engine.snapshot().speakers[2].address
    run(engine, "set_speaker_trim", address=address, delay_ms=12.5)
    run(engine, "set_speaker_trim", address=address, gain_db=-3)
    speaker = engine.snapshot().speaker(address)
    assert (speaker.delay_ms, speaker.gain_db) == (12.5, -3.0)


def test_changing_mode_clears_every_assignment(engine):
    assign_all(engine)
    run(engine, "set_mode", mode="lcrs")
    snap = engine.snapshot()
    assert all(s.channel is None for s in snap.speakers)
    assert tuple(snap.meters) == MODES["lcrs"]


# -- audio ------------------------------------------------------------------------


def test_tone_plays_only_on_its_channel_until_it_ends(engine, clock):
    start_and_settle(engine, clock)
    run(engine, "tone", channel="RL", seconds=2)
    meters = engine.snapshot().meters
    assert meters["RL"].rms_db > SILENCE_DB
    assert all(m.rms_db == SILENCE_DB for ch, m in meters.items() if ch != "RL")
    clock.t += 2.5
    assert all(m.rms_db > SILENCE_DB for m in engine.snapshot().meters.values())


def test_master_volume_moves_every_meter(engine, clock):
    start_and_settle(engine, clock)
    run(engine, "set_master", db=-10)
    loud = engine.snapshot().meters
    run(engine, "set_master", db=-40)
    quiet = engine.snapshot().meters
    for channel in MODES["quad"]:
        assert loud[channel].rms_db - quiet[channel].rms_db == pytest.approx(30)


# -- configuración y latencia -------------------------------------------------------


def test_live_config_does_not_restart_the_emitter(engine, clock):
    start_and_settle(engine, clock)
    run(engine, "set_config", key="upmix", value="surround")
    snap = engine.snapshot()
    assert snap.config.upmix == "surround"
    assert snap.service("emitter").state == "running"
    assert snap.service("emitter").restarts == 0


def test_broadcast_config_restarts_a_running_emitter(engine, clock):
    start_and_settle(engine, clock)
    run(engine, "set_config", key="presentation_delay_us", value=80000)
    snap = engine.snapshot()
    assert snap.service("emitter").state == "starting"
    assert snap.service("emitter").restarts == 1
    clock.t += SETTLE_SECONDS
    snap = engine.snapshot()
    assert snap.controller.big.presentation_delay_us == 80000
    assert snap.config.presentation_delay_us == 80000


def test_latency_estimate_follows_the_config(engine):
    first = engine.snapshot().latency
    run(engine, "set_config", key="transport", value="low_latency")
    run(engine, "set_config", key="presentation_delay_us", value=20000)
    second = engine.snapshot().latency
    assert second.total_ms < first.total_ms
    assert second.total_ms == pytest.approx(
        second.capture_ms + second.codec_ms + second.transport_ms + second.presentation_ms
    )


# -- calibración -----------------------------------------------------------------


def test_calibration_runs_and_its_result_can_be_applied(engine, clock):
    assign_all(engine)
    start_and_settle(engine, clock)
    run(engine, "calibrate")
    clock.t += CALIBRATION_SECONDS / 2
    assert engine.snapshot().calibration.progress == pytest.approx(0.5)
    clock.t += CALIBRATION_SECONDS
    cal = engine.snapshot().calibration
    assert cal.state == "done"
    assert cal.simulated
    run(engine, "calibration_apply")
    snap = engine.snapshot()
    by_channel = {r.channel: r for r in cal.results}
    for speaker in snap.speakers:
        if speaker.channel:
            assert speaker.delay_ms == by_channel[speaker.channel].delay_ms
            assert speaker.gain_db == by_channel[speaker.channel].gain_db


def test_simulated_measurements_are_never_saved(engine, clock):
    assign_all(engine)
    start_and_settle(engine, clock)
    run(engine, "calibrate")
    clock.t += CALIBRATION_SECONDS * 2
    with pytest.raises(CommandError, match="simulado"):
        run(engine, "save_measurement")


# -- reloj y logs ----------------------------------------------------------------


def test_drift_history_grows_with_running_time_up_to_a_minute(engine, clock):
    start_and_settle(engine, clock)
    clock.t += 10
    assert 9 <= len(engine.snapshot().clock.history) <= 11
    clock.t += 300
    assert len(engine.snapshot().clock.history) == 60


def test_state_is_a_function_of_the_clock(engine, clock):
    start_and_settle(engine, clock)
    clock.t += 42.3

    def without_seq() -> dict:
        data = json.loads(engine.snapshot().to_json())
        del data["seq"]
        return data

    assert without_seq() == without_seq()


def test_poll_logs_each_service_transition_once(engine, clock):
    buf = LogBuffer()
    root = logging.getLogger("aurasync")
    root.setLevel(logging.DEBUG)
    root.addHandler(buf)
    try:
        run(engine, "start")
        engine.poll()
        clock.t += SETTLE_SECONDS
        engine.poll()
        engine.poll()
        lines = [(r["service"], r["message"]) for r in buf.tail(200)]
        running = [line for line in lines if "→ running" in line[1]]
        assert {service for service, _ in running} == set(PIPELINE)
        assert len(running) == len(PIPELINE)
    finally:
        root.removeHandler(buf)
