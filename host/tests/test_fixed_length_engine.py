"""The engine thread works on fixed-length data; the history goes apart, asynchronously.

On 2026-10-02 the speakers cut after 15-30 min of playing and stopped when the session was
restarted. One thing that grew with the session on the engine thread: `QualityMeter.summary`
rebuilt the integrated loudness from every 100 ms step since the start, twice a second
(0.33 ms at the start, 1.9 ms at 15 min, linear; experimentos/12 §4.1). The rule (user,
2026-10-03): the stream is always processed over fixed-length data, and the history is kept
apart, asynchronously.
"""

import threading
import time

import numpy as np
import pytest

from aurasync.cuts import CutLog
from aurasync.dsp.loudness import GatedIntegrator, LoudnessMeter
from aurasync.quality import QualityMeter

SR = 48000
BLOCK = 4096


def _program(seconds, seed):
    """Noise whose level moves in 2 s sections between -60 and -10 dBFS: both gates bite."""
    rng = np.random.default_rng(seed)
    n = int(seconds * SR)
    sections = rng.uniform(-60, -10, int(np.ceil(seconds / 2)))
    gain = 10 ** (np.repeat(sections, 2 * SR)[:n] / 20)
    return rng.standard_normal(n) * gain


def test_a_meter_without_history_keeps_only_its_window_and_reads_the_same():
    x = _program(60, 1)
    full, bounded = LoudnessMeter(SR, 1), LoudnessMeter(SR, 1, history=False)
    for i in range(0, len(x), BLOCK):
        full.push(x[i : i + BLOCK])
        bounded.push(x[i : i + BLOCK])
        assert bounded.momentary == pytest.approx(full.momentary, abs=1e-9)
        assert bounded.short_term == pytest.approx(full.short_term, abs=1e-9)
    assert len(bounded._steps) == bounded._short_steps  # noqa: SLF001
    assert bounded.steps_total == full.steps_total == len(full._steps)  # noqa: SLF001


def test_the_gated_integrator_matches_the_full_history():
    x = _program(120, 2)
    meter = LoudnessMeter(SR, 1)
    meter.push(x)
    integrator = GatedIntegrator(meter._momentary_steps, meter._step_n)  # noqa: SLF001
    for e in meter.step_energies:
        integrator.push(float(e))
    assert integrator.integrated == pytest.approx(meter.integrated, abs=0.01)


def test_the_gated_integrator_has_fixed_size():
    integrator = GatedIntegrator(4, 4800)
    size = integrator.nbytes
    rng = np.random.default_rng(3)
    for e in rng.uniform(1e-6, 1e3, 50_000):
        integrator.push(float(e))
    assert integrator.nbytes == size


def test_silence_and_too_little_audio_read_minus_infinity():
    integrator = GatedIntegrator(4, 4800)
    assert integrator.integrated == -np.inf
    for _ in range(10):
        integrator.push(0.0)
    assert integrator.integrated == -np.inf


def test_the_quality_meter_keeps_fixed_length_state_on_the_engine_thread():
    """After minutes of audio, nothing the engine thread holds has grown."""
    left, right = _program(180, 4), 0.5 * _program(180, 5)
    meter = QualityMeter(SR, ["A", "B", "C"])
    try:
        for i in range(0, len(left), BLOCK):
            pair = (left[i : i + BLOCK], right[i : i + BLOCK])
            meter.push(pair, {"A": pair[0], "B": pair[1], "C": pair[0]})
        for m in (*meter.channels, *meter.outputs.values()):
            assert len(m._steps) <= m._short_steps  # noqa: SLF001
        assert meter.history.wait_idle()
        stereo = LoudnessMeter(SR, 2)
        stereo.push(np.column_stack([left, right]))
        assert meter.summary()["input"]["i"] == pytest.approx(stereo.integrated, abs=0.051)
    finally:
        meter.close()


def test_the_cut_log_hands_out_only_what_is_new():
    """`CutReporter` reads every block: it must not copy the whole log each time."""
    cuts = CutLog()
    for i in range(5):
        cuts.add("low", "salida", str(i))
    first = cuts.since(0)
    assert [e["detail"] for e in first] == ["0", "1", "2", "3", "4"]
    seq = first[-1]["seq"]
    assert cuts.since(seq) == []
    cuts.add("underrun", "salida", "5")
    assert [e["detail"] for e in cuts.since(seq)] == ["5"]


def test_the_cut_summary_is_built_off_the_engine_thread():
    """With a full log the summary costs ~0.7 ms (MEDIDO, PC-Ryzen5): the snapshot, built every
    block on the engine thread, reads the last one built apart."""
    cuts = CutLog()
    built_on: list[str] = []
    real = cuts.summary

    def spy():
        built_on.append(threading.current_thread().name)
        return real()

    cuts.summary = spy
    try:
        assert cuts.latest()["faults_10min"] == 0
        cuts.add("underrun", "salida", "vacía", level_ms=0.0)
        deadline = time.monotonic() + 3
        while cuts.latest()["faults_10min"] != 1 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert cuts.latest()["faults_10min"] == 1
        assert built_on
        assert set(built_on) == {"aurasync-cut-summary"}
    finally:
        cuts.close()


def test_the_simulated_room_keeps_a_bounded_buffer_nobody_reads():
    """`--simular` with no microphone reading: it kept every block (~690 MB in 30 min)."""
    from aurasync.simulated import ROOM_BUFFER_S, Room

    room = Room(["a", "b", "c"], SR)
    block = {n: np.zeros(BLOCK) for n in ("a", "b", "c")}
    for _ in range(int(3 * ROOM_BUFFER_S * SR / BLOCK)):
        room.play(block)
    heard = room.take()
    assert ROOM_BUFFER_S * SR - BLOCK <= len(heard) <= ROOM_BUFFER_S * SR
    room.play(block)
    assert len(room.take()) == BLOCK  # what is read right after is all there


def test_the_history_runs_on_its_own_thread():
    meter = QualityMeter(SR, ["A"])
    try:
        assert meter.history.thread.name == "aurasync-loudness-history"
        assert meter.history.thread.daemon
    finally:
        meter.close()
    assert not meter.history.thread.is_alive()
