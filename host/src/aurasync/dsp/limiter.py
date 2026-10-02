"""A peak limiter at the end of each speaker's chain.

The EQ only lifts (`dsp/eq.py`), so a loud passage can go past full scale. Clipping there
is the harshest distortion there is; this turns the gain down instead, sample-exact on
the way down (no overshoot, no look-ahead latency) and slowly on the way back up, so the
gain change is not heard as pumping.
"""

from __future__ import annotations

import numpy as np

CEILING = 10 ** (-1 / 20)
"""-1 dBFS: the resampler and the codec after it can add a little on top."""
RELEASE_S = 0.25
"""From full reduction back to unity."""


class PeakLimiter:
    def __init__(self, sr: int, ceiling: float = CEILING, release_s: float = RELEASE_S) -> None:
        self.ceiling = ceiling
        self.step = 1.0 / (release_s * sr)
        self.gain = 1.0

    @property
    def reduction_db(self) -> float:
        return -20 * np.log10(max(self.gain, 1e-9))

    def process(self, x: np.ndarray) -> np.ndarray:
        n = len(x)
        if n == 0:
            return x
        peak = np.abs(x)
        if self.gain >= 1.0 and peak.max() <= self.ceiling:
            return x
        needed = np.minimum(1.0, self.ceiling / np.maximum(peak, 1e-12))
        # g[k] = min(needed[k], g[k-1] + step): instant attack, linear release, carried
        # over from the previous block. Closed form: step*k + running min of (needed - step*k).
        k = np.arange(1, n + 1)
        start = self.gain
        gain = np.minimum(1.0, self.step * k + np.minimum(start, np.minimum.accumulate(needed - self.step * k)))
        gain = np.minimum(gain, needed)
        self.gain = float(gain[-1])
        return x * gain
