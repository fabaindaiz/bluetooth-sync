"""Experiment 16 §9: what survives of proposals 4 and 5 when they are taken to the product.

SIMULATED. Two checks that experiment 16 did not make:

1. **Does the per-octave advantage survive a small delay?** The bank chosen by its zero-lag
   worst pair per octave (`decorrelador_bandas.per_band_objective` + `decorrelador.minimax`,
   pool of 512) against today's greedy bank, per octave and above 500 Hz, at zero lag and
   maximised over ±0.5 / ±1 ms of relative delay. The engine itself shifts the speakers by
   `ambience x rear_delay_ms` (Haas), and the listener's position by more.
2. **Through the real engine**, with the ring's roles (`comun.installation`), four inputs (the
   two synthetic musics of probe 13, and two stereo pink noises with L/R correlation 0.5), the
   worst pair of what the speakers play: above 500 Hz at zero lag (experiment 16's §2.3 metric)
   and within ±8 ms (covering the engine's Haas offsets), and the worst octave 250 Hz-2 kHz at
   zero lag. Banks: today's in installation order, today's assigned by mix
   (`decorrelation_bank.assign`, the product's), and the octave bank in order.

    cd host && hatch run python ../probes/19-ocho-parlantes/aplicado.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))

import decorrelador as d  # noqa: E402
import decorrelador_bandas as db  # noqa: E402
from comun import SR, installation, save  # noqa: E402

from aurasync import motor  # noqa: E402
from aurasync.chain import ChainValues  # noqa: E402
from aurasync.dsp import decorrelate, decorrelation_bank  # noqa: E402
from aurasync.estimulos import ruido_rosa  # noqa: E402

OCTAVES = (250, 500, 1000, 2000)


def octave_bank(n: int, seed: int) -> list[np.ndarray]:
    pool = d.pool_grouped(256, 512, seed)
    return [pool[i] for i in d.minimax(db.per_band_objective(d.spectra(pool)), n)]


def lag_worst(filters, lo: float, hi: float, lag_ms: float) -> float:
    h = d.spectra(filters)
    w = d.PINK * ((d.F >= lo) & (d.F < hi))
    lag = int(lag_ms * SR / 1000)
    out = 0.0
    for i in range(len(h)):
        for j in range(i + 1, len(h)):
            x = np.fft.irfft(w * h[i] * np.conj(h[j]), d.NF)
            norm = np.sqrt(np.sum(w * abs(h[i]) ** 2) * np.sum(w * abs(h[j]) ** 2)) * 2 / d.NF
            near = np.concatenate([x[-lag:], x[: lag + 1]]) if lag else x[:1]
            out = max(out, float(abs(near).max() / norm))
    return round(out, 3)


def describe_bank(filters) -> dict:
    res = {}
    for lag in (0.0, 0.5, 1.0):
        res[f"lag{lag}ms"] = {
            **{str(o): lag_worst(filters, o / np.sqrt(2), o * np.sqrt(2), lag) for o in OCTAVES},
            ">=500": lag_worst(filters, 500, SR / 2, lag),
        }
    return res


def pink_stereo(seed: int, rho: float = 0.5, seconds: float = 12.0):
    n = int(seconds * SR)
    c, left, right = (ruido_rosa(n, SR, seed * 10 + k) for k in range(3))
    return np.sqrt(rho) * c + np.sqrt(1 - rho) * left, np.sqrt(rho) * c + np.sqrt(1 - rho) * right


def corr(a, b, lo, hi, lag_ms):
    k = 1 << int(np.ceil(np.log2(2 * len(a))))
    f = np.fft.rfftfreq(k, 1 / SR)
    band = (f >= lo) & (f < hi)
    fa, fb = np.fft.rfft(a, k) * band, np.fft.rfft(b, k) * band
    x = np.fft.irfft(fa * np.conj(fb), k)
    norm = np.sqrt(np.sum(abs(fa) ** 2) * np.sum(abs(fb) ** 2)) * 2 / k
    lag = int(lag_ms * SR / 1000)
    near = np.concatenate([x[-lag:], x[: lag + 1]]) if lag else x[:1]
    return float(abs(near).max() / norm)


def feeds(n: int, seed: int, variant: str, x) -> dict:
    inst = installation(n)
    m = motor.Motor(inst, SR, extraer_ambiente=True, decorrelar=True, ecualizar=False, chain=ChainValues(), semilla=seed)
    bank = decorrelation_bank.bank(n, 256, seed, decorrelate.RETARDO_MEDIO_MS, decorrelate.VARIACION_MS, SR)
    if variant == "today":
        filters = list(bank.filters)
    elif variant == "today_by_mix":
        order = decorrelation_bank.assign(bank, m._mezclas())  # noqa: SLF001
        filters = [bank.filters[k] for k in order]
    else:
        filters = octave_bank(n, seed)
    m._cambiar_filtros({p.nombre: h for p, h in zip(inst.parlantes, filters, strict=True)})  # noqa: SLF001
    out = motor.procesar_completo(m, *x, 4096)
    y = [out[p.nombre][SR:] for p in inst.parlantes]
    pairs = [(i, j) for i in range(n) for j in range(i + 1, n)]
    return {
        ">=500_lag0": round(max(corr(y[i], y[j], 500, SR / 2, 0) for i, j in pairs), 3),
        ">=500_lag8ms": round(max(corr(y[i], y[j], 500, SR / 2, 8) for i, j in pairs), 3),
        "octave_250_2k_lag0": round(
            max(corr(y[i], y[j], o / np.sqrt(2), o * np.sqrt(2), 0) for i, j in pairs for o in OCTAVES), 3
        ),
    }


def main() -> None:
    out: dict = {"banks": {}, "feeds": {}}
    for n in (2, 3, 4, 8):
        for s in (0, 1):
            today = decorrelate.banco_decorrelador(n, semilla=s)
            out["banks"][f"today|N{n}|seed{s}"] = describe_bank(today)
            if n >= 3:  # noqa: PLR2004
                out["banks"][f"octave|N{n}|seed{s}"] = describe_bank(octave_bank(n, s))
            print(n, s, flush=True)
    inputs = {
        "music0": db.sonda.music(12.0, np.random.default_rng(50)),
        "music1": db.sonda.music(12.0, np.random.default_rng(51)),
        "pink0": pink_stereo(1),
        "pink1": pink_stereo(2),
    }
    for n in (4, 6, 7, 8):
        for s in (0, 1):
            for variant in ("today", "today_by_mix", "octave"):
                key = f"N{n}|seed{s}|{variant}"
                out["feeds"][key] = {name: feeds(n, s, variant, x) for name, x in inputs.items()}
                print(key, out["feeds"][key], flush=True)
    print(save("aplicado.json", out))


if __name__ == "__main__":
    main()
