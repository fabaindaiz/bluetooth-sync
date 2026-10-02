"""What the music coming in looks like: mono or stereo, how wide, how balanced, how much of
the spectrum it carries, and whether it clips.

Everything is averaged over the last few seconds (exponential, `WINDOW_S`), so the panel
reads a steady description and not the last block. Silence is not averaged in: the
description stays the one of the last music heard, marked as stale.

- **correlation** of L and R, -1 to 1: 1 is mono (both channels the same), ~0.3-0.9 is a
  normal stereo mix, below 0 means much of it is out of phase between channels.
- **side/mid** in dB: how much of the energy is the difference between channels. Mono is
  below -40 dB; a typical mix is -15 to -6 dB.
- **balance** in dB, L minus R.
- **bandwidth**: the highest frequency the long-term spectrum keeps within `BANDWIDTH_DB`
  of its 1-4 kHz level. A lossy file or a Bluetooth source often stops at 15-16 kHz; a
  full-band source reaches 19-20 kHz.
- **clipped**: samples at full scale in the window.
"""

from __future__ import annotations

import numpy as np

WINDOW_S = 3.0
SILENCE_RMS = 10 ** (-60 / 20)
BANDWIDTH_DB = -40.0
FFT = 4096
MONO_CORRELATION = 0.98
MONO_SIDE_DB = -40.0
FULL_SCALE = 0.999
REFERENCE_HZ = (1000.0, 4000.0)
"""The band the spectrum is compared against, where any music has energy."""


class InputAnalyzer:
    def __init__(self, sr: int) -> None:
        self.sr = sr
        self._ll = self._rr = self._lr = 0.0
        self._spectrum = np.zeros(FFT // 2 + 1)
        self._clipped = 0.0
        self._peak = 0.0
        self._weight = 0.0
        self.silent = True

    def update(self, left: np.ndarray, right: np.ndarray) -> None:
        n = len(left)
        if n == 0:
            return
        ll, rr, lr = float(np.dot(left, left)) / n, float(np.dot(right, right)) / n, float(np.dot(left, right)) / n
        if max(ll, rr) < SILENCE_RMS**2:
            self.silent = True
            return
        self.silent = False
        a = float(np.exp(-n / (self.sr * WINDOW_S)))
        self._ll, self._rr, self._lr = a * self._ll + ll, a * self._rr + rr, a * self._lr + lr
        mid = (left + right) / 2
        # A short block gets a window of its own length: windowing a zero-padded block would
        # put a step inside the window and spread energy over the whole spectrum.
        frames = [mid[i : i + FFT] for i in range(0, n - FFT + 1, FFT)] or [mid[:FFT]]
        power = np.mean([np.abs(np.fft.rfft(f * np.hanning(len(f)), FFT)) ** 2 * FFT / len(f) for f in frames], axis=0)
        self._spectrum = a * self._spectrum + power
        clipped = int(np.count_nonzero(np.abs(left) >= FULL_SCALE) + np.count_nonzero(np.abs(right) >= FULL_SCALE))
        self._clipped = a * self._clipped + clipped
        self._peak = max(a * self._peak, float(np.max(np.abs(left))), float(np.max(np.abs(right))))
        self._weight = a * self._weight + 1.0

    def summary(self) -> dict | None:
        if self._weight == 0:
            return None
        ll, rr, lr = self._ll, self._rr, self._lr
        correlation = lr / np.sqrt(ll * rr) if ll > 0 and rr > 0 else 0.0
        mid = (ll + rr + 2 * lr) / 4
        side = (ll + rr - 2 * lr) / 4
        side_db = 10 * np.log10(max(side, 1e-20) / max(mid, 1e-20))
        balance_db = 10 * np.log10(max(ll, 1e-20) / max(rr, 1e-20))
        if correlation >= MONO_CORRELATION and side_db <= MONO_SIDE_DB:
            kind = "mono"
        elif correlation < 0:
            kind = "fuera de fase"
        elif abs(balance_db) > 20:  # noqa: PLR2004
            kind = "un solo canal"
        else:
            kind = "estéreo"
        return {
            "kind": kind,
            "correlation": round(float(correlation), 3),
            "side_db": round(float(side_db), 1),
            "balance_db": round(float(balance_db), 1),
            "bandwidth_hz": self._bandwidth(),
            "clipped": round(self._clipped),
            "peak_db": round(20 * np.log10(max(self._peak, 1e-6)), 1),
            "rms_db": round(10 * np.log10(max((ll + rr) / 2 / self._weight, 1e-12)), 1),
            "stale": self.silent,
        }

    def _bandwidth(self) -> int | None:
        f = np.fft.rfftfreq(FFT, 1 / self.sr)
        reference = self._spectrum[(f >= REFERENCE_HZ[0]) & (f <= REFERENCE_HZ[1])].mean()
        if reference <= 0:
            return None
        level = 10 * np.log10(self._spectrum / reference + 1e-30)
        # Smoothed over ~1/6 octave so a single quiet bin does not end the band.
        kernel = np.ones(9) / 9
        level = np.convolve(level, kernel, mode="same")
        above = np.nonzero((level > BANDWIDTH_DB) & (f > REFERENCE_HZ[1]))[0]
        return int(round(f[above.max()], -2)) if above.size else int(REFERENCE_HZ[1])
