"""Each speaker's frequency response, from the same recording the calibration makes.

The calibration plays an independent pink noise through each speaker at once. Because the
noises are independent, the cross-spectrum of the microphone with one speaker's noise
isolates that speaker (the others average out as noise): its magnitude, divided by the
noise's own spectrum, is the speaker's response at the microphone's position — speaker,
codec, room and microphone together.

What it showed on 2026-10-01 (experimentos/10 §6): the three Go 4 are usable from ~100 Hz
to ~8-10 kHz (-10 dB), the same with the Bluetooth link free or congested, so the treble
roll-off is the speaker's, not SBC's.

**Uncertainty:** with a 5 s calibration the third-octaves vary up to ~2 dB (the other
speakers' noise acts as noise, and the low thirds have few FFT bins); longer calibrations
average it down. Read a single curve as ±2 dB.
"""

from __future__ import annotations

import numpy as np

SR = 48000
THIRDS = 1000 * 2 ** (np.arange(-13, 14) / 3)
"""Third-octave centres, 50 Hz to 20 kHz."""
SEGMENT = 16384
USABLE_DB = -10.0


def response_bands(recording: np.ndarray, reference: np.ndarray, lag: int, sr: int = SR) -> np.ndarray:
    """Third-octave response in dB, normalised to the 400 Hz-2.5 kHz median.

    `lag` is where the reference starts in the recording, in samples.
    """
    mic = recording[max(0, lag) : max(0, lag) + len(reference)]
    ref = reference[: len(mic)]
    window = np.hanning(SEGMENT)
    cross, auto = 0.0, 0.0
    for i in range(0, len(ref) - SEGMENT, SEGMENT // 2):
        x = np.fft.rfft(ref[i : i + SEGMENT] * window)
        y = np.fft.rfft(mic[i : i + SEGMENT] * window)
        cross = cross + y * np.conj(x)
        auto = auto + np.abs(x) ** 2
    if np.isscalar(cross):
        return np.full(len(THIRDS), np.nan)
    magnitude = np.abs(cross) / (auto + 1e-20)
    f = np.fft.rfftfreq(SEGMENT, 1 / sr)
    bands = np.array(
        [
            10 * np.log10(np.mean(magnitude[(f >= c / 2 ** (1 / 6)) & (f < c * 2 ** (1 / 6))] ** 2) + 1e-20)
            for c in THIRDS
        ]
    )
    return bands - np.median(bands[(THIRDS > 400) & (THIRDS < 2500)])  # noqa: PLR2004


def usable_band(bands: np.ndarray) -> tuple[float | None, float | None]:
    """The lowest and highest third-octave within `USABLE_DB` of the mid band."""
    ok = np.isfinite(bands) & (bands > USABLE_DB)
    if not ok.any():
        return None, None
    return float(THIRDS[ok].min()), float(THIRDS[ok].max())
