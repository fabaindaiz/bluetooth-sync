"""Step 2 of the masked probe (roadmap i-7c8794-e3e40d): the probe built into the engine.

SIMULATED. Everything here runs the product's code (`aurasync.dsp.probe`, `probe_measure`,
`arrival_loop`, `session.Calibration`, `group_calibration`, `dsp.response`); the room, the
music and the drift are simulated. Four parts, each with two seeds (CLAUDE.md: a true delay
shows up twice):

A. **The estimator on the real engine**: 3 speakers (decorrelator on) and 8 (on and off), the
   roles of experimentos/11 (Black and Blue share a pan), the probe at -20 and -25 dB, 4 s
   windows, simultaneous probes. Error of every speaker's arrival against the room's delay.
   And the music estimator on the same recordings with the room of experimentos/11.
B. **The loop over one hour of drift** (`tests/drift_room.py`): 3 and 8 speakers, 22 and 50
   ppm, clean, with 2 % of wrong peaks passing as valid, and without following the drift.
C. **Calibrating 8 speakers in two groups** through `session.Calibration` (the product's
   timeline, slicing and join) on 12 rooms of experimentos/16 §3 (delays 0-30 ms, gains -8..0
   dB, the Go 4's colouring, a 0.4 s tail per speaker, microphone noise 0.001), against all
   eight at once in 10 s and 20 s. Truth: the same level estimator on each speaker alone,
   aligned exactly (what N adds is measured, not what the room does to a level).
D. **The coherence's error estimate** over 12 independent noises: predicted / observed.

    cd host && hatch run python ../probes/13-sonda-enmascarada/paso2.py [trials | C]

Part C runs twice, with the product's stimulus seeds and with them moved by 10.
"""

from __future__ import annotations

import json
import platform
import sys
import time
from pathlib import Path

import numpy as np

HOST = Path(__file__).resolve().parents[2] / "host"
sys.path.insert(0, str(HOST))

from aurasync import estimulos, medicion, motor, probe_measure  # noqa: E402
from aurasync import session as session_module  # noqa: E402
from aurasync.config import Instalacion, Parlante  # noqa: E402
from aurasync.dsp import probe, response  # noqa: E402
from aurasync.session import MIC_SLACK_S, Calibration  # noqa: E402
from aurasync.simulated import ROOM_COLOUR_DB  # noqa: E402
from tests import probe_room  # noqa: E402
from tests.drift_room import simulate  # noqa: E402

SR = 48000
BLOCK = 4096
DEST = Path(__file__).resolve().parents[2] / "docs/research/experimentos/datos/11/paso2.json"


# -- A ---------------------------------------------------------------------------------------


def engine_trial(n, seed, decorrelate, margin, room=None):
    rng = np.random.default_rng(seed)
    pans, amb = (-0.7, 0.7, 0.7), (0.15, 0.15, 0.55)
    inst = Instalacion(
        parlantes=[Parlante(f"s{i}", f"k{i}", pan=pans[i % 3], ambiente=amb[i % 3]) for i in range(n)],
        retardo_traseros_ms=0.0,
    )
    m = motor.Motor(inst, SR, decorrelar=decorrelate)
    m.sonda = probe.MaskedProbe([p.nombre for p in inst.parlantes], SR, margin, seed=seed)
    m.sonda.enabled = True
    left, right = probe_room.music(6.5, rng)
    names = [p.nombre for p in inst.parlantes]
    out, sent = {k: [] for k in names}, {k: [] for k in names}
    for i in range(0, len(left), BLOCK):
        for k, x in m.procesar(left[i : i + BLOCK], right[i : i + BLOCK]).items():
            out[k].append(x)
            sent[k].append(m.sonda.last[k])
    feeds = {k: np.concatenate(v) for k, v in out.items()}
    probes = {k: np.concatenate(v) for k, v in sent.items()}
    delays = list(rng.uniform(2, 30, n)) if room is None else list(room[0])
    gains = list(10 ** (rng.uniform(-6, 0, n) / 20)) if room is None else list(room[1])
    mic = probe_room.room([feeds[k] for k in names], delays, gains, rng)
    start, window, lead = SR, 4 * SR, SR // 2
    rec = mic[start - lead : start + window + SR]
    truth = {k: 500.0 + d for k, d in zip(names, delays, strict=True)}
    res = probe_measure.measure(rec, {k: p[start : start + window] for k, p in probes.items()}, SR)
    probe_err = [abs(res.arrivals_ms[k] - truth[k]) if k in res.valid else np.inf for k in names]
    music_err = None
    if room is not None:
        cal = medicion.calibrar(rec, {k: f[start : start + window] for k, f in feeds.items()}, SR)
        last = max(truth.values())
        music_err = [abs(cal.retardos_ms[k] - (last - truth[k])) if cal else np.inf for k in names]
    return probe_err, music_err


def stats(e):
    e = np.asarray(e, dtype=float)
    fin = e[np.isfinite(e)]
    return {
        "median_ms": round(float(np.median(fin)), 4),
        "p95_ms": round(float(np.percentile(fin, 95)), 4),
        "max_ms": round(float(fin.max()), 4),
        "over_1ms": round(float(np.mean(e > 1.0)), 3),
        "invalid": int(np.sum(~np.isfinite(e))),
        "n": len(e),
    }


def part_a(trials):
    out = {}
    for seed_set in (0, 1):
        for n, decorrelate in ((3, True), (8, True), (8, False)):
            for margin in (-20.0, -25.0):
                errs = []
                for t in range(trials):
                    errs += engine_trial(n, 1000 * seed_set + 10 * t + n, decorrelate, margin)[0]
                key = f"seed{seed_set}|N{n}|{'decorr' if decorrelate else 'plain'}|{margin:.0f}dB"
                out[key] = stats(errs)
                print(key, out[key], flush=True)
        probe_errs, music_errs = [], []
        for t in range(trials):
            p, mu = engine_trial(3, 5000 + 1000 * seed_set + t, True, -20.0, room=((3.13, 7.61, 12.27), (1.0, 0.8, 0.6)))
            probe_errs += p
            music_errs += mu
        out[f"seed{seed_set}|N3|exp11room|probe"] = stats(probe_errs)
        out[f"seed{seed_set}|N3|exp11room|music"] = stats(music_errs)
        print(seed_set, "exp11 room", out[f"seed{seed_set}|N3|exp11room|probe"], out[f"seed{seed_set}|N3|exp11room|music"])
    return out


# -- B ---------------------------------------------------------------------------------------


def part_b():
    out = {}
    for n in (3, 8):
        for ppm in (22.0, 50.0):
            for seed in (0, 1):
                for label, kw in (("clean", {}), ("outliers2pct", {"outliers": 0.02}), ("no_tracking", {"track_drift": False})):
                    key = f"N{n}|{ppm:.0f}ppm|seed{seed}|{label}"
                    out[key] = {k: float(v) for k, v in simulate(n, ppm, seed, **kw).items()}
                    print(key, out[key], flush=True)
    return out


# -- C ---------------------------------------------------------------------------------------

LATENCY_MS = 480.0


def draw_room(rng):
    delays = rng.uniform(0, 30, 8)
    gains = 10 ** (rng.uniform(-8, 0, 8) / 20)
    m = int(0.4 * SR)
    irs = []
    for _ in range(8):
        ir = rng.standard_normal(m) * np.exp(-np.arange(m) / (0.06 * SR)) * 0.03
        ir[0] = 1.0
        irs.append(ir)
    return delays, gains, irs


def play(cal: Calibration):
    played = {k: [] for k in cal.references}
    while not cal.emitted:
        for k, x in cal.next_blocks(BLOCK).items():
            played[k].append(x)
    return [np.concatenate(played[k]) for k in cal.references]


def through_room(outputs, delays, gains, irs, rng, noise=0.001):
    total = len(outputs[0])
    k = 1 << int(np.ceil(np.log2(total + 0.5 * SR)))
    f = np.fft.rfftfreq(k, 1 / SR)
    colour = 10 ** (np.interp(np.log10(np.maximum(f, 1)), np.log10(response.THIRDS), ROOM_COLOUR_DB) / 20)
    alone = []
    for x, d, g, ir in zip(outputs, delays, gains, irs, strict=True):
        shift = np.exp(-2j * np.pi * f * (LATENCY_MS + d) / 1000)
        alone.append(np.fft.irfft(np.fft.rfft(x, k) * np.fft.rfft(ir, k) * colour * shift, k)[:total] * g)
    slack = int(MIC_SLACK_S * SR)
    mic = np.sum(alone, axis=0) + noise * rng.standard_normal(total)
    return mic[slack:], [a[slack:] for a in alone]


def isolated_gains(cal: Calibration, alone, delays):  # noqa: ARG001
    """`medicion.niveles` on each speaker alone, with its reference placed where it is (found
    by GCC-PHAT on that speaker alone: the calibration's EQ and delay line add their own fixed
    latency, 21 ms, that a computed position would miss)."""
    levels = {}
    for (name, ref), rec in zip(cal.references.items(), alone, strict=True):
        arrival = medicion.gcc_phat(rec, ref, SR, retardo_maximo_ms=2500.0).retardo_ms * SR / 1000
        aligned = np.zeros(len(rec))
        i = round(arrival)
        aligned[i : i + len(ref)] = ref[: len(rec) - i]
        levels[name] = medicion.niveles(rec, {name: aligned}, {name: (arrival - i) / SR * 1000})[name]
    return medicion.ganancias_para_igualar(levels)


class _OffsetStimulus:
    """`estimulos` with every stimulus seed moved by `offset`: the product fixes them (0 and 1
    for the two groups); another realisation is a parameter that should not matter."""

    def __init__(self, offset: int) -> None:
        self.offset = offset

    def calibracion(self, n, seconds, semilla=0):
        return estimulos.calibracion(n, seconds, semilla=semilla + self.offset)


def part_c(rooms_per_seed=6):
    out = {}
    for offset in (0, 10):
        session_module.estimulos = _OffsetStimulus(offset)
        try:
            for key, value in _part_c(rooms_per_seed, offset).items():
                out[f"stimulus+{offset}|{key}"] = value
        finally:
            session_module.estimulos = estimulos
    return out


def _part_c(rooms_per_seed, offset):
    out = {}
    names = [f"s{i}" for i in range(8)]
    for seed in (0, 1):
        rows = {"groups_2x10s": [], "at_once_10s": [], "at_once_20s": []}
        for r in range(rooms_per_seed):
            rng = np.random.default_rng(10_000 * seed + 100 * r + 8)
            delays, gains, irs = draw_room(rng)
            truth_delay = delays.max() - delays
            for label in rows:
                seconds = 20.0 if label == "at_once_20s" else 10.0
                cal = Calibration(names, seconds, 0.1, SR)
                if label != "groups_2x10s":
                    # All eight at once: one group of 8, as the calibration did before.
                    cal.groups = [names]
                    tracks = estimulos.calibracion(8, seconds, semilla=seed + offset)
                    cal.references = {k: 0.1 * t for k, t in zip(names, tracks, strict=True)}
                    cal.segments = [(0, len(tracks[0]))]
                    cal.total = cal.before + len(tracks[0]) + cal.after
                rec, alone = through_room(play(cal), delays, gains, irs, rng)
                result = cal._calibrate(rec)  # noqa: SLF001
                truth_gain = isolated_gains(cal, alone, delays)
                if result is None:
                    rows[label].append((np.inf, np.inf, False))
                    continue
                d_err = max(abs(result.retardos_ms[k] - truth_delay[i]) for i, k in enumerate(names))
                g_err = max(abs(result.ganancias_db[k] - truth_gain[k]) for k in names)
                rows[label].append((d_err, g_err, bool(result.confiable)))
                print(seed, r, label, round(d_err, 4), round(g_err, 2), flush=True)
        for label, rr in rows.items():
            a = np.array([x[:2] for x in rr])
            out[f"seed{seed}|{label}"] = {
                "delay_err_max_ms": round(float(a[:, 0].max()), 4),
                "gain_err_median_db": round(float(np.median(a[:, 1])), 2),
                "gain_err_max_db": round(float(a[:, 1].max()), 2),
                "rooms_over_1db": int((a[:, 1] > 1.0).sum()),
                "rooms": len(rr),
                "reliable": int(sum(x[2] for x in rr)),
            }
            print(label, seed, out[f"seed{seed}|{label}"], flush=True)
    return out


# -- D ---------------------------------------------------------------------------------------


def part_d():
    out = {}
    gains = (1.0, 0.8, 0.6, 0.9, 0.7, 0.95, 0.75, 0.85)
    for n, seconds in ((3, 10.0), (3, 5.0), (8, 10.0)):
        runs = []
        for seed in range(12):
            tracks = [0.1 * t for t in estimulos.calibracion(n, seconds, semilla=seed)]
            rng = np.random.default_rng(seed + 100)
            mic = sum(g * t for g, t in zip(gains, tracks, strict=False)) + 0.001 * rng.standard_normal(len(tracks[0]))
            runs.append(response.response_with_coherence(mic, tracks[0], 0, SR))
        sel = (response.THIRDS >= 100) & (response.THIRDS <= 8000)
        observed = np.array([r[0] for r in runs]).std(axis=0)[sel]
        predicted = np.mean([r[2] for r in runs], axis=0)[sel]
        coherence = np.mean([r[1] for r in runs], axis=0)[sel]
        out[f"N{n}|{seconds:.0f}s"] = {
            "coherence_median": round(float(np.median(coherence)), 3),
            "predicted_error_db_100Hz_1k_8k": [round(float(predicted[i]), 2) for i in (0, 10, -1)],
            "ratio_observed_over_predicted_min_max": [round(float((observed / predicted).min()), 2), round(float((observed / predicted).max()), 2)],
        }
        print(f"N{n}|{seconds:.0f}s", out[f"N{n}|{seconds:.0f}s"], flush=True)
    return out


def main() -> None:
    if sys.argv[1:] == ["C"]:  # only the group calibration, into the existing data
        data = json.loads(DEST.read_text())
        data["group_calibration"] = part_c()
        DEST.write_text(json.dumps(data, indent=1) + "\n")
        return
    trials = int(sys.argv[1]) if len(sys.argv) > 1 else 8
    t0 = time.time()
    data = {
        "environment": {
            "machine": platform.node(),
            "python": platform.python_version(),
            "numpy": np.__version__,
            "date": time.strftime("%Y-%m-%d"),
        },
        "trials": trials,
        "estimator": part_a(trials),
        "loop_one_hour": part_b(),
        "group_calibration": part_c(),
        "coherence_error": part_d(),
    }
    DEST.parent.mkdir(parents=True, exist_ok=True)
    DEST.write_text(json.dumps(data, indent=1) + "\n")
    print(f"{time.time() - t0:.0f} s; {DEST}")


if __name__ == "__main__":
    main()
