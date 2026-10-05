"""Parameters that can change while audio plays without the change being heard.

Three mechanisms, all used by the motor when a control moves something live
(`docs/superpowers/specs/2026-09-29-control-service-design.md` §6):

- `Smoothed`: a value that moves towards its target at a limited speed, sample by sample.
  Used for `pan`, `ambience` and the extractor's mix factor.
- `DecibelRamp`: the same, but in dB and returning a linear gain. Used for `volume_db`.
- `FadeGate`: for the changes no ramp can hide (a preset, the decorrelator, a large delay
  jump). It fades the output to exactly zero, lets every parameter jump, and fades back in.

All three return a **scalar** when settled, so the cost at rest is the same as a fixed
parameter; only a block in which something moves pays for per-sample arrays.
"""

from __future__ import annotations

import numpy as np

SR = 48000
FADE_MS = 80.0
"""Each half of the fade gate. Short enough not to feel like a pause, long enough that a
raised cosine over it has no audible click."""


class Smoothed:
    """A value that moves towards its target at `rate` units per second."""

    def __init__(self, value: float, rate: float, sr: int = SR) -> None:
        if rate <= 0:
            msg = f"the rate must be positive; got {rate}"
            raise ValueError(msg)
        self.current = float(value)
        self.target = float(value)
        self.rate = rate
        self.sr = sr

    @property
    def settled(self) -> bool:
        return self.current == self.target

    def jump(self) -> None:
        """Reach the target at once. Only safe with the output silent (the fade gate)."""
        self.current = self.target

    def block(self, n: int) -> float | np.ndarray:
        """The value for each of the next `n` samples, advancing the state."""
        if self.settled or n == 0:
            return self.current
        step = self.rate / self.sr
        direction = 1.0 if self.target > self.current else -1.0
        values = self.current + direction * step * np.arange(1, n + 1)
        lo, hi = sorted((self.current, self.target))
        values = np.clip(values, lo, hi)
        self.current = float(values[-1])
        if abs(self.current - self.target) < 1e-12:  # noqa: PLR2004
            self.current = self.target
        return values


class DecibelRamp:
    """A gain set in dB that moves linearly in dB, returned as a linear factor."""

    def __init__(self, db: float, rate_db_s: float, sr: int = SR) -> None:
        self._db = Smoothed(db, rate_db_s, sr)

    @property
    def target_db(self) -> float:
        return self._db.target

    @target_db.setter
    def target_db(self, value: float) -> None:
        self._db.target = float(value)

    @property
    def current_db(self) -> float:
        return self._db.current

    def jump(self) -> None:
        self._db.jump()

    def block(self, n: int) -> float | np.ndarray:
        return 10 ** (self.block_db(n) / 20)

    def block_db(self, n: int) -> float | np.ndarray:
        """The next `n` values in dB (a float when settled), advancing the state like `block`."""
        return self._db.block(n)


class FadeGate:
    """Fade out, jump with the output at exactly zero, fade in.

    `request()` asks for a jump. `block(n)` returns the envelope to multiply the output
    with, and whether the jump must happen **after** this block. When the fade-out ends in
    the middle of a block, the rest of the block stays at zero, so the jump always happens
    with the output silent; the fade-in starts with the next block.

    A request while a fade is already in progress is merged into it: the jump still happens
    once, at the bottom.
    """

    IDLE, OUT, IN = "idle", "out", "in"

    def __init__(self, sr: int = SR, fade_ms: float = FADE_MS) -> None:
        self.sr = sr
        self.length = max(1, round(sr * fade_ms / 1000))
        # Raised cosine from 1 to 0, inclusive of both ends.
        self._curve = 0.5 * (1 + np.cos(np.pi * np.arange(self.length + 1) / self.length))
        self.state = self.IDLE
        self._pos = 0

    @property
    def busy(self) -> bool:
        return self.state != self.IDLE

    def request(self) -> None:
        if self.state == self.IDLE:
            self.state, self._pos = self.OUT, 0
        elif self.state == self.IN:
            # Coming back up: turn around from the current level, without a step.
            self.state, self._pos = self.OUT, self.length - self._pos

    def block(self, n: int) -> tuple[float | np.ndarray, bool]:
        if self.state == self.IDLE or n == 0:
            return 1.0, False
        if self.state == self.OUT:
            idx = self._pos + np.arange(1, n + 1)
            env = self._curve[np.minimum(idx, self.length)]
            self._pos = min(self._pos + n, self.length)
            if self._pos >= self.length:
                self.state, self._pos = self.IN, 0
                return env, True
            return env, False
        idx = self._pos + np.arange(1, n + 1)
        env = self._curve[self.length - np.minimum(idx, self.length)]
        self._pos += n
        if self._pos >= self.length:
            self.state, self._pos = self.IDLE, 0
        return env, False
