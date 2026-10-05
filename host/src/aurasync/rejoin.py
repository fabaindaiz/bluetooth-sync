"""The brake on a lost speaker returning by itself (spec 2026-10-05 §5, d-7c8794-618666).

Only a speaker that was playing in this session and whose stream died (`note_lost`) is ever
tried again; a speaker the user took out (`clear` at the leave) has no drops, so it never is.
At most one attempt every `MIN_GAP_S` (inclusive: exactly 10 s allows it); after `MAX_DROPS`
drops within `WINDOW_S` it stops trying until the user asks (`clear`). The window is inclusive
too: a drop of exactly `WINDOW_S` ago still counts. A failed automatic return counts as a drop
(the service calls `note_lost`), so a link that never comes back also meets the brake. The
policy only decides; the service runs the join.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

MIN_GAP_S = 10.0
WINDOW_S = 300.0
MAX_DROPS = 3


class RejoinPolicy:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._drops: dict[str, list[float]] = {}
        self._tried: dict[str, float] = {}

    def note_lost(self, name: str) -> None:
        """The speaker's stream died while it was playing."""
        self._drops.setdefault(name, []).append(self._clock())

    def note_try(self, name: str) -> None:
        self._tried[name] = self._clock()

    def _recent_drops(self, name: str) -> int:
        now = self._clock()
        recent = [t for t in self._drops.get(name, []) if now - t <= WINDOW_S]
        self._drops[name] = recent
        return len(recent)

    def gave_up(self, name: str) -> bool:
        return self._recent_drops(name) >= MAX_DROPS

    def may_try(self, name: str) -> bool:
        if not self._recent_drops(name) or self.gave_up(name):
            return False
        last = self._tried.get(name)
        return last is None or self._clock() - last >= MIN_GAP_S

    def clear(self, name: str) -> None:
        """Forget the speaker's history: a leave (it never returns by itself) or a join that worked."""
        self._drops.pop(name, None)
        self._tried.pop(name, None)
