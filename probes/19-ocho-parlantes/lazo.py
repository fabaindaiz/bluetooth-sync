"""Item 4 of experiment 16: the recalibration loop and the masked probe with 8 speakers.

SIMULATED. Two parts.

**A. The probe with 8 speakers.** The masked probe of experiment 11 (probe 13: synthetic
music, the probe shaped per third octave at `margin` dB under each speaker's music, a room
with known delays, the Go 4 treble roll-off, 0.4 s of reverberation, microphone noise), but
on the real engine with N = 3 or 8 speakers on the ring (`comun.role_from_angle`, the
decorrelator's limit lifted) and two schedules:

- `round_robin`: one speaker carries the probe per window (spec 2026-10-01 §4.1);
- `simultaneous`: every speaker carries its own independent probe in every window.

Error of each measured delay against the truth; two seeds (CLAUDE.md: a true delay shows up
twice).

**B. Tracking a clock drift.** Per-speaker offsets that drift linearly (uniform within
+-22 ppm, the drift measured in experiment 10 §5.3; and +-50 ppm), measured with the error
distribution of part A, and corrected with the loop's rules as they are in `sincronia`
(dead band 0.5 ms, every change confirmed by a second measurement within 0.5 ms, factor 0.5)
applied per speaker. Schedules: today's loop (all speakers every 20 s from a 10 s window),
the probe round robin (each speaker every N x W s), the probe simultaneous (every W s), and
round robin with the confirmation tolerance widened by the drift that fits between two
measurements. One simulated hour; the misalignment is the spread of the residuals.

    cd host && hatch run python ../probes/19-ocho-parlantes/lazo.py [trials]
"""

from __future__ import annotations

import importlib.util
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))

from comun import ROOT, SR, installation, lifted_limit, save  # noqa: E402

from aurasync import motor  # noqa: E402

_spec = importlib.util.spec_from_file_location("sonda13", ROOT / "probes/13-sonda-enmascarada/simular.py")
sonda = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sonda)


# -- part A ----------------------------------------------------------------------------


def feeds(n: int, left: np.ndarray, right: np.ndarray) -> list[np.ndarray]:
    inst = installation(n)
    inst.retardo_traseros_ms = 0.0  # the room's delays are the only ones here, as in probe 13
    with lifted_limit(8):
        m = motor.Motor(inst, SR, extraer_ambiente=True, decorrelar=True, ecualizar=False)
    out = motor.procesar_completo(m, left, right, 4096)
    return [out[p.nombre] for p in inst.parlantes]


def room(sent: list[np.ndarray], delays_ms: np.ndarray, gains: np.ndarray, rng) -> np.ndarray:
    """Probe 13's room for N speakers: roll-off, delay, gain, 0.4 s tail, noise at -60 dB."""
    n = len(sent[0])
    k = 1 << int(np.ceil(np.log2(n)))
    f = np.fft.rfftfreq(k, 1 / SR)
    rolloff = 1 / np.sqrt(1 + (f / 9000) ** 4)
    mic = np.zeros(n)
    for x, d, g in zip(sent, delays_ms, gains, strict=True):
        y = np.fft.irfft(np.fft.rfft(x, k) * rolloff, k)[:n]
        y = sonda.frac_delay(y, d) * g
        ir = rng.standard_normal(int(0.4 * SR)) * np.exp(-np.arange(int(0.4 * SR)) / (0.06 * SR)) * 0.03
        ir[0] = 1.0
        mic += sonda.fftconv(y, ir)
    return mic + rng.standard_normal(n) * 10 ** (-60 / 20)


def part_a(trials: int) -> dict:
    results = {}
    for seed_set in (0, 1):
        for n in (3, 8):
            for window_s in (2.0, 4.0):
                for margin in (20.0, 25.0):
                    for schedule in ("round_robin", "simultaneous"):
                        errs = []
                        for trial in range(trials):
                            rng = np.random.default_rng(1000 * seed_set + 10 * trial + n)
                            delays = rng.uniform(2, 30, n)
                            gains = 10 ** (rng.uniform(-6, 0, n) / 20)
                            left, right = sonda.music(window_s + 0.5, rng)
                            fs = feeds(n, left, right)
                            targets = [trial % n] if schedule == "round_robin" else list(range(n))
                            probes = {i: sonda.shaped_probe(fs[i], margin, rng) for i in targets}
                            sent = [fs[i] + probes.get(i, 0.0) for i in range(n)]
                            mic = room(sent, delays, gains, rng)
                            errs += [abs(sonda.gcc_band(mic, probes[i]) - delays[i]) for i in targets]
                        e = np.array(errs)
                        key = f"seed{seed_set}|N{n}|{window_s:.0f}s|-{margin:.0f}dB|{schedule}"
                        results[key] = {
                            "median_ms": round(float(np.median(e)), 4),
                            "p95_ms": round(float(np.percentile(e, 95)), 4),
                            "max_ms": round(float(e.max()), 3),
                            "over_1ms": round(float(np.mean(e > 1.0)), 3),
                            "n": len(e),
                        }
                        print(key, results[key], flush=True)
    return results


# -- part B ----------------------------------------------------------------------------

DEAD_BAND_MS, FACTOR = 0.5, 0.5
SPEED_MS_S = 0.5  # the delay line's ramp (align.delay_speed_ms_s)


def track(
    n: int,
    ppm: float,
    schedule: str,
    window_s: float,
    sigma_ms: float,
    outliers: float,
    seed: int,
    hours: float = 1.0,
    tolerance: str = "fixed",
    feedforward: bool = False,
) -> dict:
    """One simulated session. Returns the misalignment statistics after the first 10 minutes.

    `feedforward`: the controller also estimates each speaker's drift rate (Theil-Sen slope of
    its last 8 measured offsets) and moves that speaker's delay at that rate between
    measurements, as a resampler or a slow ramp would; the loop's rules correct what is left.
    """
    rng = np.random.default_rng(seed)
    rate = rng.uniform(-ppm, ppm, n) * 1e-6 * 1000  # ms per second
    rate[0] = 0.0  # the combined sink's driver (experiment 10 §5.3: Red)
    offset = rng.uniform(-0.3, 0.3, n)  # what is left after the initial calibration
    applied = np.zeros(n)  # target of each delay line
    actual = np.zeros(n)  # where the ramp is
    pending: list[float | None] = [None] * n
    slope = np.zeros(n)
    history: list[list[tuple[float, float]]] = [[] for _ in range(n)]
    dt = 0.5
    t = 0.0
    if schedule == "today":
        period, who = 20.0, lambda k: list(range(n))
        window_s = 10.0
    elif schedule == "round_robin":
        period, who = window_s, lambda k: [k % n]
    else:
        period, who = window_s, lambda k: list(range(n))
    interval = period * (n if schedule == "round_robin" else 1)  # between two measurements of one speaker
    tol = DEAD_BAND_MS if tolerance == "fixed" else DEAD_BAND_MS + 2 * ppm * 1e-6 * 1000 * interval
    k, next_t = 0, period
    spreads = []
    applied_count = 0
    while t < hours * 3600:
        t += dt
        if feedforward:
            applied += slope * dt
        actual += np.clip(applied - actual, -SPEED_MS_S * dt, SPEED_MS_S * dt)
        if t >= next_t:
            for i in who(k):
                # The residual seen by the mic, averaged over the window that just ended.
                actual_mid = actual[i] - slope[i] * window_s / 2
                truth = offset[i] + rate[i] * (t - window_s / 2) - actual_mid
                m = truth + rng.normal(0, sigma_ms)
                if rng.random() < outliers:
                    m += rng.uniform(-5, 5)
                if feedforward:
                    history[i] = [*history[i], (t - window_s / 2, actual_mid + m)][-8:]
                    if len(history[i]) >= 3:  # noqa: PLR2004
                        h = history[i]
                        pairs = [
                            (h[b][1] - h[a][1]) / (h[b][0] - h[a][0]) for a in range(len(h)) for b in range(a + 1, len(h))
                        ]
                        slope[i] = float(np.median(pairs))
                proposal = applied[i] + m  # `_componer`: what was measured adds to what is applied
                if abs(m) <= DEAD_BAND_MS:
                    pending[i] = None
                    continue
                if pending[i] is not None and abs(proposal - pending[i]) <= tol:
                    applied[i] += FACTOR * (proposal - applied[i])
                    pending[i] = None
                    applied_count += 1
                else:
                    pending[i] = proposal
            k += 1
            next_t += period
        if t > 600:
            resid = offset + rate * t - actual
            spreads.append(resid.max() - resid.min())
    s = np.array(spreads)
    return {
        "p50_ms": round(float(np.median(s)), 3),
        "p95_ms": round(float(np.percentile(s, 95)), 3),
        "max_ms": round(float(s.max()), 3),
        "over_2ms": round(float(np.mean(s > 2.0)), 3),
        "applied": applied_count,
        "between_measurements_s": interval,
    }


def part_b(sigma_ms: float) -> dict:
    out = {}
    cases = [
        ("today", 10.0, "fixed", False),
        ("today", 10.0, "fixed", True),
        ("round_robin", 4.0, "fixed", False),
        ("round_robin", 4.0, "drift", False),
        ("round_robin", 4.0, "drift", True),
        ("round_robin", 2.0, "fixed", False),
        ("simultaneous", 4.0, "fixed", False),
        ("simultaneous", 4.0, "fixed", True),
    ]
    for ppm in (22.0, 50.0):
        for outliers in (0.0, 0.02):
            for n in (3, 4, 5, 6, 7, 8):
                for schedule, window_s, tol, ff in cases:
                    for seed in (0, 1):
                        key = (
                            f"{ppm:.0f}ppm|out{outliers}|N{n}|{schedule}|{window_s:.0f}s|tol_{tol}"
                            f"|{'ff' if ff else 'noff'}|seed{seed}"
                        )
                        out[key] = track(
                            n, ppm, schedule, window_s, sigma_ms, outliers, 100 * seed + n, tolerance=tol, feedforward=ff
                        )
                print(f"{ppm:.0f}ppm out{outliers} N{n} done", flush=True)
    return out


def main() -> None:
    trials = int(sys.argv[1]) if len(sys.argv) > 1 else 8
    t0 = time.time()
    a = part_a(trials)
    # The probe's error at N = 8, 4 s, -20 dB, simultaneous (p95 of both seeds) drives part B.
    p95 = max(a[f"seed{s}|N8|4s|-20dB|simultaneous"]["p95_ms"] for s in (0, 1))
    b = part_b(sigma_ms=max(p95 / 2, 0.005))
    dest = save("lazo.json", {"trials": trials, "probe": a, "sigma_ms_used": max(p95 / 2, 0.005), "tracking": b})
    print(f"{time.time() - t0:.0f} s; {dest}")


if __name__ == "__main__":
    main()
