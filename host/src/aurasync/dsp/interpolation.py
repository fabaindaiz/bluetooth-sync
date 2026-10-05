"""Band-limited reading between samples, for the fractional delay line.

Linear interpolation is a low-pass filter whose depth depends on the fractional part: at
half a sample it takes 3.5 dB off 12.7 kHz (MEASURED offline on the live installation,
2026-10-01, `probes/11-calidad-de-la-cadena/cadena.py`), so each speaker lost a different
amount of treble depending on its delay. A Kaiser-windowed sinc of `2 * HALF` taps is flat
within 0.002 dB to 20 kHz at any fraction, and exact (a single 1) at integer positions.

It needs `HALF` samples after the reading point, so a line that uses it reads `HALF`
samples later: the same fixed latency for every speaker, which moves none relative to
another.

**Cost.** Written directly, the kernel evaluates a Bessel function for every sample and every
tap: 7.3 ms per 4096-sample block and speaker on the Mac (MEASURED 2026-10-02), a quarter of
the block's 85 ms with three speakers, and the engine missed its deadline under load. Two
things make it cheap without changing what it computes:

- **A still delay** (nearly always) has only a handful of distinct fractions in a block, so
  the weights are computed once per fraction with the same formula: the output is identical.
- **A moving delay** (a recalibration ramp) gets the kernel from a table sampled every
  1/`_STEPS` of a sample, read with 4-point Lagrange interpolation: within 1e-10 of the formula
  (`tests/test_interpolation.py`).

**Engine.** `read` dispatches through `dsp/backend.py`: this module's `read_numpy`, or the same
read in Rust (`aurasync_engine`, engine/crates/aurasync-engine), within 1e-9 of it
(`tests/test_engine_rust.py`). numpy is the default and the oracle.
"""

from __future__ import annotations

import numpy as np

# Imported both ways (backend reads this module's constants and `read_numpy`); each uses the
# other only inside functions, so either can be imported first.
from aurasync.dsp import backend

HALF = 16
BETA = 8.0
_OFFSETS = np.arange(-HALF + 1, HALF + 1)
_FEW = 64
"""Up to this many distinct fractions, the weights come straight from the formula."""
_STEPS = 2048
"""Table points per sample for a moving delay."""


def _kernel(t: np.ndarray) -> np.ndarray:
    """The windowed sinc at offsets `t` (not yet normalised)."""
    window = np.i0(BETA * np.sqrt(np.clip(1 - (t / HALF) ** 2, 0.0, 1.0))) / np.i0(BETA)
    return np.sinc(t) * window


# The kernel on t in [-HALF - 1, HALF + 1], with one extra point each side for the cubic.
_GRID_START = -HALF - 1.0
_TABLE = _kernel(_GRID_START + np.arange((2 * HALF + 2) * _STEPS + 1) / _STEPS)


def _kernel_from_table(frac: np.ndarray) -> np.ndarray:
    """`_kernel(frac - _OFFSETS)` for each fraction, by 4-point Lagrange interpolation in
    `_TABLE`. The offsets are whole samples, so the position between table points is the same
    for every tap of a sample: the four coefficients are computed once per sample.

    The error is about h⁴/24 · max|kernel⁗| · 9/16 with h = 1/`_STEPS`: ~1e-13 per tap,
    under 1e-11 on a full-scale output (MEASURED).
    """
    x = (frac - _GRID_START) * _STEPS
    base = np.floor(x).astype(int)
    u = (x - base)[:, None]
    k = base[:, None] - _OFFSETS[None, :] * _STEPS
    um1, up1, um2 = u - 1, u + 1, u - 2
    return (
        (-u * um1 * um2 / 6) * _TABLE[k - 1]
        + (up1 * um1 * um2 / 2) * _TABLE[k]
        - (up1 * u * um2 / 2) * _TABLE[k + 1]
        + (up1 * u * um1 / 6) * _TABLE[k + 2]
    )


def read(data: np.ndarray, position: np.ndarray) -> np.ndarray:
    """`data` evaluated at each (fractional) `position`, band-limited, by the active engine
    (`backend.read`: numpy's `read_numpy` or Rust's).

    Every position must have `HALF - 1` samples before and `HALF` after it in `data`.
    """
    return backend.read(data, position)


def read_numpy(data: np.ndarray, position: np.ndarray) -> np.ndarray:
    """`read` in numpy: the oracle the Rust port is held to."""
    i0 = np.floor(position).astype(int)
    frac = position - i0
    distinct, which = np.unique(frac, return_inverse=True)
    if len(distinct) <= _FEW:
        weights = _kernel(distinct[:, None] - _OFFSETS[None, :])
        weights /= weights.sum(axis=1, keepdims=True)
        weights = weights[which]
    else:
        weights = _kernel_from_table(frac)
        weights /= weights.sum(axis=1, keepdims=True)
    return np.sum(data[i0[:, None] + _OFFSETS[None, :]] * weights, axis=1)
