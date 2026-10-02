"""A short diffuse tail, different on every speaker: envelopment from any material.

Why (`docs/research/11-…` R6, and research 09): the ambience extractor (`dsp/ambience.py`)
only finds the ambience a recording already has, and dry pop mixes have little. A short
tail that is **independent on every speaker** makes a diffuse field with any material:
the speakers stop agreeing with each other a few ms after each sound, which is what
envelops (low inter-channel coherence; research 09 §11).

**The method** (REPORTADO, Välimäki et al., *Fifty Years of Artificial Reverberation*, IEEE
TASLP 20(5), 2012): of the three families (delay networks, convolution, physical models),
convolution is the one that suits numpy; a feedback delay network recurses per sample. The
impulse response is synthetic:

- Gaussian noise, a different seed per speaker (so the tails are uncorrelated);
- an exponential decay reaching -60 dB at `rt60_s` (0.4-0.8 s suggested for a room that
  already reverberates, research 11 §4);
- a pre-delay `predelay_ms` (10-20 ms) so the tail does not smear the direct sound;
- damping: above `damping_hz` the tail dies faster, its RT60 scaled by damping_hz / f
  (octave bands crossfaded in log frequency), as air and furnishings do;
- a low cut (`LOW_CUT_HZ`): no tail in the bass;
- energy normalised to 1, so `level_db` is the tail's energy relative to its feed for a
  white feed (-12 to -20 dB suggested).

It is applied by **uniform partitioned convolution** (`eq.PartitionedFIR`, overlap-save),
with the partition equal to the block: each block costs one FFT pair plus one product per
partition, and the first partition is used in the same block, so the stage adds **no
latency**.

`process(feed)` returns **only the tail**; the caller adds it to the speaker's signal. The
spec feeds it the extracted ambience plus a fraction of the direct.
"""

from __future__ import annotations

import numpy as np

from aurasync.dsp.eq import PartitionedFIR

DAMPING_OCTAVES = 3
"""Octave bands above `damping_hz` that get their own, shorter, decay."""
LOW_CUT_HZ = 150.0
"""The tail has nothing below LOW_CUT_HZ / 2 and full level above LOW_CUT_HZ. Two reasons:
a small speaker gets no extra excursion from it, and in the low octaves (few bins per octave)
a delayed copy summed with the direct is a comb that colours by more than 1 dB (MEDIDO:
+1.05 dB in the 63 Hz octave at -11 dB without the cut)."""


def impulse_response(
    sr: int,
    seed: int,
    rt60_s: float = 0.6,
    predelay_ms: float = 15.0,
    damping_hz: float = 6000.0,
) -> np.ndarray:
    """The tail's impulse response: decaying noise after a pre-delay, energy 1."""
    if rt60_s <= 0:
        msg = f"rt60 must be positive, not {rt60_s}"
        raise ValueError(msg)
    pre = round(predelay_ms / 1000 * sr)
    length = round(rt60_s * sr)
    rng = np.random.default_rng(seed)
    spectrum = np.fft.rfft(rng.standard_normal(length))
    f = np.fft.rfftfreq(length, 1 / sr)
    t = np.arange(length) / sr
    # Bands centred at damping_hz * 2^k, crossfaded with raised cosines in log2(f): band 0 is
    # everything up to damping_hz and keeps rt60; band k has rt60 / 2^k.
    position = np.log2(np.maximum(f, 1.0) / damping_hz)
    tail = np.zeros(length)
    for k in range(DAMPING_OCTAVES + 1):
        distance = np.clip(np.abs(position - k), 0.0, 1.0)
        mask = 0.5 + 0.5 * np.cos(np.pi * distance)
        if k == 0:
            mask[position <= 0] = 1.0
        if k == DAMPING_OCTAVES:
            mask[position >= k] = 1.0
        decay = 10 ** (-3 * t / (rt60_s / 2**k))
        tail += np.fft.irfft(spectrum * mask, length) * decay
    low_cut = 0.5 - 0.5 * np.cos(np.pi * np.clip(np.log2(np.maximum(f, 1.0) / (LOW_CUT_HZ / 2)), 0.0, 1.0))
    tail = np.fft.irfft(np.fft.rfft(tail) * low_cut, length)
    h = np.concatenate([np.zeros(pre), tail])
    return h / np.sqrt(np.sum(h**2))


class Diffuse:
    """The tail of one speaker, by blocks. `process(feed)` returns the tail at `level_db`.

    `level_db` can be changed live: the gain moves linearly across the next block. None (or
    -inf) is off, and the convolution then is not run.
    """

    latency = 0

    def __init__(
        self,
        sr: int,
        seed: int,
        level_db: float | None = -16.0,
        rt60_s: float = 0.6,
        predelay_ms: float = 15.0,
        damping_hz: float = 6000.0,
        block: int = 4096,
    ) -> None:
        self.sr, self.seed, self.block = sr, seed, block
        self.ir = impulse_response(sr, seed, rt60_s, predelay_ms, damping_hz)
        self.level_db = level_db
        self._current = _gain(level_db)
        self._fir = PartitionedFIR(self.ir, block)

    def process(self, x: np.ndarray) -> np.ndarray:
        x = np.asarray(x, dtype=float)
        n = len(x)
        target = _gain(self.level_db)
        if n == 0:
            return np.zeros(0)
        if target == 0.0 and self._current == 0.0:
            self._fir.skip(x)
            return np.zeros(n)
        wet = self._fir.process(x)
        if target != self._current:
            wet *= self._current + (target - self._current) * np.arange(1, n + 1) / n
            self._current = target
        else:
            wet *= target
        return wet


def _gain(level_db: float | None) -> float:
    if level_db is None or not np.isfinite(level_db):
        return 0.0
    return float(10 ** (level_db / 20))


def tail(x: np.ndarray, sr: int, seed: int, level_db: float = -16.0, **ir_args: float) -> np.ndarray:
    """The tail of a whole signal at once (the reference for `Diffuse`)."""
    h = impulse_response(sr, seed, **ir_args)
    x = np.asarray(x, dtype=float)
    size = 1 << int(np.ceil(np.log2(len(x) + len(h) - 1)))
    return np.fft.irfft(np.fft.rfft(x, size) * np.fft.rfft(h, size), size)[: len(x)] * _gain(level_db)
