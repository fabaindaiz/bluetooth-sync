"""The base sync estimator: every measurement in, a suggested delay per speaker out.

Spec 2026-10-03 §4. It runs on a thread of its own (d-7c8794-589dec: history apart and
asynchronous): `submit` is one queue put, whoever calls it — the engine thread with the server
microphone's measurement, an HTTP thread with a phone's. The thread keeps the measurements,
fits them (`sync_methods`) and publishes a `Suggestion`: **absolute** delays (never
corrections on top of corrections, experimentos/09 §5) that the listener applies from the
panel (d-7c8794-2c6f91). It never writes the motor or the installation itself.
"""

from __future__ import annotations

import queue
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from aurasync import sync_levels, sync_methods

if TYPE_CHECKING:
    from collections.abc import Callable

    from aurasync.sync_measurement import Measurement
    from aurasync.sync_methods import SyncSettings

QUEUE_MAX = 64
"""Measurements waiting for the thread; past it the new one is dropped (and counted)."""
HISTORY_MAX = 2000
"""Measurements kept (the window decides which ones the fit uses)."""


@dataclass(frozen=True)
class Suggestion:
    delays_ms: dict[str, float]
    """The delay each placed speaker should have, absolute (the smallest at 0)."""
    current_ms: dict[str, float]
    sigma_ms: dict[str, float]
    spread_after_ms: float | None
    """What is left after applying it: the estimate's own uncertainty (twice the largest sigma)."""
    spread_now_ms: float | None
    """The misalignment at the anchor with the current delays, as estimated."""
    anchor: str | None
    based_on: int
    at: float
    reason: str | None
    id: int
    drift_ppm: dict[str, float] = field(default_factory=dict)
    jumps: list[tuple[float, str, float]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "delays_ms": {n: round(v, 3) for n, v in self.delays_ms.items()},
            "current_ms": {n: round(v, 3) for n, v in self.current_ms.items()},
            "sigma_ms": {n: round(v, 3) for n, v in self.sigma_ms.items()},
            "spread_after_ms": None if self.spread_after_ms is None else round(self.spread_after_ms, 3),
            "spread_now_ms": None if self.spread_now_ms is None else round(self.spread_now_ms, 3),
            "anchor": self.anchor,
            "based_on": self.based_on,
            "at": self.at,
            "reason": self.reason,
            "drift_ppm": {n: round(v, 1) for n, v in self.drift_ppm.items()},
            "jumps": [[round(t, 1), s, round(ms, 3)] for t, s, ms in self.jumps],
        }


class SyncEstimator:
    def __init__(
        self,
        settings: SyncSettings,
        current_delays: Callable[[], dict[str, float]],
        server_position: str = "server",
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.settings = settings
        self.current_delays = current_delays
        self.server_position = server_position
        self.clock = clock
        self.dropped = 0
        self.suggestion: Suggestion | None = None
        self.explain: dict | None = None
        """Filled on this thread by `request_explain` (`sync_docs`); None until then."""
        self.explainer: Callable[[SyncSettings], dict] | None = None
        self.error: str | None = None
        """The last exception of the thread, if any: it is reported, and the thread goes on."""
        self._jumps: tuple[tuple[float, str, float], ...] = ()
        """Confirmed jumps, carried from fit to fit (`sync_methods.fit`, `known_jumps`)."""
        self._signature: tuple | None = None
        self._history: deque[Measurement] = deque(maxlen=HISTORY_MAX)
        self._queue: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._waiting = 0
        self._lock = threading.Lock()
        self._busy = False
        self._next_id = 1
        self._sources: dict[tuple[str, str], dict[str, Any]] = {}
        self.thread = threading.Thread(target=self._work, name="aurasync-sync-estimator", daemon=True)
        self.thread.start()

    # -- what other threads call: O(1) -------------------------------------------------

    def submit(self, m: Measurement) -> None:
        with self._lock:
            if self._waiting >= QUEUE_MAX:
                self.dropped += 1
                return
            self._waiting += 1
        self._queue.put(("measurement", m))

    def set_settings(self, settings: SyncSettings) -> None:
        self.settings = settings
        self._queue.put(("refit", None))
        self.request_explain()

    def request_explain(self) -> None:
        self._queue.put(("explain", None))

    def reset(self) -> None:
        """Forget the measurements and the suggestion: a new session reopens every stream, and
        the latencies measured before are not the ones now (review 2026-10-03)."""
        self._queue.put(("reset", None))

    def levels(self) -> dict:
        return sync_levels.summarise(list(self._history))

    def sources(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(v) for v in self._sources.values()]

    def wait_idle(self, timeout: float = 5.0) -> bool:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            with self._lock:
                if self._queue.empty() and not self._busy:
                    return True
            time.sleep(0.005)
        return False

    def close(self) -> None:
        self._queue.put(("stop", None))
        self.thread.join(timeout=2.0)

    # -- the thread ---------------------------------------------------------------------

    def _work(self) -> None:
        while True:
            kind, item = self._queue.get()
            with self._lock:
                self._busy = True
            try:
                if kind == "stop":
                    return
                batch, refit, explain, reset = [], kind == "refit", kind == "explain", kind == "reset"
                if kind == "measurement":
                    batch.append(item)
                while True:  # drain what is waiting: one fit for a burst
                    try:
                        k, it = self._queue.get_nowait()
                    except queue.Empty:
                        break
                    if k == "stop":
                        return
                    if k == "measurement":
                        batch.append(it)
                    refit |= k == "refit"
                    explain |= k == "explain"
                    reset |= k == "reset"
                if batch:
                    with self._lock:
                        self._waiting -= len(batch)
                self._step(batch, refit=refit, explain=explain, reset=reset)
            finally:
                with self._lock:
                    self._busy = False

    def _step(self, batch: list[Measurement], *, refit: bool, explain: bool, reset: bool) -> None:
        """One turn of the thread. An exception is reported and the thread goes on (review
        2026-10-03: one killed it silently, and the last suggestion stayed applyable)."""
        try:
            if reset:
                self._history.clear()
                self._jumps, self._signature, self.suggestion = (), None, None
                with self._lock:
                    self._sources.clear()
            if batch:
                self._take(batch)
            if batch or refit:
                self._fit(force=refit)
            if explain and self.explainer is not None:
                self.explain = self.explainer(self.settings)
            self.error = None
        except Exception as exc:  # noqa: BLE001 - the estimator must outlive any one fit
            self.error = f"{type(exc).__name__}: {exc}"
            self.suggestion = Suggestion(
                {}, {}, {}, None, None, None, 0, self.clock(), f"estimator error: {self.error}", self._next_id
            )
            self._next_id += 1
            self._signature = None

    def _take(self, batch: list[Measurement]) -> None:
        for m in batch:
            self._history.append(m)
            with self._lock:
                key = (m.source_id, m.position_id)
                row = self._sources.setdefault(
                    key,
                    {"source_id": m.source_id, "position_id": m.position_id, "count": 0, "kind": m.kind, "rejected": 0},
                )
                row["count"] += 1
                why = sync_methods.rejection(m, self.settings)
                if why is not None:
                    row["rejected"] += 1
                    row["last_rejected"] = why
                row["last_t"] = m.t
                row["kind"] = m.kind
                row["origin"] = m.origin

    def _fit(self, *, force: bool = False) -> None:
        now = self.clock()
        f = sync_methods.fit(list(self._history), self.settings, now, self.server_position, self._jumps)
        self._jumps = tuple(f.jumps)
        # Nothing new kept (a rejected measurement): the same suggestion, the same id, so that an
        # "Aplicar" in flight still names the current one (review 2026-10-03).
        signature = (f.used, f.last_t, f.anchor)
        if not force and signature == self._signature and self.suggestion is not None:
            return
        self._signature = signature
        delays = sync_methods.suggested_delays(f)
        current = self.current_delays()
        spread_now = None
        if len(f.latency_ms) >= 2:  # noqa: PLR2004
            heard = [f.latency_ms[s] + current.get(s, 0.0) for s in f.latency_ms]
            spread_now = float(max(heard) - min(heard))
        spread_after = 2 * max(f.sigma_ms.values()) if f.sigma_ms else None
        self.suggestion = Suggestion(
            delays,
            {s: current.get(s, 0.0) for s in delays},
            f.sigma_ms,
            spread_after,
            spread_now,
            f.anchor,
            f.used,
            now,
            f.reason,
            self._next_id,
            f.drift_ppm,
            f.jumps,
        )
        self._next_id += 1
