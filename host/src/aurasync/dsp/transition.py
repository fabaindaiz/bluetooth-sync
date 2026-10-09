"""The clock of a crossfade transition, and the weights it mixes with.

Spec: `docs/superpowers/specs/2026-10-08-seamless-transitions-design.md` §3.

A *cut* transition (the default today) stays on `FadeGate` in `dsp/ramps.py`: output to zero,
jump, back in. A *crossfade* transition instead lets every smoother glide to its target and
every delay line crossfade between its old and new read positions, over `length` samples.
`Transition` is only the clock and the queue of what to do; the motor owns the audio.

Collapse rule: while a transition runs, further changes are not applied one by one. Each is
queued, and when the fade ends all of them start together as ONE next transition (the single
pending batch). So fifty changes during a fade cost one more fade, never fifty.

How the motor drives it (IDLE, an optional WARM, and FADE):

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
4. Stage 2 (stateful stages): a starting action may call `need_warm(samples)`. The new instance
   of the stage must then run in the shadow, fed the same input, before the mixing starts. After
   running the starting actions the motor checks `state == Transition.WARM`. In WARM,
   `block_weights` gives `(1, 0)` (only the old output is heard) and `advance` counts warm samples;
   when they are done it returns the marker `Transition.FADE_STARTS` ONCE (the motor starts the
   glides now) and the FADE runs as above. Without `need_warm` the clock is exactly stage 1's.
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


def _mix(old: object, new: object, w_old: object, w_new: object) -> object:
    """`old*w_old + new*w_new` for arrays; None stays None; anything else comes from `new`.

    Weights are scalars or arrays that broadcast (numpy rules, so per-sample weights match the
    LAST axis). A scalar weight of exactly 0 drops that side, so a warming instance's output
    (whatever it holds) never leaks into the mix."""
    if old is None or new is None:
        return None
    if not (isinstance(old, np.ndarray) and isinstance(new, np.ndarray)):
        return new
    if old.shape != new.shape:
        msg = f"cannot crossfade outputs of different shapes {old.shape} and {new.shape}"
        raise ValueError(msg)
    if np.ndim(w_new) == 0 and w_new == 0:
        return old * w_old
    if np.ndim(w_old) == 0 and w_old == 0:
        return new * w_new
    return old * w_old + new * w_new


class Crossfaded:
    """Two instances of a stateful stage, mixed while a transition runs.

    A method called on it runs on BOTH `old` and `new` (so each keeps its state) and the array
    results are mixed with the current block's weights, `weights() -> (w_old, w_new) | None`
    (None: only `new` runs and answers). Other attributes are read from `new`. `resolve()` gives
    the instance to keep once the transition ends. It never nests: when a stage changes again
    during a transition, the motor resolves first or queues the change (collapse rule)."""

    def __init__(self, old: object, new: object, weights: Callable[[], tuple | None]) -> None:
        if isinstance(old, Crossfaded) or isinstance(new, Crossfaded):
            msg = "Crossfaded does not nest; resolve() one before crossfading again"
            raise ValueError(msg)  # noqa: TRY004  (the contract says ValueError)
        self.old, self.new, self._weights = old, new, weights

    def resolve(self) -> object:
        return self.new

    def __getattr__(self, name: str) -> object:
        # Only called for attributes not found normally (so not old/new/resolve/_weights).
        if name.startswith("__") or name == "_weights":
            raise AttributeError(name)
        target = getattr(self.new, name)
        if not callable(target):
            return target

        def call(*args: object, **kwargs: object) -> object:
            weights = self._weights()
            if weights is None:
                return target(*args, **kwargs)
            old_result = getattr(self.old, name)(*args, **kwargs)
            return _mix(old_result, target(*args, **kwargs), *weights)

        return call


def _nothing() -> None:
    """What a bare request queues in the pending batch."""


class Transition:
    """IDLE, WARM or FADE, the samples left in it, and the actions waiting for it.

    Not thread-safe: it has a single writer, the motor's (the service's engine thread)."""

    IDLE, WARM, FADE = "idle", "warm", "fade"
    MAX_WARM = 48000  # samples; the warm length is capped at one second at 48 kHz
    FADE_STARTS = object()  # returned once by `advance` when WARM ends

    def __init__(self, sr: int = 48000) -> None:
        self._max_warm = self.MAX_WARM * sr // 48000
        self._warm = 0
        self._warm_pos = 0
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

    def need_warm(self, samples: int) -> None:
        """Ask, from a starting action, for `samples` of shadow run before the fade (capped at
        `MAX_WARM`; the largest ask wins). Enters WARM; ignored when not in the starting phase."""
        if self.state == self.IDLE or self.started:
            return
        self._warm = max(self._warm, min(int(samples), self._max_warm))
        if self._warm > 0:
            self.state = self.WARM

    def block_weights(self, n: int, shape: str) -> tuple[float | np.ndarray, float | np.ndarray] | None:
        """The (old, new) weights for the block of `n` samples about to be processed, or None when
        IDLE. Pure: `advance` moves the position."""
        if self.state == self.IDLE:
            return None
        if self.state == self.WARM:
            return 1.0, 0.0
        return fade_weights(shape, self._pos, n, self.length)

    def begin(self, length: int, actions: list[Callable[[], None]] | None = None) -> None:
        """Start a fade of `length` samples; `actions` (a pending batch) join the starting ones."""
        self.state = self.FADE
        self.started = False
        self.length = max(1, length)
        self._pos = 0
        self._warm = self._warm_pos = 0
        if actions:
            self._starting = [*actions, *self._starting]

    def take_starting(self) -> list[Callable[[], None]]:
        """The actions to run when the fade starts, in request order; clears them."""
        actions, self._starting = self._starting, []
        return actions

    def advance(self, n: int) -> list[Callable[[], None]] | None:
        """Count `n` processed samples (and mark the transition started). At the end of the fade
        return the pending batch, possibly empty, and go IDLE; otherwise None. When a WARM ends,
        return `FADE_STARTS` (once) and go FADE."""
        if self.state == self.IDLE:
            return None
        if not self.started:
            # An action requested after `take_starting`, during the first block (re-entrancy: an
            # action or a callback inside the motor's block asking for a change), missed the
            # start: it waits in the pending batch instead of being left behind.
            self._pending, self._starting = [*self._starting, *self._pending], []
        self.started = True
        if self.state == self.WARM:
            self._warm_pos += n
            if self._warm_pos < self._warm:
                return None
            self.state = self.FADE
            return self.FADE_STARTS  # type: ignore[return-value]
        self._pos += n
        if self._pos < self.length:
            return None
        pending, self._pending = self._pending, []
        self.state, self.started, self._pos = self.IDLE, False, 0
        self._warm = self._warm_pos = 0
        return pending

    def cancel(self) -> list[Callable[[], None]]:
        """End everything; return every queued action, starting ones first, in order."""
        actions = [*self._starting, *self._pending]
        self._starting, self._pending = [], []
        self.state, self.started, self._pos = self.IDLE, False, 0
        self._warm = self._warm_pos = 0
        return actions
