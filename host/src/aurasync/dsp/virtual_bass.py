"""Psychoacoustic bass: the harmonics of the bass a small speaker cannot play.

Why (`docs/research/11-…` R3): a Go 4 high-passed at its cutoff (`dsp/crossover.py`) loses
the bass below it. The ear rebuilds a fundamental from its harmonics (the missing
fundamental), so adding the harmonics of that bass, inside the band the speaker plays,
gives back the pitch of the bass without the excursion.

**The method** is the non-linear device (NLD) of Larsen and Aarts (VERIFICADO in the text,
*MPCA-2002 Part I*, summarising JAES 50(3):147-164, 2002,
https://www.sps.tue.nl/rmaarts/RMA_papers/aar02n4.pdf), in the form Moliner, Rämö and
Välimäki use for its transient path (VERIFICADO, DAFx-20,
https://dafx2020.mdw.ac.at/proceedings/papers/DAFx2020_paper_40.pdf):

1. band-pass 20 Hz-`cutoff_hz` (LR4 slopes; the 20 Hz edge only keeps DC and rumble out,
   and Larsen-Aarts warn that past ~3 octaves the intermodulation "would become clearly
   audible");
2. a full-wave rectifier, which makes mostly the 2nd harmonic and is linear in amplitude:
   "the system should add the same amount of harmonics to the signal, independent of signal
   level" (so the knob means the same at any volume);
3. band-pass `cutoff_hz`-4 `cutoff_hz` (Moliner's band; LR8 at the low edge so the
   harmonics add as little as possible below the cutoff), and the gain `harmonics_db`.

The output is **only the harmonics**: the caller adds them to the high-passed path.

**What `harmonics_db` means**: the energy of the added harmonics relative to the energy of
the bass band they replace, for pink-spectrum material (the calibration runs on pink noise from 20 Hz;
music is roughly pink). 0 dB: as much energy as the bass that was removed. None is off.
With a 50 Hz sine, 0 dB gives a 2nd harmonic 0.7 dB above the sine (MEDIDO). With pink
noise, 100-400 Hz rises 0.9 / 2.7 / 6.5 dB at -6 / 0 / +6 dB, and the output stays 11-17 dB
under the input below 80 Hz (MEDIDO, `tests/test_virtual_bass.py`).

**Known limit** (MEDIDO, `tests/test_virtual_bass.py`): two bass notes at once produce
intermodulation at their sum (50 + 70 Hz -> 120 Hz) ~8 dB *above* their harmonics. It is
not an implementation flaw: |cos a + cos b| = 2 |cos((a+b)/2)| |cos((a-b)/2)|, and every
memoryless even non-linearity does the same. Moliner et al. send tonal content through a
phase vocoder instead for that reason, at the cost of tens of ms of latency. Some
listeners prefer no processing at all (Larsen-Aarts, 15 listeners): this is a knob, off by
default.

**Latency**: none on the main path. The harmonics come out of minimum-phase-like filters,
so they trail the bass they replace by the filters' group delay (~10 ms at 90 Hz), as in
any IIR NLD.
"""

from __future__ import annotations

import functools
from typing import Any

import numpy as np

from aurasync.dsp import backend, crossover
from aurasync.dsp.eq import PartitionedFIR, _RustOwned, _vector

LOW_EDGE_HZ = 20.0
"""The bottom of the band taken to make harmonics: only DC and rumble are left out."""
TOP_RATIO = 4.0
"""The harmonics are kept up to this times the cutoff (Moliner et al.: fc to 4 fc)."""
_CALIBRATION_SAMPLES = 1 << 18


def _fft_convolve(x: np.ndarray, h: np.ndarray) -> np.ndarray:
    size = 1 << int(np.ceil(np.log2(len(x) + len(h) - 1)))
    return np.fft.irfft(np.fft.rfft(x, size) * np.fft.rfft(h, size), size)[: len(x)]


@functools.lru_cache(maxsize=16)
def _filters(sr: int, cutoff_hz: float) -> tuple[np.ndarray, np.ndarray, float]:
    """(bass band FIR, harmonics band FIR, gain that makes 0 dB mean 'same energy').

    'Same energy' as what an LR4 high-pass at the cutoff removes, for pink noise from 20 Hz.
    """
    if not LOW_EDGE_HZ * 1.5 < cutoff_hz < sr / 2 / TOP_RATIO:
        msg = f"cutoff {cutoff_hz} Hz is outside ({LOW_EDGE_HZ * 1.5}, {sr / 2 / TOP_RATIO}) Hz"
        raise ValueError(msg)
    low, _ = crossover.impulses(sr, cutoff_hz, 4)
    _, rumble = crossover.impulses(sr, LOW_EDGE_HZ, 4)
    band = np.convolve(low, rumble)
    _, above = crossover.impulses(sr, cutoff_hz, 8)
    top, _ = crossover.impulses(sr, TOP_RATIO * cutoff_hz, 4)
    out = np.convolve(above, top)
    rng = np.random.default_rng(0)
    spec = np.fft.rfft(rng.standard_normal(_CALIBRATION_SAMPLES))
    f = np.fft.rfftfreq(_CALIBRATION_SAMPLES, 1 / sr)
    spec[f >= LOW_EDGE_HZ] /= np.sqrt(f[f >= LOW_EDGE_HZ])
    spec[f < LOW_EDGE_HZ] = 0
    pink = np.fft.irfft(spec, _CALIBRATION_SAMPLES)
    removed = _fft_convolve(pink, low)
    made = _fft_convolve(np.abs(_fft_convolve(pink, band)), out)
    skip = len(band) + len(out)
    gain = float(np.sqrt(np.sum(removed[skip:] ** 2) / np.sum(made[skip:] ** 2)))
    return band, out, gain


def _gain(harmonics_db: float | None) -> float:
    if harmonics_db is None or not np.isfinite(harmonics_db):
        return 0.0
    return float(10 ** (harmonics_db / 20))


def harmonics(x: np.ndarray, sr: int, cutoff_hz: float = 90.0, harmonics_db: float | None = 0.0) -> np.ndarray:
    """The harmonics for a whole signal at once (the reference for `VirtualBass`)."""
    band, out, calibration = _filters(sr, float(cutoff_hz))
    x = np.asarray(x, dtype=float)
    return _fft_convolve(np.abs(_fft_convolve(x, band)), out) * calibration * _gain(harmonics_db)


class VirtualBass(_RustOwned):
    """The NLD, by blocks. `process(x)` returns the harmonics to add to the high-passed path.

    `harmonics_db` can be changed live: the gain moves linearly across the next block. While
    it is off the filters are not run (and restart from rest when it comes back on).

    **The engine** (`dsp/backend.py`). With `engine=rust` the stage owns one Rust `VirtualBass`
    that holds the two partitioned filters itself, so a block is one call (band, rectifier,
    harmonics band, calibration, gain ramp). It is built on the first block that runs the filters
    (a stage whose harmonics stay off builds nothing and registers nothing). The numpy
    `_fir_band` / `_fir_out` stay as the numpy path's filters and as the place where the Rust
    state lands at a switch; they never build a Rust object of their own. The gain's position
    (`_current`) and `_dirty` live here, in both engines.
    """

    latency = 0

    def __init__(self, sr: int, cutoff_hz: float = 90.0, harmonics_db: float | None = None, block: int = 4096) -> None:
        self.sr, self.cutoff_hz, self.block = sr, float(cutoff_hz), block
        self._band, self._out, self._calibration = _filters(sr, self.cutoff_hz)
        self.harmonics_db = harmonics_db
        self._current = _gain(harmonics_db)
        self._dirty = True
        self._reset()
        self.added_db: float | None = None
        """Energy of the last block's harmonics relative to the bass band they came from (dB)."""

    def _reset(self) -> None:
        # Fresh filters are only needed after the old ones ran: while the harmonics stay off the
        # ones already there are at rest, and building two partitioned filters costs ~0.2 ms in
        # numpy (their partitions' spectra), which was paid on every block of the default chain.
        if not self._dirty:
            return
        self._dirty = False
        if self._rust is not None:
            # The Rust filters rest; the numpy ones are stale and are overwritten at a switch.
            # A torn object is not called again (`_restart` at the switch rebuilds everything).
            if not self._rust_broken:
                self._rust_call(lambda rust: rust.reset())
            return
        self._fir_band = PartitionedFIR(self._band, self.block)
        self._fir_out = PartitionedFIR(self._out, self.block)

    @property
    def memory_samples(self) -> int:
        """How much input from before a block its output depends on: the band filter, then the
        output filter after the rectifier."""
        return len(self._band) - 1 + len(self._out) - 1

    def process(self, x: np.ndarray) -> np.ndarray:
        x = np.asarray(x, dtype=float)
        n = len(x)
        target = _gain(self.harmonics_db)
        if target == 0.0 and self._current == 0.0:
            if self._rust is not None or self._rust_broken:
                self._ready()  # follows the engine; a stage that never ran Rust is not built here
            self._reset()
            self.added_db = None
            return np.zeros(n)
        if not self._ready():
            return np.zeros(n)  # a Rust failure: silence until the cut's bottom
        self._dirty = True
        if self._rust is not None:
            return self._process_rust(_vector(x), target)
        return self._process_numpy(x, target)

    def _process_rust(self, x: np.ndarray, target: float) -> np.ndarray:
        n, current = len(x), self._current

        def silence() -> tuple[np.ndarray, float, float]:
            self._broke()
            return np.zeros(n), 0.0, 0.0

        rust = self._rust
        made, e_bass, e_made = backend.guarded(lambda: rust.process(x, current, target), silence)
        if self._rust_broken:
            self.added_db = None
            return made
        if n:
            self._current = target
        self.added_db = 10 * np.log10(e_made / e_bass) if e_bass > 0 and e_made > 0 else None
        return made

    def _process_numpy(self, x: np.ndarray, target: float) -> np.ndarray:
        n = len(x)
        bass = self._fir_band.process(x)
        made = self._fir_out.process(np.abs(bass)) * self._calibration
        if n and target != self._current:
            ramp = self._current + (target - self._current) * np.arange(1, n + 1) / n
            made *= ramp
        else:
            made *= target
        if n:
            self._current = target
        e_bass, e_made = float(np.dot(bass, bass)), float(np.dot(made, made))
        self.added_db = 10 * np.log10(e_made / e_bass) if e_bass > 0 and e_made > 0 else None
        return made

    # -- the engine (dsp/backend.py) ---------------------------------------------------------

    def _build_rust(self) -> Any:
        rust = backend.module().VirtualBass(_vector(self._band), _vector(self._out), self._calibration, self.block)
        rust.set_state(self._numpy_state())
        return rust

    def _numpy_state(self) -> dict[str, Any]:
        return {"band": self._fir_band._numpy_state(), "out": self._fir_out._numpy_state()}  # noqa: SLF001

    def _load_state(self, state: dict[str, Any]) -> None:
        self._fir_band._load_state(state["band"])  # noqa: SLF001
        self._fir_out._load_state(state["out"])  # noqa: SLF001

    def _restart(self) -> None:
        """After a Rust failure: fresh filters and the gain where the level asks it to be."""
        self._current = _gain(self.harmonics_db)
        self._dirty = True
        self._reset()
