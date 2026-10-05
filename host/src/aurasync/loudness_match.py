"""Two listening paths at the same loudness: a makeup gain per mode (brief 2026-10-05).

The headphone monitor's modes reach the ear by different roads: `stereo` is the input at the
chosen volume (the **reference**), `mix` folds what the speakers got, `binaural` goes through
PipeWire's HRTF. Each road changes the loudness its own way, and a switch between them that
jumps by 20 dB is what the user heard (MEDIDO offline, experimentos/18 "Volumen entre modos del
monitor"). This is the pure decision; the meters are the caller's (`quality.LoudnessMeter`, the
K-weighted short-term loudness, 3 s).

Per block it gets the reference's and the **candidate's** loudness (the mode's output before the
makeup) and moves that mode's makeup toward `reference - candidate`:

- slowly, `RATE_PER_S` of the error per second, and as a ramp over the block (no zipper);
- never beyond `CAP_DB` either way;
- **frozen** while the gate (the input, before the volume) is under `FLOOR_LUFS` (a pause:
  nothing to match), while the candidate is silent under a playing reference (every speaker
  muted), and on request (`hold`: a cut, a calibration, when what the speakers play is not the
  music);
- **remembered per mode**: switching mode starts at that mode's makeup, so going back never
  jumps; a mode never seen starts from the caller's estimate and is refined in seconds.

It knows nothing about audio or threads; the monitor calls it on the engine thread. Reusable by
the speakers' own match (phase 2).
"""

from __future__ import annotations

import math

CAP_DB = 12.0
RATE_PER_S = 0.1
"""Fraction of the error corrected per second: a 10 dB error is 1 dB after one second."""
FLOOR_LUFS = -50.0
"""Under this the reference is a pause, not music."""
LOCKED_LU = 0.5
"""Within this of its target the makeup is `locked`."""
STATUSES = ("measuring", "locked", "frozen", "unmeasured")


def _clamp(x: float, cap: float) -> float:
    return max(-cap, min(cap, x))


class LoudnessMatch:
    def __init__(
        self,
        *,
        cap_db: float = CAP_DB,
        rate_per_s: float = RATE_PER_S,
        floor_lufs: float = FLOOR_LUFS,
        locked_lu: float = LOCKED_LU,
    ) -> None:
        self.cap_db = cap_db
        self.rate_per_s = rate_per_s
        self.floor_lufs = floor_lufs
        self.locked_lu = locked_lu
        self.makeup: dict[str, float] = {}
        """dB per mode: what each one needs, kept while another one plays."""
        self.mode: str | None = None
        self.status = "measuring"

    @property
    def current_db(self) -> float:
        return self.makeup.get(self.mode, 0.0) if self.mode is not None else 0.0

    def select(self, mode: str, estimate_db: float = 0.0) -> float:
        """Switch to `mode`: its remembered makeup, or `estimate_db` for a mode never seen."""
        if mode not in self.makeup:
            self.makeup[mode] = _clamp(float(estimate_db), self.cap_db)
        self.mode = mode
        self.status = "measuring"
        return self.makeup[mode]

    def forget(self) -> None:
        """Drop every remembered makeup (the speakers changed: what each mode needs changed)."""
        self.makeup.clear()
        self.mode = None
        self.status = "measuring"

    def update(
        self,
        reference_lufs: float,
        candidate_lufs: float | None,
        seconds: float,
        *,
        hold: bool = False,
        gate_lufs: float | None = None,
    ) -> tuple[float, float]:
        """One block of `seconds`: the makeup at its start and at its end (dB), for a ramp.

        `candidate_lufs` is the mode's loudness before the makeup; `None` when it cannot be known
        (`unmeasured`). `gate_lufs` is what the floor decides a pause on (default: the reference
        itself). The monitor gives the input's momentary loudness **before** the volume: a pause
        freezes it in 0.4 s and not in 3 s, and a quiet knob is not taken for a pause (with the
        floor on the reference at the chosen volume, a knob under about -30 dB froze the match
        while the music played: review, 2026-10-05)."""
        if self.mode is None:
            msg = "select a mode first"
            raise ValueError(msg)
        start = self.makeup[self.mode]
        gate = reference_lufs if gate_lufs is None else gate_lufs
        if candidate_lufs is None:
            self.status = "unmeasured"
            return start, start
        if (
            hold
            or not math.isfinite(gate)
            or gate < self.floor_lufs
            or not math.isfinite(reference_lufs)
            or not math.isfinite(candidate_lufs)
        ):
            self.status = "frozen"
            return start, start
        target = _clamp(reference_lufs - candidate_lufs, self.cap_db)
        error = target - start
        end = _clamp(start + error * min(1.0, self.rate_per_s * seconds), self.cap_db)
        self.makeup[self.mode] = end
        self.status = "locked" if abs(target - end) <= self.locked_lu else "measuring"
        return start, end
