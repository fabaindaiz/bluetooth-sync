"""The Rust DSP of `aurasync-dsp` as a Python module (type stub).

Every array argument must be a 1-D float64 numpy array in native byte order (a strided view is
copied); anything else is a `TypeError`, nothing is converted silently. A refused configuration,
argument or state is a `ValueError` and changes nothing. A Rust panic is `EnginePanic`, after which
the object that raised it must not be used again.
"""

import numpy
from numpy.typing import NDArray

_Vector = NDArray[numpy.float64]
_Matrix = NDArray[numpy.float64]
_State = dict[str, _Vector | _Matrix | int | bool | list[int] | dict[str, object]]

class EngineError(RuntimeError):
    """The engine failed in a way that is not the caller's fault: a broken invariant of the
    extension. The host takes it (it is a `RuntimeError`) as the engine failing."""

class EnginePanic(EngineError):
    """A Rust panic, caught at the boundary. The object that raised it may be half-written and
    must not be used again."""

class Reader:
    """`aurasync.dsp.interpolation.read`, the band-limited read, as an object that owns its table
    and scratch buffers (the host keeps one)."""

    def __init__(self, max_block: int = 8192) -> None:
        """A reader for blocks of up to `max_block` positions (a larger block rebuilds it, once,
        for the next power of two, at least 8192)."""

    def read(self, data: _Vector, position: _Vector) -> _Vector:
        """`data` evaluated at each (fractional) `position`, band-limited: a new float64 array.

        A position without `HALF - 1` samples before it and `HALF` after it is a `ValueError`.
        """

def capabilities() -> dict[str, dict[str, float | int]]:
    """The constants each stage was built with, for the host to check against its own:
    `interpolation`, `spatial`, `ambience`, and the `version` of `fir`, `virtual_bass` and `api`.
    """

class SpatialUpmix:
    """`aurasync.dsp.spatial.SpatialUpmix`'s work. Speakers are indices, in the numpy stage's
    `names` order."""

    def __init__(self, speakers: int, sr: int, n_fft: int, hop: int, max_block: int = 8192) -> None:
        """A stage for `speakers` outputs at `sr` Hz, with an STFT of `n_fft` points and hop `hop`,
        its buffers sized for blocks of `max_block` (larger blocks grow them, once)."""

    def set_params(
        self,
        *,
        arc_deg: float,
        ambience: float,
        ambient_level_db: float,
        haas_ms: float,
        threshold: float,
        lam: float,
        front_intact: bool,
    ) -> None:
        """The knobs, live (`SpatialParams`'s fields, in its order)."""

    def set_layout(
        self,
        angles: list[float | None],
        ambient: list[bool],
        classic: list[tuple[float, float]] | None = None,
    ) -> None:
        """The layout, live: each speaker's angle (`None` without one), whether it is ambient, and
        the classic mix's `(pan, ambience)` pairs or `None`."""

    def process(self, left: _Vector, right: _Vector) -> tuple[_Matrix, _Matrix]:
        """One stereo block: `(direct, ambience)`, each a new `(speakers, len(left))` float64
        array."""

    def state(self) -> _State:
        """The whole state, as numpy's stage keeps it: a dict of float64 arrays (2-D per speaker),
        `haas_read` a list of ints and `emitted` an int."""

    def set_state(self, state: _State) -> None:
        """Takes a state from numpy's stage (the keys of `state()`). Every size is checked first;
        a `ValueError` changes nothing."""

class AmbienceExtractor:
    """`aurasync.dsp.ambience.Extractor`'s work: the mono ambience of a stereo stream."""

    def __init__(self, n_fft: int, hop: int, max_block: int = 8192) -> None:
        """An extractor with an STFT of `n_fft` points and hop `hop`, its buffers sized for blocks
        of `max_block` (larger blocks grow them, once)."""

    def set_params(
        self,
        *,
        lam: float,
        threshold: float,
        mu0: float,
        mu1: float,
        sigma: float,
        min_energy: float,
    ) -> None:
        """The knobs (`Parametros`'s `lam`, `umbral`, `mu0`, `mu1`, `sigma`, `energia_minima`)."""

    def reset(self) -> None:
        """Back to a new extractor's state (numpy's `reiniciar`); the params stay."""

    def process(self, left: _Vector, right: _Vector) -> _Vector:
        """One stereo block: its mono ambience, a new float64 array of `len(left)` samples."""

    def state(self) -> _State:
        """The whole state, as numpy's extractor keeps it: a dict of 1-D float64 arrays."""

    def set_state(self, state: _State) -> None:
        """Takes a state from numpy's extractor (the keys of `state()`). Every size is checked
        first; a `ValueError` changes nothing."""

class StreamingFIR:
    """`aurasync.dsp.eq.StreamingFIR`'s work: an FFT convolution by overlap-add with a tail."""

    def __init__(self, taps: _Vector, block: int = 4096) -> None:
        """A filter with `taps` (at least one tap) and a silent tail; the FFT size for blocks of
        `block` samples is built now (others on their first block)."""

    def set_taps(self, taps: _Vector) -> None:
        """numpy's `set_taps`: the tail is reset only when the length changes."""

    def replace_taps(self, taps: _Vector) -> None:
        """numpy's `taps = ...`: the tail is kept as it is."""

    def process(self, x: _Vector) -> _Vector:
        """One block: a new float64 array of `len(x)` samples."""

    def state(self) -> dict[str, _Vector]:
        """The whole state, as numpy's filter keeps it: `{"taps": ..., "tail": ...}`."""

    def set_state(self, state: dict[str, _Vector]) -> None:
        """Takes a state from numpy's filter (the keys of `state()`); a `ValueError` changes
        nothing."""

class PartitionedFIR:
    """`aurasync.dsp.eq.PartitionedFIR`'s work: a uniform partitioned overlap-save convolution."""

    def __init__(self, taps: _Vector, block: int) -> None:
        """A filter with `taps` (at least one tap) cut into partitions of `block` samples, at
        rest."""

    def process(self, x: _Vector) -> _Vector:
        """One block (any length): a new float64 array of `len(x)` samples."""

    def skip(self, x: _Vector) -> None:
        """Takes `x` as input without computing its output (numpy's `skip`)."""

    def state(self) -> _State:
        """The whole state, as numpy's filter keeps it: `history` (1-D), `fdl_re` and `fdl_im`
        (`(partitions, block + 1)`), `head` (an int) and `fdl_valid` (a bool)."""

    def set_state(self, state: _State) -> None:
        """Takes a state from numpy's filter (the keys of `state()`). Every size is checked first;
        a `ValueError` changes nothing."""

class VirtualBass:
    """`aurasync.dsp.virtual_bass.VirtualBass`'s per-block work. It owns the two partitioned
    filters, so a block is one call."""

    def __init__(self, band: _Vector, out: _Vector, calibration: float, block: int) -> None:
        """A generator for the bass band's `band` taps and the harmonics band's `out` taps (at
        least one tap each), the `calibration` gain, filters cut into partitions of `block`
        samples, at rest."""

    def process(self, x: _Vector, current: float, target: float) -> tuple[_Vector, float, float]:
        """One block with the gain going from `current` to `target` across it (constant when they
        are equal): `(harmonics, bass_energy, harmonics_energy)`, the harmonics a new float64
        array of `len(x)` samples."""

    def reset(self) -> None:
        """Both filters back at rest."""

    def state(self) -> dict[str, _State]:
        """The whole state, as numpy's stage keeps it: `{"band": ..., "out": ...}`, each the state
        of a numpy `PartitionedFIR` (`PartitionedFIR.state`'s keys)."""

    def set_state(self, state: dict[str, _State]) -> None:
        """Takes a state from numpy's stage (the keys of `state()`); a `ValueError` changes
        nothing."""
