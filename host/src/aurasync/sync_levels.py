"""Level statistics from the measurements: for showing and comparing, never for equalising.

d-7c8794-e61118: the microphones are not accurate (a phone's own response, its gain control),
so what each one heard is kept as a statistic per microphone position and speaker — its
median, how much it moves, how many readings — and per speaker how much the positions
disagree. Nothing here feeds back to the engine.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:
    from collections.abc import Iterable

    from aurasync.sync_measurement import Measurement


def summarise(measurements: Iterable[Measurement]) -> dict[str, Any]:
    readings: dict[str, dict[str, list[float]]] = {}
    for m in measurements:
        for s, level in m.levels_db.items():
            if np.isfinite(level):
                readings.setdefault(m.position_id, {}).setdefault(s, []).append(level)
    by_position = {
        p: {
            s: {
                "median_db": round(float(np.median(v)), 2),
                "spread_db": round(float(np.percentile(v, 90) - np.percentile(v, 10)), 2),
                "n": len(v),
            }
            for s, v in speakers.items()
        }
        for p, speakers in readings.items()
    }
    by_speaker: dict[str, dict[str, Any]] = {}
    names = {s for speakers in by_position.values() for s in speakers}
    for s in sorted(names):
        medians = [speakers[s]["median_db"] for speakers in by_position.values() if s in speakers]
        by_speaker[s] = {"spread_across_positions_db": round(float(max(medians) - min(medians)), 2)}
    return {"by_position": by_position, "by_speaker": by_speaker}
