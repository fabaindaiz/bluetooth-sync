"""Item 2, second part: the per-band limit, and what the speakers actually get.

SIMULATED. Two questions that `decorrelador.py` left open:

1. Does a bank chosen to minimise the worst pair **per octave band** (250 Hz..4 kHz),
   instead of the broadband correlation, get the per-band worst pair down with 8 filters?
2. What correlation do the 8 speakers' feeds actually have, through the real engine, with
   the ring's roles (each speaker gets a different L/R/ambience mix, `comun.role_from_angle`)?
   Synthetic music of probe 13; worst pair among neighbours and among all pairs, broadband
   and per octave; today's bank forced to 8 against N = 4 (quad) and N = 3; two music seeds.

    cd host && hatch run python ../probes/19-ocho-parlantes/decorrelador_bandas.py
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))

import decorrelador as d  # noqa: E402
from comun import ROOT, SR, installation, lifted_limit, save  # noqa: E402

from aurasync import motor  # noqa: E402

_spec = importlib.util.spec_from_file_location("sonda13", ROOT / "probes/13-sonda-enmascarada/simular.py")
sonda = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sonda)

BANDS = [250, 500, 1000, 2000, 4000]


def per_band_objective(h: np.ndarray) -> np.ndarray:
    mats = [d.corr_matrix(h, o / np.sqrt(2), o * np.sqrt(2)) for o in BANDS]
    return np.max(mats, axis=0)


def octave_corr(a: np.ndarray, b: np.ndarray, lo: float, hi: float) -> float:
    k = 1 << int(np.ceil(np.log2(len(a))))
    f = np.fft.rfftfreq(k, 1 / SR)
    band = (f >= lo) & (f < hi)
    fa, fb = np.fft.rfft(a, k) * band, np.fft.rfft(b, k) * band
    return float(abs(np.real(np.sum(fa * np.conj(fb)))) / np.sqrt(np.sum(abs(fa) ** 2) * np.sum(abs(fb) ** 2)))


def feeds_correlation(n: int, seed: int, decorrelate_on: bool) -> dict:
    rng = np.random.default_rng(seed)
    left, right = sonda.music(12.0, rng)
    inst = installation(n)
    with lifted_limit(8):
        m = motor.Motor(inst, SR, extraer_ambiente=True, decorrelar=decorrelate_on, ecualizar=False)
    out = motor.procesar_completo(m, left, right, 4096)
    y = [out[p.nombre][SR:] for p in inst.parlantes]  # skip the first second
    res = {}
    for label, lo, hi in [("full", 20, SR / 2), (">=500", 500, SR / 2)] + [
        (str(o), o / np.sqrt(2), o * np.sqrt(2)) for o in BANDS
    ]:
        c = np.array([[octave_corr(y[i], y[j], lo, hi) if i != j else 0 for j in range(n)] for i in range(n)])
        neigh = max(c[i, (i + 1) % n] for i in range(n))
        res[label] = {"worst_neighbours": round(float(neigh), 3), "worst_any": round(float(c.max()), 3)}
    return res


def main() -> None:
    out: dict = {"per_band_minimax": {}, "feeds": {}}
    for s in (0, 1):
        for length in (256, 512):
            pool = d.pool_grouped(length, 512, s)
            hp = d.spectra(pool)
            c = per_band_objective(hp)
            for n in (3, 6, 8):
                idx = d.minimax(c, n)
                key = f"L{length}|N{n}|seed{s}"
                out["per_band_minimax"][key] = d.describe([pool[i] for i in idx])
                print(key, out["per_band_minimax"][key], flush=True)
    for s in (0, 1):
        for n in (3, 4, 8):
            for dec in (True, False):
                key = f"N{n}|{'decorr' if dec else 'no_decorr'}|music{s}"
                out["feeds"][key] = feeds_correlation(n, 50 + s, dec)
                print(key, out["feeds"][key], flush=True)
    print(save("decorrelador_bandas.json", out))


if __name__ == "__main__" and not sys.argv[1:]:
    main()


def assignment_search(permutations: int = 24) -> dict:
    """Which filter goes to which speaker: today, bank filter k goes to speaker k. Try other
    assignments of today's 8-filter bank to the ring, pick the best on one music (worst pair
    >= 500 Hz) and check it on another (a choice that only fits one material would not repeat).

        hatch run python ../probes/19-ocho-parlantes/decorrelador_bandas.py asignacion
    """
    from aurasync.config import Instalacion  # noqa: PLC0415

    rng = np.random.default_rng(3)
    musics = [sonda.music(12.0, np.random.default_rng(50 + s)) for s in (0, 1)]
    base = installation(8)

    def worst_500(order: list[int], music) -> float:
        # Speakers keep their place on the ring; reordering the list reassigns the bank's filters.
        inst = Instalacion(parlantes=[base.parlantes[i] for i in order])
        with lifted_limit(8):
            m = motor.Motor(inst, SR, extraer_ambiente=True, decorrelar=True, ecualizar=False)
        out = motor.procesar_completo(m, *music, 4096)
        y = [out[p.nombre][SR:] for p in inst.parlantes]
        return max(octave_corr(y[i], y[j], 500, SR / 2) for i in range(8) for j in range(i + 1, 8))

    rows = []
    for k in range(permutations):
        order = list(range(8)) if k == 0 else [int(i) for i in rng.permutation(8)]
        rows.append((order, worst_500(order, musics[0])))
        print(k, rows[-1], flush=True)
    rows.sort(key=lambda r: r[1])
    best, today = rows[0], next(r for r in rows if r[0] == list(range(8)))
    res = {
        "today_music0": round(today[1], 3),
        "today_music1": round(worst_500(today[0], musics[1]), 3),
        "best_order": best[0],
        "best_music0": round(best[1], 3),
        "best_music1": round(worst_500(best[0], musics[1]), 3),
        "worst_of_tried_music0": round(rows[-1][1], 3),
        "tried": permutations,
    }
    print(res)
    print(save("decorrelador_asignacion.json", res))
    return res


if __name__ == "__main__" and sys.argv[1:] == ["asignacion"]:
    assignment_search()
