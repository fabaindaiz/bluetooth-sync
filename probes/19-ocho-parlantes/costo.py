"""Item 1 of experiment 16: the engine's cost per block of 4096 with N = 3..8 speakers.

SIMULATED input (noise), real engine. For each N: defaults and "everything on" (as
probe 18), with the decorrelator (its limit of 6 lifted in this process) and without it,
plus the decorrelator's own cost per speaker at 256/512/1024 taps (direct `np.convolve`,
what the engine does, against FFT overlap-add). The engines of every N are fed interleaved,
block by block in shuffled order, so the machine's load hits them alike; a straight line
through the medians gives the cost per speaker. Two runs with different seeds. The time is the
thread's CPU time (`time.thread_time`), not the wall clock: on 2026-10-02 the Mac ran at load
10-30 from other sessions and the wall clock gave slopes of 1.8 to 6.4 ms per speaker for the
same configuration in two runs; the CPU time does not count the time the thread waits for a
core. The wall clock is kept next to it.

    cd host && hatch run python ../probes/19-ocho-parlantes/costo.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))

from comun import installation, lifted_limit, save  # noqa: E402

from aurasync import chain, motor  # noqa: E402
from aurasync.chain import ChainValues  # noqa: E402
from aurasync.dsp import decorrelate  # noqa: E402

SR, BLOCK, BLOCKS, WARM = 48000, 4096, 130, 10
BUDGET_MS = BLOCK / SR * 1000


def everything(bass: str) -> ChainValues:
    v = ChainValues()
    v = v.with_algorithm("diffuse", "noise_tail").with_algorithm("bass", bass).with_algorithm("limiter", "true_peak")
    if bass == "protect":
        v = v.with_change(chain.validate_set("bass", params={"harmonics_db": 0.0}))
    return v.with_change(chain.validate_set("eq", params={"budget_db": 3.0, "treble_cap_db": 3.0}))


def stats(ms: np.ndarray) -> dict:
    med = float(np.median(ms))
    return {
        "median_ms": round(med, 2),
        "p99_ms": round(float(np.percentile(ms, 99)), 2),
        "x_realtime": round(BUDGET_MS / med, 1),
    }


def engine_cost(seed: int) -> dict:
    """For each configuration, one engine per N, fed **interleaved** block by block in a shuffled
    order: the machine's load (other processes) hits every N alike, so the slope with N survives
    it even when the absolute numbers do not."""
    rng = np.random.default_rng(seed)
    x = 0.1 * rng.standard_normal((BLOCK * BLOCKS, 2))
    out: dict = {}
    cases = {
        "defaults": (False, ChainValues()),
        "everything_protect": (False, everything("protect")),
        "everything_crossover": (True, everything("crossover")),
    }
    for label, (charge, values) in cases.items():
        for decorr in (True, False):
            tag = f"{label}|{'decorr' if decorr else 'no_decorr'}"
            with lifted_limit(8):
                engines = {
                    n: motor.Motor(
                        installation(n, charge_first=charge, eq_db=3.0),
                        SR,
                        ecualizar=True,
                        chain=values,
                        bloque=BLOCK,
                        decorrelar=decorr,
                    )
                    for n in range(3, 9)
                }
            times: dict[int, list[float]] = {n: [] for n in engines}
            walls: dict[int, list[float]] = {n: [] for n in engines}
            for i in range(BLOCKS):
                a, b = x[i * BLOCK : (i + 1) * BLOCK, 0], x[i * BLOCK : (i + 1) * BLOCK, 1]
                for n in rng.permutation(list(engines)):
                    t, w = time.thread_time(), time.perf_counter()
                    engines[int(n)].procesar(a, b)
                    times[int(n)].append(time.thread_time() - t)
                    walls[int(n)].append(time.perf_counter() - w)
            for n, ts in times.items():
                out[f"N{n}|{tag}"] = {
                    **stats(np.array(ts[WARM:]) * 1000),
                    "wall_median_ms": round(float(np.median(walls[n][WARM:])) * 1000, 2),
                }
                print(seed, f"N{n}|{tag}", out[f"N{n}|{tag}"], flush=True)
            meds = np.array([out[f"N{n}|{tag}"]["median_ms"] for n in engines])
            slope, intercept = np.polyfit(np.array(list(engines)), meds, 1)
            out[f"fit|{tag}"] = {"ms_per_speaker": round(float(slope), 2), "ms_fixed": round(float(intercept), 2)}
            print(seed, tag, out[f"fit|{tag}"], flush=True)
    return out


def decorrelator_alone(seed: int) -> dict:
    """The decorrelator's convolution per speaker and block, as the engine does it and by FFT."""
    rng = np.random.default_rng(seed)
    x = rng.standard_normal(BLOCK)
    out = {}
    for length in (256, 512, 1024):
        h = decorrelate.filtro_todo_paso(length, semilla=seed)
        k = 1 << int(np.ceil(np.log2(BLOCK + length - 1)))
        hf = np.fft.rfft(h, k)
        for label, fn in (
            ("direct", lambda: np.convolve(x, h)),
            ("fft", lambda: np.fft.irfft(np.fft.rfft(x, k) * hf, k)),
        ):
            ts = []
            for _ in range(200):
                t = time.thread_time()
                fn()
                ts.append(time.thread_time() - t)
            ms = np.array(ts[20:]) * 1000
            out[f"{length}|{label}"] = {
                "median_ms": round(float(np.median(ms)), 3),
                "p99_ms": round(float(np.percentile(ms, 99)), 3),
            }
            print(seed, length, label, out[f"{length}|{label}"], flush=True)
    return out


def limit_check() -> dict:
    """What the product does today with 7 speakers and the decorrelator on (the service's case)."""
    try:
        motor.Motor(installation(7), SR, decorrelar=True)
    except ValueError as exc:
        return {"N7_decorrelar_true": f"ValueError: {exc}"}
    return {"N7_decorrelar_true": "built"}


def main() -> None:
    runs = {
        "run_a": {"engine": engine_cost(0), "decorrelator": decorrelator_alone(0)},
        "run_b": {"engine": engine_cost(1), "decorrelator": decorrelator_alone(1)},
    }
    dest = save("costo.json", {"block": BLOCK, "budget_ms": round(BUDGET_MS, 2), "limit": limit_check(), **runs})
    print(dest)


if __name__ == "__main__":
    main()
