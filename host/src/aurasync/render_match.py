"""Every render at `classic`'s loudness (spec 2026-10-05-virtual-speakers-and-hot-join §9).

`direct` ("pure aligned stereo") drops the chain's effects and its EQ boost; `spatial` and `front`
change the loudness their own way. Switching render to compare them must not change the volume,
or the louder one wins (the same reason the A/B matches presets). This keeps one makeup gain per
render with the monitor's unit (`loudness_match.LoudnessMatch`):

- the **reference** is `classic`'s net loudness: the speakers' summed short-term loudness minus
  the input's (`quality.QualityMeter`, every speaker weighted 1), without the volume and the
  makeup. It is measured while `classic` plays and remembered, averaged slowly, while another
  render plays; `classic` itself never gets a makeup;
- the **candidate** is the playing render's net loudness, measured the same way, and its makeup
  moves toward `reference - candidate`: slowly (`loudness_match.RATE_PER_S`), capped at
  +/-12 dB, frozen on silence (the gate is the input's momentary loudness, before the volume: a
  quiet knob is not a pause, the monitor's lesson), during cuts and calibrations (`hold`), and
  while a blind A/B runs (`hold_while`: nothing is learned and the makeups stay; a switch still
  starts at the remembered makeup, so an A/B between presets of different renders compares them
  at matched loudness even without its own `match_loudness`);
- a multichannel source (`bypass`: the render is made elsewhere) plays with no makeup, ramped to
  0 dB and back to the render's own when it ends;
- a render **never measured** starts at 0 dB and is corrected faster (`FIRST_VISIT_RATE_PER_S`)
  until it locks once, so the first visit converges within 10 s; after that each switch starts at
  the remembered makeup, applied at the cut's bottom or glided over a render crossfade
  (`Motor.on_render_switch`, stage 3), and never jumps through the music;
- what the meter holds right after a cut or a crossfade (`en_corte`: the fade, the old render
  still in the delay lines) is not measured: the window starts `SETTLE_S` after the cut ends and grows to the meter's 3 s;
- the volume, the makeup and the A/B's compensation are taken back out of the measurement by what
  each block was really made with (`Motor.volumen_del_bloque_db`, `Motor.render_makeup_block_db`,
  `Motor.comparison_block_db`), averaged over the window, so a volume change while `direct` plays
  is not taken for a difference between renders (review 2026-10-06: the A/B's compensation leaked
  into classic's reference and stayed in the other render's makeup after the A/B);
- other speakers (one joined or left) forget everything, as the monitor's unit does: what each
  render needs changed;
- the reference is tied to the speakers that sound (the installation's and the muted ones): a
  speaker muted under `direct` lowers its loudness, not the render's, and matching it would turn
  the others up. A reference taken with other speakers waits (`reference_stale`) for `classic` to
  be heard again; the makeups are kept.

Everything runs on the engine thread, after the quality meter has taken the block
(`AudioSession.step`); the service keeps one per process, so the makeups outlive a session.
"""

from __future__ import annotations

import math
from collections import deque
from typing import TYPE_CHECKING, Any, Protocol

import numpy as np

from aurasync.loudness_match import CAP_DB, RATE_PER_S, LoudnessMatch

if TYPE_CHECKING:
    from collections.abc import Callable, Hashable

__all__ = ["CAP_DB", "REFERENCE", "RenderMatch"]

REFERENCE = "classic"
"""The render every other one is held to: the sound the engine always had."""
SETTLE_S = 0.5
"""After a cut ends, how long the meter still holds the fade and the old render (the fade-in is
80 ms; the delay lines hold up to 250 ms of what was playing; the extractor and the EQ ~65 ms)."""
MIN_WINDOW_S = 1.0
"""The shortest window measured: a momentary reading moves too much with the music."""
FIRST_VISIT_RATE_PER_S = 0.5
"""Fraction of the error corrected per second on a render's first visit, until it locks: a
10 dB error is under 0.2 dB in 10 s. After that, the monitor's `RATE_PER_S`."""
REFERENCE_RATE_PER_S = 0.1
"""How fast the remembered reference follows `classic`'s net loudness while it plays."""


class Meter(Protocol):
    steps_total: int
    step_samples: int
    short_steps: int
    input_momentary: float

    def net_lu(self, steps: int) -> float: ...


def _mean_power(db: float | np.ndarray) -> float:
    """The mean power gain of a block made with these gains (dB, one or one per sample)."""
    if isinstance(db, np.ndarray):
        return float(np.mean(10 ** (db / 10))) if len(db) else 1.0
    return 10 ** (float(db) / 10)


class RenderMatch:
    def __init__(self, *, first_rate_per_s: float = FIRST_VISIT_RATE_PER_S) -> None:
        self.match = LoudnessMatch()
        self.first_rate_per_s = first_rate_per_s
        self.reference_lu: float | None = None
        """`classic`'s net loudness without volume and makeup, remembered (LU)."""
        self._reference_key: Hashable | None = None
        self.render: str | None = None
        self.locked_once: set[str] = set()
        """Renders whose makeup has locked at least once: from then on, the slow pace."""
        self.reason: str | None = None
        """Why the playing render is not matched: `no_reference`, `reference_stale`, or None."""
        self._clean_from: int | None = None
        """The first meter step that is only the playing render; None while a cut is under way."""
        self._gains: deque[tuple[int, float]] = deque()
        """(samples, mean power gain) of the recent blocks: the volume and makeup they carried."""
        self._status = "measuring"
        self.hold_while: Callable[[], bool] | None = None
        """Asked each block: True holds the match (the service: while a blind A/B runs)."""
        self._speakers: tuple[str, ...] | None = None

    # -- the motor's side ---------------------------------------------------------------------

    def bind(self, motor: Any) -> None:
        """A new motor (a session opened): it asks at each render switch, and starts at the makeup
        its render needs. Before audio flows, so the makeup jumps."""
        motor.on_render_switch = self.select
        self._check_speakers(motor)
        motor.jump_render_makeup(self.select(motor.render))

    def select(self, render: str) -> float:
        """The render that plays from now on (at a cut's bottom): its remembered makeup, 0 dB for
        the reference and for a render never heard. The measurement window starts again."""
        self.render = render
        self._clean_from = None
        self._gains.clear()
        self.reason = None
        if render == REFERENCE:
            self.match.makeup[REFERENCE] = 0.0
        self._status = "reference" if render == REFERENCE else "measuring"
        return self.match.select(render, 0.0)

    # -- each block ---------------------------------------------------------------------------

    def after_block(self, motor: Any, meter: Meter, n: int, *, hold: bool = False, bypass: bool = False) -> None:
        """One block of `n` samples, after the meter took it. `hold`: the speakers did not play
        the render (a calibration's stimulus). `bypass`: they play a render made elsewhere (a
        multichannel source), which gets no makeup."""
        if self._check_speakers(motor):
            motor.render_makeup_db = self.select(motor.render)
        if self.render != motor.render:
            # A motor built elsewhere, or a switch nobody announced: take it as a switch.
            self.select(motor.render)
        motor.render_makeup_db = 0.0 if bypass else self.match.current_db
        gains = 1.0
        for name in ("volumen_del_bloque_db", "render_makeup_block_db", "comparison_block_db"):
            gains *= _mean_power(getattr(motor, name, 0.0))
        self._gains.append((n, gains))
        reach = meter.short_steps * meter.step_samples
        while len(self._gains) > 1 and self._gains_samples() - self._gains[0][0] >= reach:
            self._gains.popleft()
        if hold or bypass or motor.en_corte or (self.hold_while is not None and self.hold_while()):
            self._clean_from = None
            self._freeze()
            return
        steps = meter.steps_total
        if self._clean_from is None:
            self._clean_from = steps + math.ceil(SETTLE_S * _rate(meter) / meter.step_samples)
        window = min(meter.short_steps, steps - self._clean_from)
        if window * meter.step_samples < MIN_WINDOW_S * _rate(meter):
            self._status = "reference" if self.render == REFERENCE else "measuring"
            return
        net = meter.net_lu(window) - self._applied_db(window * meter.step_samples)
        gate = meter.input_momentary
        key = (tuple(p.nombre for p in motor.instalacion.parlantes), frozenset(motor.silenciados))
        seconds = n / _rate(meter)
        if self.render == REFERENCE:
            self._follow_reference(net, gate, key, seconds)
            return
        candidate: float | None = None
        if self.reference_lu is None:
            self.reason = "no_reference"
        elif key != self._reference_key:
            self.reason = "reference_stale"
        else:
            self.reason = None
            candidate = net
        self.match.rate_per_s = RATE_PER_S if self.render in self.locked_once else self.first_rate_per_s
        _start, end = self.match.update(
            self.reference_lu if self.reference_lu is not None else 0.0, candidate, seconds, gate_lufs=gate
        )
        self._status = self.match.status
        if self._status == "locked":
            self.locked_once.add(self.render)
        motor.render_makeup_db = end

    def _check_speakers(self, motor: Any) -> bool:
        """Other speakers than last time: forget every makeup and the reference. True if so."""
        speakers = tuple(p.nombre for p in motor.instalacion.parlantes)
        changed = self._speakers is not None and speakers != self._speakers
        self._speakers = speakers
        if changed:
            self.match.forget()
            self.locked_once.clear()
            self.reference_lu = self._reference_key = None
            self.render = None
        return changed

    def _freeze(self) -> None:
        if self.render != REFERENCE:
            self._status = "frozen"

    def _follow_reference(self, net: float, gate: float, key: Hashable, seconds: float) -> None:
        self._status = "reference"
        if not (math.isfinite(net) and math.isfinite(gate) and gate >= self.match.floor_lufs):
            return
        if self.reference_lu is None or key != self._reference_key:
            self.reference_lu, self._reference_key = net, key
            return
        self.reference_lu += (net - self.reference_lu) * min(1.0, REFERENCE_RATE_PER_S * seconds)

    def _applied_db(self, samples: int) -> float:
        """The mean gain (volume x makeup) over the last `samples`, in dB."""
        total = power = 0.0
        for count, gain in reversed(self._gains):
            take = min(count, samples - total)
            total += take
            power += take * gain
            if total >= samples:
                break
        return 10 * math.log10(power / total) if total and power > 0 else 0.0

    def _gains_samples(self) -> int:
        return sum(count for count, _ in self._gains)

    def view(self) -> dict:
        """`quality.render_match` (contract_types.RenderMatchView)."""
        makeups = {r: round(v, 2) for r, v in self.match.makeup.items() if r != REFERENCE}
        return {
            "render": self.render,
            "makeup_db": round(self.match.current_db, 2) if self.render is not None else 0.0,
            "status": self._status,
            "reason": self.reason,
            "reference_lu": round(self.reference_lu, 2) if self.reference_lu is not None else None,
            "makeups_db": makeups,
        }


def _rate(meter: Meter) -> float:
    """The sample rate, from the meter's 100 ms step."""
    return meter.step_samples * 10
