"""Calibrating more than six speakers in two groups that share an anchor speaker.

experimentos/16 §3.3 (SIMULADO): with one microphone and every speaker playing its own pink
noise at once, the delay scales to 8 speakers (≤ 0.006 ms of error), but the **gain** does not:
the other N - 1 noises leak into each speaker's level, and from 7 speakers on a calibration
passes 1 dB of error (4 of 12 rooms with 8 speakers at 10 s; 2 of 12 at 20 s). Two groups of
4-5, each with its own recording and joined through a speaker that plays in both, gave **0 of
12 rooms** over 1 dB at 2 x 10 s, in both stimulus seeds. At the same total time as 10 s (2 x
5 s) they did not help: each group needs the full duration.

So: up to `MAX_AT_ONCE` speakers calibrate as before, all at once; above it, in two groups,
each playing for the calibration's full duration (the calibration takes twice as long), that
share `SHARED` speakers. The delay does not need the groups; it is joined too, because each
group's recording has its own playback offset.

**Three shared speakers, not one (2026-10-02, SIMULADO, experimentos/11 paso 2 §C).** With
the product's own stimulus (seeds 0 and 1 for the two groups), one anchor left 2 of 12 rooms
over 1 dB (max 1.19 dB): the whole second group moved together, by the anchor's own level
error. The 0 of 12 of experimentos/16 had come with other stimulus seeds, so it did not
survive a change of a parameter that should not matter. Joining through the mean over three
shared speakers gave 0 of 12 with both stimulus sets (max 0.79 and 0.98 dB); two, 1 of 12 and
0 of 12. The shared speakers are the installation's first three.
"""

from __future__ import annotations

import math

import numpy as np

from aurasync import medicion

MAX_AT_ONCE = 6
"""Up to this many speakers, one group (as before). experimentos/16 §3.2: with 6, 0 of 12
rooms over 1 dB with the corrected level estimator; with 7, 1 of 12."""
SHARED = 3
"""Speakers that play in both groups and join them (module doc)."""


def groups(names: list[str], max_at_once: int = MAX_AT_ONCE, shared: int = SHARED) -> list[list[str]]:
    """The groups to calibrate `names` in. One group up to `max_at_once`; else two that share
    the first `shared` speakers, the first group the larger (8: 0-5 and 0-2 + 6-7)."""
    if len(names) <= max_at_once:
        return [list(names)]
    common, rest = list(names[:shared]), list(names[shared:])
    first = math.ceil(len(rest) / 2)
    return [[*common, *rest[:first]], [*common, *rest[first:]]]


def join(calibrations: list[medicion.Calibracion | None], groups_: list[list[str]]) -> medicion.Calibracion | None:
    """Join the groups' calibrations through the speakers they share.

    Delays: each group's corrections are (last arrival - arrival), so -correction is the
    arrival up to the group's own constant. Levels: up to the group's own scale. Each group
    after the first is brought to the first's frame by the mean, over the shared speakers, of
    the arrival difference and of the log-level ratio; a shared speaker's values are the mean
    of its two (in that frame), its stability the worse of the two. `None` if a group could
    not be aligned or no shared speaker has a finite delay and level in both.
    """
    if len(calibrations) != len(groups_) or any(c is None for c in calibrations):
        return None
    if len(calibrations) == 1:
        return calibrations[0]
    common = [n for n in groups_[0] if all(n in g for g in groups_)]
    arrival: dict[str, list[float]] = {}
    log_level: dict[str, list[float]] = {}
    stability: dict[str, float] = {}
    for cal, names in zip(calibrations, groups_, strict=True):
        a = {n: -cal.retardos_ms[n] for n in names}
        lv = {n: float(np.log(cal.niveles[n])) if cal.niveles.get(n, 0.0) > 0 else float("nan") for n in names}
        if arrival:
            usable = [n for n in common if np.isfinite(a[n]) and np.isfinite(lv[n]) and np.isfinite(arrival[n][0])]
            if not usable:
                return None
            shift_a = float(np.mean([arrival[n][0] - a[n] for n in usable]))
            shift_l = float(np.mean([log_level[n][0] - lv[n] for n in usable]))
        else:
            shift_a = shift_l = 0.0
        for n in names:
            arrival.setdefault(n, []).append(a[n] + shift_a)
            log_level.setdefault(n, []).append(lv[n] + shift_l)
            stability[n] = max(stability.get(n, 0.0), cal.estabilidad_ms[n])
    joined = {n: float(np.mean(v)) for n, v in arrival.items()}
    finite = [v for v in joined.values() if np.isfinite(v)]
    last = max(finite) if finite else 0.0
    levels = {n: float(np.exp(np.mean(v))) if np.all(np.isfinite(v)) else 0.0 for n, v in log_level.items()}
    return medicion.Calibracion(
        retardos_ms={n: (last - v if np.isfinite(v) else float("nan")) for n, v in joined.items()},
        ganancias_db=medicion.ganancias_para_igualar(levels),
        estabilidad_ms=stability,
        desfase_grueso_ms=calibrations[0].desfase_grueso_ms,
        niveles=levels,
    )
