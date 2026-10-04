"""The cuts reach the service log (cut_report.py): a cut that only lived in memory was lost
with its session on 2026-10-02, and with it the only evidence of what caused it."""

import threading
import time

from aurasync import cut_report
from aurasync.config import Instalacion, Parlante
from aurasync.cut_report import REPORT_S, CutReporter
from aurasync.cuts import CutLog
from aurasync.logbuffer import LogBuffer
from aurasync.service import Service
from aurasync.session import SessionOptions
from aurasync.simulated import SimulatedObserver, SimulatedSession


class Clock:
    t = 100.0

    def __call__(self):
        return self.t


def test_nothing_is_reported_without_faults():
    clock = Clock()
    cuts = CutLog(clock)
    reporter = CutReporter(cuts, clock)
    cuts.add("fade", None, "preset")  # intentional: never a fault
    clock.t += REPORT_S
    assert reporter.tick() is None


def test_a_window_of_faults_becomes_one_line_with_the_evidence():
    clock = Clock()
    cuts = CutLog(clock)
    reporter = CutReporter(cuts, clock)
    for _ in range(18):
        cuts.add("underrun", "salida", "vacía", level_ms=0.0)
    cuts.add("low", "salida", "quedaban 12 ms", level_ms=12.4)
    cuts.add("late", "motor", "95 ms tarde", late_ms=95)
    clock.t += REPORT_S / 2
    assert reporter.tick() is None  # not before the window closes
    clock.t += REPORT_S / 2
    line = reporter.tick(block_ms=6.1)
    assert line is not None
    assert "20 in 10 s" in line
    assert "underrun 18" in line
    assert "low 1" in line
    assert "late 1" in line
    assert "pipe min 0.0 ms" in line
    assert "latest late 95 ms" in line
    assert "engine 6.1 ms/block" in line
    # Each event is counted once: the next window starts empty.
    clock.t += REPORT_S
    assert reporter.tick() is None


def test_the_session_total_outlives_the_cut_log_capacity():
    clock = Clock()
    cuts = CutLog(clock)
    reporter = CutReporter(cuts, clock)
    for _ in range(40):
        for _ in range(20):
            cuts.add("underrun", "salida", "vacía", level_ms=0.0)
        clock.t += REPORT_S
        reporter.tick()
    # 800 events, more than CutLog keeps (300): the total still has them all.
    total = reporter.session_summary()
    assert "800 faults" in total
    assert "underrun 800" in total


def test_the_session_summary_counts_what_was_not_reported_yet():
    clock = Clock()
    cuts = CutLog(clock)
    reporter = CutReporter(cuts, clock)
    cuts.add("xrun", "JBL Go 4 Red", "pw-top")
    clock.t += 1.0
    summary = reporter.session_summary()
    assert "1 faults" in summary
    assert "xrun 1" in summary


def test_a_clean_session_says_so():
    clock = Clock()
    reporter = CutReporter(CutLog(clock), clock)
    clock.t += 600
    assert "no faults" in reporter.session_summary()


def test_the_service_writes_the_cuts_to_its_log(tmp_path, monkeypatch):
    """End to end: the simulated service, a session playing, faults in its cut log."""
    monkeypatch.setattr(cut_report, "REPORT_S", 0.3)
    inst = Instalacion(parlantes=[Parlante(f"s{i}", f"sink{i}", pan=(-0.7, 0.7, 0.0)[i]) for i in range(3)])
    inst.guardar(tmp_path / "i.json")
    lines: list[str] = []
    service = Service(
        tmp_path / "i.json",
        tmp_path / "p.json",
        options=SessionOptions(microphone="sim", block=1024),
        session_factory=SimulatedSession,
        observer=SimulatedObserver(inst),
        simulated=True,
        measurements_path=tmp_path / "mediciones",
        log=lines.append,
        logs=LogBuffer(),
    )
    thread = threading.Thread(target=service.run, daemon=True)
    thread.start()
    try:
        assert service.handle({"v": 1, "op": "start", "recalibrate": False})["ok"]
        for _ in range(3):
            service.session.cuts.add("underrun", "salida", "vacía", level_ms=0.0)
        deadline = time.monotonic() + 5
        while not any("cuts: 3 in" in line for line in lines) and time.monotonic() < deadline:
            time.sleep(0.05)
        window = next(line for line in lines if "cuts: 3 in" in line)
        assert "underrun 3" in window
        assert "pipe min 0.0 ms" in window
        assert service.handle({"v": 1, "op": "stop"})["ok"]
        assert any("cuts: 3 faults in" in line and "underrun 3" in line for line in lines)
    finally:
        service.handle({"v": 1, "op": "shutdown"})
        thread.join(timeout=5)
        service.close()
