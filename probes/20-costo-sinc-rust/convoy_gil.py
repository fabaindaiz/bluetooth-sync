#!/usr/bin/env python3
"""The GIL's convoy effect on the real `Motor` in Rust, and what the detach threshold does to it.

experimentos/20 §15. Since 2026-10-09 the extension's per-block calls let go of the GIL
(`Python::detach`). Taking it back is cheap while no other Python thread wants it, but when one
is busy the engine thread waits up to `sys.getswitchinterval()` (5 ms) on each call, and a block
makes dozens of calls. `set_detach_min_samples` (gil.rs) decides from how many input samples a
call lets go; this probe runs the same build with three thresholds:

- `siempre` (0): every call lets go, as on 2026-10-09 (a08b8a2);
- `defecto` (65536): what the extension does by default;
- `nunca` (2**62): no call lets go, as before 2026-10-09.

Each against three companions, a Python thread beside the engine like the service's:

- `ninguno`: no other thread;
- `ocupado`: a thread that never stops running Python (the worst case: a long analysis in pure
  Python, a busy request);
- `rafagas`: a thread that wakes every 20 ms and runs `--burst-ms` (2 ms) of Python (the panel's requests, the
  monitor's writer, the meters' history). It notes how late it woke: the price the other threads
  pay when the engine keeps the GIL.

The engine is probe 25's: the real `Motor` with 4 speakers and HP-O16's chain (front, diffuse
noise_tail, bass protect, true_peak, EQ), 4096-sample blocks back to back. Every (threshold,
companion) pair runs `--blocks` blocks per round, in the same order each round, for `--rounds`
rounds, so a load that drifts weighs on all alike. `--switch-ms` sets `sys.setswitchinterval` for
the whole run (the other mitigation the 2026-10-09 review named).

    cd host && nice -n 19 $(hatch env find hatch-test.py3.12)/bin/python ../probes/20-costo-sinc-rust/convoy_gil.py
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import platform
import sys
import threading
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "host" / "src"))

import aurasync_engine  # noqa: E402

from aurasync.dsp import backend  # noqa: E402

THRESHOLDS = {"siempre": 0, "defecto": 1 << 16, "nunca": 1 << 62}
COMPANIONS = ("ninguno", "ocupado", "rafagas")
BURST_PERIOD = 0.020


def _motor_builder():
    path = Path(__file__).resolve().parents[1] / "25-prioridad-motor" / "medir.py"
    spec = importlib.util.spec_from_file_location("medir_prioridad", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.build, module.BLOCK


def _spin(seconds: float) -> int:
    """Pure Python for `seconds`: it holds the GIL except at the switch interval."""
    end, n = time.perf_counter() + seconds, 0
    while time.perf_counter() < end:
        n += sum(range(50))
    return n


class Companion:
    def __init__(self, kind: str, burst_s: float) -> None:
        self.kind = kind
        self.burst_s = burst_s
        self.stop = threading.Event()
        self.late_ms: list[float] = []
        self.spins = 0
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        if self.kind == "ocupado":
            while not self.stop.is_set():
                _spin(0.001)
                self.spins += 1
        elif self.kind == "rafagas":
            due = time.perf_counter()
            while not self.stop.is_set():
                due += BURST_PERIOD
                pause = due - time.perf_counter()
                if pause > 0:
                    time.sleep(pause)
                self.late_ms.append((time.perf_counter() - due) * 1000)
                _spin(self.burst_s)
                self.spins += 1

    def __enter__(self) -> Companion:
        if self.kind != "ninguno":
            self.thread.start()
            time.sleep(0.05)
        return self

    def __exit__(self, *_exc) -> None:
        self.stop.set()
        if self.thread.is_alive():
            self.thread.join()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--blocks", type=int, default=60)
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--burst-ms", type=float, default=2.0, help="Python work per 20 ms burst")
    parser.add_argument("--switch-ms", type=float, default=None, help="sys.setswitchinterval, in ms")
    args = parser.parse_args()
    if args.switch_ms is not None:
        sys.setswitchinterval(args.switch_ms / 1000)
    backend.reset()
    backend.use(backend.RUST)
    build, block = _motor_builder()
    previous = aurasync_engine.set_detach_min_samples(THRESHOLDS["defecto"])
    print(f"extension: {aurasync_engine.__file__}, umbral al cargar: {previous}")
    print(f"equipo {platform.node()}, {platform.platform()}, python {platform.python_version()}, "
          f"numpy {np.__version__}, {os.cpu_count()} CPU, switchinterval {sys.getswitchinterval() * 1000:.1f} ms")
    print(f"load1 antes: {os.getloadavg()[0]:.2f}")
    motor = build()
    rng = np.random.default_rng(3)
    blocks = [(rng.standard_normal(block) * 0.1, rng.standard_normal(block) * 0.1) for _ in range(16)]
    for k in range(20):  # warm up: the filters' first blocks, the allocator
        motor.procesar(*blocks[k % len(blocks)])
    work: dict[tuple[str, str], list[float]] = {}
    late: dict[tuple[str, str], list[float]] = {}
    spins: dict[tuple[str, str], list[float]] = {}
    for _round in range(args.rounds):
        for tname, threshold in THRESHOLDS.items():
            aurasync_engine.set_detach_min_samples(threshold)
            for companion in COMPANIONS:
                key = (tname, companion)
                with Companion(companion, args.burst_ms / 1000) as other:
                    start, times = time.perf_counter(), []
                    for k in range(args.blocks):
                        t0 = time.perf_counter()
                        motor.procesar(*blocks[k % len(blocks)])
                        times.append((time.perf_counter() - t0) * 1000)
                    elapsed = time.perf_counter() - start
                work.setdefault(key, []).extend(times)
                late.setdefault(key, []).extend(other.late_ms)
                spins.setdefault(key, []).append(other.spins / elapsed)
    aurasync_engine.set_detach_min_samples(THRESHOLDS["defecto"])
    print(f"bloques por par: {args.blocks} x {args.rounds} rondas, rafagas de {args.burst_ms} ms cada 20 ms")
    print(f"{'umbral':>8} {'otro hilo':>9} {'med ms':>8} {'p95 ms':>8} {'p99 ms':>8} {'max ms':>8} "
          f"{'otro/s':>8} {'despertar p99 ms':>17}")
    for (tname, companion), times in work.items():
        w = np.asarray(times)
        wake = late[(tname, companion)]
        wake_p99 = f"{np.percentile(wake, 99):.2f}" if wake else "-"
        print(f"{tname:>8} {companion:>9} {np.median(w):>8.2f} {np.percentile(w, 95):>8.2f} "
              f"{np.percentile(w, 99):>8.2f} {w.max():>8.2f} {np.mean(spins[(tname, companion)]):>8.0f} {wake_p99:>17}")
    print(f"load1 despues: {os.getloadavg()[0]:.2f}")


if __name__ == "__main__":
    main()
