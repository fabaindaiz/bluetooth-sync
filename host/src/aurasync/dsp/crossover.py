"""A Linkwitz-Riley crossover: the bass out of the small speakers, and into a bigger one.

Why (`docs/research/11-…` R1): the Go 4 reproduces from ~90-100 Hz (maker: 90 Hz,
REPORTADO in `dsp/profiles.py`). What it gets below that is excursion that gives no sound
and, by the listeners' account, triggers its bass protection (REPORTADO: the Go 4 DSP cuts
bass above ~50 % volume; INFERIDO that the excursion is what triggers it). A high-pass on
its path removes that (`protect`); with a bass-capable speaker in the room, the low-passed
sum of L+R goes to it (`crossover`).

**The filter** (VERIFICADO, Linkwitz, https://www.linkwitzlab.com/filters.htm): a
Linkwitz-Riley of order 2n is a Butterworth of order n applied twice. Its two outputs are
"360 degrees offset in phase at all frequencies", -6 dB at the crossover, and they sum to
an all-pass: |LP + HP| = 1 everywhere. Because they are in phase, no output needs a delay to
match the other, so the crossover adds **no latency**: a transient's peak stays at sample 0.
What the all-pass does do is rotate the phase of the bass, a group delay of
`group_delay_dc_ms` at DC (4.5 ms for LR4 at 100 Hz) falling to ~0 well above the
crossover. It is the same on both outputs, so it never moves one speaker relative to another.
Linkwitz's own warning applies: the electrical sum is flat only "if the drivers are flat and
have wide overlap. This is seldom the case" — the acoustic crossover must be measured with
the microphone, not assumed.

**How it is built**: the Butterworth sections are designed by the bilinear transform with
the cutoff prewarped (so -6 dB lands exactly on `cutoff_hz`), evaluated on the unit circle,
and turned into their impulse response with an inverse FFT on a grid long enough that time
aliasing is negligible. That response, truncated where its tail is below -120 dB, is
applied as a causal FIR by FFT overlap-add (`eq.StreamingFIR`), so the output does not
depend on the block size. With numpy only, a recursive IIR would run sample by sample in
Python; the FIR costs ~0.03 ms per 4096-sample block (MEDIDO, Mac).

**Order** (MEDIDO, `tests/test_crossover.py`): LR4 (the default) at 100 Hz takes 28.3 dB of
pink noise's energy out of 20-60 Hz, but point by point it leaves -18.8 dB at 60 Hz and
-24.6 dB at 50 Hz (24 dB/octave, and 60 Hz is under an octave below 100). Where every
frequency under 60 Hz must be 24 dB down, LR8 at 100 Hz (-35.6 dB at 60 Hz; -46.9 dB of
pink energy) or LR4 at 118 Hz does it.
"""

from __future__ import annotations

import numpy as np

from aurasync.dsp.eq import StreamingFIR

ORDERS = (4, 8)
"""Linkwitz-Riley orders offered: LR4 (24 dB/octave) and LR8 (48 dB/octave)."""
TAIL_DB = -120.0
"""The FIR is truncated where the energy left in the impulse response's tail falls below this."""
_GRID = 1 << 18
"""FFT grid used to sample the IIR's response; its impulse response is time-aliased by this
many samples, where it has long decayed (the slowest case, LR8 at 20 Hz, decays by ~1e-30)."""


def _butterworth(n: int) -> np.ndarray:
    """Coefficients (highest power first) of the normalised Butterworth polynomial B(s), B(0) = 1."""
    k = np.arange(1, n + 1)
    poles = np.exp(1j * np.pi * (2 * k + n - 1) / (2 * n))
    return np.real(np.poly(poles))


def _check(sr: int, cutoff_hz: float, order: int) -> None:
    if order not in ORDERS:
        msg = f"order must be one of {ORDERS}, not {order}"
        raise ValueError(msg)
    if not 0 < cutoff_hz < sr / 2:
        msg = f"cutoff {cutoff_hz} Hz is outside (0, {sr / 2}) Hz"
        raise ValueError(msg)


def response(freqs: np.ndarray, sr: int, cutoff_hz: float, order: int = 4) -> tuple[np.ndarray, np.ndarray]:
    """The complex response (low-pass, high-pass) of the bilinear LR filter at `freqs`.

    The bilinear transform maps the digital frequency f to the analog s = j * 2 sr tan(pi f/sr);
    prewarping the cutoff the same way makes the normalised variable tan(pi f/sr)/tan(pi fc/sr).
    """
    _check(sr, cutoff_hz, order)
    poly = _butterworth(order // 2)
    s = 1j * np.tan(np.pi * np.asarray(freqs, dtype=float) / sr) / np.tan(np.pi * cutoff_hz / sr)
    b = np.polyval(poly, s)
    lp = 1 / b
    hp = s ** (order // 2) / b
    return lp**2, hp**2


def impulses(sr: int, cutoff_hz: float, order: int = 4, taps: int | None = None) -> tuple[np.ndarray, np.ndarray]:
    """The causal FIRs (low-pass, high-pass), truncated at `taps` or where the tail is < `TAIL_DB`."""
    lp_f, hp_f = response(np.fft.rfftfreq(_GRID, 1 / sr), sr, cutoff_hz, order)
    lp, hp = np.fft.irfft(lp_f, _GRID), np.fft.irfft(hp_f, _GRID)
    if taps is None:
        energy = lp**2 + hp**2
        tail = np.cumsum(energy[::-1])[::-1] / energy.sum()
        needed = int(np.argmax(tail < 10 ** (TAIL_DB / 10)))
        taps = max(256, 1 << int(np.ceil(np.log2(max(needed, 1)))))
    return lp[:taps].copy(), hp[:taps].copy()


def group_delay_dc_ms(cutoff_hz: float, order: int = 4, sr: int = 48000) -> float:
    """The all-pass's group delay at DC, in ms: the most the bass is late after LP + HP.

    The sum is B(-s)/B(s), whose group delay at DC is 2 b1 / wc, b1 being B's coefficient of s.
    """
    _check(sr, cutoff_hz, order)
    b1 = _butterworth(order // 2)[-2]
    fc_analog = sr / np.pi * np.tan(np.pi * cutoff_hz / sr)
    return float(2 * b1 / (2 * np.pi * fc_analog) * 1000)


class _Branch:
    """One output of the crossover, streamed by FFT overlap-add."""

    latency = 0
    """Samples of delay this adds: none (see the module's docstring for the all-pass phase)."""

    def __init__(self, sr: int, cutoff_hz: float, order: int, taps: int | None, *, high: bool) -> None:
        lp, hp = impulses(sr, cutoff_hz, order, taps)
        self.sr, self.cutoff_hz, self.order = sr, cutoff_hz, order
        self._fir = StreamingFIR(hp if high else lp)
        self.removed_db = 0.0
        """How much energy the last block lost through the filter, in dB (>= 0 for a high-pass)."""

    @property
    def taps(self) -> np.ndarray:
        return self._fir.taps

    def process(self, x: np.ndarray) -> np.ndarray:
        x = np.asarray(x, dtype=float)
        y = self._fir.process(x)
        e_in, e_out = float(np.dot(x, x)), float(np.dot(y, y))
        self.removed_db = 10 * np.log10(e_in / e_out) if e_in > 0 and e_out > 0 else 0.0
        return y


class HighPass(_Branch):
    """The high-pass output: what a small speaker keeps (`protect`, or its side of `crossover`)."""

    def __init__(self, sr: int, cutoff_hz: float = 100.0, order: int = 4, taps: int | None = None) -> None:
        super().__init__(sr, cutoff_hz, order, taps, high=True)


class LowPass(_Branch):
    """The low-pass output, in phase with `HighPass` of the same cutoff and order."""

    def __init__(self, sr: int, cutoff_hz: float = 100.0, order: int = 4, taps: int | None = None) -> None:
        super().__init__(sr, cutoff_hz, order, taps, high=False)


class BassFeed:
    """The bass speaker's extra feed: the low-passed mid, 0.5 * (L + R).

    The mid keeps the level of bass mixed to the centre (nearly all of it in today's music,
    INFERIDO) and is what the small speakers, high-passed at the same cutoff, no longer play.
    It is in phase with their `HighPass`, so the room gets LP + HP = an all-pass, provided the
    speakers are time-aligned (the calibration does that; at 100 Hz 1 ms is 36 degrees).
    """

    latency = 0

    def __init__(self, sr: int, cutoff_hz: float = 100.0, order: int = 4, taps: int | None = None) -> None:
        self._lp = LowPass(sr, cutoff_hz, order, taps)

    def process(self, left: np.ndarray, right: np.ndarray) -> np.ndarray:
        return self._lp.process(0.5 * (np.asarray(left, dtype=float) + np.asarray(right, dtype=float)))
