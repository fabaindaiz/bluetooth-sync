"""The headphone monitor's service side (spec 2026-10-04-headphone-monitor-design.md §2).

The choice lives in the service and outlives sessions. Opening touches PipeWire (a module and a
`pw-play`; the binaural filter can take seconds to appear), so it happens on a worker thread and
the open output is attached to the session on the engine thread. Where the audio really went is
read back after opening: what is asked of PipeWire is verified, not assumed (CLAUDE.md).

It also owns the loudness match (`loudness_match.py`): one per service, so each mode's makeup
outlives the output that a mode change reopens, and it hands each output where the chosen volume,
the engine's digital volume and the hold (a cut, a calibration) come from.
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import replace
from typing import TYPE_CHECKING, Any

import numpy as np

from aurasync import control
from aurasync.bt_volume import READBACK_TRIES, READBACK_WAIT_S, TOLERANCE_PCT
from aurasync.loudness_match import LoudnessMatch
from aurasync.monitor import (
    DEFAULT_DEVICE_LIMIT_PCT,
    DEFAULT_GAIN_DB,
    Levels,
    MonitorError,
    MonitorSettings,
    candidates,
    check_target,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from aurasync.config import Instalacion

log = logging.getLogger("aurasync.monitor")

REFRESH_S = 2.0
"""How often the sink's real volume is read again (the headphone buttons move it too)."""
SAFETY_TIMEOUT_S = 10.0
"""How long the open waits for the safety; slower, the volume counts as not verified."""
SWITCH_LIMIT_PCT = DEFAULT_DEVICE_LIMIT_PCT
"""Switching software -> device: the ceiling is at most this, unless the level is moved too
(the sink may sit at an old ceiling while the software gain was low)."""
FALLBACK_REASON = "no se pudo verificar el volumen del audífono: usando volumen por software"
"""Shown in the panel as is (panel copy is Spanish): the monitor plays at `gain_db` instead."""


def forbidden_targets(installation: Instalacion | None, sink_name: str) -> set[str]:
    """The speakers' sinks and aurasync's own nodes: a monitor there would loop back."""
    # A virtual speaker has no sink: there is nothing of it to loop back into.
    speakers = {p.sink for p in (installation.parlantes if installation else []) if p.sink is not None}
    return speakers | {sink_name, f"{sink_name}_salida", f"{sink_name}_salida_b", f"{sink_name}_monitor"}


def _round(x: float | None) -> float | None:
    return None if x is None else round(float(x), 1)


class MonitorController:
    def __init__(
        self,
        settings: MonitorSettings,
        factory: Callable[..., Any],
        *,
        on_engine: Callable[[Callable[[], None]], None],
        save: Callable[[dict], None],
        volume: Callable[[], float | None] | None = None,
        backend: Any = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """`volume`: the volume the listener chose (the service's `volume_db`); `None` or absent,
        the engine's digital volume. `backend`: the sink volume (`bt_volume.PactlVolume`'s
        interface) for `volume_control == "device"`; `None`, there is none and the view says so."""
        self.settings = settings
        self.factory = factory
        self.on_engine = on_engine
        self.save = save
        self.volume = volume
        self.match = LoudnessMatch()
        self._layout: tuple | None = None
        self.state = "off" if settings.mode == "off" else "waiting"
        self.error: str | None = None
        self.routed_to: str | None = None
        self._generation = 0
        self._session: Any = None
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="aurasync-monitor-open")
        self._future: Future | None = None
        self.backend = backend
        self._sleep = sleep
        self._clock = clock
        # Everything that talks to the sink runs here, never on the engine thread.
        self._vol_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="aurasync-monitor-volume")
        self._vol_future: Future | None = None
        self._read_at: float | None = None
        self._reading = False
        self.device_read: float | None = None
        self.device_reason: str | None = None
        self._wanted: tuple[str, float] | None = None
        """A level asked for and not seen yet on the sink: its reason stays until it is."""
        self.fallback_reason: str | None = None
        """Why the open output plays at the software gain although the choice is the device."""

    # -- engine thread ----------------------------------------------------------------

    def set(
        self,
        settings: MonitorSettings,
        session: Any,
        installation: Instalacion | None,
        *,
        sink_name: str,
        rate: int,
        block: int = 4096,
        level: float | None = None,
    ) -> None:
        """`level`: a sink volume the listener asked for now (the panel's slider), applied
        always, even when it equals the stored one, and stored as the new ceiling. `None`:
        nothing asked; the sink is only ever lowered, by the safety at open."""
        check_target(settings, forbidden_targets(installation, sink_name))
        old = self.settings
        if level is not None:
            settings = replace(settings, device_volume_pct=level)
        elif (
            old.volume_control == "software"
            and settings.volume_control == "device"
            and settings.device_volume_pct is not None
            and settings.device_volume_pct > SWITCH_LIMIT_PCT
        ):
            # By software the sink was not the level: it may sit at an old, high ceiling.
            settings = replace(settings, device_volume_pct=SWITCH_LIMIT_PCT)
        self.settings = settings
        self.save(settings.to_json())
        if settings.target != old.target:
            # A failed set on the old sink says nothing about the new one.
            self._wanted, self.device_reason = None, None
        level_requested = level is not None
        only_level = replace(old, device_volume_pct=settings.device_volume_pct) == settings
        if self._device_active(settings) and level_requested:
            # Before the (re)open: its safety then finds the sink at the new value, not above it.
            self._volume_job(self._apply_level, settings.target, settings.device_volume_pct)
        if only_level and old.mode != "off" and self.state != "failed":
            return  # the level moved the sink; nothing to reopen
        if not self._device_active(settings):
            self.device_read, self.device_reason, self._read_at, self._wanted = None, None, None, None
        self._apply(session, installation, sink_name, rate, block)

    @staticmethod
    def _device_active(settings: MonitorSettings) -> bool:
        return settings.mode != "off" and settings.volume_control == "device" and bool(settings.target)

    # -- the sink's volume (worker thread) -----------------------------------------------

    def _volume_job(self, fn: Callable[..., None], *args: Any) -> Future:
        self._vol_future = self._vol_pool.submit(fn, *args)
        return self._vol_future

    def _unavailable(self) -> str | None:
        """Why the sink volume cannot be used now; then nothing read before is shown."""
        reason = "this service has no sink volume backend" if self.backend is None else self.backend.unavailable()
        if reason is not None:
            self.device_read, self._wanted = None, None
            self.device_reason = reason
        return reason

    def _apply_level(self, sink: str, pct: float) -> bool:
        """Ask the sink for `pct` and read it back: what is asked of the system is verified.
        True only when the sink was read back at `pct`."""
        if self._unavailable() is not None:
            return False
        accepted = self.backend.set_percent(sink, pct)
        read: float | None = None
        for attempt in range(READBACK_TRIES):
            read = self.backend.get_percent(sink)
            if read is not None and abs(read - pct) <= TOLERANCE_PCT:
                break
            if attempt < READBACK_TRIES - 1:
                self._sleep(READBACK_WAIT_S)
        self.device_read = read
        self._read_at = self._clock()
        if read is not None and abs(read - pct) <= TOLERANCE_PCT:
            self.device_reason, self._wanted = None, None
            return True
        self._wanted = (sink, pct)
        if not accepted:
            self.device_reason = f"{sink} refused the volume {pct:g} %"
        else:
            shown = "unreadable" if read is None else f"{read:g} %"
            self.device_reason = f"{sink} did not take {pct:g} %: it reads {shown}"
        return False

    def _safety(self, settings: MonitorSettings) -> tuple[bool, str | None]:
        """At open: a sink above the last value the listener set is lowered to it; a lower one is
        left alone (the volume is never raised by itself). Answers whether the sink was **seen**
        at or below that value, and if not, why: then the monitor must not open at 0 dB."""
        sink = settings.target
        limit = settings.device_volume_pct if settings.device_volume_pct is not None else DEFAULT_DEVICE_LIMIT_PCT
        reason = self._unavailable()
        if reason is not None:
            return False, reason
        read = self.backend.get_percent(sink)
        self.device_read, self._read_at = read, self._clock()
        if read is None:
            self.device_reason = f"could not read the volume of {sink}"
            return False, self.device_reason
        if read <= limit + TOLERANCE_PCT:
            if self._wanted is None:
                self.device_reason = None
            return True, None
        log.warning("monitor: %s was at %g %%, lowering it to %g %% before opening", sink, read, limit)
        if self._apply_level(sink, limit):
            return True, None
        return False, self.device_reason

    def _read(self, sink: str) -> None:
        try:
            if self._unavailable() is not None:
                return
            read = self.backend.get_percent(sink)
            self.device_read = read
            wanted = self._wanted
            if read is None:
                if wanted is None:
                    self.device_reason = f"could not read the volume of {sink}"
            elif wanted is None or (wanted[0] == sink and abs(read - wanted[1]) <= TOLERANCE_PCT):
                # A failed set keeps its reason until the sink is seen at what was asked
                # (or the next set): a read 2 s later must not hide it.
                self.device_reason, self._wanted = None, None
        finally:
            self._read_at = self._clock()
            self._reading = False

    def _refresh(self) -> None:
        """Called from `view`: answers from the cache and, now and then, reads again off this thread."""
        s = self.settings
        if not self._device_active(s) or self._reading or self.state == "opening":
            return
        if self._read_at is not None and self._clock() - self._read_at < REFRESH_S:
            return
        self._reading = True
        self._volume_job(self._read, s.target)

    def session_opened(
        self, session: Any, installation: Instalacion, *, sink_name: str, rate: int, block: int = 4096
    ) -> None:
        try:
            check_target(self.settings, forbidden_targets(installation, sink_name))
        except MonitorError as exc:  # the installation changed since it was chosen
            self.state, self.error = "failed", str(exc)
            return
        self._apply(session, installation, sink_name, rate, block)

    def session_closed(self) -> None:
        self._generation += 1
        self._session = None
        self.routed_to = None
        if self.settings.mode != "off":
            self.state = "waiting"

    def _apply(
        self, session: Any, installation: Instalacion | None, sink_name: str, rate: int, block: int = 4096
    ) -> None:
        self._generation += 1
        generation = self._generation
        self._session = session
        self.error, self.routed_to, self.fallback_reason = None, None, None
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
        layout = tuple((n, round(angles[n], 1)) for n in names)
        if layout != self._layout:
            # Other speakers, or the same ones elsewhere: what each mode needed no longer holds.
            self.match.forget()
            self._layout = layout
        settings = self.settings

        def open_it() -> None:
            built, fallback = settings, None
            if self._device_active(settings):
                # The volume first, so the audio never starts above what the listener allowed.
                # Not seen at or below it: the software gain instead, never 0 dB (fails closed).
                verified, why = self._checked_safety(settings)
                if not verified:
                    fallback = f"{FALLBACK_REASON} ({why})"
                    log.warning("monitor: %s; opening at the software gain", why)
                    # A `gain_db` stored at 0 dB would make this full level: at most -12 dB.
                    built = replace(settings, volume_control="software", gain_db=min(settings.gain_db, DEFAULT_GAIN_DB))
            out = self.factory(built, names, angles, rate, f"{sink_name}_monitor", block=block)
            try:
                out.open()
            except Exception as exc:  # noqa: BLE001 - reported in the panel; the speakers are unaffected
                out.close()
                reason = str(exc)  # `exc` is gone once the except block ends; the call runs later
                self.on_engine(lambda: self._failed(generation, reason))
                return
            where = out.where()
            self.on_engine(lambda: self._opened(generation, session, out, where, fallback))

        self._future = self._pool.submit(open_it)

    def _checked_safety(self, settings: MonitorSettings) -> tuple[bool, str]:
        """The safety on the volume thread, waited for at most `SAFETY_TIMEOUT_S`. Anything but
        a sink seen at or below the ceiling is "not verified", with why."""
        try:
            verified, why = self._volume_job(self._safety, settings).result(timeout=SAFETY_TIMEOUT_S)
        except FutureTimeout:
            return False, f"the volume of {settings.target} was not checked within {SAFETY_TIMEOUT_S:g} s"
        except Exception as exc:  # noqa: BLE001 - not verified, whatever failed
            return False, f"the volume of {settings.target} could not be checked: {exc}"
        return verified, why or ""

    def _opened(self, generation: int, session: Any, out: Any, where: str | None, fallback: str | None = None) -> None:
        if generation != self._generation:
            out.close()
            return
        if where != self.settings.target:
            log.warning("the monitor went to %s, not to %s", where or "nothing", self.settings.target)
            if fallback is None and self._device_active(self.settings):
                # The volume checked is not the one that plays it: the software gain, before
                # the first block reaches it.
                fallback = (
                    f"{FALLBACK_REASON} (PipeWire sent it to {where or 'nothing'}, not to {self.settings.target})"
                )
                use_software = getattr(out, "use_software_gain", None)
                if use_software is not None:
                    use_software()
        self.fallback_reason = fallback
        bind = getattr(out, "bind", None)
        if bind is not None:
            bind(self.match, self._levels(session))
        session.attach_monitor(out)
        self.routed_to = where
        self.state = "on"

    def _levels(self, session: Any) -> Callable[[], Levels]:
        """Read on the engine thread at every block, where the service and the motor live."""

        def levels() -> Levels:
            motor = getattr(session, "motor", None)
            try:
                target = float(getattr(motor, "volumen_db", 0.0))
            except (TypeError, ValueError):
                target = 0.0
            # What the last block was made with, not the target: at a cut's bottom the target
            # has already jumped, the block had not (Levels).
            made = getattr(motor, "volumen_del_bloque_db", None)
            digital = made if isinstance(made, (float, int, np.ndarray)) else target
            chosen = self.volume() if self.volume is not None else None
            calibration = getattr(session, "calibration", None)
            hold = bool(getattr(motor, "en_corte", False)) or getattr(calibration, "state", None) == "running"
            return Levels(volume_db=target if chosen is None else float(chosen), digital_db=digital, hold=hold)

        return levels

    def _failed(self, generation: int, reason: str) -> None:
        if generation == self._generation:
            self.state, self.error = "failed", reason

    def view(self, sinks: list[dict[str, str]], installation: Instalacion | None, sink_name: str) -> dict[str, Any]:
        monitor = getattr(self._session, "monitor", None) if self._session is not None else None
        drops = getattr(getattr(monitor, "writer", None), "drops", 0)
        cushion = getattr(monitor, "cushion", None)
        self._refresh()
        device = self._device_active(self.settings)
        matched = monitor is not None and self.state == "on" and getattr(monitor, "match", None) is self.match
        return {
            **self.settings.to_json(),
            "state": self.state,
            "error": self.error,
            "routed_to": self.routed_to,
            "reached": self.state == "on" and self.routed_to == self.settings.target,
            "drops": drops,
            "cushion_ms": None if cushion is None else round(cushion.target_ms, 1),
            "level_ms": None if cushion is None or cushion.level_ms is None else round(cushion.level_ms, 1),
            "refills": 0 if cushion is None else cushion.refills,
            "pipe_bytes": getattr(monitor, "pipe_bytes", None),
            "trims": 0 if cushion is None else cushion.trims,
            "makeup_db": round(self.match.current_db, 1) if matched else None,
            "loudness_reference": _round(getattr(monitor, "loudness_reference", None)) if matched else None,
            "loudness_monitor": _round(getattr(monitor, "loudness_monitor", None)) if matched else None,
            "match": self.match.status if matched else None,
            "match_reason": getattr(monitor, "match_reason", None) if matched else None,
            "device_volume_set_pct": self.settings.device_volume_pct,
            "device_volume_pct": None if not device else _round(self.device_read),
            "device_volume_reason": self._reason() if device else None,
            "candidates": candidates(sinks, forbidden_targets(installation, sink_name)),
        }

    def _reason(self) -> str | None:
        reasons = [r for r in (self.fallback_reason, self.device_reason) if r]
        return "; ".join(dict.fromkeys(reasons)) or None

    # -- tests and shutdown -------------------------------------------------------------

    def wait(self) -> None:
        for future in (self._future, self._vol_future):
            if future is not None:
                future.result(timeout=10)

    def close(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
        self._vol_pool.shutdown(wait=False, cancel_futures=True)
