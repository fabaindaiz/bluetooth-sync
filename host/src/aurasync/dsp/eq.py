"""Per-speaker equalisation, designed from the response the calibration measures.

The calibration gives each speaker's third-octave response at the microphone
(`dsp/response.py`). The EQ **only lifts, never cuts** (the listener's call, 2026-10-01: a
first version that cut to a flat target took 18 dB out of the Go 4's bass and left the music
dull and quiet):

- what the speaker does strongly (the Go 4's bass hump) is part of how it sounds, and stays;
- a dip inside the band the speaker can reproduce is lifted, up to `MAX_BOOST_DB`;
- the measurement is read **optimistically**: the microphone is assumed to hear less than
  the speaker gives, so the band that may be lifted is the maker's (`dsp/profiles.py`) or
  the measured one widened by an octave;
- smoothed across neighbouring thirds (a single curve is ±2 dB of noise), and corrections
  under `DEAD_BAND_DB` are left out.

Lifting can push a loud passage past full scale; the limiter at the end of each chain
(`dsp/limiter.py`) takes care of it.

The filter is linear-phase and the same length for every speaker, so it delays all of
them by the same `LATENCY_SAMPLES` and never moves one relative to another. With no
correction it is a delayed impulse: switching EQ on or off keeps the timing.
"""

from __future__ import annotations

import numpy as np

from aurasync.dsp.response import THIRDS

SR = 48000
TAPS = 2048
LATENCY_SAMPLES = (TAPS - 1) // 2
MAX_BOOST_DB = 6.0
DEAD_BAND_DB = 1.0
"""Corrections smaller than this are measurement noise and are left out."""


def smooth(values: np.ndarray) -> np.ndarray:
    """Average each third with its neighbours (weights 1-2-1)."""
    padded = np.concatenate([values[:1], values, values[-1:]])
    return (padded[:-2] + 2 * padded[1:-1] + padded[2:]) / 4


def correction(
    response_db: list[float | None] | np.ndarray,
    previous_db: list[float] | None = None,
    band: tuple[float, float] = (100.0, 20000.0),
) -> np.ndarray:
    """The new EQ curve (dB per third, all >= 0) from a measured residual response.

    `previous_db` is the EQ the measurement was made through: the calibration measures what
    is left to correct, so the new curve is the previous one plus the inverse of the
    residual. `band` is where lifting is allowed (`profiles.boost_band`). Missing thirds
    (None) keep the previous value.
    """
    measured = np.array([np.nan if v is None else float(v) for v in response_db])
    previous = np.zeros(len(THIRDS)) if previous_db is None else np.maximum(0.0, np.asarray(previous_db, dtype=float))
    wanted = smooth(-np.where(np.isfinite(measured), measured, 0.0))
    wanted[np.abs(wanted) < DEAD_BAND_DB] = 0.0
    inside = (band[0] / 2 ** (1 / 6) <= THIRDS) & (band[1] * 2 ** (1 / 6) >= THIRDS)
    curve = np.clip(previous + wanted, 0.0, np.where(inside, MAX_BOOST_DB, 0.0))
    return np.round(curve, 2)


def fir(curve_db: list[float] | np.ndarray | None, taps: int = TAPS, sr: int = SR) -> np.ndarray:
    """A linear-phase FIR whose magnitude follows the curve (interpolated in log frequency)."""
    h = np.zeros(taps)
    if curve_db is None or not np.any(np.asarray(curve_db)):
        h[LATENCY_SAMPLES if taps == TAPS else (taps - 1) // 2] = 1.0
        return h
    n = 8 * taps
    f = np.fft.rfftfreq(n, 1 / sr)
    logf = np.log10(np.maximum(f, 1.0))
    gain_db = np.interp(logf, np.log10(THIRDS), np.asarray(curve_db, dtype=float))
    magnitude = 10 ** (gain_db / 20)
    impulse = np.fft.irfft(magnitude, n)
    centre = (taps - 1) // 2
    impulse = np.roll(impulse, centre)[:taps]
    return impulse * np.hanning(taps)


def response_of(h: np.ndarray, sr: int = SR) -> np.ndarray:
    """Third-octave magnitude in dB of a filter (for tests and the panel)."""
    n = 1 << 16
    f = np.fft.rfftfreq(n, 1 / sr)
    m = np.abs(np.fft.rfft(h, n)) ** 2
    return np.array(
        [10 * np.log10(np.mean(m[(f >= c / 2 ** (1 / 6)) & (f < c * 2 ** (1 / 6))]) + 1e-20) for c in THIRDS]
    )


class StreamingFIR:
    """FFT overlap-add convolution that keeps state between blocks.

    `set_taps` swaps the filter; the caller does it at the bottom of a fade (the motor's
    `cortar`), so the jump between filters is never heard.
    """

    def __init__(self, taps: np.ndarray) -> None:
        self.taps = np.asarray(taps, dtype=float)
        self._tail = np.zeros(len(self.taps) - 1)

    def set_taps(self, taps: np.ndarray) -> None:
        taps = np.asarray(taps, dtype=float)
        if len(taps) != len(self.taps):
            self._tail = np.zeros(len(taps) - 1)
        self.taps = taps

    def process(self, x: np.ndarray) -> np.ndarray:
        n = len(x)
        if n == 0:
            return np.zeros(0)
        size = 1 << int(np.ceil(np.log2(n + len(self.taps) - 1)))
        full = np.fft.irfft(np.fft.rfft(x, size) * np.fft.rfft(self.taps, size), size)[: n + len(self.taps) - 1]
        full[: len(self._tail)] += self._tail
        self._tail = full[n:].copy()
        return full[:n]
