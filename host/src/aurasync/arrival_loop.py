"""The recalibration loop for absolute arrivals: per speaker, and following each one's drift.

**Why a second controller, and the error it fixes.** The session's loop correlates the
microphone against what each speaker was sent, taken *after* the delay line
(`sincronia.VentanaDeEmision` stores the blocks the player gets). So what it measures is each
speaker's own playback latency, and **the delay the loop applied is not in it**. SIMULADO on
2026-10-02 with the real engine and `medicion.calibrar`: applied delays of (0, 0, 0),
(0, 5, 0) and (4, 0, 2) ms all measured the same corrections, 9.00 / 4.50 / 0.00 ms
(within 0.01 ms). `sincronia.Controlador.proponer` treats the measurement as the *residual*
left after the corrections and adds it to them (`_componer`), so every accepted round adds the
whole offset again: the delays grow instead of converging. That is the signature of
experimentos/09 §5 — "las propuestas siguientes para Blue crecieron (+11,3 ms), cuando con
ganancia de lazo 0,5 y un objetivo estable tendrían que encogerse" — which until now was put
down only to correlated content (INFERIDO that it explains that session too: the log has no
references to check it against). The same holds for the level: the loop's level correction was
added again on every accepted round. This controller corrects delays only (module end).

**What it does, from experimentos/16 §4.2** (the prototype is `probes/19-ocho-parlantes/
lazo.py`, `track(feedforward=True)`):

- **per speaker**: each speaker's measurement is judged on its own (`probe_measure`), and one
  bad speaker does not discard the rest;
- **a common frame**: arrivals are absolute within one measurement, with an offset that changes
  between measurements (the microphone and the output are not read at the same instant). Each
  measurement is brought to the loop's frame by the median, over the speakers with a history,
  of (arrival - predicted arrival): one wrong speaker does not move the frame;
- **repetition** (CLAUDE.md): a speaker's new arrival is believed if it agrees with what its
  history predicts within `tolerance_ms`; one that does not is held, and believed only if the
  next measurement agrees with it (a real jump, as an A2DP stream that resynchronises). A
  speaker is corrected only after two agreeing measurements;
- **its drift**: a Theil-Sen slope over the last `history` arrivals of each speaker. Between
  measurements `advance` moves each speaker's delay at its rate (what a resampler would do),
  so the dead band no longer has to absorb the drift accumulated between corrections;
- **the rules that stay**: dead band (`sincronia.ZONA_MUERTA_MS`), gain 0.5, and the smallest
  delay at 0 (no latency added for nothing).

The arrival of what plays now is `delay + latency`: the loop aligns that sum, and the Haas
delay of the rear speakers (`retardo_traseros_ms x ambiente`) stays on top of it, as the
engine adds it outside `retardo_ms`.

**Levels are not corrected here.** The reference is taken after the gain too, so the measured
level would be the room's alone; equalising it on every round would override the gains the
listener or the calibration chose. Levels belong to the calibration, which plays its stimulus
through the applied gain and measures a true residual (`session.Calibration`).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np

from aurasync import sincronia

if TYPE_CHECKING:
    from collections.abc import Callable

    from aurasync.config import Instalacion
    from aurasync.motor import Motor

HISTORY = 8
"""Arrivals per speaker the slope is fitted on (experimentos/16 §4.2)."""
MIN_SPAN_S = 10.0
"""A slope needs its points this far apart: over 4 s, 0.01 ms of noise is 2.5 ppm."""
MAX_DRIFT_PPM = 100.0
"""Twice the worst drift considered (50 ppm): a slope beyond it is the estimator's, not the
speaker's, and is clipped."""
_NOTHING_MS = 1e-6
ADVANCE_EVERY_S = 1.0
"""How often `advance` moves the delays: at 50 ppm, 0.05 ms per step, far under the delay
line's ramp (0.5 ms/s) and inaudible."""


@dataclass
class _Track:
    points: list[tuple[float, float]] = field(default_factory=list)
    """(time, arrival in the loop's frame), the believed ones, oldest first."""
    held: tuple[float, float] | None = None
    """An arrival that did not agree with the history, waiting for the next one."""
    slope: float = 0.0
    """ms per second."""
    confirmed: bool = False
    """The last two believed arrivals agree: the speaker can be corrected."""

    def predict(self, t: float) -> float | None:
        if not self.points:
            return None
        ts = np.array([p[0] for p in self.points])
        ys = np.array([p[1] for p in self.points])
        return float(np.median(ys - self.slope * ts) + self.slope * t)


class ArrivalLoop:
    """Takes absolute arrivals per speaker and keeps the installation's delays aligned."""

    def __init__(
        self,
        installation: Instalacion,
        motor: Motor | None = None,
        *,
        dead_band_ms: float = sincronia.ZONA_MUERTA_MS,
        tolerance_ms: float = sincronia.ZONA_MUERTA_MS,
        factor: float = sincronia.FACTOR_POR_DEFECTO,
        track_drift: bool = True,
        history: int = HISTORY,
        clock: Callable[[], float] | None = None,
    ) -> None:
        """`clock`: the time the measurements' `t` are on (default `time.monotonic`, read at
        each call, so a test that replaces it is followed)."""
        self.installation = installation
        self.motor = motor
        self.dead_band_ms = dead_band_ms
        self.tolerance_ms = tolerance_ms
        self.factor = factor
        self.track_drift = track_drift
        self.history = history
        self.clock = clock or (lambda: time.monotonic())
        self._tracks: dict[str, _Track] = {}
        self._advanced_at: float | None = None
        self._historial: list[tuple[float, dict[str, float]]] = []
        self.ajustes: list[sincronia.Ajuste] = []

    # -- a measurement --------------------------------------------------------------

    def propose(self, arrivals_ms: dict[str, float], valid: set[str] | frozenset[str], t: float) -> sincronia.Ajuste:
        """One measurement: `arrivals_ms` of the speakers in `valid` (others are ignored),
        taken around time `t` (the middle of the measured window, on `clock`)."""
        adjustment = self._decide(arrivals_ms, valid, t)
        self.ajustes.append(adjustment)
        return adjustment

    def _decide(self, arrivals_ms: dict[str, float], valid, t: float) -> sincronia.Ajuste:
        names = {p.nombre for p in self.installation.parlantes}
        seen = {n: float(a) for n, a in arrivals_ms.items() if n in valid and n in names and np.isfinite(a)}
        if len(seen) < 2:  # noqa: PLR2004 - an alignment needs two speakers
            return sincronia.Ajuste(False, "fewer than two speakers were measured reliably")
        for n in seen:
            self._tracks.setdefault(n, _Track())
        predicted = {n: self._tracks[n].predict(t) for n in seen}
        known = [seen[n] - predicted[n] for n in seen if predicted[n] is not None]
        offset = float(np.median(known)) if known else float(np.median(list(seen.values())))

        held, jumped = [], []
        for n, a in seen.items():
            track, value = self._tracks[n], a - offset
            if predicted[n] is None or abs(value - predicted[n]) <= self.tolerance_ms:
                track.confirmed = predicted[n] is not None
                track.points = [*track.points, (t, value)][-self.history :]
                track.held = None
            elif track.held is not None and abs(value - (track.held[1] + track.slope * (t - track.held[0]))) <= (
                self.tolerance_ms
            ):
                # The same jump twice: it is real (a stream that resynchronised). Start over.
                track.points, track.held, track.confirmed, track.slope = [track.held, (t, value)], None, True, 0.0
                jumped.append(n)
            else:
                track.held, track.confirmed = (t, value), False
                held.append(n)
            self._fit(track)

        now = self.clock()
        current = {p.nombre: p.retardo_ms for p in self.installation.parlantes}
        sums = {
            n: current[n] + track.predict(now)
            for n, track in self._tracks.items()
            if track.confirmed and n in seen and n not in held
        }
        if len(sums) < 2:  # noqa: PLR2004
            reason = f"waiting for a second agreeing measurement ({', '.join(sorted(held or seen))})"
            return sincronia.Ajuste(False, reason)
        target = float(np.median(list(sums.values())))
        changes = {}
        # The dead band is on the misalignment (the spread of the arrivals), not on each
        # speaker: two speakers 0.4 ms off in opposite directions are 0.8 ms apart, and a dead
        # band per speaker would never touch them.
        if max(sums.values()) - min(sums.values()) > self.dead_band_ms:
            for n, total in sums.items():
                step = self.factor * (target - total)
                if abs(step) > _NOTHING_MS:
                    self.installation.por_nombre(n).retardo_ms += step
                    changes[n] = step
        if not changes:
            note = f"; held: {', '.join(sorted(held))}" if held else ""
            return sincronia.Ajuste(True, f"aligned within the measurement's noise{note}")
        self._normalise()
        if self.motor is not None:
            self.motor.actualizar()
        self._historial.append((now, {p.nombre: p.retardo_ms for p in self.installation.parlantes}))
        worst = max(abs(v) for v in changes.values())
        note = f"; jump confirmed in {', '.join(sorted(jumped))}" if jumped else ""
        return sincronia.Ajuste(True, f"applied: the largest step was {worst:.2f} ms{note}", cambios_ms=changes)

    def _fit(self, track: _Track) -> None:
        pts = track.points
        if not self.track_drift or len(pts) < 3 or pts[-1][0] - pts[0][0] < MIN_SPAN_S:  # noqa: PLR2004
            return
        slopes = [
            (pts[j][1] - pts[i][1]) / (pts[j][0] - pts[i][0])
            for i in range(len(pts))
            for j in range(i + 1, len(pts))
            if pts[j][0] > pts[i][0]
        ]
        limit = MAX_DRIFT_PPM * 1e-6 * 1000  # ms per second
        track.slope = float(np.clip(np.median(slopes), -limit, limit))

    # -- between measurements -------------------------------------------------------

    def advance(self, now: float | None = None) -> bool:
        """Move each speaker's delay at its drift rate since the last call. True if it moved.

        A speaker whose playback latency grows by `slope` ms per second needs that much less
        delay per second to keep arriving with the others."""
        now = self.clock() if now is None else now
        if self._advanced_at is None:
            self._advanced_at = now
            return False
        dt = now - self._advanced_at
        if not self.track_drift or dt < ADVANCE_EVERY_S:
            return False
        self._advanced_at = now
        moved = False
        for p in self.installation.parlantes:
            track = self._tracks.get(p.nombre)
            if track is not None and track.slope and track.confirmed:
                p.retardo_ms -= track.slope * dt
                moved = True
        if moved:
            self._normalise()
            if self.motor is not None:
                self.motor.actualizar()
        return moved

    def _normalise(self) -> None:
        low = min(p.retardo_ms for p in self.installation.parlantes)
        if low != 0.0:
            for p in self.installation.parlantes:
                p.retardo_ms -= low

    # -- what the panel reads -------------------------------------------------------

    def drift_ppm(self) -> dict[str, float]:
        """Each speaker's drift against the others' frame, in ppm (slopes, once fitted)."""
        return {n: round(t.slope * 1000, 2) for n, t in self._tracks.items() if len(t.points) >= 3}  # noqa: PLR2004

    def deriva_ms_h(self) -> dict[str, float] | None:
        """The same as `sincronia.Controlador.deriva_ms_h`, from the fitted slopes (ms/h)."""
        fitted = {n: t.slope * 3600 for n, t in self._tracks.items() if len(t.points) >= 3}  # noqa: PLR2004
        return fitted or None

    @property
    def historial(self) -> list[tuple[float, dict[str, float]]]:
        return list(self._historial)
