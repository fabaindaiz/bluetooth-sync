"""How late the engine thread gets, and what it was doing.

Runs the real Service with the simulated session (real motor, real loop, real snapshot)
and times every engine step and every snapshot build. A step that starts later than the
audio it must deliver is a cut on the speakers: with the pipe at ~135 ms (pw-play 50 ms +
one 85 ms block), any gap between steps longer than that empties it.

    cd host && hatch run python ../probes/12-cortes/jitter.py [seconds]
"""

import sys
import tempfile
import threading
import time
from pathlib import Path

import numpy as np

from aurasync import service as S
from aurasync.config import Instalacion, Parlante
from aurasync.logbuffer import LogBuffer
from aurasync.session import SessionOptions
from aurasync.simulated import SimulatedObserver, SimulatedSession



def main() -> None:
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 40
    tmp = Path(tempfile.mkdtemp())
    inst = Instalacion(parlantes=[Parlante(n, f"bluez_output.{i}") for i, n in enumerate(["Red", "Black", "Blue"])])
    inst.guardar(tmp / "i.json")
    svc = S.Service(
        tmp / "i.json", tmp / "p.json",
        options=SessionOptions(microphone="simulado", block=4096),
        session_factory=SimulatedSession, observer=SimulatedObserver(inst), simulated=True,
        measurements_path=tmp / "m", log=lambda _: None, logs=LogBuffer(),
    )
    steps, publishes = [], []
    orig_step, orig_publish = svc._step, svc._publish  # noqa: SLF001


    def timed_step():
        t = time.perf_counter()
        orig_step()
        steps.append((t, time.perf_counter() - t, bool(svc.session and svc.session.loop and svc.session.loop and svc.session._measurer.ocupado)))  # noqa: SLF001


    def timed_publish():
        t = time.perf_counter()
        orig_publish()
        publishes.append(time.perf_counter() - t)


    svc._step, svc._publish = timed_step, timed_publish  # noqa: SLF001
    threading.Thread(target=svc.run, daemon=True).start()
    svc.handle({"v": 1, "op": "set", "changes": {"recalibrate_every_s": 5}})
    svc.handle({"v": 1, "op": "start"})
    time.sleep(2)
    steps.clear()
    # Orders while it plays, as a person moving sliders: each runs on the engine thread.
    end = time.monotonic() + seconds
    k = 0
    while time.monotonic() < end:
        svc.handle({"v": 1, "op": "set", "changes": {"volume_db": -20 - (k % 5)}})
        k += 1
        time.sleep(0.08)
    state = svc.handle({"v": 1, "op": "state"})["result"]
    print("session:", state["session"], "| loop:", state["recalibration"]["active"], state["recalibration"]["last"])
    for r in svc.logs.since(0, 400)["records"]:
        if r["level"] in ("warning", "error") or "lazo" in r["message"] or "session" in r["service"]:
            print("  log:", r["service"], r["level"], r["message"][:160])
    svc.handle({"v": 1, "op": "shutdown"})

    t = np.array([s[0] for s in steps])
    gaps = np.diff(t) * 1000
    dur = np.array([s[1] for s in steps]) * 1000
    measuring = np.array([s[2] for s in steps])[1:]
    block_ms = 4096 / 48000 * 1000
    print(f"steps {len(steps)}  block {block_ms:.1f} ms")
    print(f"gap between steps: median {np.median(gaps):.1f}  p99 {np.percentile(gaps, 99):.1f}  max {gaps.max():.1f} ms")
    print(f"  while the loop measures: max {gaps[measuring].max() if measuring.any() else 0:.1f} ms; otherwise max {gaps[~measuring].max():.1f} ms")
    print(f"step duration: median {np.median(dur):.2f}  max {dur.max():.1f} ms")
    print(f"snapshot build: median {np.median(publishes) * 1000:.2f}  max {max(publishes) * 1000:.1f} ms  ({len(publishes)} builds)")
    late = gaps > block_ms + 50
    print(f"gaps over block + 50 ms: {late.sum()}")


if __name__ == "__main__":  # the loop measures in a forkserver process, which re-imports this module
    main()
