"""The decorrelator's bank for many speakers: how far apart its filters are, and which filter
goes to which speaker.

`dsp/decorrelate.py` makes the filters and chooses a bank greedily by the broadband correlation
with pink noise. Experiment 16 (`docs/research/experimentos/16-…` §2, SIMULATED) asked what
happens with 7-8 speakers, and the 2026-10-02 follow-up (§9 there) corrected part of the answer:

- **Per octave below ~2 kHz, two of these filters are almost the same signal, shifted.** Their
  group delay barely changes within an octave there, so inside it a pair differs by little more
  than a delay. At zero lag that shows as a correlation anywhere between 0 and 1 depending on
  the delay; **allowing ±1 ms of relative delay, the worst pair is 0.87-1.0 coherent in every
  octave of 250 Hz-2 kHz, already with 2 filters** (0.72-1.0 with ±0.5 ms). The engine itself shifts the speakers by
  more than that (the Haas delay, `ambience x rear_delay_ms`), and so does where the listener
  stands. So the per-octave limit is the filters' design, not the number of speakers, and a
  bank chosen to minimise the zero-lag worst pair per octave (prototyped in experiment 16)
  only re-centres that delay: through the real engine it came out **worse** with 7-8
  (§9). It is not in the product.
- **Above 500 Hz the filters do differ**, at any lag, and that degrades only slowly with N:
  the worst pair within ±1 ms goes from 0.46 with 3 filters to 0.50 with 8 (median of six
  seeds; 0.55 at most). That is what `Bank.worst_above_500` reports and what `notice` quotes.
- **Which filter goes to which speaker** (`assign`): what two speakers play correlates, in a
  model, as (their filters' correlation) x (their mixes' similarity), so neighbours with almost
  the same (pan, ambience) should get the filters that are least alike. The model's worst pair
  drops (0.38-0.44 to 0.35-0.36 with 8), **but through the real engine the speakers' feeds did
  not follow**: above 500 Hz at zero lag it was worse in 20 of 32 cases and better in 4; within
  ±8 ms, even (§9). So it is only a knob (`decorrelate.assignment: mix`) for the blind A/B, and
  the default stays `order`.

`bank()` is the one entry point, cached for the service's engine and the chain description
alike. It returns the greedy bank of `decorrelate.banco_decorrelador`, unchanged: with the
defaults every installation sounds bit for bit as before (`tests/test_chain_golden.py`).
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from functools import lru_cache

import numpy as np

from aurasync.dsp import decorrelate

GRID = 16384
"""FFT points for the analytic correlations (2.9 Hz per bin at 48 kHz)."""
ABOVE_HZ = 500.0
"""The band that is compared: experiment 16's criterion (< 0.6 above 500 Hz)."""
LAG_MS = 1.0
"""Relative delay within which a pair counts as alike (experiment 16's ±1 ms column)."""
ASSIGNMENTS = ("order", "mix")
"""`order`: filter k to speaker k (the default, the sound of before). `mix`: by `assign`."""
LR_CORRELATION = 0.5
"""How alike the left and right channels are assumed to be when the mixes are compared (0:
two independent channels, 1: mono). Music sits in between; 0.5 is a middle, not a
measurement (INFERIDO)."""
EXHAUSTIVE_UP_TO = 8
"""Up to this many speakers `assign` tries every permutation (8! = 40 320); above it, swaps."""


@dataclass(frozen=True)
class Bank:
    filters: tuple[np.ndarray, ...]
    pairs: np.ndarray
    """(filter, filter): the worst correlation of each pair's outputs above `ABOVE_HZ` with pink
    noise, over relative delays within ±`LAG_MS` (0 on the diagonal)."""

    @property
    def worst_above_500(self) -> float:
        """The worst pair: the number the status quotes."""
        return round(float(self.pairs.max()), 3) if len(self.filters) > 1 else 0.0


@lru_cache(maxsize=16)
def bank(
    n: int,
    length: int = decorrelate.LARGO_POR_DEFECTO,
    seed: int = 0,
    mean_ms: float = decorrelate.RETARDO_MEDIO_MS,
    spread_ms: float = decorrelate.VARIACION_MS,
    sr: int = 48000,
) -> Bank:
    """`decorrelate.banco_decorrelador`'s bank of `n` filters, with how far apart they are.
    Cached: the service's engine and the chain description ask for the same bank."""
    filters = decorrelate.banco_decorrelador(
        n, largo=length, semilla=seed, retardo_medio_ms=mean_ms, variacion_ms=spread_ms
    )
    for h in filters:
        h.flags.writeable = False  # shared by every engine that asks for this bank
    return Bank(tuple(filters), pair_correlations(filters, sr))


def assign(bank: Bank, mixes: list[tuple[float, float]]) -> list[int]:
    """Which filter of `bank` each speaker gets: `result[i]` is speaker i's filter.

    `mixes` is each speaker's (pan, ambience): with A2DP a speaker gets a mix, not a channel
    (`control.ROLES`). What two speakers play correlates as the correlation of their filters
    (`Bank.pairs`) times the similarity of their mixes (`mix_similarity`), so the permutation
    minimises the worst such product (ties: the smaller mean, then the installation's order).
    Up to `EXHAUSTIVE_UP_TO` speakers every permutation is tried; above, pairwise swaps from
    the installation's order.
    """
    n = len(mixes)
    if n != len(bank.filters):
        msg = f"{n} mixes for a bank of {len(bank.filters)} filters"
        raise ValueError(msg)
    if n < 2:  # noqa: PLR2004
        return list(range(n))
    f, s = bank.pairs, mix_similarity(mixes)
    iu = np.triu_indices(n, 1)
    if n <= EXHAUSTIVE_UP_TO:
        perms = _permutations(n)
        products = f[perms[:, iu[0]], perms[:, iu[1]]] * s[iu]
        score = np.round(products.max(axis=1) + 1e-3 * products.mean(axis=1), 9)
        # `argmin` keeps the first of equals, and the identity is the first permutation.
        return [int(i) for i in perms[int(np.argmin(score))]]

    def score_of(order: list[int]) -> float:
        o = np.asarray(order)
        p = f[o[iu[0]], o[iu[1]]] * s[iu]
        return round(float(p.max() + 1e-3 * p.mean()), 9)

    order = list(range(n))
    best = score_of(order)
    improved = True
    while improved:
        improved = False
        for i, j in itertools.combinations(range(n), 2):
            trial = order.copy()
            trial[i], trial[j] = trial[j], trial[i]
            score = score_of(trial)
            if score < best:
                order, best, improved = trial, score, True
    return order


@lru_cache(maxsize=EXHAUSTIVE_UP_TO)
def _permutations(n: int) -> np.ndarray:
    perms = np.array(list(itertools.permutations(range(n))), dtype=np.int8)
    perms.flags.writeable = False
    return perms


def worst_feeds(bank: Bank, mixes: list[tuple[float, float]], order: list[int]) -> float:
    """The model's worst pair of what the speakers play with this assignment (above 500 Hz,
    within ±1 ms): the number `assign` minimises."""
    if len(mixes) < 2:  # noqa: PLR2004
        return 0.0
    o = np.asarray(order)
    return round(float((bank.pairs[np.ix_(o, o)] * mix_similarity(mixes)).max()), 3)


def mix_similarity(mixes: list[tuple[float, float]], lr_correlation: float = LR_CORRELATION) -> np.ndarray:
    """How alike two speakers' mixes are, in [0, 1], for every pair (1 on the diagonal).

    The engine gives speaker i `(1 - a) * ((1 - p) / 2 * L + (1 + p) / 2 * R) + a * A` (`motor`),
    with `p` its pan and `a` its ambience. With L and R split into a common part C and two
    independent sides (powers `lr_correlation` and `1 - lr_correlation`), and the ambience A
    independent of both, each mix is a vector of weights over (C, sides, A), and the similarity
    is their normalised, power-weighted inner product.
    """
    rho = lr_correlation
    power = np.array([rho, 1 - rho, 1 - rho, 1.0])
    w = np.array([[(1 - a), (1 - a) * (1 - p) / 2, (1 - a) * (1 + p) / 2, a] for p, a in mixes], dtype=float)
    g = (w * power) @ w.T
    d = np.sqrt(np.clip(np.diag(g), 1e-30, None))
    return np.clip(g / np.outer(d, d), 0.0, 1.0)


def pair_correlations(filters: list[np.ndarray] | tuple[np.ndarray, ...], sr: int = 48000) -> np.ndarray:
    """|correlation| of every pair's outputs with pink noise above `ABOVE_HZ`, maximised over
    relative delays within ±`LAG_MS` (`decorrelate.correlacion_rosa`'s weighting)."""
    f = np.fft.rfftfreq(GRID, 1 / sr)
    w = np.where(f >= ABOVE_HZ, 1 / np.maximum(f, 1), 0.0)
    h = np.array([np.fft.rfft(x, GRID) for x in filters])
    lag = int(LAG_MS * sr / 1000)
    n = len(h)
    out = np.zeros((n, n))
    for i, j in itertools.combinations(range(n), 2):
        x = np.fft.irfft(w * h[i] * np.conj(h[j]), GRID)
        norm = np.sqrt(np.sum(w * np.abs(h[i]) ** 2) * np.sum(w * np.abs(h[j]) ** 2)) * 2 / GRID
        near = np.concatenate([x[-lag:], x[: lag + 1]]) if lag else x[:1]
        out[i, j] = out[j, i] = float(np.abs(near).max() / norm)
    return out


def notice(bank: Bank, three: Bank) -> str | None:
    """The warning for the status with more speakers than fixed filters fully decorrelate
    (`decorrelate.MAXIMO_FIJOS`), in Spanish for the panel; None up to that many. `three` is
    the bank of 3 with the same knobs, for comparison."""
    n = len(bank.filters)
    if n <= decorrelate.MAXIMO_FIJOS:
        return None

    def number(x: float) -> str:
        return f"{x:.2f}".replace(".", ",")

    return (
        f"con {n} parlantes la separación es menor: el peor par sobre 500 Hz es "
        f"{number(bank.worst_above_500)} (con 3, {number(three.worst_above_500)}); debajo de 2 kHz "
        "ningún banco de filtros fijos separa, con ningún número de parlantes"
    )


def notice_for(n: int, length: int, seed: int, mean_ms: float, spread_ms: float, sr: int = 48000) -> str | None:
    """`notice` for the bank of `n` with these knobs (both banks cached)."""
    if n <= decorrelate.MAXIMO_FIJOS:
        return None
    return notice(bank(n, length, seed, mean_ms, spread_ms, sr), bank(3, length, seed, mean_ms, spread_ms, sr))
