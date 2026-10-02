"""Item 2 of experiment 16: the decorrelator's limit of 6, and what passes with 7 and 8.

SIMULATED (filters and pink noise; no speakers). For N = 3..8 and several banks:

- `current`: `decorrelate.banco_decorrelador` as the product builds it (256 taps, 64
  candidates), forced past its limit;
- `L512`, `L1024`, `L1024_s2`: the same generator with longer filters (and the chain's
  largest spread, 2 ms);
- `cand1024`: the same greedy pick over 1024 candidates;
- `minimax_L*`: a pool of 512 candidates and a local search that minimises the WORST pair
  (greedy start, then swap the members of the worst pair while it improves);
- `velvet_*`: velvet-noise decorrelators (Alary, Politis, Välimäki, DAFx-17): sparse +-1
  impulses with a decaying envelope; cheap, but not all-pass, so the flatness decides.

Metrics, per bank (analytic, on a 16384-point grid, pink weighting like
`decorrelate.correlacion_rosa`):

- `worst_full`: the worst pair's zero-lag correlation with pink noise (the product's metric);
- `worst_500`: the same restricted to >= 500 Hz (the criterion: < 0.6, what 3 get today);
- `worst_octave`: the worst pair in each octave band 125 Hz..8 kHz;
- `worst_lag1ms_500`: the worst pair's |correlation| maximised over lags within +-1 ms (>= 500 Hz);
- `flat_db`: the largest third-octave deviation of any output, 50 Hz..16 kHz (criterion +-0.5 dB).

Then a check with signals: one pink noise (fully correlated input) through the chosen banks,
correlation per octave measured on the outputs, with two noise seeds.

    cd host && hatch run python ../probes/19-ocho-parlantes/decorrelador.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))

from comun import SR, save  # noqa: E402

from aurasync.dsp import decorrelate  # noqa: E402

NF = 16384
F = np.fft.rfftfreq(NF, 1 / SR)
PINK = np.where(F > 20, 1 / np.maximum(F, 1), 0.0)  # noqa: PLR2004
OCTAVES = [125, 250, 500, 1000, 2000, 4000, 8000]
THIRDS = 1000 * 2 ** (np.arange(-13, 13) / 3)  # 50 Hz .. 16 kHz
THIRDS = THIRDS[(THIRDS >= 50) & (THIRDS <= 16000)]
NS = range(3, 9)


# -- generators -----------------------------------------------------------------------


def velvet(length_ms: float, density: float, decay_db: float, seed: int) -> np.ndarray:
    """Velvet noise: one +-1 impulse per segment of SR/density samples, at a random place,
    with an exponential envelope that falls `decay_db` over the filter."""
    rng = np.random.default_rng(seed)
    n = int(length_ms * SR / 1000)
    td = SR / density
    m = int(n / td)
    pos = (np.arange(m) * td + rng.uniform(0, 1, m) * (td - 1)).astype(int)
    sign = rng.choice([-1.0, 1.0], m)
    env = 10 ** (-decay_db / 20 * pos / n)
    h = np.zeros(n)
    h[pos] = sign * env
    return h / np.sqrt((h**2).sum())


def pool_grouped(length: int, count: int, seed: int, spread: float = decorrelate.VARIACION_MS) -> list[np.ndarray]:
    rng = np.random.default_rng(seed)
    seeds = rng.integers(0, 2**31 - 1, count)
    return [decorrelate.filtro_todo_paso(length, int(s), variacion_ms=spread) for s in seeds]


def pool_velvet(length_ms: float, density: float, decay_db: float, count: int, seed: int) -> list[np.ndarray]:
    rng = np.random.default_rng(seed)
    return [velvet(length_ms, density, decay_db, int(s)) for s in rng.integers(0, 2**31 - 1, count)]


# -- metrics --------------------------------------------------------------------------


def spectra(bank: list[np.ndarray]) -> np.ndarray:
    return np.array([np.fft.rfft(h, NF) for h in bank])


def corr_matrix(h: np.ndarray, lo: float = 0.0, hi: float = SR / 2) -> np.ndarray:
    w = PINK * ((F >= lo) & (F < hi))
    cross = np.real((h * w) @ np.conj(h).T)
    d = np.sqrt(np.clip(np.diag(cross), 1e-30, None))
    return np.abs(cross) / np.outer(d, d)


def worst(c: np.ndarray, idx: list[int] | None = None) -> float:
    if idx is not None:
        c = c[np.ix_(idx, idx)]
    iu = np.triu_indices(len(c), 1)
    return float(c[iu].max()) if len(iu[0]) else 0.0


def worst_lag(h: np.ndarray, lo: float = 500.0, max_lag_ms: float = 1.0) -> float:
    w = PINK * (F >= lo)
    lag = int(max_lag_ms * SR / 1000)
    out = 0.0
    for i in range(len(h)):
        for j in range(i + 1, len(h)):
            x = np.fft.irfft(w * h[i] * np.conj(h[j]), NF)
            norm = np.sqrt(np.sum(w * np.abs(h[i]) ** 2) * np.sum(w * np.abs(h[j]) ** 2)) / NF * 2
            seg = np.concatenate([x[-lag:], x[: lag + 1]])
            out = max(out, float(np.abs(seg).max() / norm))
    return out


def flatness_db(h: np.ndarray) -> float:
    worst_dev = 0.0
    p = np.abs(h) ** 2
    for c in THIRDS:
        band = (F >= c / 2 ** (1 / 6)) & (F < c * 2 ** (1 / 6))
        lv = 10 * np.log10(p[:, band].mean(axis=1))
        worst_dev = max(worst_dev, float(np.abs(lv - 10 * np.log10(p[:, (F > 50) & (F < 16000)].mean(axis=1))).max()))
    return worst_dev


def describe(bank: list[np.ndarray]) -> dict:
    h = spectra(bank)
    return {
        "worst_full": round(worst(corr_matrix(h)), 3),
        "worst_500": round(worst(corr_matrix(h, 500)), 3),
        "worst_octave": {
            str(o): round(worst(corr_matrix(h, o / np.sqrt(2), o * np.sqrt(2))), 3) for o in OCTAVES
        },
        "worst_lag1ms_500": round(worst_lag(h), 3),
        "flat_db": round(flatness_db(h), 2),
        "taps": len(bank[0]),
        "nonzero_taps": int(np.count_nonzero(bank[0])),
    }


# -- selection ------------------------------------------------------------------------


def greedy(c: np.ndarray, n: int) -> list[int]:
    chosen = [0]
    while len(chosen) < n:
        rest = [i for i in range(len(c)) if i not in chosen]
        scores = [c[i, chosen].max() for i in rest]
        chosen.append(rest[int(np.argmin(scores))])
    return chosen


def minimax(c: np.ndarray, n: int) -> list[int]:
    """Greedy start, then: take the worst pair and try every swap of either member."""
    chosen = greedy(c, n)
    best = worst(c, chosen)
    improved = True
    while improved:
        improved = False
        sub = c[np.ix_(chosen, chosen)].copy()
        np.fill_diagonal(sub, -1)
        a, b = np.unravel_index(np.argmax(sub), sub.shape)
        for member in (a, b):
            for cand in range(len(c)):
                if cand in chosen:
                    continue
                trial = chosen.copy()
                trial[member] = cand
                score = worst(c, trial)
                if score < best - 1e-9:
                    chosen, best, improved = trial, score, True
            if improved:
                break
    return chosen


def combined(h: np.ndarray) -> np.ndarray:
    """What the optimiser minimises: the worse of the full-band and the >= 500 Hz correlations."""
    return np.maximum(corr_matrix(h), corr_matrix(h, 500))


# -- signals --------------------------------------------------------------------------


def signal_check(bank: list[np.ndarray], seed: int, seconds: float = 10.0) -> dict:
    """One pink noise through every filter; zero-lag correlation per octave, worst pair."""
    from aurasync.estimulos import ruido_rosa  # noqa: PLC0415

    x = ruido_rosa(int(seconds * SR), SR, seed)
    k = 1 << int(np.ceil(np.log2(len(x) + max(len(h) for h in bank))))
    xf = np.fft.rfft(x, k)
    f = np.fft.rfftfreq(k, 1 / SR)
    outs = [xf * np.fft.rfft(h, k) for h in bank]
    res = {}
    for label, lo, hi in [("full", 20, SR / 2), (">=500", 500, SR / 2)] + [
        (str(o), o / np.sqrt(2), o * np.sqrt(2)) for o in OCTAVES
    ]:
        band = (f >= lo) & (f < hi)
        ys = [np.fft.irfft(np.where(band, y, 0), k)[: len(x)] for y in outs]
        w = 0.0
        for i in range(len(ys)):
            for j in range(i + 1, len(ys)):
                r = abs(float(ys[i] @ ys[j]) / np.sqrt(float(ys[i] @ ys[i]) * float(ys[j] @ ys[j])))
                w = max(w, r)
        res[label] = round(w, 3)
    return res


def main() -> None:
    t0 = time.time()
    results: dict = {"current_seeds": {}, "banks": {}, "signals": {}}

    # 1. Today's bank, ten seeds: the distribution of the worst pair per N.
    for n in NS:
        rows = []
        for s in range(10):
            bank = decorrelate.banco_decorrelador(n, semilla=s)
            h = spectra(bank)
            rows.append((worst(corr_matrix(h)), worst(corr_matrix(h, 500))))
        a = np.array(rows)
        results["current_seeds"][str(n)] = {
            "worst_full_median": round(float(np.median(a[:, 0])), 3),
            "worst_full_max": round(float(a[:, 0].max()), 3),
            "worst_500_median": round(float(np.median(a[:, 1])), 3),
            "worst_500_max": round(float(a[:, 1].max()), 3),
        }
        print("current seeds", n, results["current_seeds"][str(n)], flush=True)

    # 2. Banks per method and N, with two selection seeds each.
    methods = {
        "current": lambda n, s: decorrelate.banco_decorrelador(n, semilla=s),
        "L512": lambda n, s: decorrelate.banco_decorrelador(n, largo=512, semilla=s),
        "L1024": lambda n, s: decorrelate.banco_decorrelador(n, largo=1024, semilla=s),
        "L1024_s2": lambda n, s: decorrelate.banco_decorrelador(n, largo=1024, semilla=s, variacion_ms=2.0),
        "cand1024": lambda n, s: decorrelate.banco_decorrelador(n, candidatos=1024, semilla=s),
    }
    pools = {
        "minimax_L256": lambda s: pool_grouped(256, 512, s),
        "minimax_L512": lambda s: pool_grouped(512, 512, s),
        "minimax_L1024": lambda s: pool_grouped(1024, 512, s),
        "velvet_30ms_1k": lambda s: pool_velvet(30, 1000, 20, 512, s),
        "velvet_20ms_2k": lambda s: pool_velvet(20, 2000, 20, 512, s),
    }
    for s in (0, 1):
        for label, make in methods.items():
            for n in NS:
                key = f"{label}|N{n}|seed{s}"
                results["banks"][key] = describe(make(n, s))
                print(key, results["banks"][key], flush=True)
        for label, make in pools.items():
            pool = make(s)
            hp = spectra(pool)
            if label.startswith("velvet"):
                # Keep the flattest quarter, then optimise among those.
                flat = np.array([flatness_db(hp[i : i + 1]) for i in range(len(pool))])
                keep = np.argsort(flat)[: len(pool) // 4]
                pool, hp = [pool[i] for i in keep], hp[keep]
            c = combined(hp)
            for n in NS:
                idx = minimax(c, n)
                key = f"{label}|N{n}|seed{s}"
                results["banks"][key] = describe([pool[i] for i in idx])
                print(key, results["banks"][key], flush=True)
                if n == 8 and label in {"minimax_L512", "minimax_L1024", "velvet_30ms_1k"}:
                    results.setdefault("chosen_banks", {})[f"{label}|seed{s}"] = [pool[i].tolist() for i in idx]

    # 3. Signals: one pink noise through the N = 8 banks, two noise seeds.
    for label in ("current", "L1024", "minimax_L1024"):
        if label == "current":
            bank = decorrelate.banco_decorrelador(8, semilla=0)
        elif label == "L1024":
            bank = decorrelate.banco_decorrelador(8, largo=1024, semilla=0)
        else:
            bank = [np.array(h) for h in results["chosen_banks"]["minimax_L1024|seed0"]]
        for noise_seed in (11, 12):
            key = f"{label}|N8|noise{noise_seed}"
            results["signals"][key] = signal_check(bank, noise_seed)
            print(key, results["signals"][key], flush=True)
        bank3 = decorrelate.banco_decorrelador(3, semilla=0)
        results["signals"][f"current|N3|noise{11}"] = signal_check(bank3, 11)

    results.pop("chosen_banks", None)
    dest = save("decorrelador.json", results)
    print(f"{time.time() - t0:.0f} s; {dest}")


if __name__ == "__main__":
    main()
