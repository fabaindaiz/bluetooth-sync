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

**Coherence per third (2026-10-02)**, so that the panel can fade the stretches it should not
trust, as Smaart's *coherence blanking* does. `response_with_coherence` gives, per third:

- `coherence`: the magnitude-squared coherence gamma^2 between the speaker's noise and the
  microphone (Welch, the same segments as the response), averaged over the band's bins. With
  N speakers playing equal independent noises it is about 1/N even when the response is right:
  for this speaker, the others are noise. So gamma^2 alone is not a threshold that works for every
  N;
- `error_db`: the random error (1sigma, dB) of that third's level, from gamma^2 and the number of
  averages, `8.69 · sqrt(1 - gamma^2) / (|gamma| · sqrt(2 · n))` (Bendat & Piersol, *Random Data*,
  §9.2: the normalised random error of the H₁ gain estimate), with n = segments x bins in the
  band x `INDEPENDENCE`. **This is the field to blank on**: it already accounts for N and for
  the length of the calibration. `tests/test_response.py` checks it against the spread of
  the same band over independent noises (SIMULADO).
"""

from __future__ import annotations

import numpy as np

SR = 48000
THIRDS = 1000 * 2 ** (np.arange(-13, 14) / 3)
"""Third-octave centres, 50 Hz to 20 kHz."""
SEGMENT = 16384
USABLE_DB = -10.0


INDEPENDENCE = 0.5
"""The share of the segments x bins of a band that count as independent averages: the Hann
segments overlap by half and neighbouring bins of a Hann window are correlated. Calibrated
against simulation in `tests/test_response.py` (the predicted error within a factor 2 of the
spread over independent noises)."""
SILENT_SEGMENT = 1e-20
"""A segment of the reference with less energy than this is not averaged: a speaker of the
other group of a group calibration is silent there (`group_calibration.py`)."""


def _spectra(recording: np.ndarray, reference: np.ndarray, lag: int):
    """Welch sums over the reference's segments: (cross, auto ref, auto mic, segments)."""
    mic = recording[max(0, lag) : max(0, lag) + len(reference)]
    ref = reference[: len(mic)]
    window = np.hanning(SEGMENT)
    cross, auto, auto_mic, count = 0.0, 0.0, 0.0, 0
    for i in range(0, len(ref) - SEGMENT, SEGMENT // 2):
        x = np.fft.rfft(ref[i : i + SEGMENT] * window)
        power = np.abs(x) ** 2
        if power.sum() <= SILENT_SEGMENT:
            continue
        y = np.fft.rfft(mic[i : i + SEGMENT] * window)
        cross = cross + y * np.conj(x)
        auto = auto + power
        auto_mic = auto_mic + np.abs(y) ** 2
        count += 1
    return cross, auto, auto_mic, count


def _bands(f: np.ndarray) -> list[np.ndarray]:
    return [(f >= c / 2 ** (1 / 6)) & (f < c * 2 ** (1 / 6)) for c in THIRDS]


def _response(cross, auto, f: np.ndarray) -> np.ndarray:
    magnitude = np.abs(cross) / (auto + 1e-20)
    bands = np.array([10 * np.log10(np.mean(magnitude[b] ** 2) + 1e-20) for b in _bands(f)])
    return bands - np.median(bands[(THIRDS > 400) & (THIRDS < 2500)])  # noqa: PLR2004


def response_bands(recording: np.ndarray, reference: np.ndarray, lag: int, sr: int = SR) -> np.ndarray:
    """Third-octave response in dB, normalised to the 400 Hz-2.5 kHz median.

    `lag` is where the reference starts in the recording, in samples.
    """
    cross, auto, _, _ = _spectra(recording, reference, lag)
    if np.isscalar(cross):
        return np.full(len(THIRDS), np.nan)
    return _response(cross, auto, np.fft.rfftfreq(SEGMENT, 1 / sr))


def response_with_coherence(
    recording: np.ndarray, reference: np.ndarray, lag: int, sr: int = SR
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """`(response_db, coherence, error_db)` per third (module doc). NaN where there is no data."""
    cross, auto, auto_mic, count = _spectra(recording, reference, lag)
    if np.isscalar(cross):
        empty = np.full(len(THIRDS), np.nan)
        return empty, empty.copy(), empty.copy()
    f = np.fft.rfftfreq(SEGMENT, 1 / sr)
    gamma2 = np.abs(cross) ** 2 / (auto * auto_mic + 1e-30)
    coherence, error = np.full(len(THIRDS), np.nan), np.full(len(THIRDS), np.nan)
    for k, b in enumerate(_bands(f)):
        if not b.any():
            continue
        g = float(np.clip(np.mean(gamma2[b]), 0.0, 1.0))
        coherence[k] = g
        n = max(1.0, count * int(b.sum()) * INDEPENDENCE)
        error[k] = 20 / np.log(10) * np.sqrt(1 - g) / (np.sqrt(g) * np.sqrt(2 * n)) if g > 0 else np.inf
    return _response(cross, auto, f), coherence, error


def usable_band(bands: np.ndarray) -> tuple[float | None, float | None]:
    """The lowest and highest third-octave within `USABLE_DB` of the mid band."""
    ok = np.isfinite(bands) & (bands > USABLE_DB)
    if not ok.any():
        return None, None
    return float(THIRDS[ok].min()), float(THIRDS[ok].max())
