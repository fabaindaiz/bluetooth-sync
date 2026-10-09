"""A peak limiter at the end of each speaker's chain.

The EQ only lifts (`dsp/eq.py`), so a loud passage can go past full scale. Clipping there
is the harshest distortion there is; this turns the gain down instead, sample-exact on
the way down (no overshoot, no look-ahead latency) and slowly on the way back up, so the
gain change is not heard as pumping.

**The engine** (`dsp/backend.py`): `TruePeakLimiter`'s per-block work has a Rust port
(`aurasync_engine.TruePeakLimiter`), which the limiter owns when the engine is Rust; `PeakLimiter`
stays numpy in both engines.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

from aurasync.dsp import backend
from aurasync.dsp.eq import _RustOwned, _vector

CEILING = 10 ** (-1 / 20)
"""-1 dBFS: the resampler and the codec after it can add a little on top."""
RELEASE_S = 0.25
"""From full reduction back to unity."""
HOLD_MS = 15.0
"""`TruePeakLimiter`: how long the gain stays down after a peak before it releases. Longer
than half a period of the lowest bass worth keeping clean (34 Hz and up)."""
MARGIN_DB = 0.01
"""`TruePeakLimiter` aims this far under the ceiling: the gain moves while it acts, and the
oversampled product of a moving gain and the signal is not exactly the gain times the
oversampled signal (MEDIDO: up to 1e-4 dB over without it)."""
NEAR_CEILING = 0.25
"""`TruePeakLimiter`: a block whose samples all stay under this fraction of the ceiling
(-12 dB) is not oversampled; no point between two such samples reaches the ceiling
(an abrupt full-level onset overshoots its neighbours by ~6.4 dB at most, MEDIDO in
`tests/test_loudness.py`)."""


class PeakLimiter:
    """The engine's limiter of always (`limiter.peak`): instant attack, linear release.

    It stays numpy under either engine (`dsp/backend.py`): a closed form of a few vectorised
    operations per block, which costs next to nothing, so a Rust port would save nothing.
    """

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


class TruePeakLimiter(_RustOwned):
    """A look-ahead limiter on the true peak (4x oversampled), with a cosine attack.

    Why (`docs/research/11-…` R2, INFERIDO there): `PeakLimiter` changes the gain from one
    sample to the next exactly at the peaks, which is broadband amplitude modulation —
    distortion, most audible on loud bass, just where the EQ lifts — and it looks at sample
    peaks, missing the peaks between samples that a resampler or codec downstream rebuilds.
    BS.1770 defines the true peak by 4x oversampling and EBU R 128 sets "shall not exceed
    -1 dBTP" (VERIFICADO, https://tech.ebu.ch/docs/r/r128.pdf).

    How:

    1. **Detection**: every sample and the three points between it and each neighbour
       (`loudness.interpolation_kernels`, applied by a sliding product), with enough of the
       previous block kept that a peak on a block edge is seen whole.
    2. **Look-ahead**: the output is the input delayed by `latency` samples (`lookahead_ms`).
       The gain each peak needs is spread backwards over the attack: a running minimum over
       the attack length, then a raised-cosine (Hann) average of the same length. Since every
       average around a peak includes only values at or below what that peak needs, the gain
       at the peak is low enough, and it got there along a cosine. The interpolator needs
       `loudness.HALF_WIDTH` samples of the look-ahead, so the attack is the rest (2.7 ms
       of the 3 ms).
    3. **Release**: exponential towards unity, 1 - g shrinking by e every `release_ms`.
       After a peak the gain holds for `hold_ms` before releasing: a steady low tone
       (60 Hz: one peak every 8.3 ms) then sees a constant gain instead of a ripple at
       twice its frequency, which is the distortion this replaces.

    MEDIDO (`tests/test_limiter.py`, Mac, simulated): a 60 Hz sine 6 dB over the ceiling
    gets harmonics at -35 dB re the fundamental from `PeakLimiter`, -43 dB from this limiter
    without hold, and none measurable (< -150 dB) with the hold. On a corpus with peaks
    between samples, band-limited to 20 kHz, up to +11 dBTP in, the output's 4x peak is
    -1.01 dBTP by this interpolator and within 0.01 dB of the ceiling by an ideal one.

    The detection and smoothing are vectorised; the release, a recursion, becomes a running
    maximum in the log domain. Same input and same parameters give the same output whatever
    the block size (tested to 1e-9).

    **The engine** (`dsp/backend.py`). With `engine=rust` the limiter owns one Rust
    `TruePeakLimiter`, built on its first block with the design computed here (the look-ahead,
    attack and hold lengths and the interpolation kernels) and this limiter's state (`_x` and
    `gain`); every block is one call, which returns the output and the three metrics kept here.
    `configure` reaches it too. The state moves at an engine switch between blocks, so the
    output is the same samples (within 1e-9, `tests/test_limiter_rust.py`); after a Rust failure
    the block is silence and numpy starts again from a limiter at rest.
    """

    def __init__(
        self,
        sr: int,
        ceiling_db: float = -1.0,
        lookahead_ms: float = 3.0,
        release_ms: float = 250.0,
        hold_ms: float = HOLD_MS,
    ) -> None:
        from aurasync.dsp import loudness  # noqa: PLC0415 - loudness imports nothing from here

        self.sr = sr
        self._ceiling_db, self._release_ms = ceiling_db, release_ms
        self.ceiling = 10 ** (ceiling_db / 20)
        self._target = 10 ** ((ceiling_db - MARGIN_DB) / 20)
        self.latency = round(lookahead_ms / 1000 * sr)
        """Samples of delay, the same on every speaker: the look-ahead."""
        self._w = loudness.HALF_WIDTH
        self._attack = self.latency - self._w - 1
        if self._attack < 1:
            msg = f"look-ahead of {lookahead_ms} ms leaves no attack"
            raise ValueError(msg)
        self._hold = round(hold_ms / 1000 * sr)
        self._rate = 1.0 / (release_ms / 1000 * sr)
        self._kernels = np.ascontiguousarray(loudness.interpolation_kernels(self._w)[:, ::-1].T)
        weights = 0.5 - 0.5 * np.cos(2 * np.pi * np.arange(1, self._attack + 2) / (self._attack + 2))
        self._norm = float(weights.sum())
        self._theta = 2 * np.pi / (self._attack + 2)
        self._cos = self._sin = np.zeros(0)
        # Input kept from before the block: what the earliest output's smoothing reaches back to.
        self._keep = self.latency + self._attack + self._hold + self._w + 2
        self._x = np.zeros(self._keep)
        self.gain = 1.0
        self.max_reduction_db = 0.0
        """The deepest reduction in the last block, dB."""
        self.active_fraction = 0.0
        """Share of the last block's samples with the gain under unity."""

    def configure(self, ceiling_db: float | None = None, release_ms: float | None = None) -> None:
        """Change the live knobs (the chain's `limiter.ceiling_db` and `release_ms`)."""
        if ceiling_db is not None:
            self._ceiling_db = ceiling_db
            self.ceiling = 10 ** (ceiling_db / 20)
            self._target = 10 ** ((ceiling_db - MARGIN_DB) / 20)
        if release_ms is not None:
            self._release_ms = release_ms
            self._rate = 1.0 / (release_ms / 1000 * self.sr)
        if self._rust is not None:
            self._rust_call(lambda rust: rust.configure(ceiling_db=ceiling_db, release_ms=release_ms))

    @property
    def reduction_db(self) -> float:
        """The reduction on the last sample out, dB."""
        return -20 * np.log10(max(self.gain, 1e-9))

    def _needed(self, x: np.ndarray) -> np.ndarray:
        """The gain each of x[w : len - w] needs so that it and the points on both sides of it
        stay under the ceiling (w = the interpolator's half width)."""
        w = self._w
        mag = np.abs(x)
        if mag.max() < NEAR_CEILING * self._target:
            return np.ones(len(x) - 2 * w)
        # Window i is x[i .. i + 2w - 1]; its points lie between x[i + w - 1] and x[i + w].
        between = np.abs(self._kernels.T @ sliding_window_view(x, 2 * w).T).max(axis=0)
        peak = np.maximum(mag[w : len(x) - w], np.maximum(between[:-1], between[1:]))
        return np.minimum(1.0, self._target / np.maximum(peak, 1e-12))

    def process(self, x: np.ndarray) -> np.ndarray:
        x = np.asarray(x, dtype=float)
        n = len(x)
        if n == 0:
            return np.zeros(0)
        if not self._ready():
            self.max_reduction_db, self.active_fraction = 0.0, 0.0
            return np.zeros(n)  # a Rust failure: silence until the cut's bottom
        if self._rust is not None:
            return self._process_rust(_vector(x))
        return self._process_numpy(x)

    def _process_rust(self, x: np.ndarray) -> np.ndarray:
        n = len(x)

        def silence() -> tuple[np.ndarray, float, float, float]:
            self._broke()
            return np.zeros(n), self.gain, 0.0, 0.0

        rust = self._rust
        out, self.gain, self.max_reduction_db, self.active_fraction = backend.guarded(lambda: rust.process(x), silence)
        return out

    def _process_numpy(self, x: np.ndarray) -> np.ndarray:
        n = len(x)
        seg = np.concatenate([self._x, x])
        self._x = seg[-self._keep :]
        # Output j is seg[o + j] (the input `latency` samples ago). Its gain looks at the need
        # of seg[o + j - hold .. o + j + attack].
        o = self._keep - self.latency
        first = o - self._attack - self._hold
        needed = self._needed(seg[first - self._w : o + n + self._attack + self._w])
        if self.gain >= 1.0 and needed.min() >= 1.0:
            self.max_reduction_db, self.active_fraction = 0.0, 0.0
            return seg[o : o + n].copy()
        # held[i] = min(needed[i .. i + attack + hold]); output j sits at needed[j + attack + hold],
        # and the raised-cosine average of held[j .. j + attack] is its attack envelope.
        held = _running_min(needed, self._attack + self._hold + 1)
        smooth = self._hann_average(held)[:n] if held[: n + self._attack].min() < 1.0 else np.ones(n)
        smooth[smooth > 1.0 - 1e-12] = 1.0  # running sums leave 1 - 4e-16 where nothing is needed
        gain = self._release(np.minimum(smooth, 1.0))
        self.gain = float(gain[-1])
        self.max_reduction_db = float(-20 * np.log10(max(gain.min(), 1e-9)))
        self.active_fraction = float(np.mean(gain < 1.0 - 1e-9))
        return seg[o : o + n] * gain

    def _hann_average(self, h: np.ndarray) -> np.ndarray:
        """s[j] = sum_k w[k] h[j + k], w a raised cosine over attack + 1 taps (sums to 1).

        Done with running sums of h, h cos(theta m) and h sin(theta m), so it costs O(n) for
        any attack: the weight of h[m] in s[j] is 0.5 - 0.5 cos(theta (j + a + 1 - m)), and
        the cosine of a difference splits into those two products.
        """
        a = self._attack
        if len(self._cos) < len(h) + 1:
            angle = self._theta * np.arange(2 * (len(h) + 1))
            self._cos, self._sin = np.cos(angle), np.sin(angle)
        cos, sin = self._cos[: len(h)], self._sin[: len(h)]
        count = len(h) - a

        def window(values: np.ndarray) -> np.ndarray:
            c = np.concatenate([[0.0], np.cumsum(values)])
            return c[a + 1 : a + 1 + count] - c[:count]

        box, by_cos, by_sin = window(h), window(h * cos), window(h * sin)
        centre = slice(a + 1, a + 1 + count)
        cos_part = self._cos[centre] * by_cos + self._sin[centre] * by_sin
        return (0.5 * box - 0.5 * cos_part) / self._norm

    def _release(self, target: np.ndarray) -> np.ndarray:
        """g[k] = min(target[k], 1 - (1 - g[k-1]) e^{-rate}), from the last gain."""
        depth = 1.0 - target
        k = np.arange(1, len(target) + 1)
        with np.errstate(divide="ignore"):
            logs = np.log(depth) + k * self._rate
        start = np.log(1.0 - self.gain) if self.gain < 1.0 else -np.inf
        carried = np.maximum.accumulate(np.maximum(logs, start))
        return 1.0 - np.exp(carried - k * self._rate)

    # -- the engine (dsp/backend.py) ---------------------------------------------------------

    def _build_rust(self) -> Any:
        kernels = _vector(np.ascontiguousarray(self._kernels.T).ravel())
        rust = backend.module().TruePeakLimiter(
            kernels,
            self._w,
            self._ceiling_db,
            self.latency,
            self._attack,
            self._hold,
            self._release_ms,
            self.sr,
        )
        rust.set_state(self._numpy_state())
        return rust

    def _numpy_state(self) -> dict[str, Any]:
        return {"x": _vector(self._x), "gain": float(self.gain)}

    def _load_state(self, state: dict[str, Any]) -> None:
        self._x = np.array(state["x"])
        self.gain = float(state["gain"])

    def _restart(self) -> None:
        """After a Rust failure: a limiter at rest (silence kept, unity gain)."""
        self._x = np.zeros(self._keep)
        self.gain = 1.0


def _running_min(x: np.ndarray, width: int) -> np.ndarray:
    """out[i] = min(x[i : i + width]) for i in 0 .. len(x) - width (van Herk / Gil-Werman)."""
    count = len(x) - width + 1
    blocks = -(-len(x) // width)
    padded = np.full(blocks * width, np.inf)
    padded[: len(x)] = x
    grid = padded.reshape(blocks, width)
    prefix = np.minimum.accumulate(grid, axis=1).ravel()
    suffix = np.minimum.accumulate(grid[:, ::-1], axis=1)[:, ::-1].ravel()
    i = np.arange(count)
    return np.minimum(suffix[i], prefix[i + width - 1])
