"""Per-speaker equalisation, designed from the response the calibration measures.

The calibration gives each speaker's third-octave response at the microphone
(`dsp/response.py`). The EQ **only lifts, never cuts** (the listener's call, 2026-10-01: a
first version that cut to a flat target took 18 dB out of the Go 4's bass and left the music
dull and quiet):

- what the speaker does strongly (the Go 4's bass hump) is part of how it sounds, and stays;
- a dip inside the band the speaker can reproduce is lifted, up to `MAX_BOOST_DB`;
- the measurement is read **optimistically**: the microphone is assumed to hear less than
  the speaker gives, so the band that may be lifted is the maker's (`dsp/profiles.py`) or
  the measured one widened by an octave;
- smoothed across neighbouring thirds (a single curve is ±2 dB of noise), and corrections
  under `DEAD_BAND_DB` are left out.

Lifting can push a loud passage past full scale; the limiter at the end of each chain
(`dsp/limiter.py`) takes care of it.

The filter is linear-phase and the same length for every speaker, so it delays all of
them by the same `LATENCY_SAMPLES` and never moves one relative to another. With no
correction it is a delayed impulse: switching EQ on or off keeps the timing.

**Engine** (spec rust-engine §5, `dsp/backend.py`): with `engine=rust` `StreamingFIR` and
`PartitionedFIR` hand their work to `aurasync_engine.StreamingFIR` / `PartitionedFIR`
(engine/crates/aurasync-dsp/src/fir.rs), within 1e-9 of this code, which stays the oracle
(tests/test_eq_rust.py). Every user (the EQ, the crossover, the bass protection and its
all-pass, the virtual bass, the diffuse tail) gets Rust with no change. A filter builds its Rust
object on its first block (one built and never run costs nothing: `VirtualBass` only rebuilds its
filters after they ran, so its off state builds none), registers then, and moves its state into it, or back, when
the engine switches at a cut's bottom (`on_engine_switch`): the tail and the taps; the history,
the delay line, its head and whether it is valid. The move is exact. After a Rust failure every
filter gives silence until the cut's bottom, then numpy; the one that failed starts afresh (its
Rust state may be torn).
"""

from __future__ import annotations

from typing import Any

import numpy as np

from aurasync.dsp import backend
from aurasync.dsp.response import THIRDS

SR = 48000
TAPS = 2048
LATENCY_SAMPLES = (TAPS - 1) // 2
MAX_BOOST_DB = 6.0
DEAD_BAND_DB = 1.0
"""Corrections smaller than this are measurement noise and are left out."""
TREBLE_CAP_FROM_HZ = 8000.0
"""`treble_cap_db` applies to the thirds centred here and above."""


def smooth(values: np.ndarray) -> np.ndarray:
    """Average each third with its neighbours (weights 1-2-1)."""
    padded = np.concatenate([values[:1], values, values[-1:]])
    return (padded[:-2] + 2 * padded[1:-1] + padded[2:]) / 4


def correction(
    response_db: list[float | None] | np.ndarray,
    previous_db: list[float] | None = None,
    band: tuple[float, float] = (100.0, 20000.0),
    budget_db: float | None = None,
    treble_cap_db: float | None = None,
    dead_band_db: float = DEAD_BAND_DB,
) -> np.ndarray:
    """The new EQ curve (dB per third, all >= 0) from a measured residual response.

    `previous_db` is the EQ the measurement was made through: the calibration measures what
    is left to correct, so the new curve is the previous one plus the inverse of the
    residual. `band` is where lifting is allowed (`profiles.boost_band`). Missing thirds
    (None) keep the previous value.

    `dead_band_db` (the chain's `eq.dead_band_db`): wanted corrections smaller than this are
    measurement noise and left out.

    Two optional limits, off by default (the curve is then exactly what it always was):

    - `treble_cap_db`: no third centred at or above `TREBLE_CAP_FROM_HZ` is lifted more
      than this. None is off; 0 means no treble lift at all.
    - `budget_db`: the total lift, measured as the energy rise of pink noise through the
      curve (`boost_energy_db`), is at most this. A curve over budget is scaled down as a
      whole (its shape kept), not clipped. None or 0 is off (the chain's "no budget").

    Why (INFERIDO, `docs/research/11-…` R5): the EQ lifts what the microphone hears as
    missing, and every lifted dB is headroom the limiter has to take back; a budget keeps
    the limiter a safety net, and the treble cap keeps a cheap microphone's roll-off from
    turning into hiss.
    """
    measured = np.array([np.nan if v is None else float(v) for v in response_db])
    previous = np.zeros(len(THIRDS)) if previous_db is None else np.maximum(0.0, np.asarray(previous_db, dtype=float))
    wanted = smooth(-np.where(np.isfinite(measured), measured, 0.0))
    wanted[np.abs(wanted) < dead_band_db] = 0.0
    inside = (band[0] / 2 ** (1 / 6) <= THIRDS) & (band[1] * 2 ** (1 / 6) >= THIRDS)
    curve = np.clip(previous + wanted, 0.0, np.where(inside, MAX_BOOST_DB, 0.0))
    if treble_cap_db is not None:
        curve = np.where(THIRDS >= TREBLE_CAP_FROM_HZ, np.minimum(curve, max(0.0, treble_cap_db)), curve)
    if budget_db and boost_energy_db(curve) > budget_db:
        curve = curve * _budget_scale(curve, budget_db)
    return np.round(curve, 2)


def limited(curve_db: list[float] | np.ndarray, budget_db: float | None, treble_cap_db: float | None) -> np.ndarray:
    """A stored curve with the treble cap and the budget applied (as `correction` does), for
    the curve that plays: the stored one is kept, so relaxing a limit gives it back."""
    curve = np.asarray(curve_db, dtype=float)
    if treble_cap_db is not None:
        curve = np.where(THIRDS >= TREBLE_CAP_FROM_HZ, np.minimum(curve, max(0.0, treble_cap_db)), curve)
    if budget_db and boost_energy_db(curve) > budget_db:
        curve = curve * _budget_scale(curve, budget_db)
    return curve


def boost_energy_db(curve_db: list[float] | np.ndarray) -> float:
    """How much a curve raises the energy of pink noise, in dB.

    Pink noise has the same energy in every third octave, so the rise is the mean of the
    thirds' power gains over the curve's band (50 Hz-20 kHz).
    """
    return float(10 * np.log10(np.mean(10 ** (np.asarray(curve_db, dtype=float) / 10))))


def _budget_scale(curve: np.ndarray, budget_db: float) -> float:
    """The factor in [0, 1] that brings the curve's pink-noise rise down to the budget."""
    low, high = 0.0, 1.0
    for _ in range(60):
        mid = (low + high) / 2
        if boost_energy_db(curve * mid) > budget_db:
            high = mid
        else:
            low = mid
    return low


def fir(curve_db: list[float] | np.ndarray | None, taps: int = TAPS, sr: int = SR) -> np.ndarray:
    """A linear-phase FIR whose magnitude follows the curve (interpolated in log frequency)."""
    h = np.zeros(taps)
    if curve_db is None or not np.any(np.asarray(curve_db)):
        h[LATENCY_SAMPLES if taps == TAPS else (taps - 1) // 2] = 1.0
        return h
    n = 8 * taps
    f = np.fft.rfftfreq(n, 1 / sr)
    logf = np.log10(np.maximum(f, 1.0))
    gain_db = np.interp(logf, np.log10(THIRDS), np.asarray(curve_db, dtype=float))
    magnitude = 10 ** (gain_db / 20)
    impulse = np.fft.irfft(magnitude, n)
    centre = (taps - 1) // 2
    impulse = np.roll(impulse, centre)[:taps]
    return impulse * np.hanning(taps)


def response_of(h: np.ndarray, sr: int = SR) -> np.ndarray:
    """Third-octave magnitude in dB of a filter (for tests and the panel)."""
    n = 1 << 16
    f = np.fft.rfftfreq(n, 1 / sr)
    m = np.abs(np.fft.rfft(h, n)) ** 2
    return np.array(
        [10 * np.log10(np.mean(m[(f >= c / 2 ** (1 / 6)) & (f < c * 2 ** (1 / 6))]) + 1e-20) for c in THIRDS]
    )


def _vector(x: np.ndarray) -> np.ndarray:
    """What the Rust filters take: float64, C-contiguous (no copy when it already is)."""
    return np.ascontiguousarray(x, dtype=np.float64)


class _RustOwned:
    """What both filters share to own a Rust object (`dsp/backend.py`): following the engine,
    building, configuring and failing. A subclass gives `_build_rust`, `_numpy_state`,
    `_load_state` and `_restart`."""

    _rust: Any = None
    """The Rust filter while the engine is Rust; the numpy state is then stale until the switch
    back moves Rust's state into it."""
    _rust_broken = False
    """A Rust call failed: its state may be torn, so the next switch restarts in numpy."""
    _registered = False

    def on_engine_switch(self, _active: str) -> None:
        """`backend.use` at a cut's bottom: move the state to the engine that runs now."""
        self._follow(rust=backend.rust_active())

    def _ready(self) -> bool:
        """Before a block: follow the engine. False while the output must be silence (a Rust
        failure, until the cut's bottom)."""
        if backend.silent() is None:
            self._follow(rust=backend.rust_active())
        return backend.silent() is None

    def _follow(self, *, rust: bool) -> None:
        """Run on Rust (`rust`) or numpy from now on, moving the state across; after a failure,
        restart in numpy first."""
        if self._rust_broken:
            self._rust, self._rust_broken = None, False
            self._restart()
        if rust and self._rust is None:
            self._rust = backend.built(self._build_rust, lambda: None)
            if self._rust is not None and not self._registered:
                backend.register(self)
                self._registered = True
        elif not rust and self._rust is not None:
            # Numpy was chosen already: a failed state read is not a Rust failure to report (no
            # second cut); the filter just restarts in numpy.
            try:
                state = self._rust.state()
            except Exception as exc:  # noqa: BLE001 - any trouble reading the state restarts numpy
                backend.log_switch_failure(exc)
                state = None
            self._rust = None
            if state is None:
                self._restart()
            else:
                self._load_state(state)

    def _rust_call(self, call: Any) -> None:
        if self._rust_broken:
            return  # torn: never called again (lib.rs); the switch restarts in numpy
        rust = self._rust
        backend.built(lambda: call(rust), self._broke)

    def _rust_block(self, call: Any, n: int) -> np.ndarray:
        """A per-block Rust call; a Rust failure gives `n` samples of silence."""

        def silence() -> np.ndarray:
            self._broke()
            return np.zeros(n)

        rust = self._rust
        return backend.guarded(lambda: call(rust), silence)

    def _broke(self) -> None:
        self._rust_broken = True

    def _build_rust(self) -> Any:
        raise NotImplementedError

    def _numpy_state(self) -> dict[str, Any]:
        raise NotImplementedError

    def _load_state(self, state: dict[str, Any]) -> None:
        raise NotImplementedError

    def _restart(self) -> None:
        raise NotImplementedError


class StreamingFIR(_RustOwned):
    """FFT overlap-add convolution that keeps state between blocks.

    `set_taps` swaps the filter; the caller does it at the bottom of a fade (the motor's
    `cortar`), so the jump between filters is never heard.

    The taps' spectrum is computed once per FFT size and kept until the taps change: the
    output is bit for bit what recomputing it every block gave, for ~37 % less CPU (MEDIDO,
    `docs/research/11-…` R5).
    """

    def __init__(self, taps: np.ndarray) -> None:
        self._taps = np.asarray(taps, dtype=float)
        self._spectra: dict[int, np.ndarray] = {}
        self._tail = np.zeros(len(self._taps) - 1)

    @property
    def taps(self) -> np.ndarray:
        return self._taps

    @taps.setter
    def taps(self, taps: np.ndarray) -> None:
        """New taps; the tail is kept as it is."""
        self._taps = np.asarray(taps, dtype=float)
        self._spectra = {}
        if self._rust is not None:
            self._rust_call(lambda rust: rust.replace_taps(_vector(self._taps)))

    def set_taps(self, taps: np.ndarray) -> None:
        """New taps; the tail is reset to silence only when the length changes."""
        taps = np.asarray(taps, dtype=float)
        if len(taps) != len(self._taps):
            self._tail = np.zeros(len(taps) - 1)
        self._taps = taps
        self._spectra = {}
        if self._rust is not None:
            self._rust_call(lambda rust: rust.set_taps(_vector(taps)))

    def _spectrum(self, size: int) -> np.ndarray:
        spectrum = self._spectra.get(size)
        if spectrum is None:
            spectrum = self._spectra[size] = np.fft.rfft(self._taps, size)
        return spectrum

    def process(self, x: np.ndarray) -> np.ndarray:
        n = len(x)
        if n == 0:
            return np.zeros(0)
        if not self._ready():
            return np.zeros(n)
        if self._rust is not None:
            x = _vector(x)
            return self._rust_block(lambda rust: rust.process(x), n)
        return self._process_numpy(x)

    def _process_numpy(self, x: np.ndarray) -> np.ndarray:
        n = len(x)
        m = len(self._taps)
        size = 1 << int(np.ceil(np.log2(n + m - 1)))
        full = np.fft.irfft(np.fft.rfft(x, size) * self._spectrum(size), size)[: n + m - 1]
        full[: len(self._tail)] += self._tail
        self._tail = full[n:].copy()
        return full[:n]

    # -- the engine (dsp/backend.py) ---------------------------------------------------------

    def _build_rust(self) -> Any:
        rust = backend.module().StreamingFIR(_vector(self._taps))
        rust.set_state(self._numpy_state())
        return rust

    def _numpy_state(self) -> dict[str, Any]:
        return {"taps": _vector(self._taps), "tail": _vector(self._tail)}

    def _load_state(self, state: dict[str, Any]) -> None:
        taps = np.array(state["taps"])
        if not np.array_equal(taps, self._taps):
            self._taps, self._spectra = taps, {}
        self._tail = np.array(state["tail"])

    def _restart(self) -> None:
        self._tail = np.zeros(len(self._taps) - 1)


class PartitionedFIR(_RustOwned):
    """Uniform partitioned convolution (overlap-save), for long filters, with no latency.

    The filter is cut into partitions of `block` samples; each block of input costs one FFT
    pair of 2 * block plus one product per partition, against an FFT pair of the next power
    of two over block + taps for `StreamingFIR` (for 10 000 taps and 4096-sample blocks:
    8192 instead of 16 384). The first partition acts in the same block, so nothing is
    delayed. Blocks longer than `block` are cut; a shorter one (the last of a file) goes
    through an exact convolution from the kept history, after which the partitions'
    spectra are rebuilt from that history. The output is the same for any block size
    (within 1e-12).
    """

    def __init__(self, taps: np.ndarray, block: int = 4096) -> None:
        self.taps = np.asarray(taps, dtype=float)
        self.block = block
        parts = -(-len(self.taps) // block)
        padded = np.zeros(parts * block)
        padded[: len(self.taps)] = self.taps
        self._parts = np.fft.rfft(padded.reshape(parts, block), 2 * block, axis=1)
        self._history = np.zeros((parts + 1) * block)
        self._fdl = np.zeros_like(self._parts)
        self._head = 0
        self._fdl_valid = True
        self._spectra: dict[int, np.ndarray] = {}

    def skip(self, x: np.ndarray) -> None:
        """Take `x` as input without computing its output (the caller does not need it)."""
        x = np.asarray(x, dtype=float)
        if not self._ready():
            return
        if self._rust is not None:
            x = _vector(x)
            rust = self._rust
            backend.guarded(lambda: rust.skip(x), self._broke)
            return
        self._skip_numpy(x)

    def _skip_numpy(self, x: np.ndarray) -> None:
        self._push(x)
        self._fdl_valid = False

    def _push(self, x: np.ndarray) -> None:
        if len(x) >= len(self._history):
            self._history = x[-len(self._history) :].copy()
        else:
            self._history = np.concatenate([self._history[len(x) :], x])

    def _one(self, x: np.ndarray) -> np.ndarray:
        n, p = len(x), self.block
        self._push(x)
        parts = len(self._parts)
        if n == p:
            if self._fdl_valid:
                self._head = (self._head + 1) % parts
                self._fdl[self._head] = np.fft.rfft(self._history[-2 * p :])
            else:
                for k in range(parts):
                    end = len(self._history) - k * p
                    self._fdl[(self._head - k) % parts] = np.fft.rfft(self._history[end - 2 * p : end])
                self._fdl_valid = True
            order = (self._head - np.arange(parts)) % parts
            total = np.einsum("kf,kf->f", self._fdl[order], self._parts)
            return np.fft.irfft(total, 2 * p)[p:]
        self._fdl_valid = False
        span = min(len(self._history), len(self.taps) - 1 + n)
        size = 1 << int(np.ceil(np.log2(span + len(self.taps) - 1)))
        spectrum = self._spectra.get(size)
        if spectrum is None:
            spectrum = self._spectra[size] = np.fft.rfft(self.taps, size)
        out = np.fft.irfft(np.fft.rfft(self._history[-span:], size) * spectrum, size)
        return out[span - n : span]

    def process(self, x: np.ndarray) -> np.ndarray:
        x = np.asarray(x, dtype=float)
        n = len(x)
        if n == 0:
            return np.zeros(0)
        if not self._ready():
            return np.zeros(n)
        if self._rust is not None:
            x = _vector(x)
            return self._rust_block(lambda rust: rust.process(x), n)
        return self._process_numpy(x)

    def _process_numpy(self, x: np.ndarray) -> np.ndarray:
        n, p = len(x), self.block
        if n <= p:
            return self._one(x)
        return np.concatenate([self._one(x[i : i + p]) for i in range(0, n, p)])

    # -- the engine (dsp/backend.py) ---------------------------------------------------------

    def _build_rust(self) -> Any:
        rust = backend.module().PartitionedFIR(_vector(self.taps), self.block)
        rust.set_state(self._numpy_state())
        return rust

    def _numpy_state(self) -> dict[str, Any]:
        return {
            "history": _vector(self._history),
            "fdl_re": _vector(self._fdl.real),
            "fdl_im": _vector(self._fdl.imag),
            "head": int(self._head),
            "fdl_valid": bool(self._fdl_valid),
        }

    def _load_state(self, state: dict[str, Any]) -> None:
        self._history = np.array(state["history"])
        self._fdl = np.empty(np.shape(state["fdl_re"]), dtype=complex)
        self._fdl.real = state["fdl_re"]
        self._fdl.imag = state["fdl_im"]
        self._head = int(state["head"])
        self._fdl_valid = bool(state["fdl_valid"])

    def _restart(self) -> None:
        self._history = np.zeros(len(self._history))
        self._fdl = np.zeros_like(self._parts)
        self._head = 0
        self._fdl_valid = True
