//! `aurasync_engine`: the Rust DSP of `aurasync-dsp` as a Python module.
//!
//! - `Reader`: `aurasync.dsp.interpolation.read`, the band-limited read, within 1e-9 of numpy
//!   (`host/tests/test_engine_rust.py`). `Reader().read(data, position)` owns its table and
//!   scratch buffers (the host keeps one); both arguments must be 1-D float64 numpy arrays; a strided view is copied first, any other dtype is a `TypeError` (never converted
//!   silently). A position without `HALF - 1` samples before it and `HALF` after it is a
//!   `ValueError`.
//! - `SpatialUpmix`: `aurasync.dsp.spatial.SpatialUpmix`'s work in Rust (the spatial and "frente
//!   intacto" renders), within 1e-9 of numpy (`host/tests/test_spatial_rust.py`). The numpy stage
//!   owns one when the engine is Rust, and moves its state in and out (`state`, `set_state`) at a
//!   cut's bottom. Inputs are 1-D float64 arrays (a strided view is copied); `process` gives two
//!   new `(speakers, block)` float64 arrays, the direct and the ambience blocks.
//! - `AmbienceExtractor`: `aurasync.dsp.ambience.Extractor`'s work in Rust (the mono ambience of
//!   a stereo stream), within 1e-9 of numpy (`host/tests/test_ambience_rust.py`). The numpy
//!   extractor owns one when the engine is Rust, and moves its state in and out (`state`,
//!   `set_state`) at a cut's bottom. Inputs are 1-D float64 arrays (a strided view is copied);
//!   `process` gives a new 1-D float64 array as long as the block.
//! - `StreamingFIR` and `PartitionedFIR`: `aurasync.dsp.eq`'s FFT convolutions in Rust (overlap-add
//!   with a tail; uniform partitioned overlap-save), within 1e-9 of numpy
//!   (`host/tests/test_eq_rust.py`). The numpy filters own one when the engine is Rust (so the EQ,
//!   the crossover, the bass protection, the virtual bass and the diffuse tail all get it), and
//!   move their state in and out (`state`, `set_state`) at a cut's bottom. Inputs are 1-D float64
//!   arrays (a strided view is copied); `process` gives a new 1-D float64 array as long as the
//!   block.
//! - `VirtualBass`: `aurasync.dsp.virtual_bass.VirtualBass`'s per-block work in Rust (the bass
//!   band, the rectifier, the harmonics band, the calibration and the gain's ramp), within 1e-9
//!   of numpy (`host/tests/test_virtual_bass_rust.py`). It owns its two partitioned filters
//!   directly, so a block is one call: `process(x, current, target)` gives the harmonics and the
//!   two energies. The numpy stage owns one when the engine is Rust and moves the filters'
//!   states in and out (`state`, `set_state`, `reset`) at a cut's bottom.
//! - `capabilities()`: the constants each stage was built with, for the host to check against its
//!   own.
//!
//! A panic never reaches Python as `PyO3`'s `PanicException`, which derives from `BaseException`
//! and that the service's loop would not catch: the whole body of every exported function (the
//! argument checks, the read and building the result) runs inside `guard`, which catches it and
//! raises `EnginePanic`. The module's exceptions (see `error`): `EngineError(RuntimeError)`, the
//! base, for a broken internal invariant, and `EnginePanic(EngineError)`, a caught panic; a
//! refused configuration or state stays `ValueError` and a wrong argument type `TypeError`. The
//! host's dispatcher (`aurasync/dsp/backend.py`) takes a `RuntimeError` as the engine failing:
//! that block is silence and numpy reads from the next cut.
//!
//! The source is one file per concern: `error` (the error type and the exceptions), `convert`
//! (Python values to Rust ones), `planted` (the `test-panic` switch), `reader` (`Reader`),
//! `capabilities`, and one file per class (`spatial`, `ambience`, `fir`, `virtual_bass`). The
//! module itself is declared below, and `aurasync_engine.pyi` is its type stub.
#![forbid(unsafe_code)]

mod ambience;
mod capabilities;
mod convert;
mod error;
mod fir;
mod planted;
mod reader;
mod spatial;
mod virtual_bass;

/// The Python module `aurasync_engine`.
#[pyo3::pymodule]
mod aurasync_engine {
    #[pymodule_export]
    use super::ambience::AmbienceExtractor;
    #[pymodule_export]
    use super::capabilities::capabilities;
    #[pymodule_export]
    use super::error::exceptions::{EngineError, EnginePanic};
    #[pymodule_export]
    use super::fir::{PartitionedFir, StreamingFir};
    #[pymodule_export]
    #[cfg(feature = "test-panic")]
    #[pymodule_export]
    use super::planted::_panic_outside_the_read;
    #[pymodule_export]
    use super::reader::Reader;
    #[pymodule_export]
    use super::spatial::SpatialUpmix;
    #[pymodule_export]
    use super::virtual_bass::VirtualBass;
}
