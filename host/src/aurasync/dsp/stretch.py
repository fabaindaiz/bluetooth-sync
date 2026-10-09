"""Playing a group of outputs slightly slower or faster for a while, to refill or drain a cushion.

Spec: docs/superpowers/specs/2026-10-08-seamless-transitions-design.md §4b (stage 4).

A cushion that runs low (`cushion.py`) used to get silence. An `OutputStretcher` sits after the
motor and before the outputs, one per output group (one shared by every real speaker, one for the
headphone monitor), and resamples the group's blocks by `r = 1 + ε`: `n` frames in, `n·r` out. It
reads each output sample at a fractional position of the input with the same band-limited read as
the delay line (`interpolation.read`, numpy and Rust), so a tone keeps its purity and only its pitch
moves, by `ε` (0.1 % is 1.7 cents).

**One position for the whole group.** Every channel is read at the same positions, so a group's
channels get the same number of frames and keep their alignment to the sample.

**`ε` only moves by ramps.** A change of the read speed is never a step: `ε` follows a trapezoid
(`_Plan`), up at `RAMP_PPM_PER_S`, held, and down again, sized so that the frames it adds (or drops)
come out exact. Each output sample's step is `1 - a` where `a` is the plan's mean over that sample, so
the frames added are the plan's integral, and the read position lands on a whole sample when the plan
ends. From there the input is copied again, and once idle a block comes back as it came, the same
object (no copy, no allocation): with `ε = 0` the stretcher costs nothing.

**The adaptive control** (`want`). The first request starts at `start_ppm`. Asked again in the same
direction with more frames than it still owes (beyond `tolerance`), the pipe kept falling: `ε` steps
up by `start_ppm`, at most to `max_ppm`. With fewer, it recovered: `ε` steps down, not under
`start_ppm`. Zero, or the other direction, lands it: `ε` ramps back to 0 at the next whole frame.

**Latency.** A fractional read needs `HALF` input samples after its position, so a group that is
stretching holds back about `HALF` samples (0.3 ms) of each block, the same for all its channels,
and copies them out when the stretch ends: nothing is lost or repeated.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import numpy as np

from aurasync.dsp import backend, interpolation

if TYPE_CHECKING:
    from collections.abc import Hashable

HALF = interpolation.HALF
LEAD = HALF - 1
"""Input samples a read needs before its position."""
RAMP_PPM_PER_S = 4000.0
"""How fast `ε` moves: from 0 to the default start (1000 ppm) in 0.25 s, to the default maximum
(5000 ppm) in 1.25 s. A pitch glide of 1.7 cents over a quarter of a second is not heard."""


def _read(bufs: dict[Hashable, np.ndarray], positions: np.ndarray) -> dict[Hashable, np.ndarray]:
    """Every channel read at the same `positions`. In numpy the weights are computed once for the
    group (they are most of a read's cost: 2.45 of 3.49 ms per channel and block, review 2026-10-09),
    the very same computation as `interpolation.read_numpy`. In Rust (or silenced after a Rust
    failure) each channel goes through `interpolation.read`, as the delay line's do."""
    if backend.rust_active() or backend.silent() is not None:
        return {k: interpolation.read(buf, positions) for k, buf in bufs.items()}
    index, weights = interpolation.taps_numpy(positions)
    return {k: np.sum(buf[index] * weights, axis=1) for k, buf in bufs.items()}


def _part(ppm: float) -> float:
    """Frames added per output sample at a ratio of `1 + ppm·1e-6`: `1 - 1/(1 + ε)`."""
    e = ppm * 1e-6
    return e / (1 + e)


def _ppm(a: float) -> float:
    """The inverse of `_part`."""
    return a / (1 - a) * 1e6


@dataclass(frozen=True)
class _Plan:
    """What each output sample adds, `a(t)` for output samples `t ≥ 0` since the plan started, in
    the plan's direction (`sign`): from `a0` to `peak` linearly until `t1`, held until `t2`, down to 0
    at `t3`, then 0. Its integral up to `t3` is the frames it adds."""

    a0: float
    peak: float
    t1: float
    t2: float
    t3: float
    sign: int

    def at(self, t: float) -> float:
        """The instantaneous value at `t`, signed."""
        if t >= self.t3:
            return 0.0
        if t >= self.t2:
            value = self.peak * (self.t3 - t) / (self.t3 - self.t2)
        elif t >= self.t1:
            value = self.peak
        else:
            value = self.a0 + (self.peak - self.a0) * t / self.t1
        return self.sign * value

    def integral(self, t: np.ndarray | float) -> np.ndarray:
        """The frames added from 0 to `t`, signed."""
        t = np.clip(np.asarray(t, dtype=float), 0.0, self.t3)
        a0, peak, t1, t2, t3 = self.a0, self.peak, self.t1, self.t2, self.t3
        f1 = (a0 + peak) / 2 * t1
        f2 = f1 + peak * (t2 - t1)
        rising = a0 * t + ((peak - a0) / (2 * t1) * t * t if t1 > 0 else 0.0)
        held = f1 + peak * (t - t1)
        down = t - t2
        falling = f2 + peak * down - (peak / (t3 - t2) / 2 * down * down if t3 > t2 else 0.0)
        return self.sign * np.where(t <= t1, rising, np.where(t <= t2, held, falling))

    @property
    def end(self) -> int:
        """The first whole output sample past the plan."""
        return math.ceil(self.t3)


def _plan(a0: float, frames: float, peak: float, slope: float, sign: int) -> _Plan:
    """A plan that adds exactly `frames` (≥ 0) starting at `a0` (≥ 0), ramped at `slope` per sample,
    held at `peak`. `frames` must be at least `a0²/(2·slope)`, what ramping down at once adds; when
    `peak` cannot be reached with these frames the plan turns before it."""
    if peak >= a0:
        if (2 * peak * peak - a0 * a0) / (2 * slope) > frames:
            peak = math.sqrt(max(a0 * a0, (2 * slope * frames + a0 * a0) / 2))
        t1 = (peak - a0) / slope
        ramps = (2 * peak * peak - a0 * a0) / (2 * slope)
    else:
        t1 = (a0 - peak) / slope
        ramps = a0 * a0 / (2 * slope)
    hold = max(0.0, frames - ramps) / peak if peak > 0 else 0.0
    t2 = t1 + hold
    return _Plan(a0, peak, t1, t2, t2 + peak / slope, sign)


class OutputStretcher:
    """Resamples one output group by `1 + ε` while a cushion needs frames; passes it through otherwise.

    `channels` is the group's channel names (blocks as `{name: 1-D array}`, as the speakers' are) or
    their count (blocks as one `(frames, channels)` array, as the monitor's). A dict's names may
    change between blocks (a speaker joins or leaves): a new one starts from silence, at the same
    position as the others. `tolerance` is how many frames a repeated `want` may differ from what is
    still owed before it counts as the pipe falling or recovering (the cushions pass a driver quantum:
    their level reads move by that much on their own)."""

    def __init__(
        self,
        channels: int | list[str],
        sr: int = 48000,
        start_ppm: float = 1000,
        max_ppm: float = 5000,
        *,
        tolerance: float = 0,
    ) -> None:
        self.channels = channels
        self.sr = sr
        self.tolerance = tolerance
        self._limits = (float(start_ppm), float(max_ppm))
        self._applied = self._limits
        self._slope = _part(RAMP_PPM_PER_S) / sr
        self._plan: _Plan | None = None
        self._t = 0
        """Output samples since the plan started."""
        self._p0 = 0.0
        """The read position when the plan started, relative to the first sample of `_hist`."""
        self._done = 0.0
        """Frames added in this episode before the plan, signed."""
        self._goal = 0
        """Frames this episode adds in all, signed: a whole number, so the position lands on a sample."""
        self._hist: dict[Hashable, np.ndarray] | None = None
        """The input kept for the next block's reads; `None` while idle (or not yet started)."""
        self._level = 0.0
        """The `ε` the plan holds at, in ppm (unsigned)."""
        self._landing = False
        self._finished = 0
        """Frames moved by the episodes that ended, unsigned."""

    # -- what the cushions and the state read ------------------------------------------------

    @property
    def enabled(self) -> bool:
        start, top = self._limits
        return start > 0 and top > 0

    # Read from another thread too (the monitor's: its writer plays it, the panel's view reads it):
    # each reads `_plan` once, so a plan that ends in between cannot make it raise.

    @property
    def active(self) -> bool:
        return self._plan is not None or self._hist is not None

    @property
    def direction(self) -> int:
        """1 while adding frames, -1 while dropping them, 0 idle."""
        plan = self._plan
        return 0 if plan is None else plan.sign

    @property
    def epsilon_ppm(self) -> float:
        """`ε` now, in ppm: positive plays slower (adds frames), negative faster."""
        plan = self._plan
        return 0.0 if plan is None else _ppm(plan.at(self._t))

    @property
    def pending_frames(self) -> int:
        """Frames still to add (negative: to drop) before `ε` is back at 0."""
        plan = self._plan
        return 0 if plan is None else round(self._goal - self._moved(plan))

    @property
    def stretched_frames(self) -> int:
        """Frames added or dropped by stretching since this stretcher was made, unsigned."""
        plan = self._plan
        return self._finished + (0 if plan is None else abs(round(self._moved(plan))))

    def set_limits(self, start_ppm: float, max_ppm: float) -> None:
        """The knobs (`transition.start_stretch_ppm`, `max_stretch_ppm`). Takes effect at the next
        block, by a ramp; a stretch the knobs turn off lands. Safe from another thread."""
        self._limits = (float(start_ppm), float(max_ppm))

    # -- what the cushions ask ------------------------------------------------------------------

    def want(self, frames: int) -> None:
        """Frames still to add (positive) or drop (negative), as the pipe reads now."""
        frames = int(frames)
        if not self.enabled:
            return
        start, top = self._limits
        plan = self._plan
        if plan is None:
            if frames == 0:
                return
            self._level = min(start, top)
            self._done, self._goal, self._landing = 0.0, 0, False
            self._replan(abs(frames), 0.0, 1 if frames > 0 else -1)
            return
        if frames == 0 or (frames > 0) != (plan.sign > 0):
            self._land()
            return
        owed = abs(self._goal - self._moved())
        if abs(frames) > owed + self.tolerance:
            self._level = min(self._level + start, top)
        elif abs(frames) < owed - self.tolerance:
            self._level = max(min(self._level - start, top), min(start, top))
        else:
            return
        self._landing = False
        self._replan(abs(frames), plan.at(self._t), plan.sign)

    def cancel(self) -> None:
        """Back to `ε = 0`, by a ramp, at the next whole frame. Nothing to do when idle; a stretch
        asked for and not started yet is simply dropped."""
        if self._plan is not None and self._hist is None:
            self._plan, self._done, self._goal, self._t, self._landing = None, 0.0, 0, 0, False
        elif self._plan is not None:
            self._land()

    # -- the audio ------------------------------------------------------------------------------

    def process(self, blocks: Any) -> Any:
        """One block of the group, stretched: `{name: array}` or a `(frames, channels)` array (or a
        1-D one), in the same shape. Idle, the very same object comes back."""
        if self._plan is None and self._hist is None:
            return blocks
        if self._limits != self._applied:
            self._follow_limits()
        if isinstance(blocks, dict):
            return self._run(blocks)
        x = np.asarray(blocks)
        if x.ndim == 1:
            return self._run({0: x})[0]
        out = self._run({i: x[:, i] for i in range(x.shape[1])})
        return np.column_stack([out[i] for i in range(x.shape[1])]) if out else x

    # -- inside ---------------------------------------------------------------------------------

    def _moved(self, plan: _Plan | None = None) -> float:
        """Frames added in this episode up to now, signed (by `plan`, or the current one)."""
        plan = self._plan if plan is None else plan
        return self._done + (0.0 if plan is None else float(plan.integral(self._t)))

    def _replan(self, frames: float, a_now: float, sign: int) -> None:
        """A new plan from now (where `ε` is `a_now`, signed) that moves `frames` (≥ 0) more in the
        direction `sign`, the episode's total rounded to a whole frame; never less than ramping down
        at once moves."""
        moved = self._moved()
        if self._plan is not None:
            self._p0 = self._position(self._t)
            self._done, self._t = moved, 0
        a0 = max(0.0, sign * a_now)
        least = a0 * a0 / (2 * self._slope)
        done = sign * moved
        goal = max(round(done + frames), math.ceil(done + least - 1e-9))
        self._goal = sign * goal
        peak = a0 if self._landing and a0 > 0 else abs(_part(sign * self._level))
        self._plan = _plan(a0, max(0.0, goal - done), peak, self._slope, sign)

    def _land(self) -> None:
        if self._landing or self._plan is None:
            return
        self._landing = True
        self._replan(0.0, self._plan.at(self._t), self._plan.sign)

    def _follow_limits(self) -> None:
        self._applied = self._limits
        plan = self._plan
        if plan is None or self._landing:
            return
        if not self.enabled:
            self._land()
            return
        start, top = self._limits
        level = min(max(self._level, min(start, top)), top)
        if level != self._level:
            self._level = level
            self._replan(max(0.0, plan.sign * (self._goal - self._moved())), plan.at(self._t), plan.sign)

    def _position(self, t: float) -> float:
        """The read position of output sample `t` of the plan, relative to `_hist`."""
        plan = self._plan
        return self._p0 + t - (0.0 if plan is None else float(plan.integral(t)))

    def _run(self, blocks: dict[Hashable, np.ndarray]) -> dict[Hashable, np.ndarray]:
        plan = self._plan
        if not blocks or plan is None:  # nothing to read, or nothing to stretch (`process` saw to it)
            return blocks if blocks else {}
        n = len(next(iter(blocks.values())))
        heads: dict[Hashable, list[np.ndarray]] = {k: [] for k in blocks}
        if self._hist is None:
            if n <= LEAD + HALF:
                return blocks  # too short to start in: start with the next one
            # Starting: the first LEAD samples go out as they came (ε is 0 there), and the reads
            # start right after them, with those samples behind.
            for k, x in blocks.items():
                heads[k].append(x[:LEAD])
            self._hist = {}
            self._p0 = float(LEAD)
            bufs = {k: np.asarray(x, dtype=float) for k, x in blocks.items()}
        else:
            kept = len(next(iter(self._hist.values()))) if self._hist else 0
            bufs = {
                k: np.concatenate([self._hist.get(k, np.zeros(kept)), np.asarray(x, dtype=float)])
                for k, x in blocks.items()
            }
        avail = len(next(iter(bufs.values())))
        limit = avail - HALF  # a read at p needs floor(p) + HALF inside the buffer
        p_now = self._position(self._t)
        fastest = max(abs(plan.a0), plan.peak) if plan.sign > 0 else 0.0
        count_max = max(0, math.ceil((limit - p_now) / (1 - fastest)) + 2)
        to_end = max(0, plan.end - self._t)
        t = self._t + np.arange(min(count_max, to_end + 1))
        positions = self._p0 + t - plan.integral(t)
        count = min(int(np.searchsorted(positions, limit, side="left")), to_end)
        reads = positions[:count]
        read = _read(bufs, reads) if count else {}
        out_parts = {k: [*heads[k], read[k]] if count else heads[k] for k in bufs}
        self._t += count
        if count == to_end:
            # The plan is over: the position is a whole sample (within float rounding; the tests
            # hold it to the sample, bit for bit), and the rest of the block is a copy.
            position = float(positions[count]) if count < len(positions) else self._position(self._t)
            start = round(position)
            for k, buf in bufs.items():
                out_parts[k].append(buf[start:])
            self._finished += abs(self._goal)
            self._plan, self._hist, self._landing = None, None, False
            self._done, self._goal, self._t = 0.0, 0, 0
            return {k: np.concatenate(parts) for k, parts in out_parts.items()}
        keep = math.floor(self._position(self._t)) - LEAD
        self._hist = {k: buf[keep:].copy() for k, buf in bufs.items()}
        self._p0 -= keep
        return {k: np.concatenate(parts) for k, parts in out_parts.items()}
