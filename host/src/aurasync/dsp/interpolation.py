"""Band-limited reading between samples, for the fractional delay line.

Linear interpolation is a low-pass filter whose depth depends on the fractional part: at
half a sample it takes 3.5 dB off 12.7 kHz (MEASURED offline on the live installation,
2026-10-01, `probes/11-calidad-de-la-cadena/cadena.py`), so each speaker lost a different
amount of treble depending on its delay. A Kaiser-windowed sinc of `2 * HALF` taps is flat
within 0.002 dB to 20 kHz at any fraction, and exact (a single 1) at integer positions.

It needs `HALF` samples after the reading point, so a line that uses it reads `HALF`
samples later: the same fixed latency for every speaker, which moves none relative to
another.
"""

from __future__ import annotations

import numpy as np

HALF = 16
BETA = 8.0
_OFFSETS = np.arange(-HALF + 1, HALF + 1)


def read(data: np.ndarray, position: np.ndarray) -> np.ndarray:
    """`data` evaluated at each (fractional) `position`, band-limited.

    Every position must have `HALF - 1` samples before and `HALF` after it in `data`.
    """
    i0 = np.floor(position).astype(int)
    frac = position - i0
    t = frac[:, None] - _OFFSETS[None, :]
    window = np.i0(BETA * np.sqrt(np.clip(1 - (t / HALF) ** 2, 0.0, 1.0))) / np.i0(BETA)
    weights = np.sinc(t) * window
    weights /= weights.sum(axis=1, keepdims=True)
    return np.sum(data[i0[:, None] + _OFFSETS[None, :]] * weights, axis=1)
