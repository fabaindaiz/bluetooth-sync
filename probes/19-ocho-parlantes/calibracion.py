"""Item 3 of experiment 16: calibrating N = 3..8 speakers at once with one microphone.

SIMULATED. The real stimulus (`estimulos.calibracion`: independent pink noise per speaker,
amplitude 0.1 as the service uses) and the real estimator (`medicion.calibrar`), on a
simulated room with known delays (0-30 ms) and gains (-8..0 dB) per speaker, the Go 4's
colouring (`simulated.ROOM_COLOUR_DB`), a reverberant tail per speaker (0.4 s, as probe 13)
and microphone noise (0.001, `simulated.ROOM_NOISE`; and 4x that).

Per condition: the worst delay error (ms) and the worst gain-correction error (dB) over the
speakers, whether `calibrar` called it reliable, and how long it took. 10 s and 20 s; two
stimulus seeds (0 is the product's) with independent rooms; 4 rooms each.

Two gain errors: the product's (`calibrar` as it is) and with `levels_fixed`, a prototype of
the fix for the level estimator's hint that this probe found (experiment 16 §3).

And for N = 8, calibrating in two groups that share an anchor speaker (0-4, then 0 + 5-7),
at 2 x 5 s and 2 x 10 s, against all eight at once.

    cd host && hatch run python ../probes/19-ocho-parlantes/calibracion.py [rooms]
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))

from comun import SR, save  # noqa: E402

from aurasync import estimulos, medicion  # noqa: E402
from aurasync.dsp import response  # noqa: E402
from aurasync.simulated import ROOM_COLOUR_DB  # noqa: E402

AMPLITUDE = 0.1
TAIL_SCALE = 1.0
FIXED_IN_GROUPS = True
"""The groups use the fixed level estimator (`levels_fixed`): what is compared there is the
grouping, not the hint's bug."""
BEFORE_S = 0.3
TAIL_S = 0.4
ERR_DELAY_MS, ERR_GAIN_DB = 0.5, 1.0


def draw_room(n: int, rng) -> dict:
    """Delays, gains and a reverberant tail per speaker, and each speaker's level as
    `medicion.niveles` defines it.

    That level is the energy of the speaker's own impulse response in the estimator's window
    (the direct sound plus 20 ms): the direct path plus its early reflections. Each speaker's
    reflections are a different random tail, so it is not the bare gain: against the bare gain
    the estimator is off by 1-3 dB already with 3 speakers (a property of the room, not of N;
    seen while writing this probe).
    """
    m, window = int(TAIL_S * SR), int(0.020 * SR)
    delays = rng.uniform(0, 30, n)
    gains = 10 ** (rng.uniform(-8, 0, n) / 20)
    irs = []
    for _ in range(n):
        ir = rng.standard_normal(m) * np.exp(-np.arange(m) / (0.06 * SR)) * 0.03 * TAIL_SCALE
        ir[0] = 1.0
        irs.append(ir)
    levels = np.array([g * np.sqrt(np.sum(ir[:window] ** 2)) for g, ir in zip(gains, irs, strict=True)])
    return {"delays": delays, "gains": gains, "irs": irs, "levels": levels}


def record(
    tracks: list[np.ndarray], idx: list[int], room: dict, noise: float, rng
) -> tuple[np.ndarray, list[np.ndarray]]:
    """The microphone, and what each speaker alone contributes to it (without noise)."""
    n = len(tracks[0]) + int((BEFORE_S + 0.3) * SR)
    k = 1 << int(np.ceil(np.log2(n + TAIL_S * SR)))
    f = np.fft.rfftfreq(k, 1 / SR)
    colour_db = np.interp(np.log10(np.maximum(f, 1)), np.log10(response.THIRDS), ROOM_COLOUR_DB)
    colour = 10 ** (colour_db / 20)
    alone = []
    for x, i in zip(tracks, idx, strict=True):
        shift = np.exp(-2j * np.pi * f * (BEFORE_S * 1000 + room["delays"][i]) / 1000)
        alone.append(np.fft.irfft(np.fft.rfft(x, k) * np.fft.rfft(room["irs"][i], k) * colour * room["gains"][i] * shift, k)[:n])
    return np.sum(alone, axis=0) + noise * rng.standard_normal(n), alone


def isolated_levels(cal, alone: list[np.ndarray], names: list[str], tracks: list[np.ndarray], room: dict, idx) -> np.ndarray:
    """The truth for the gain: `medicion.niveles` on each speaker ALONE (no other speaker, no
    noise), through the same alignment. It keeps what the room does to each speaker's level
    (its early reflections interfere with the direct sound within the pink noise's correlation
    width) and leaves only what N adds: the other speakers' crosstalk and the noise."""
    out = []
    for mic_i, nm, x, i in zip(alone, names, tracks, idx, strict=True):
        aligned = medicion.alinear(mic_i, {nm: x}, cal.desfase_grueso_ms)
        hint = BEFORE_S * 1000 + room["delays"][i] - max(0.0, cal.desfase_grueso_ms)
        out.append(medicion.niveles(mic_i, aligned, {nm: hint})[nm])
    return np.array(out)


def truth(delays_ms: np.ndarray, levels: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    return delays_ms.max() - delays_ms, 20 * np.log10(levels.min() / levels)


def levels_fixed(cal, mic: np.ndarray, refs: dict[str, np.ndarray]) -> dict[str, float]:
    """A prototype of the fix for the level estimator's hint (experiment 16 §3).

    `medicion.calibrar` hands `niveles` each speaker's delay relative to the EARLIEST speaker,
    but `niveles` looks for the peak in the references aligned by the coarse offset, which is
    the MEDIAN arrival: every hint is off by (earliest - median). When that passes
    `BUSQUEDA_MS` (15 ms) the window misses the speaker's peak and its level comes from
    crosstalk. Here the earliest speaker's own residual against the coarse alignment is
    measured (one GCC-PHAT over the whole recording) and added to every hint."""
    aligned = medicion.alinear(mic, refs, cal.desfase_grueso_ms)
    rel = {nm: -cal.retardos_ms[nm] for nm in refs}  # arrival, up to a constant
    first = min(rel, key=rel.get)
    est = medicion.gcc_phat(mic, aligned[first], retardo_maximo_ms=150.0, retardo_minimo_ms=-150.0).retardo_ms
    return medicion.niveles(mic, aligned, {nm: rel[nm] - rel[first] + est for nm in refs})


def calibrate_all(n: int, seconds: float, stim_seed: int, room: dict, noise: float, rng):
    names = [f"s{i}" for i in range(n)]
    tracks = [AMPLITUDE * t for t in estimulos.calibracion(n, seconds, semilla=stim_seed)]
    mic, alone = record(tracks, list(range(n)), room, noise, rng)
    t0 = time.perf_counter()
    cal = medicion.calibrar(mic, dict(zip(names, tracks, strict=True)))
    took = time.perf_counter() - t0
    if cal is None:
        return np.inf, np.inf, False, took
    td, tg = truth(room["delays"], isolated_levels(cal, alone, names, tracks, room, list(range(n))))
    d = np.array([cal.retardos_ms[nm] for nm in names])
    g = np.array([cal.ganancias_db[nm] for nm in names])
    bare = 20 * np.log10(room["gains"].min() / room["gains"])
    lf = levels_fixed(cal, mic, dict(zip(names, tracks, strict=True)))
    gf = np.array([medicion.ganancias_para_igualar(lf)[nm] for nm in names])
    return (
        float(np.max(np.abs(d - td))),
        float(np.max(np.abs(g - tg))),
        cal.confiable,
        took,
        float(np.max(np.abs(g - bare))),
        float(np.max(np.abs(gf - tg))),
    )


def calibrate_groups(seconds: float, stim_seed: int, room: dict, noise: float, rng):
    """Two groups sharing speaker 0 (same room); arrivals and levels referred to it, then joined."""
    groups = [[0, 1, 2, 3, 4], [0, 5, 6, 7]]
    arrival, level, true_level = np.zeros(8), np.zeros(8), np.zeros(8)
    reliable, took = True, 0.0
    for gi, idx in enumerate(groups):
        names = [f"s{i}" for i in idx]
        tracks = [AMPLITUDE * t for t in estimulos.calibracion(len(idx), seconds, semilla=stim_seed * 10 + gi)]
        mic, alone = record(tracks, idx, room, noise, rng)
        t0 = time.perf_counter()
        cal = medicion.calibrar(mic, dict(zip(names, tracks, strict=True)))
        took += time.perf_counter() - t0
        if cal is None:
            return np.inf, np.inf, False, took
        iso = isolated_levels(cal, alone, names, tracks, room, idx)
        reliable &= cal.confiable
        # corrections are (last arrival - arrival): arrival = -correction, up to a constant
        a = np.array([-cal.retardos_ms[nm] for nm in names])
        lv_map = levels_fixed(cal, mic, dict(zip(names, tracks, strict=True))) if FIXED_IN_GROUPS else cal.niveles
        lv = np.array([lv_map[nm] for nm in names])
        arrival[idx] = a - a[0]
        level[idx] = lv / lv[0]
        true_level[idx] = iso / iso[0]
    corr_d = arrival.max() - arrival
    corr_g = 20 * np.log10(level.min() / level)
    td, tg = truth(room["delays"], true_level)
    return float(np.max(np.abs(corr_d - td))), float(np.max(np.abs(corr_g - tg))), reliable, took


def summarise(rows: list[tuple]) -> dict:
    a = np.array([r[:2] for r in rows], dtype=float)
    rel = np.array([r[2] for r in rows])
    bad = (a[:, 0] > ERR_DELAY_MS) | (a[:, 1] > ERR_GAIN_DB)
    return {
        "delay_err_median_ms": round(float(np.median(a[:, 0])), 3),
        "delay_err_max_ms": round(float(a[:, 0].max()), 3),
        "gain_err_median_db": round(float(np.median(a[:, 1])), 2),
        "gain_err_max_db": round(float(a[:, 1].max()), 2),
        "fails": int(bad.sum()),
        "silent_fails": int((bad & rel).sum()),
        "reliable": int(rel.sum()),
        "n": len(rows),
        "seconds_cpu": round(float(np.median([r[3] for r in rows])), 2),
        **(
            {
                "gain_err_vs_bare_gain_median_db": round(float(np.median([r[4] for r in rows])), 2),
                "gain_err_fixed_median_db": round(float(np.median([r[5] for r in rows])), 2),
                "gain_err_fixed_max_db": round(float(np.max([r[5] for r in rows])), 2),
                "fails_fixed": int(sum((r[0] > ERR_DELAY_MS) or (r[5] > ERR_GAIN_DB) for r in rows)),
            }
            if all(len(r) > 5 for r in rows)  # noqa: PLR2004
            else {}
        ),
    }


def main() -> None:
    rooms = int(sys.argv[1]) if len(sys.argv) > 1 else 4
    t0 = time.time()
    out: dict = {}
    for noise in (0.001, 0.004):
        for stim_seed in (0, 1):
            for seconds in (10.0, 20.0):
                for n in range(3, 9):
                    rows = []
                    for r in range(rooms):
                        rng = np.random.default_rng(10_000 * stim_seed + 100 * r + n)
                        room = draw_room(n, rng)
                        rows.append(calibrate_all(n, seconds, stim_seed, room, noise, rng))
                    key = f"noise{noise}|seed{stim_seed}|{seconds:.0f}s|N{n}"
                    out[key] = summarise(rows)
                    print(key, out[key], flush=True)
            # N = 8 in two groups with an anchor, against all at once (above).
            for per_group in (5.0, 10.0):
                rows = []
                for r in range(rooms):
                    rng = np.random.default_rng(10_000 * stim_seed + 100 * r + 8)
                    room = draw_room(8, rng)
                    rows.append(calibrate_groups(per_group, stim_seed, room, noise, rng))
                key = f"noise{noise}|seed{stim_seed}|groups2x{per_group:.0f}s|N8"
                out[key] = summarise(rows)
                print(key, out[key], flush=True)
    dest = save(
        "calibracion.json",
        {"rooms": rooms, "amplitude": AMPLITUDE, "criteria": {"delay_ms": ERR_DELAY_MS, "gain_db": ERR_GAIN_DB}, "results": out},
    )
    print(f"{time.time() - t0:.0f} s; {dest}")


if __name__ == "__main__":
    main()
