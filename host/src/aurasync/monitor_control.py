"""The headphone monitor's service side (spec 2026-10-04-headphone-monitor-design.md §2).

The choice lives in the service and outlives sessions. Opening touches PipeWire (a module and a
`pw-play`; the binaural filter can take seconds to appear), so it happens on a worker thread and
the open output is attached to the session on the engine thread. Where the audio really went is
read back after opening: what is asked of PipeWire is verified, not assumed (CLAUDE.md).
"""

from __future__ import annotations

import logging
from concurrent.futures import Future, ThreadPoolExecutor
from typing import TYPE_CHECKING, Any

from aurasync import control
from aurasync.monitor import MonitorError, MonitorSettings, candidates, check_target

if TYPE_CHECKING:
    from collections.abc import Callable

    from aurasync.config import Instalacion

log = logging.getLogger("aurasync.monitor")


def forbidden_targets(installation: Instalacion | None, sink_name: str) -> set[str]:
    """The speakers' sinks and aurasync's own nodes: a monitor there would loop back."""
    # A virtual speaker has no sink: there is nothing of it to loop back into.
    speakers = {p.sink for p in (installation.parlantes if installation else []) if p.sink is not None}
    return speakers | {sink_name, f"{sink_name}_salida", f"{sink_name}_salida_b", f"{sink_name}_monitor"}


class MonitorController:
    def __init__(
        self,
        settings: MonitorSettings,
        factory: Callable[..., Any],
        *,
        on_engine: Callable[[Callable[[], None]], None],
        save: Callable[[dict], None],
    ) -> None:
        self.settings = settings
        self.factory = factory
        self.on_engine = on_engine
        self.save = save
        self.state = "off" if settings.mode == "off" else "waiting"
        self.error: str | None = None
        self.routed_to: str | None = None
        self._generation = 0
        self._session: Any = None
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="aurasync-monitor-open")
        self._future: Future | None = None

    # -- engine thread ----------------------------------------------------------------

    def set(
        self, settings: MonitorSettings, session: Any, installation: Instalacion | None, *, sink_name: str, rate: int
    ) -> None:
        check_target(settings, forbidden_targets(installation, sink_name))
        self.settings = settings
        self.save(settings.to_json())
        self._apply(session, installation, sink_name, rate)

    def session_opened(self, session: Any, installation: Instalacion, *, sink_name: str, rate: int) -> None:
        try:
            check_target(self.settings, forbidden_targets(installation, sink_name))
        except MonitorError as exc:  # the installation changed since it was chosen
            self.state, self.error = "failed", str(exc)
            return
        self._apply(session, installation, sink_name, rate)

    def session_closed(self) -> None:
        self._generation += 1
        self._session = None
        self.routed_to = None
        if self.settings.mode != "off":
            self.state = "waiting"

    def _apply(self, session: Any, installation: Instalacion | None, sink_name: str, rate: int) -> None:
        self._generation += 1
        generation = self._generation
        self._session = session
        self.error, self.routed_to = None, None
        attach = getattr(session, "attach_monitor", None)
        if attach is not None:
            attach(None)
        if self.settings.mode == "off":
            self.state = "off"
            return
        if session is None or installation is None:
            self.state = "waiting"
            return
        if attach is None:
            self.state, self.error = "failed", "this session has no monitor output"
            return
        self.state = "opening"
        names = [p.nombre for p in installation.parlantes]
        angles = {p.nombre: control.angle_of(p.pan, p.ambiente) for p in installation.parlantes}
        settings = self.settings

        def open_it() -> None:
            out = self.factory(settings, names, angles, rate, f"{sink_name}_monitor")
            try:
                out.open()
            except Exception as exc:  # noqa: BLE001 - reported in the panel; the speakers are unaffected
                out.close()
                reason = str(exc)  # `exc` is gone once the except block ends; the call runs later
                self.on_engine(lambda: self._failed(generation, reason))
                return
            where = out.where()
            self.on_engine(lambda: self._opened(generation, session, out, where))

        self._future = self._pool.submit(open_it)

    def _opened(self, generation: int, session: Any, out: Any, where: str | None) -> None:
        if generation != self._generation:
            out.close()
            return
        session.attach_monitor(out)
        self.routed_to = where
        self.state = "on"
        if where != self.settings.target:
            log.warning("the monitor went to %s, not to %s", where or "nothing", self.settings.target)

    def _failed(self, generation: int, reason: str) -> None:
        if generation == self._generation:
            self.state, self.error = "failed", reason

    def view(self, sinks: list[dict[str, str]], installation: Instalacion | None, sink_name: str) -> dict[str, Any]:
        monitor = getattr(self._session, "monitor", None) if self._session is not None else None
        drops = getattr(getattr(monitor, "writer", None), "drops", 0)
        return {
            **self.settings.to_json(),
            "state": self.state,
            "error": self.error,
            "routed_to": self.routed_to,
            "reached": self.state == "on" and self.routed_to == self.settings.target,
            "drops": drops,
            "candidates": candidates(sinks, forbidden_targets(installation, sink_name)),
        }

    # -- tests and shutdown -------------------------------------------------------------

    def wait(self) -> None:
        future = self._future
        if future is not None:
            future.result(timeout=10)

    def close(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
