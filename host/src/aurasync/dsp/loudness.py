"""Loudness and true peak, after ITU-R BS.1770-5, for the quality strip and the A/B.

The digital chain is the reliable place to measure (exact reference, one clock, no room):
loudness in, loudness out, true peak and PSR say whether the chain kept the level and the
dynamics (`docs/research/11-…` §1; spec 2026-10-02 §6).

**The algorithm** (VERIFICADO, BS.1770-5, November 2023, and EBU R 128 / Tech 3341):

- K-weighting in two stages, a high shelf and a high-pass ("RLB"), with coefficients given
  for 48 kHz in tables 1 and 2 (`SHELF_48K`, `HIGHPASS_48K`);
- mean square per channel, summed with a weight per channel (here G = 1 for every feed: a
  listener who moves has no azimuth, INFERIDO in research 11 §1.4);
- loudness = -0.691 + 10 log10(sum); the constant cancels K's +0.691 dB at 997 Hz;
- momentary: 400 ms window; short-term: 3 s (Tech 3341);
- integrated: 400 ms blocks with 75 % overlap, an absolute gate at -70 LUFS and a relative
  gate 10 LU under the loudness of the blocks that passed the absolute one;
- true peak (Annex 2): 4x oversampling, absolute value, the maximum, in dBTP. The annex's
  12.04 dB attenuation exists for fixed-point headroom and is not needed in floating point.

**How it is computed here.** numpy has no IIR filter, and a sample-by-sample loop in Python
costs milliseconds per block, so K is applied in the frequency domain, as the spec allowed:
every 100 ms step (the gating step) is transformed once, and its power spectrum weighted by
|K(f)|^2 of the standard's biquads gives the step's K-weighted energy (Parseval). The
gating blocks, momentary and short-term are then sums of 4 or 30 steps, so nothing depends
on the block size the caller pushes. It differs from filtering in the time domain only by
the filter's memory at the step's edges and by spectral leakage: integrated loudness within
0.005 LU on the Tech 3341 signals and pink noise, -0.06 LU on a bass-heavy music-like
signal, single 100 ms steps within 0.6 dB (MEDIDO, `tests/test_loudness.py`, against the
IIR's impulse response applied as an FIR, `k_weighting_fir`). Sqrt-Hann frames of two
steps bring the bass-heavy case to 0.002 LU, at twice the FFT cost and 50 ms more delay
(MEDIDO, not adopted: the spec's tolerance is 0.1 LU). The true peak uses a Kaiser-windowed sinc
with 24 taps per phase (the annex's example uses 12; any filter meeting its response is
allowed).

Other sample rates use the analog prototype behind the tables (shelf 1681.97 Hz, +4.0 dB,
Q 0.7072; high-pass 38.135 Hz, Q 0.5003), as libebur128 does (REPORTADO,
https://github.com/jiixyj/libebur128); at 48 kHz it reproduces the tables within 1e-8.

**The engine** (`dsp/backend.py`). `LoudnessMeter`'s per-block work (the step energies and the
true peak) has a Rust port (`aurasync_engine.LoudnessMeter`), which the meter owns when the engine
is Rust; the readings, `GatedIntegrator` (on its own thread, one step at a time) and the design
stay numpy.

**PSR** (REPORTADO, MeterPlugs / Ian Shepherd, research 11 §1.5): true peak minus
short-term loudness, here the highest true peak of the last 3 s minus the short-term
loudness. It is the number against "aplanada": if the output's PSR falls under the input's,
the chain is flattening the music.
"""

from __future__ import annotations

import functools
import itertools
from collections import deque
from typing import Any

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

from aurasync.dsp import backend
from aurasync.dsp.eq import _RustOwned, _vector

SHELF_48K = (
    (1.53512485958697, -2.69169618940638, 1.19839281085285),
    (1.0, -1.69065929318241, 0.73248077421585),
)
"""BS.1770-5 table 1: the stage-1 shelving filter at 48 kHz, (b, a)."""
HIGHPASS_48K = (
    (1.0, -2.0, 1.0),
    (1.0, -1.99004745483398, 0.99007225036621),
)
"""BS.1770-5 table 2: the stage-2 high-pass at 48 kHz, (b, a)."""

OFFSET = -0.691
ABSOLUTE_GATE = -70.0
RELATIVE_GATE = -10.0
MOMENTARY_S = 0.4
SHORT_TERM_S = 3.0
STEP_S = 0.1
"""Gating blocks start every 100 ms (400 ms blocks with 75 % overlap)."""
OVERSAMPLING = 4
HALF_WIDTH = 12
"""The interpolator uses 2 * HALF_WIDTH input samples per output point (0.25 ms ahead at 48 kHz).
With `KAISER_BETA` it reproduces a sine's value between samples within 0.015 dB up to 20 kHz
(MEDIDO; 16 taps per side gain nothing audible and cost a third more)."""
KAISER_BETA = 6.0
NEAR_PEAK = 0.5
"""True peak: only positions next to a sample at least this fraction of the block's sample peak are interpolated."""
TAIL_DB = -120.0
_GRID = 1 << 18


def k_weighting_biquads(sr: int) -> tuple[tuple[np.ndarray, np.ndarray], tuple[np.ndarray, np.ndarray]]:
    """The two K-weighting biquads ((b, a) shelf, (b, a) high-pass) for any sample rate."""
    k = np.tan(np.pi * 1681.974450955533 / sr)
    q = 0.7071752369554196
    vh = 10 ** (3.999843853973347 / 20)
    vb = vh**0.4996667741545416
    a0 = 1 + k / q + k * k
    shelf = (
        np.array([(vh + vb * k / q + k * k) / a0, 2 * (k * k - vh) / a0, (vh - vb * k / q + k * k) / a0]),
        np.array([1.0, 2 * (k * k - 1) / a0, (1 - k / q + k * k) / a0]),
    )
    k = np.tan(np.pi * 38.13547087602444 / sr)
    q = 0.5003270373238773
    a0 = 1 + k / q + k * k
    high = (np.array([1.0, -2.0, 1.0]), np.array([1.0, 2 * (k * k - 1) / a0, (1 - k / q + k * k) / a0]))
    return shelf, high


def _biquad_response(b: np.ndarray, a: np.ndarray, z: np.ndarray) -> np.ndarray:
    return (b[0] + b[1] * z + b[2] * z * z) / (a[0] + a[1] * z + a[2] * z * z)


def k_weighting_response(freqs: np.ndarray, sr: int) -> np.ndarray:
    """K(f), complex, of the two biquads (the standard's tables at 48 kHz) at `freqs`."""
    if sr == 48000:  # noqa: PLR2004 - the rate the standard's tables are given for
        stages = [tuple(np.array(c) for c in SHELF_48K), tuple(np.array(c) for c in HIGHPASS_48K)]
    else:
        stages = list(k_weighting_biquads(sr))
    z = np.exp(-1j * 2 * np.pi * np.asarray(freqs, dtype=float) / sr)
    return _biquad_response(*stages[0], z) * _biquad_response(*stages[1], z)


def k_weighting_power(freqs: np.ndarray, sr: int) -> np.ndarray:
    """|K(f)|^2 at `freqs`: what the meter weights each step's power spectrum with."""
    return np.abs(k_weighting_response(freqs, sr)) ** 2


@functools.lru_cache(maxsize=8)
def k_weighting_fir(sr: int) -> np.ndarray:
    """K-weighting as a causal FIR (the IIR's impulse response, truncated at -120 dB).

    The time-domain reference the meter's frequency-domain weighting is checked against.
    """
    h = np.fft.irfft(k_weighting_response(np.fft.rfftfreq(_GRID, 1 / sr), sr), _GRID)
    energy = h**2
    tail = np.cumsum(energy[::-1])[::-1] / energy.sum()
    needed = int(np.argmax(tail < 10 ** (TAIL_DB / 10)))
    h = h[: 1 << int(np.ceil(np.log2(max(needed, 2))))].copy()
    h.flags.writeable = False
    return h


@functools.lru_cache(maxsize=4)
def interpolation_kernels(half_width: int = HALF_WIDTH) -> np.ndarray:
    """(3, 2 * half_width) kernels for the points 1/4, 2/4 and 3/4 of the way to the next sample.

    Row p, column m multiplies the sample at offset `half_width - m` from the current one; as a
    convolution, point p after sample j is `np.convolve(x, kernel[p])[j + half_width]`.
    """
    beta = KAISER_BETA
    kernels = np.empty((OVERSAMPLING - 1, 2 * half_width))
    m = np.arange(2 * half_width)
    offsets = half_width - m  # sample offsets i = half_width .. -half_width + 1
    for p in range(1, OVERSAMPLING):
        t = p / OVERSAMPLING - offsets  # distance from the point to each sample
        window = np.kaiser(4 * half_width + 1, beta)
        # The window centred on the point, sampled at the taps' distances.
        w = np.interp(t, np.linspace(-half_width, half_width, 4 * half_width + 1), window)
        kernel = np.sinc(t) * w
        kernels[p - 1] = kernel / kernel.sum()
    kernels.flags.writeable = False
    return kernels


def _lufs(mean_square: float) -> float:
    return OFFSET + 10 * np.log10(mean_square) if mean_square > 0 else -np.inf


def _db(x: float) -> float:
    return 20 * np.log10(x) if x > 0 else -np.inf


class LoudnessMeter(_RustOwned):
    """BS.1770-5 momentary, short-term and integrated loudness, true peak and PSR, by blocks.

    `push(block)` takes (n, channels) samples (or (n,) for one channel). The K-weighted energy
    is taken every 100 ms step (aligned to the start, so the block size does not matter):
    one FFT of the step per channel, its power spectrum weighted by |K(f)|^2 (Parseval).
    Momentary, short-term and the gating blocks are sums of 4 or 30 steps, so the readings
    move at 10 Hz (EBU Tech 3341 asks for at least 10 Hz). They are computed when read.

    **The engine** (`dsp/backend.py`). With `engine=rust` the meter owns one Rust `LoudnessMeter`,
    built on its first block with the design computed here (the step, `_k_power`, the weights and
    the kernels) and this meter's state (`_context` and `_pending`); each block is one call, which
    gives the block's true peak and the energies of the steps it completed, and everything kept
    from them (the steps, the peaks, the totals) stays here. The state moves at an engine switch
    between blocks, so the readings are the same (within 1e-9, `tests/test_loudness_rust.py`).
    A meter measures, it plays nothing: it never goes silent with the rest after a Rust failure.
    It measures in numpy from the failing block on, from a context and a pending step of zeros
    (the pending step keeps its length, so the steps stay aligned with the other meters').
    """

    def __init__(self, sr: int, channels: int, weights: list[float] | None = None, *, history: bool = True) -> None:
        """`history=False` keeps only the last 3 s of steps (what momentary and short-term read):
        fixed-length state for the engine thread, which never asks for `integrated`. The
        integrated loudness of a live stream is `GatedIntegrator`'s, fed apart."""
        self.sr, self.channels = sr, channels
        self.history = history
        self.weights = np.ones(channels) if weights is None else np.asarray(weights, dtype=float)
        self._kernels_ascending = np.ascontiguousarray(interpolation_kernels()[:, ::-1].T)
        self._step_n = round(STEP_S * sr)
        self._momentary_steps = round(MOMENTARY_S / STEP_S)
        self._short_steps = round(SHORT_TERM_S / STEP_S)
        f = np.fft.rfftfreq(self._step_n, 1 / sr)
        parseval = np.full(len(f), 2.0)
        parseval[0] = 1.0
        if self._step_n % 2 == 0:
            parseval[-1] = 1.0
        self._k_power = parseval * k_weighting_power(f, sr) / self._step_n
        self.reset()

    def reset(self) -> None:
        self._context = np.zeros((self.channels, 2 * HALF_WIDTH))
        self._pending = np.zeros((self.channels, 0))
        self._steps: list[float] | deque[float] = [] if self.history else deque(maxlen=self._short_steps)
        self.steps_total = 0
        """Steps completed since the start (or the last `reset`), kept or not."""
        self._total = 0
        self._peak = 0.0
        self._recent_peaks: deque[tuple[int, float]] = deque()
        if self._rust is not None:
            self._rust_call(lambda rust: rust.set_state(self._numpy_state()))

    def push(self, block: np.ndarray) -> None:
        frames = np.asarray(block, dtype=float)
        x = (frames.reshape(-1, 1) if frames.ndim == 1 else frames).T  # (channels, n)
        n = x.shape[1]
        if n == 0:
            return
        self._follow(rust=backend.rust_active())
        measured = self._measure_rust(frames, x.shape[0]) if self._rust is not None else None
        peak, new = self._measure_numpy(x) if measured is None else measured
        self._peak = max(self._peak, peak)
        self._total += n
        self._recent_peaks.append((self._total, peak))
        while self._recent_peaks and self._recent_peaks[0][0] <= self._total - self._short_steps * self._step_n:
            self._recent_peaks.popleft()
        if new:
            self._steps.extend(new)
            self.steps_total += len(new)

    def _measure_numpy(self, x: np.ndarray) -> tuple[float, list[float]]:
        """The block's true peak and the energies of the steps it completes, oldest first."""
        peak = self._true_peak(x)
        pending = np.concatenate([self._pending, x], axis=1)
        whole = pending.shape[1] // self._step_n
        new: list[float] = []
        if whole:
            steps = pending[:, : whole * self._step_n].reshape(self.channels, whole, self._step_n)
            power = np.abs(np.fft.rfft(steps, axis=2)) ** 2 @ self._k_power  # (channels, whole)
            new = (self.weights @ power).tolist()
        self._pending = pending[:, whole * self._step_n :]
        return peak, new

    def _measure_rust(self, frames: np.ndarray, channels: int) -> tuple[float, list[float]] | None:
        """`_measure_numpy` by the Rust meter; None when Rust failed (the meter is then numpy, from
        a restarted state, and this block is measured there)."""
        if channels != self.channels:
            msg = f"a block of {channels} channels for a meter of {self.channels}"
            raise ValueError(msg)
        rust = self._rust
        measured = backend.guarded(lambda: rust.push(_vector(frames).ravel()), self._broke)
        if measured is None:
            self._follow(rust=backend.rust_active())
        return measured

    def _true_peak(self, x: np.ndarray) -> float:
        """The highest 4x-oversampled value of the positions that now have samples on both sides.

        Only the positions next to a sample within 6 dB of the block's sample peak are
        interpolated: band-limited audio does not put a peak more than 6 dB above both of its
        neighbours (a sine's worst case is 3 dB, at fs/4), and checking all positions costs 3x
        more. `tests/test_loudness.py` checks this against interpolating every position.
        """
        seg = np.concatenate([self._context, x], axis=1)
        self._context = seg[:, -2 * HALF_WIDTH :]
        # Positions j (between seg[j] and seg[j+1]) that have HALF_WIDTH samples after them.
        start, stop = HALF_WIDTH - 1, seg.shape[1] - HALF_WIDTH
        mag = np.abs(seg)
        sample_peak = float(mag[:, start + 1 : stop + 1].max())
        if sample_peak == 0.0:
            return 0.0
        near = np.maximum(mag[:, start:stop], mag[:, start + 1 : stop + 1]) >= NEAR_PEAK * sample_peak
        rows, cols = np.nonzero(near)
        if len(cols) == 0:
            return sample_peak
        # Window of position j: seg[j - HALF_WIDTH + 1 .. j + HALF_WIDTH], oldest first.
        windows = sliding_window_view(seg, 2 * HALF_WIDTH, axis=1)[rows, cols + start - HALF_WIDTH + 1]
        between = windows @ self._kernels_ascending
        return max(sample_peak, float(np.abs(between).max()))

    # -- the engine (dsp/backend.py) ---------------------------------------------------------

    def _build_rust(self) -> Any:
        rust = backend.module().LoudnessMeter(
            kernels=_vector(self._kernels_ascending.T).ravel(),
            half_width=HALF_WIDTH,
            k_power=_vector(self._k_power),
            weights=_vector(self.weights),
            step_n=self._step_n,
        )
        rust.set_state(self._numpy_state())
        return rust

    def _numpy_state(self) -> dict[str, Any]:
        return {"context": _vector(self._context), "pending": _vector(self._pending)}

    def _load_state(self, state: dict[str, Any]) -> None:
        self._context = np.array(state["context"])
        self._pending = np.array(state["pending"])

    def _restart(self) -> None:
        """After a Rust failure: silence for context, and a pending step of zeros as long as the
        one lost (`_total % _step_n`: the steps start at the stream's start), so the steps stay
        on the same grid as the other meters' (`QualityMeter` sums them step by step)."""
        self._context = np.zeros((self.channels, 2 * HALF_WIDTH))
        self._pending = np.zeros((self.channels, self._total % self._step_n))

    def _mean(self, count: int) -> float:
        """Mean square of the last `count` steps (zeros before the start)."""
        return float(sum(itertools.islice(reversed(self._steps), count))) / (count * self._step_n)

    @property
    def step_energies(self) -> np.ndarray:
        """K-weighted energy (channel-weighted sum) of every completed 100 ms step so far
        (without `history`, of the last 3 s)."""
        return np.asarray(self._steps)

    def last_steps(self, count: int) -> list[float]:
        """The last `count` step energies, oldest first (at most what is kept)."""
        if count <= 0:
            return []
        return list(itertools.islice(reversed(self._steps), count))[::-1]

    @property
    def momentary(self) -> float:
        """Loudness of the last 400 ms, LUFS."""
        return _lufs(self._mean(self._momentary_steps)) if self._steps else -np.inf

    @property
    def short_term(self) -> float:
        """Loudness of the last 3 s, LUFS."""
        return _lufs(self._mean(self._short_steps)) if self._steps else -np.inf

    @property
    def integrated(self) -> float:
        """Gated loudness since the start (or the last `reset`), LUFS."""
        if not self.history:
            msg = "a meter without history has no integrated loudness: feed a GatedIntegrator"
            raise ValueError(msg)
        if len(self._steps) < self._momentary_steps:
            return -np.inf
        steps = np.asarray(self._steps)
        window = self._momentary_steps
        sums = np.convolve(steps, np.ones(window), mode="valid")
        blocks = sums / (window * self._step_n)
        with np.errstate(divide="ignore"):
            levels = OFFSET + 10 * np.log10(blocks)
        kept = blocks[levels > ABSOLUTE_GATE]
        if len(kept) == 0:
            return -np.inf
        relative = _lufs(float(kept.mean())) + RELATIVE_GATE
        kept = blocks[(levels > ABSOLUTE_GATE) & (levels > relative)]
        return _lufs(float(kept.mean()))

    @property
    def true_peak_dbtp(self) -> float:
        """The highest true peak since the start, dBTP."""
        return _db(self._peak)

    @property
    def true_peak_short_dbtp(self) -> float:
        """The highest true peak of the last 3 s, dBTP."""
        return _db(max((p for _, p in self._recent_peaks), default=0.0))

    @property
    def psr(self) -> float:
        """Peak to short-term loudness ratio, dB."""
        short = self.short_term
        return self.true_peak_short_dbtp - short if np.isfinite(short) else np.nan


BIN_DB = 0.01
"""Width of `GatedIntegrator`'s level bins. A gating block within one bin of the relative gate
can land on the wrong side of it: the error is far under the 0.1 LU the meter is read at."""
TOP_LUFS = 20.0
"""Gating blocks louder than this go in the top bin (a 0 dBFS square wave is +3 LUFS)."""


class GatedIntegrator:
    """BS.1770 integrated loudness of a stream, in fixed memory and fixed time per step.

    It is fed the 100 ms step energies (`LoudnessMeter.last_steps`, the channels already
    summed) and keeps a histogram of the 400 ms gating blocks above the absolute gate: their
    count and summed energy per `BIN_DB` of level. The relative gate is applied over the bins,
    so reading it costs the same at minute 1 and at hour 4, unlike `LoudnessMeter.integrated`,
    which goes over every step since the start.
    """

    def __init__(self, window: int, step_n: int) -> None:
        self.window, self.step_n = window, step_n
        self._recent: deque[float] = deque(maxlen=window)
        bins = int(np.ceil((TOP_LUFS - ABSOLUTE_GATE) / BIN_DB)) + 1
        self._count = np.zeros(bins)
        self._energy = np.zeros(bins)
        self._floor = ABSOLUTE_GATE + (np.arange(bins) + 0.5) * BIN_DB
        """Each bin's middle level, against which the relative gate is compared."""

    @property
    def nbytes(self) -> int:
        return self._count.nbytes + self._energy.nbytes + self._floor.nbytes

    def push(self, energy: float) -> None:
        self._recent.append(energy)
        if len(self._recent) < self.window:
            return
        block = sum(self._recent) / (self.window * self.step_n)
        if block <= 0:
            return
        level = OFFSET + 10 * np.log10(block)
        if level <= ABSOLUTE_GATE:
            return
        i = min(int((level - ABSOLUTE_GATE) / BIN_DB), len(self._count) - 1)
        self._count[i] += 1
        self._energy[i] += block

    @property
    def integrated(self) -> float:
        total = self._count.sum()
        if total == 0:
            return -np.inf
        relative = _lufs(float(self._energy.sum() / total)) + RELATIVE_GATE
        kept = self._floor > relative
        n = self._count[kept].sum()
        return _lufs(float(self._energy[kept].sum() / n)) if n else -np.inf


def integrated_lufs(x: np.ndarray, sr: int, weights: list[float] | None = None) -> float:
    """The integrated loudness of a whole signal ((n, channels) or (n,))."""
    x = np.asarray(x, dtype=float)
    meter = LoudnessMeter(sr, 1 if x.ndim == 1 else x.shape[1], weights)
    meter.push(x)
    return meter.integrated


def true_peak_dbtp(x: np.ndarray, sr: int) -> float:
    """The true peak of a whole signal, dBTP."""
    x = np.asarray(x, dtype=float)
    meter = LoudnessMeter(sr, 1 if x.ndim == 1 else x.shape[1])
    meter.push(x)
    meter.push(np.zeros((2 * HALF_WIDTH, meter.channels)))
    return meter.true_peak_dbtp
