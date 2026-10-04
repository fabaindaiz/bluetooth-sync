"""The cuts, written to the service log: one line per window with faults, and a session total.

Why: `cuts.CutLog` keeps its events in memory, for the panel. On 2026-10-02 the speakers cut
about twice a second for minutes, the session was restarted, and every event went with it:
the log said nothing, and nothing could tell the engine (`late`), the output pipe
(`underrun`, `low`), PipeWire (`xrun`) or the radio apart. The log is what survives a
session (and, with `tee`, the service), so the evidence goes there.

One line per `REPORT_S` and only when there were faults: at two cuts a second, a line per
cut would bury everything else in the log.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from aurasync.cuts import likely_cause

if TYPE_CHECKING:
    from collections.abc import Callable

    from aurasync.cuts import CutLog

REPORT_S = 10.0
"""How often a window of faults becomes one log line."""


class CutReporter:
    """Reads a session's `CutLog` from the engine thread and turns new faults into log lines."""

    def __init__(self, cuts: CutLog, clock: Callable[[], float] = time.monotonic) -> None:
        self.cuts = cuts
        self.clock = clock
        self.started = clock()
        self._window_start = self.started
        self._seen = 0
        """The `seq` of the last event read."""
        self._window: list[dict[str, Any]] = []
        self.totals: dict[str, int] = {}
        """Faults per kind over the whole session (the cut log keeps only its last events)."""

    def _collect(self) -> None:
        # Only what is new: this runs every block, on the engine thread.
        new = self.cuts.since(self._seen)
        if not new:
            return
        self._seen = new[-1]["seq"]
        for e in new:
            if e["fault"]:
                self._window.append(e)
                self.totals[e["kind"]] = self.totals.get(e["kind"], 0) + 1

    def tick(self, block_ms: float | None = None) -> str | None:
        """Call once per block. A line when a window with faults closes, else None."""
        self._collect()
        now = self.clock()
        if now - self._window_start < REPORT_S:
            return None
        seconds = now - self._window_start
        faults, self._window, self._window_start = self._window, [], now
        if not faults:
            return None
        return _line(f"{len(faults)} in {seconds:.0f} s", faults, block_ms)

    def session_summary(self) -> str:
        """The whole session's faults, for the log when it closes."""
        self._collect()
        minutes = (self.clock() - self.started) / 60
        total = sum(self.totals.values())
        if not total:
            return f"cuts: no faults in {minutes:.1f} min"
        kinds = ", ".join(f"{k} {n}" for k, n in sorted(self.totals.items(), key=lambda kv: -kv[1]))
        return f"cuts: {total} faults in {minutes:.1f} min ({kinds})"


def _line(head: str, faults: list[dict[str, Any]], block_ms: float | None) -> str:
    counts: dict[str, int] = {}
    for e in faults:
        counts[e["kind"]] = counts.get(e["kind"], 0) + 1
    parts = [", ".join(f"{k} {n}" for k, n in sorted(counts.items(), key=lambda kv: -kv[1]))]
    levels = [e["level_ms"] for e in faults if e.get("level_ms") is not None]
    if levels:
        parts.append(f"pipe min {min(levels):.1f} ms")
    lates = [e["late_ms"] for e in faults if e.get("late_ms") is not None]
    if lates:
        parts.append(f"latest late {lates[-1]:.0f} ms")
    where = sorted({str(e["where"]) for e in faults if e.get("where") and e["kind"] in {"xrun", "radio", "lost"}})
    if where:
        parts.append(f"at {', '.join(where)}")
    if block_ms is not None:
        parts.append(f"engine {block_ms:.1f} ms/block")
    reading = likely_cause(faults)
    if reading:
        parts.append(reading)
    return f"cuts: {head}: " + "; ".join(parts)
