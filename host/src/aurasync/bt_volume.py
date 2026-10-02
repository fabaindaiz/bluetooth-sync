"""The volume in the speakers (AVRCP), with every change read back (spec 2026-10-02 §5).

Why: the SBC encoder gets 16 bits without dither, so 20-40 dB of digital attenuation throws
resolution away (research 11 §3). With `volume.algorithm = avrcp` the panel's volume moves the
Bluetooth volume of each speaker — PipeWire's volume of its `bluez_output…` sink, which
WirePlumber turns into AVRCP Absolute Volume (experimentos/10 §5.4: 80 % → AVRCP 102 of 127)
— and the digital volume stays at 0 dB.

**What the knob means in this mode.** `volume_db` is the PipeWire volume of the loudest
speaker, in dB on PipeWire's cubic curve (`percent = 100 * 10^(dB/60)`: 80 % is -5.81 dB).
Every speaker keeps the difference it had with the loudest when the mode was entered
(`offsets_db`): the calibration measured the gains with those volumes in place, so moving
them all by the same dB keeps the balance. The Go 4's real curve is not PipeWire's (-6 dB
asked gave -4.1 dB at the microphone, experimentos/10 §5.4); until
`probes/15-…/curva_avrcp.py` measures it, the mapping is PipeWire's.

**What is asked of PipeWire is verified** (CLAUDE.md): after each `pactl set-sink-volume`
the volume is read back with `pactl get-sink-volume`, a few times over ~0.5 s; a speaker
whose volume did not land within `TOLERANCE_PCT` (one AVRCP step is 0.79 %) is reported, and
the service does not pretend it did: while entering the mode, the digital volume only goes
to 0 dB after every speaker confirmed its lower volume.

**No jump in level when the mode changes** (the service's `_volume_mode`): the level heard is
digital + speaker. Entering, the speakers go down first (`v + top + offset`), and only once
they confirmed does the digital volume rise to 0 dB through the cut; leaving, the digital
volume goes down through the cut first, and the speakers go back up `RAISE_AFTER_S` later,
when that quieter audio is the one being heard. Either way the transient is a short dip,
never a burst.

All the work runs on one worker thread (`pactl` takes ~10-20 ms per call): the engine thread
never waits for it. Requests are coalesced: a slider dragged quickly only sends its last value.
"""

from __future__ import annotations

import math
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

TOLERANCE_PCT = 1.0
"""How far the volume read back may be from the one asked, in percentage points. One AVRCP
step is 100/127 = 0.79 %; PipeWire reports the volume it set, rounded to the device's step."""
READBACK_TRIES = 5
READBACK_WAIT_S = 0.1
RAISE_AFTER_S = 0.6
"""Leaving the mode: how long after the digital volume went down the speakers go back up. The
audio written is heard ~0.5 s later (experimentos/10 §5.2), so the louder speaker meets the
quieter audio, not the other way round."""
FLOOR_DB = -60.0
_PERCENT = re.compile(r"(\d+(?:\.\d+)?)%")


def percent_of(db: float) -> float:
    """PipeWire's cubic volume curve: dB → percent."""
    return 100.0 * 10 ** (max(db, FLOOR_DB) / 60)


def db_of(percent: float) -> float:
    return 60 * math.log10(percent / 100) if percent > 0 else -math.inf


class PactlVolume:
    """The real thing: `pactl set-sink-volume` and `get-sink-volume`."""

    def __init__(
        self,
        run: Callable[[list[str]], subprocess.CompletedProcess[str] | None] | None = None,
        which: Callable[[str], str | None] = shutil.which,
    ) -> None:
        self._run = run or _run
        self._which = which

    def unavailable(self) -> str | None:
        return (
            None if self._which("pactl") else "este equipo no tiene pactl: el volumen en el parlante no se puede pedir"
        )

    def set_percent(self, sink: str, percent: float) -> bool:
        result = self._run(["pactl", "set-sink-volume", sink, f"{percent:.2f}%"])
        return result is not None and result.returncode == 0

    def get_percent(self, sink: str) -> float | None:
        """The sink's volume (the loudest channel), or None if it cannot be read."""
        result = self._run(["pactl", "get-sink-volume", sink])
        if result is None or result.returncode != 0:
            return None
        values = [float(v) for v in _PERCENT.findall(result.stdout or "")]
        return max(values) if values else None


def _run(args: list[str]) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=3, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None


@dataclass
class SpeakerVolume:
    asked_pct: float | None = None
    read_pct: float | None = None
    ok: bool | None = None

    def view(self) -> dict[str, Any]:
        return {
            "asked_pct": round(self.asked_pct, 1) if self.asked_pct is not None else None,
            "read_pct": round(self.read_pct, 1) if self.read_pct is not None else None,
            "read_db": round(db_of(self.read_pct), 2) if self.read_pct else None,
            "ok": self.ok,
        }


@dataclass
class _State:
    state: str = "off"
    """`off` (digital), `entering`, `on`, `leaving`, `failed` (chosen but not in effect)."""
    baseline_pct: dict[str, float] = field(default_factory=dict)
    offsets_db: dict[str, float] = field(default_factory=dict)
    top_db: float = 0.0
    volume_db: float | None = None
    speakers: dict[str, SpeakerVolume] = field(default_factory=dict)
    error: str | None = None


class BluetoothVolume:
    """The `avrcp` mode's state and its worker. `sinks` maps speaker name → sink.

    The callbacks run on the worker thread; the service hands them to its engine thread.
    """

    def __init__(self, backend: Any = None, *, sleep: Callable[[float], None] = time.sleep) -> None:
        self.backend = backend if backend is not None else PactlVolume()
        self._sleep = sleep
        self._s = _State()
        self._lock = threading.Lock()
        self._jobs: list[Callable[[], None]] = []
        self._wake = threading.Condition(self._lock)
        self._thread: threading.Thread | None = None
        self._busy = False

    # -- reading -------------------------------------------------------------------------

    @property
    def state(self) -> str:
        return self._s.state

    @property
    def active(self) -> bool:
        """Whether the digital volume belongs at 0 dB now (the speakers carry the volume)."""
        return self._s.state in {"on", "leaving"}

    def status(self) -> dict[str, Any]:
        with self._lock:
            s = self._s
            return {
                "state": s.state,
                "pending": self._busy or bool(self._jobs),
                "volume_db": s.volume_db,
                "top_db": round(s.top_db, 2),
                # `list()` copies in one step: the worker may be filling them meanwhile.
                "offsets_db": {n: round(v, 2) for n, v in list(s.offsets_db.items())},
                "speakers": {n: v.view() for n, v in list(s.speakers.items())},
                "error": s.error,
            }

    def warnings(self) -> list[str]:
        s = self._s
        out = []
        if s.state == "failed":
            out.append(f"volume in the speakers is chosen but not in effect: {s.error}; the volume stays digital")
        bad = [n for n, v in list(s.speakers.items()) if v.ok is False]
        if bad and s.state != "failed":
            out.append(f"the speaker volume did not take on {', '.join(bad)}: read back differs from what was asked")
        return out

    # -- the worker ------------------------------------------------------------------------

    def _submit(self, job: Callable[[], None], *, replace: bool = False) -> None:
        with self._wake:
            if replace:
                self._jobs = [j for j in self._jobs if not getattr(j, "replaceable", False)]
            self._jobs.append(job)
            if self._thread is None:
                self._thread = threading.Thread(target=self._work, name="aurasync-bt-volume", daemon=True)
                self._thread.start()
            self._wake.notify()

    def _work(self) -> None:
        while True:
            with self._wake:
                while not self._jobs:
                    self._wake.wait()
                job = self._jobs.pop(0)
                self._busy = True
            try:
                job()
            finally:
                with self._lock:
                    self._busy = False

    def wait_idle(self, timeout: float = 5.0) -> bool:
        """For tests and the CLI: wait until no job is queued or running."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            with self._lock:
                if not self._jobs and not self._busy:
                    return True
            time.sleep(0.01)
        return False

    def _set_and_verify(self, targets: dict[str, tuple[str, float]]) -> list[str]:
        """Ask each sink for its percent, read it back; the names that did not take it."""
        for name, (sink, pct) in targets.items():
            self._s.speakers[name] = SpeakerVolume(asked_pct=pct)
            self.backend.set_percent(sink, pct)
        pending = dict(targets)
        for attempt in range(READBACK_TRIES):
            for name, (sink, pct) in list(pending.items()):
                read = self.backend.get_percent(sink)
                self._s.speakers[name].read_pct = read
                if read is not None and abs(read - pct) <= TOLERANCE_PCT:
                    self._s.speakers[name].ok = True
                    del pending[name]
            if not pending:
                break
            if attempt < READBACK_TRIES - 1:
                self._sleep(READBACK_WAIT_S)
        for name in pending:
            self._s.speakers[name].ok = False
        return sorted(pending)

    def _targets(self, sinks: dict[str, str], volume_db: float) -> dict[str, tuple[str, float]]:
        return {name: (sink, percent_of(volume_db + self._s.offsets_db.get(name, 0.0))) for name, sink in sinks.items()}

    # -- the mode ----------------------------------------------------------------------------

    def enter(
        self,
        sinks: dict[str, str],
        digital_db: float,
        done: Callable[[float | None], None],
    ) -> None:
        """Enter the mode from a digital volume of `digital_db`.

        `done(volume_db)` is called with the mode's new `volume_db` (the level heard kept: the
        loudest speaker goes to `digital_db + top`), once every speaker confirmed it; with None
        if it failed (the speakers are put back where they were, and the volume stays digital).
        """

        def job() -> None:
            s = self._s
            s.state, s.error = "entering", None
            reason = self.backend.unavailable()
            baseline: dict[str, float] = {}
            if reason is None:
                for name, sink in sinks.items():
                    pct = self.backend.get_percent(sink)
                    if pct is None or pct <= 0:
                        reason = f"no se pudo leer el volumen de {name}"
                        break
                    baseline[name] = pct
            if reason is not None:
                s.state, s.error = "failed", reason
                done(None)
                return
            levels = {n: db_of(p) for n, p in baseline.items()}
            s.baseline_pct = baseline
            s.top_db = max(levels.values())
            s.offsets_db = {n: v - s.top_db for n, v in levels.items()}
            target = max(FLOOR_DB, min(0.0, digital_db + s.top_db))
            missed = self._set_and_verify(self._targets(sinks, target))
            if missed:
                # Put back what was there: the digital volume did not change, so this is the level
                # that was being heard.
                for name, pct in baseline.items():
                    self.backend.set_percent(sinks[name], pct)
                s.state, s.error = "failed", f"no tomaron el volumen: {', '.join(missed)}"
                done(None)
                return
            s.state, s.volume_db = "on", target
            done(target)

        self._submit(job)

    def apply(self, sinks: dict[str, str], volume_db: float, done: Callable[[list[str]], None] | None = None) -> None:
        """While the mode is on: move every speaker to `volume_db` plus its offset."""

        def job() -> None:
            if self._s.state != "on":
                return
            missed = self._set_and_verify(self._targets(sinks, volume_db))
            self._s.volume_db = volume_db
            if done is not None:
                done(missed)

        job.replaceable = True  # type: ignore[attr-defined]
        self._submit(job, replace=True)

    def leave_levels(self, volume_db: float) -> tuple[float, float]:
        """(speaker level to go back to, digital volume) that keep the level heard on leaving.

        The speakers go back to where they were (their loudest at `top`), or stay higher if the
        listener raised them above it: the digital volume can only attenuate.
        """
        restore = max(self._s.top_db, volume_db)
        return restore, volume_db - restore

    def leave(self, sinks: dict[str, str], volume_db: float, done: Callable[[list[str]], None] | None = None) -> None:
        """Leave the mode: after `RAISE_AFTER_S`, the speakers go to the level of `leave_levels`.

        The caller has already put the digital volume down through the cut."""
        restore, _ = self.leave_levels(volume_db)
        self._s.state = "leaving"

        def job() -> None:
            self._sleep(RAISE_AFTER_S)
            missed = self._set_and_verify(self._targets(sinks, restore))
            self._s.state, self._s.volume_db = "off", None
            if missed:
                self._s.error = f"no volvieron a su volumen: {', '.join(missed)}"
            if done is not None:
                done(missed)

        self._submit(job)

    def forget(self) -> None:
        """Chosen `digital` while the mode was not in effect: nothing to put back."""
        self._s.state, self._s.error = "off", None
