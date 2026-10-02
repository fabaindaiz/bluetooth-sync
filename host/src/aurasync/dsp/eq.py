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
TREBLE_CAP_FROM_HZ = 8000.0
"""`treble_cap_db` applies to the thirds centred here and above."""


def smooth(values: np.ndarray) -> np.ndarray:
    """Average each third with its neighbours (weights 1-2-1)."""
    padded = np.concatenate([values[:1], values, values[-1:]])
    return (padded[:-2] + 2 * padded[1:-1] + padded[2:]) / 4


def correction(
    response_db: list[float | None] | np.ndarray,
    previous_db: list[float] | None = None,
    band: tuple[float, float] = (100.0, 20000.0),
    budget_db: float | None = None,
    treble_cap_db: float | None = None,
    dead_band_db: float = DEAD_BAND_DB,
) -> np.ndarray:
    """The new EQ curve (dB per third, all >= 0) from a measured residual response.

    `previous_db` is the EQ the measurement was made through: the calibration measures what
    is left to correct, so the new curve is the previous one plus the inverse of the
    residual. `band` is where lifting is allowed (`profiles.boost_band`). Missing thirds
    (None) keep the previous value.

    `dead_band_db` (the chain's `eq.dead_band_db`): wanted corrections smaller than this are
    measurement noise and left out.

    Two optional limits, off by default (the curve is then exactly what it always was):

    - `treble_cap_db`: no third centred at or above `TREBLE_CAP_FROM_HZ` is lifted more
      than this. None is off; 0 means no treble lift at all.
    - `budget_db`: the total lift, measured as the energy rise of pink noise through the
      curve (`boost_energy_db`), is at most this. A curve over budget is scaled down as a
      whole (its shape kept), not clipped. None or 0 is off (the chain's "no budget").

    Why (INFERIDO, `docs/research/11-…` R5): the EQ lifts what the microphone hears as
    missing, and every lifted dB is headroom the limiter has to take back; a budget keeps
    the limiter a safety net, and the treble cap keeps a cheap microphone's roll-off from
    turning into hiss.
    """
    measured = np.array([np.nan if v is None else float(v) for v in response_db])
    previous = np.zeros(len(THIRDS)) if previous_db is None else np.maximum(0.0, np.asarray(previous_db, dtype=float))
    wanted = smooth(-np.where(np.isfinite(measured), measured, 0.0))
    wanted[np.abs(wanted) < dead_band_db] = 0.0
    inside = (band[0] / 2 ** (1 / 6) <= THIRDS) & (band[1] * 2 ** (1 / 6) >= THIRDS)
    curve = np.clip(previous + wanted, 0.0, np.where(inside, MAX_BOOST_DB, 0.0))
    if treble_cap_db is not None:
        curve = np.where(THIRDS >= TREBLE_CAP_FROM_HZ, np.minimum(curve, max(0.0, treble_cap_db)), curve)
    if budget_db and boost_energy_db(curve) > budget_db:
        curve = curve * _budget_scale(curve, budget_db)
    return np.round(curve, 2)


def limited(curve_db: list[float] | np.ndarray, budget_db: float | None, treble_cap_db: float | None) -> np.ndarray:
    """A stored curve with the treble cap and the budget applied (as `correction` does), for
    the curve that plays: the stored one is kept, so relaxing a limit gives it back."""
    curve = np.asarray(curve_db, dtype=float)
    if treble_cap_db is not None:
        curve = np.where(THIRDS >= TREBLE_CAP_FROM_HZ, np.minimum(curve, max(0.0, treble_cap_db)), curve)
    if budget_db and boost_energy_db(curve) > budget_db:
        curve = curve * _budget_scale(curve, budget_db)
    return curve


def boost_energy_db(curve_db: list[float] | np.ndarray) -> float:
    """How much a curve raises the energy of pink noise, in dB.

    Pink noise has the same energy in every third octave, so the rise is the mean of the
    thirds' power gains over the curve's band (50 Hz-20 kHz).
    """
    return float(10 * np.log10(np.mean(10 ** (np.asarray(curve_db, dtype=float) / 10))))


def _budget_scale(curve: np.ndarray, budget_db: float) -> float:
    """The factor in [0, 1] that brings the curve's pink-noise rise down to the budget."""
    low, high = 0.0, 1.0
    for _ in range(60):
        mid = (low + high) / 2
        if boost_energy_db(curve * mid) > budget_db:
            high = mid
        else:
            low = mid
    return low


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

    The taps' spectrum is computed once per FFT size and kept until the taps change: the
    output is bit for bit what recomputing it every block gave, for ~37 % less CPU (MEDIDO,
    `docs/research/11-…` R5).
    """

    def __init__(self, taps: np.ndarray) -> None:
        self._taps = np.asarray(taps, dtype=float)
        self._spectra: dict[int, np.ndarray] = {}
        self._tail = np.zeros(len(self._taps) - 1)

    @property
    def taps(self) -> np.ndarray:
        return self._taps

    @taps.setter
    def taps(self, taps: np.ndarray) -> None:
        self._taps = np.asarray(taps, dtype=float)
        self._spectra = {}

    def set_taps(self, taps: np.ndarray) -> None:
        taps = np.asarray(taps, dtype=float)
        if len(taps) != len(self._taps):
            self._tail = np.zeros(len(taps) - 1)
        self.taps = taps

    def _spectrum(self, size: int) -> np.ndarray:
        spectrum = self._spectra.get(size)
        if spectrum is None:
            spectrum = self._spectra[size] = np.fft.rfft(self._taps, size)
        return spectrum

    def process(self, x: np.ndarray) -> np.ndarray:
        n = len(x)
        if n == 0:
            return np.zeros(0)
        m = len(self._taps)
        size = 1 << int(np.ceil(np.log2(n + m - 1)))
        full = np.fft.irfft(np.fft.rfft(x, size) * self._spectrum(size), size)[: n + m - 1]
        full[: len(self._tail)] += self._tail
        self._tail = full[n:].copy()
        return full[:n]


class PartitionedFIR:
    """Uniform partitioned convolution (overlap-save), for long filters, with no latency.

    The filter is cut into partitions of `block` samples; each block of input costs one FFT
    pair of 2 * block plus one product per partition, against an FFT pair of the next power
    of two over block + taps for `StreamingFIR` (for 10 000 taps and 4096-sample blocks:
    8192 instead of 16 384). The first partition acts in the same block, so nothing is
    delayed. Blocks longer than `block` are cut; a shorter one (the last of a file) goes
    through an exact convolution from the kept history, after which the partitions'
    spectra are rebuilt from that history. The output is the same for any block size
    (within 1e-12).
    """

    def __init__(self, taps: np.ndarray, block: int = 4096) -> None:
        self.taps = np.asarray(taps, dtype=float)
        self.block = block
        parts = -(-len(self.taps) // block)
        padded = np.zeros(parts * block)
        padded[: len(self.taps)] = self.taps
        self._parts = np.fft.rfft(padded.reshape(parts, block), 2 * block, axis=1)
        self._history = np.zeros((parts + 1) * block)
        self._fdl = np.zeros_like(self._parts)
        self._head = 0
        self._fdl_valid = True
        self._spectra: dict[int, np.ndarray] = {}

    def skip(self, x: np.ndarray) -> None:
        """Take `x` as input without computing its output (the caller does not need it)."""
        self._push(np.asarray(x, dtype=float))
        self._fdl_valid = False

    def _push(self, x: np.ndarray) -> None:
        if len(x) >= len(self._history):
            self._history = x[-len(self._history) :].copy()
        else:
            self._history = np.concatenate([self._history[len(x) :], x])

    def _one(self, x: np.ndarray) -> np.ndarray:
        n, p = len(x), self.block
        self._push(x)
        parts = len(self._parts)
        if n == p:
            if self._fdl_valid:
                self._head = (self._head + 1) % parts
                self._fdl[self._head] = np.fft.rfft(self._history[-2 * p :])
            else:
                for k in range(parts):
                    end = len(self._history) - k * p
                    self._fdl[(self._head - k) % parts] = np.fft.rfft(self._history[end - 2 * p : end])
                self._fdl_valid = True
            order = (self._head - np.arange(parts)) % parts
            total = np.einsum("kf,kf->f", self._fdl[order], self._parts)
            return np.fft.irfft(total, 2 * p)[p:]
        self._fdl_valid = False
        span = min(len(self._history), len(self.taps) - 1 + n)
        size = 1 << int(np.ceil(np.log2(span + len(self.taps) - 1)))
        spectrum = self._spectra.get(size)
        if spectrum is None:
            spectrum = self._spectra[size] = np.fft.rfft(self.taps, size)
        out = np.fft.irfft(np.fft.rfft(self._history[-span:], size) * spectrum, size)
        return out[span - n : span]

    def process(self, x: np.ndarray) -> np.ndarray:
        x = np.asarray(x, dtype=float)
        n, p = len(x), self.block
        if n == 0:
            return np.zeros(0)
        if n <= p:
            return self._one(x)
        return np.concatenate([self._one(x[i : i + p]) for i in range(0, n, p)])
