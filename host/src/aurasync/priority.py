"""Raising the engine thread's priority (roadmap i-7c8794-246f79, experiment 23 §4).

The engine thread (the one that runs `Service.run`) delivers a block every 85 ms; on a loaded
machine other processes can take the CPU and the block arrives late, which the listener hears as a
cut. Experiment 23 §4 MEASURED 179 late blocks in 10 min while a test suite ran beside the service,
with the engine needing only 13.9 of its 85.3 ms: it was starved, not slow.

`engine_nice` in `service.json` (-15 by default since 2026-10-09, after experiment 23 §4.1; null
touches nothing) asks for a niceness for the
engine thread when `Service.run` starts. Linux gives a new thread or process its creator's niceness
(VERIFICADO on HP-O16), so what the engine thread starts afterwards (the radio monitor, the workers,
the `pw-play` players) inherits it; the HTTP threads, started by the server's own thread, do not. A
thread already above the value asked is never lowered. In order:

1. `os.setpriority` on the thread's id: it works within `RLIMIT_NICE` (whose floor is
   `20 - RLIMIT_NICE`; -11 on HP-O16) and for a privileged service;
2. if refused, RealtimeKit's `MakeThreadHighPriorityWithPID`, through `busctl` (no D-Bus library;
   RealtimeKit's own floor is its `MinNiceLevel`, -15 by default);
3. otherwise the lowest value `RLIMIT_NICE` allows, if it is better than the current one.

Whatever happens, the niceness is read back from the system and that is what is reported
(CLAUDE.md: what the program asks of the system is verified, not assumed). Nothing here raises: a
refused priority leaves the engine as it was, with the reason. Real-time scheduling (`SCHED_FIFO`)
is deliberately not offered: a Python thread waiting on the GIL held by a normal thread would be a
priority inversion, and RealtimeKit kills a real-time thread that keeps the CPU for 200 ms.
"""

from __future__ import annotations

import os
import resource
import subprocess
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

MIN_NICE = -20
MAX_NICE = 19
BUSCTL_TIMEOUT_S = 5.0


@dataclass(frozen=True)
class PriorityResult:
    """What was asked (`wanted`, None: nothing), the niceness the thread has now (read back), how
    it got there (`setpriority`, `rtkit`, or None when nothing changed), and why it is not what
    was asked (None when it is)."""

    wanted: int | None
    nice: int | None
    how: str | None
    reason: str | None

    def view(self) -> dict:
        return {"wanted": self.wanted, "nice": self.nice, "how": self.how, "reason": self.reason}


def check_nice(value: object) -> str | None:
    """Why `value` is not a valid `engine_nice` (None when it is: null or an integer -20…19)."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not MIN_NICE <= value <= MAX_NICE:
        return f"engine_nice must be null or an integer from {MIN_NICE} to {MAX_NICE}; got {value!r}"
    return None


def rtkit_high_priority(pid: int, tid: int, value: int) -> str | None:
    """Ask RealtimeKit for niceness `value` on thread `tid` of process `pid`; the error, or None."""
    cmd = [
        "busctl",
        "--system",
        "call",
        "--",  # the niceness is negative: without this, busctl reads "-15" as an option
        "org.freedesktop.RealtimeKit1",
        "/org/freedesktop/RealtimeKit1",
        "org.freedesktop.RealtimeKit1",
        "MakeThreadHighPriorityWithPID",
        "tti",
        str(pid),
        str(tid),
        str(value),
    ]
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=BUSCTL_TIMEOUT_S, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"{type(exc).__name__}: {exc}"
    if done.returncode != 0:
        return (done.stderr or f"busctl exited {done.returncode}").strip()
    return None


def _rlimit_nice() -> int:
    return resource.getrlimit(resource.RLIMIT_NICE)[0]


def raise_engine_priority(
    wanted: int | None,
    *,
    tid: int | None = None,
    pid: int | None = None,
    getpriority: Callable[[int, int], int] = os.getpriority,
    setpriority: Callable[[int, int, int], None] = os.setpriority,
    rtkit: Callable[[int, int, int], str | None] = rtkit_high_priority,
    rlimit_nice: Callable[[], int] = _rlimit_nice,
) -> PriorityResult:
    """Give the calling thread (or `tid`) niceness `wanted`, as far as the system allows."""
    tid = threading.get_native_id() if tid is None else tid
    pid = os.getpid() if pid is None else pid
    before = getpriority(os.PRIO_PROCESS, tid)
    if wanted is None:
        return PriorityResult(None, before, None, None)
    if before <= wanted:
        # Already at least as high as asked (root, a manual renice): never lowered.
        reason = None if before == wanted else f"already at {before}, above the {wanted} asked"
        return PriorityResult(wanted, before, None, reason)
    how, problem = None, None
    try:
        # First, always: it works within RLIMIT_NICE and for a privileged service (CAP_SYS_NICE).
        setpriority(os.PRIO_PROCESS, tid, wanted)
        how = "setpriority"
    except OSError as exc:
        problem = f"setpriority({wanted}) refused ({exc})"
        refused = rtkit(pid, tid, wanted)
        if refused is None:
            how, problem = "rtkit", None
        else:
            problem += f"; RealtimeKit refused {wanted} ({refused})"
            limit = rlimit_nice()
            floor = 20 - limit if limit > 0 else None
            if floor is not None and floor < before:
                try:
                    setpriority(os.PRIO_PROCESS, tid, floor)
                    how = "setpriority"
                    problem += f"; RLIMIT_NICE allows {floor}"
                except OSError as exc2:
                    problem += f"; setpriority({floor}) refused ({exc2})"
            else:
                problem += f"; RLIMIT_NICE ({limit}) allows nothing below {before}"
    now = getpriority(os.PRIO_PROCESS, tid)
    if now == before:
        how = None  # whatever was called, nothing changed
    if now != wanted and problem is None:
        problem = f"asked {wanted}, but the thread reads back {now}"
    return PriorityResult(wanted, now, how, problem)
