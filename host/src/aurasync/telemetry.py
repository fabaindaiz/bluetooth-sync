"""What the engine just played, timed to when it is heard, for the live stream.

The engine thread writes; the stream threads read (spec §17.2). The engine never waits for a
reader: it appends to a bounded ring under a lock held only for the append, and a reader
copies what it needs under the same short lock.

**Timed to the ear.** A block is written long before the speakers play it (pipe, `pw-play`,
Bluetooth: ~0.5 s here). Each chunk carries its *play time* = write time + the output
latency, and a reader asks for the newest chunk whose play time has arrived. One clock
(`time.monotonic`) decides: a frame is picked by comparing its play time with now, never by
counting ticks (card `derive-state-from-one-clock`).
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import TYPE_CHECKING

import numpy as np

from aurasync.dsp.response import THIRDS

if TYPE_CHECKING:
    from collections.abc import Callable

CHUNK = 1024
"""Samples per meter frame: 21 ms at 48 kHz, so a 20 Hz stream never repeats a frame."""
RING_S = 4.0
"""How much is kept: more than the output latency, or the frame due now would be gone."""
SILENCE_DB = -120.0
SPECTRUM_FFT = 4096


def level_db(x: np.ndarray) -> tuple[float, float]:
    if len(x) == 0:
        return SILENCE_DB, SILENCE_DB
    rms = float(np.sqrt(np.mean(x * x)))
    peak = float(np.max(np.abs(x)))
    return (
        max(SILENCE_DB, 20 * np.log10(rms + 1e-12)),
        max(SILENCE_DB, 20 * np.log10(peak + 1e-12)),
    )


def thirds_db(x: np.ndarray, sr: int) -> list[float]:
    """Third-octave levels (dBFS per band, 50 Hz to 20 kHz) of a block."""
    n = SPECTRUM_FFT
    frames = [x[i : i + n] for i in range(0, max(1, len(x) - n + 1), n)] or [x]
    power = np.mean([np.abs(np.fft.rfft(f * np.hanning(len(f)), n)) ** 2 / (len(f) ** 2 / 8) for f in frames], axis=0)
    f = np.fft.rfftfreq(n, 1 / sr)
    out = []
    for c in THIRDS:
        band = power[(f >= c / 2 ** (1 / 6)) & (f < c * 2 ** (1 / 6))]
        out.append(round(max(SILENCE_DB, 10 * np.log10(band.sum() + 1e-20)), 1) if band.size else SILENCE_DB)
    return out


class Telemetry:
    def __init__(self, sr: int, clock: Callable[[], float] = time.monotonic) -> None:
        self.sr = sr
        self.clock = clock
        self.latency_s = 0.0
        """Write to ear. 0 until a calibration measures it."""
        self.synced = False
        frames = int(RING_S * sr / CHUNK) + 1
        self._meters: deque[tuple[float, dict]] = deque(maxlen=frames)
        self._input: deque[tuple[float, dict]] = deque(maxlen=int(RING_S * 20) + 1)
        self._lock = threading.Lock()
        self._mic: tuple[float, tuple[float, float]] | None = None

    def set_latency(self, latency_ms: float | None) -> None:
        self.latency_s = max(0.0, (latency_ms or 0.0) / 1000)
        self.synced = latency_ms is not None

    def record(
        self,
        outputs: dict[str, np.ndarray],
        inputs: tuple[np.ndarray, np.ndarray] | None,
        limiter_db: dict[str, float] | None = None,
    ) -> None:
        """One written block: meter frames per chunk, and one input frame for the block."""
        written = self.clock()
        n = len(next(iter(outputs.values()), np.zeros(0)))
        channels = dict(outputs)
        if inputs is not None:
            channels = {"in L": inputs[0], "in R": inputs[1], **channels}
        frames = []
        for start in range(0, n, CHUNK):
            meters = {name: level_db(x[start : start + CHUNK]) for name, x in channels.items()}
            frames.append((written + self.latency_s + start / self.sr, meters))
        frame_input = None
        if inputs is not None and len(inputs[0]):
            left, right = inputs
            mid = (left + right) / 2
            ll, rr, lr = float(np.dot(left, left)), float(np.dot(right, right)), float(np.dot(left, right))
            side = (ll + rr - 2 * lr) / 4
            middle = (ll + rr + 2 * lr) / 4
            frame_input = (
                written + self.latency_s,
                {
                    "bands_db": thirds_db(mid, self.sr),
                    "correlation": round(lr / np.sqrt(ll * rr), 3) if ll > 0 and rr > 0 else None,
                    "side_db": round(10 * np.log10(max(side, 1e-20) / max(middle, 1e-20)), 1) if middle > 0 else None,
                },
            )
        extra = {"limiter_db": limiter_db or {}}
        with self._lock:
            for t, meters in frames:
                self._meters.append((t, {"meters": meters, **extra}))
            if frame_input is not None:
                self._input.append(frame_input)

    def record_microphone(self, x: np.ndarray) -> None:
        """What the microphone hears now. Not delayed: the room is already playing it."""
        with self._lock:
            self._mic = (self.clock(), level_db(x))

    def _due(self, ring: deque, now: float) -> tuple[float, dict] | None:
        with self._lock:
            items = list(ring)
        due = None
        for t, frame in items:
            if t <= now:
                due = (t, frame)
            else:
                break
        return due

    def meters_at(self, now: float | None = None) -> dict | None:
        """The meter frame being heard at `now` (default: the clock's now)."""
        now = self.clock() if now is None else now
        found = self._due(self._meters, now)
        if found is None:
            return None
        t, frame = found
        meters = {name: {"rms_db": round(r, 1), "peak_db": round(p, 1)} for name, (r, p) in frame["meters"].items()}
        mic = self._mic
        if mic is not None and now - mic[0] < 1.0:
            meters["mic"] = {"rms_db": round(mic[1][0], 1), "peak_db": round(mic[1][1], 1)}
        return {
            "meters": meters,
            "limiter_db": {k: round(v, 1) for k, v in frame["limiter_db"].items()},
            "age_ms": round((now - t) * 1000),
            "synced": self.synced,
        }

    def input_at(self, now: float | None = None) -> dict | None:
        now = self.clock() if now is None else now
        found = self._due(self._input, now)
        return None if found is None else {**found[1], "age_ms": round((now - found[0]) * 1000)}
