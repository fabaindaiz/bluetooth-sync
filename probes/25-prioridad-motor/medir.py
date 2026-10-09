"""Late engine blocks under CPU load, with and without the engine thread's priority (experiment 23 §4).

Throwaway probe (d-7c8794-3208b7). No PipeWire, no service: the real `Motor` (4 speakers, HP-O16's
chain: front, diffuse noise_tail, bass protect, true_peak, EQ with a test curve) runs in a thread that
must deliver one 4096-sample block every 85.33 ms, as the service's engine does. A block is late when
it finishes after its deadline. Conditions alternate every `slice_s` seconds (normal / raised), so a
load that changes over time weighs on both alike. Run it while something loads the CPU.

    python medir.py --seconds 600 --slice 60 --nice -15 --engine numpy > out.jsonl
"""

from __future__ import annotations

import argparse
import json
import os
import threading
import time

import numpy as np

from aurasync import motor as motor_module
from aurasync import priority
from aurasync.chain import ChainValues
from aurasync.config import Instalacion, Parlante
from aurasync.dsp import backend

SR = 48000
BLOCK = 4096
CURVE = [6.0] * 5 + [4.99, 3.69, 2.05] + [0.0] * 12 + [2.04, 3.35] + [5.0] * 5


def build() -> motor_module.Motor:
    inst = Instalacion(
        parlantes=[
            Parlante(f"V{i}", None, pan=p, retardo_ms=r, ecualizacion_db=CURVE)
            for i, (p, r) in enumerate([(-0.8, 3.2), (0.8, 7.5), (-0.6, 11.0), (0.6, 0.0)])
        ]
    )
    chain = ChainValues()
    for stage, algorithm in {"spatial": "front", "diffuse": "noise_tail", "bass": "protect", "limiter": "true_peak"}.items():
        chain = chain.with_algorithm(stage, algorithm)
    return motor_module.Motor(inst, SR, ecualizar=True, chain=chain, semilla=1, bloque=BLOCK)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=600)
    parser.add_argument("--slice", type=float, default=60)
    parser.add_argument("--nice", type=int, default=-15)
    parser.add_argument("--engine", default="numpy")
    args = parser.parse_args()
    backend.reset()
    backend.use(args.engine)
    m = build()
    rng = np.random.default_rng(3)
    blocks = [(rng.standard_normal(BLOCK) * 0.1, rng.standard_normal(BLOCK) * 0.1) for _ in range(16)]
    period = BLOCK / SR
    tid_box = {}

    def engine() -> None:
        tid = threading.get_native_id()
        tid_box["tid"] = tid
        start = time.monotonic()
        k = 0
        slice_no = -1
        late, worst, work = 0, 0.0, []
        while time.monotonic() - start < args.seconds:
            now_slice = int((time.monotonic() - start) // args.slice)
            if now_slice != slice_no:
                if slice_no >= 0:
                    print(json.dumps({"slice": slice_no, "mode": mode, "nice": got.nice, "how": got.how,
                                      "reason": got.reason, "blocks": len(work), "late": late,
                                      "worst_late_ms": round(worst, 1),
                                      "work_ms_median": round(float(np.median(work)), 2),
                                      "work_ms_p99": round(float(np.percentile(work, 99)), 2),
                                      "load1": os.getloadavg()[0]}), flush=True)
                slice_no = now_slice
                mode = "raised" if slice_no % 2 else "normal"
                got = priority.raise_engine_priority(args.nice if mode == "raised" else 0)
                late, worst, work = 0, 0.0, []
            deadline = start + (k + 1) * period
            t0 = time.monotonic()
            left, right = blocks[k % len(blocks)]
            m.procesar(left, right)
            t1 = time.monotonic()
            work.append((t1 - t0) * 1000)
            if t1 > deadline:
                late += 1
                worst = max(worst, (t1 - deadline) * 1000)
                k = int((t1 - start) / period)  # a real output would have lost those blocks: resync
            k += 1
            pause = start + k * period - time.monotonic()
            if pause > 0:
                time.sleep(pause)

    thread = threading.Thread(target=engine, name="engine")
    thread.start()
    thread.join()


if __name__ == "__main__":
    main()
