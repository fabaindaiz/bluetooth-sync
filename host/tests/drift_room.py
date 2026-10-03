"""A room whose speakers drift, for the recalibration loop (`arrival_loop.ArrivalLoop`).

SIMULATED, the model of `probes/19-ocho-parlantes/lazo.py` part B (experimentos/16 §4.2) with
the product's controller instead of the probe's copy of its rules:

- each speaker's playback latency drifts linearly, at a rate drawn within ±`ppm` (the first
  is the combined sink's driver, rate 0, as in experimentos/10 §5.3);
- every `every_s` the loop gets one measurement of the window that ended `margin_s` before:
  each speaker's arrival at the window's middle, plus an offset common to the measurement
  (the microphone and the output are not read at the same instant), plus the estimator's
  error (`sigma_ms`; and a share `outliers` of wrong peaks of ±5 ms that pass as valid);
- the delay lines move towards their targets at 0.5 ms/s (`align.delay_speed_ms_s`), and the
  loop's `advance` runs every `dt_s`.

The misalignment is the spread (max - min) of delay + latency over the speakers.
"""

from __future__ import annotations

import numpy as np

from aurasync.arrival_loop import ArrivalLoop
from aurasync.config import Instalacion, Parlante

SPEED_MS_S = 0.5


def simulate(
    n: int,
    ppm: float,
    seed: int,
    *,
    hours: float = 1.0,
    every_s: float = 4.0,
    window_s: float = 4.0,
    margin_s: float = 1.0,
    sigma_ms: float = 0.005,
    outliers: float = 0.0,
    start_spread_ms: float = 0.6,
    settle_s: float = 600.0,
    dt_s: float = 0.25,
    **loop_options,
) -> dict:
    rng = np.random.default_rng(seed)
    rate = rng.uniform(-ppm, ppm, n) * 1e-3  # ms per second
    rate[0] = 0.0
    latency = rng.uniform(0, 30, n)  # what the calibration found and corrected
    inst = Instalacion(
        parlantes=[Parlante(f"s{i}", f"sink{i}") for i in range(n)],
        retardo_traseros_ms=0.0,
    )
    # After the calibration: aligned, but for a small residual each.
    for p, lat in zip(inst.parlantes, latency, strict=True):
        p.retardo_ms = latency.max() - lat + rng.uniform(-start_spread_ms / 2, start_spread_ms / 2)
    clock = {"t": 0.0}
    loop = ArrivalLoop(inst, clock=lambda: clock["t"], **loop_options)
    actual = np.array([p.retardo_ms for p in inst.parlantes])
    names = [p.nombre for p in inst.parlantes]
    next_measure, spreads, applied = every_s, [], 0
    t = 0.0
    while t < hours * 3600:
        t += dt_s
        clock["t"] = t
        loop.advance(t)
        target = np.array([p.retardo_ms for p in inst.parlantes])
        actual += np.clip(target - actual, -SPEED_MS_S * dt_s, SPEED_MS_S * dt_s)
        if t >= next_measure:
            next_measure += every_s
            mid = t - margin_s - window_s / 2
            common = rng.uniform(-50, 50)
            measured = latency + rate * mid + common + rng.normal(0, sigma_ms, n)
            wrong = rng.random(n) < outliers
            measured[wrong] += rng.uniform(-5, 5, wrong.sum())
            if loop.propose(dict(zip(names, measured, strict=True)), set(names), mid).hubo_cambios:
                applied += 1
        if t > settle_s:
            arrival = actual + latency + rate * t
            spreads.append(arrival.max() - arrival.min())
    s = np.array(spreads)
    return {
        "p50_ms": round(float(np.median(s)), 3),
        "p95_ms": round(float(np.percentile(s, 95)), 3),
        "max_ms": round(float(s.max()), 3),
        "applied": applied,
        "max_delay_ms": round(max(p.retardo_ms for p in inst.parlantes), 1),
    }
