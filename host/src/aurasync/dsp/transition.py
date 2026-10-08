"""The clock of a crossfade transition, and the weights it mixes with.

Spec: `docs/superpowers/specs/2026-10-08-seamless-transitions-design.md` §3.

A *cut* transition (the default today) stays on `FadeGate` in `dsp/ramps.py`: output to zero,
jump, back in. A *crossfade* transition instead lets every smoother glide to its target and
every delay line crossfade between its old and new read positions, over `length` samples.
`Transition` is only the clock and the queue of what to do; the motor owns the audio.

Collapse rule: while a transition runs, further changes are not applied one by one. Each is
queued, and when the fade ends all of them start together as ONE next transition (the single
pending batch). So fifty changes during a fade cost one more fade, never fifty.

How the motor drives it (stage 1 has only the FADE phase; a WARM phase can be added later
next to IDLE and FADE):

1. `request(action)`; when it returns True the transition was IDLE and the motor must call
   `begin(length)` at once (the action is already queued as a starting action).
2. At the start of its next block, if `busy and not started`, the motor calls `take_starting()`
   (those actions run, then the glides and delay crossfades are set up) and processes the block.
3. After every processed block the motor calls `advance(n)`. The first `advance` marks the
   transition `started`: a `request` after that goes to the pending batch instead of joining
   the starting actions (and one that arrived after `take_starting`, during that first block,
   moves to the pending batch). When `length` samples have passed, `advance` returns the pending
   batch (an empty list when nothing waits) and the clock goes IDLE; the motor then begins a
   new transition with `begin(length, batch)` if the batch is not empty. While fading or idle
   it returns None.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from collections.abc import Callable

SHAPES = ("equal_gain", "equal_power")


def fade_weights(shape: str, position: int, n: int, length: int) -> tuple[np.ndarray, np.ndarray]:
    """The (old, new) weights for samples `position + 1 ... position + n` of a fade of `length`.

    Clamped to the end: after `length` the old weight is 0 and the new one 1.
    `equal_gain` is cos^2 / sin^2, which sums to 1 (for correlated signals, like the same audio
    at two delays); `equal_power` is cos / sin, whose squares sum to 1 (for uncorrelated ones).
    """
    if shape not in SHAPES:
        msg = f"unknown fade shape {shape!r}; use one of {SHAPES}"
        raise ValueError(msg)
    t = np.clip((position + np.arange(1, n + 1)) / max(1, length), 0.0, 1.0)
    old, new = np.cos(0.5 * np.pi * t), np.sin(0.5 * np.pi * t)
    if shape == "equal_gain":
        return old**2, new**2
    return old, new


def _nothing() -> None:
    """What a bare request queues in the pending batch."""


class Transition:
    """IDLE or FADE, the samples left in the fade, and the actions waiting for it.

    Not thread-safe: it has a single writer, the motor's (the service's engine thread)."""

    IDLE, FADE = "idle", "fade"

    def __init__(self) -> None:
        self.state = self.IDLE
        self.started = False
        self.length = 0
        self._pos = 0
        self._starting: list[Callable[[], None]] = []
        self._pending: list[Callable[[], None]] = []

    @property
    def busy(self) -> bool:
        return self.state != self.IDLE

    def request(self, action: Callable[[], None] | None) -> bool:
        """Queue `action`. True when the transition was IDLE: the caller must `begin` now."""
        idle = self.state == self.IDLE
        if action is None and self.started and not idle:
            # A bare request during a fade (its change is already in the installation) still asks
            # for the next transition: a pending batch of nothing would read as "done".
            action = _nothing
        if action is not None:
            (self._pending if self.started and not idle else self._starting).append(action)
        return idle

    def begin(self, length: int, actions: list[Callable[[], None]] | None = None) -> None:
        """Start a fade of `length` samples; `actions` (a pending batch) join the starting ones."""
        self.state = self.FADE
        self.started = False
        self.length = max(1, length)
        self._pos = 0
        if actions:
            self._starting = [*actions, *self._starting]

    def take_starting(self) -> list[Callable[[], None]]:
        """The actions to run when the fade starts, in request order; clears them."""
        actions, self._starting = self._starting, []
        return actions

    def advance(self, n: int) -> list[Callable[[], None]] | None:
        """Count `n` processed samples (and mark the transition started). At the end of the fade
        return the pending batch, possibly empty, and go IDLE; otherwise None."""
        if self.state == self.IDLE:
            return None
        if not self.started:
            # An action requested after `take_starting`, during the first block (re-entrancy: an
            # action or a callback inside the motor's block asking for a change), missed the
            # start: it waits in the pending batch instead of being left behind.
            self._pending, self._starting = [*self._starting, *self._pending], []
        self.started = True
        self._pos += n
        if self._pos < self.length:
            return None
        pending, self._pending = self._pending, []
        self.state, self.started, self._pos = self.IDLE, False, 0
        return pending

    def cancel(self) -> list[Callable[[], None]]:
        """End everything; return every queued action, starting ones first, in order."""
        actions = [*self._starting, *self._pending]
        self._starting, self._pending = [], []
        self.state, self.started, self._pos = self.IDLE, False, 0
        return actions
