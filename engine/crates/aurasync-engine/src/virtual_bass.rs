//! `VirtualBass`: `aurasync.dsp.virtual_bass.VirtualBass`'s per-block work (the bass band, the
//! rectifier, the harmonics band, the calibration and the gain's ramp).

use aurasync_dsp::fir::PartitionedState;
use aurasync_dsp::virtual_bass::{self, VirtualBassState};
use numpy::PyArray1;
use pyo3::prelude::*;
use pyo3::types::PyDict;

use crate::convert::{float64_vector, item, samples, taps_of};
use crate::error::{EngineError, guard};
use crate::fir::{partitioned_dict, partitioned_state};
use crate::planted::Planted;

/// `aurasync.dsp.virtual_bass.VirtualBass`'s per-block work: the numpy stage owns one when the
/// engine is Rust. It owns the two partitioned filters (one call per block). Every method's whole
/// body runs inside `guard`: a panic is an `EnginePanic`, after which the host never calls this
/// object again (its state may be half-written).
#[pyclass(name = "VirtualBass", module = "aurasync_engine")]
pub(crate) struct VirtualBass {
    inner: virtual_bass::VirtualBass,
    planted: Planted,
}

#[pymethods]
impl VirtualBass {
    /// A generator for the bass band's `band` taps and the harmonics band's `out` taps (1-D
    /// float64 arrays, at least one tap each), the `calibration` gain, filters cut into
    /// partitions of `block` samples, at rest.
    #[new]
    fn new(
        band: &Bound<'_, PyAny>,
        out: &Bound<'_, PyAny>,
        calibration: f64,
        block: usize,
    ) -> PyResult<Self> {
        guard(|| {
            Ok(Self {
                inner: virtual_bass::VirtualBass::new(
                    &taps_of(band)?,
                    &taps_of(out)?,
                    calibration,
                    block,
                )?,
                planted: Planted::default(),
            })
        })
    }

    /// One block with the gain going from `current` to `target` across it (constant when they are
    /// equal): `(harmonics, bass_energy, harmonics_energy)`, the harmonics a new float64 array of
    /// `len(x)` samples.
    fn process<'py>(
        &mut self,
        py: Python<'py>,
        x: &Bound<'py, PyAny>,
        current: f64,
        target: f64,
    ) -> PyResult<(Bound<'py, PyArray1<f64>>, f64, f64)> {
        guard(|| {
            self.planted.check("VirtualBass", "process");
            let x = float64_vector("x", x)?;
            let x = samples(&x);
            let mut out = vec![0.0; x.len()];
            let energies = self.inner.process(&x, current, target, &mut out)?;
            Ok((PyArray1::from_vec(py, out), energies.bass, energies.made))
        })
    }

    /// Both filters back at rest.
    fn reset(&mut self) -> PyResult<()> {
        guard(|| {
            self.planted.check("VirtualBass", "reset");
            self.inner.reset();
            Ok(())
        })
    }

    /// The whole state, as numpy's stage keeps it: `{"band": ..., "out": ...}`, each the state of
    /// a numpy `PartitionedFIR` (`PartitionedFIR.state`'s keys).
    fn state<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
        guard(|| {
            let state = self.inner.to_state();
            let bins = self.inner.block() + 1;
            let out = PyDict::new(py);
            out.set_item("band", partitioned_dict(py, &state.band, bins)?)?;
            out.set_item("out", partitioned_dict(py, &state.out, bins)?)?;
            Ok(out)
        })
    }

    /// Takes a state from numpy's stage (the keys of `state()`); a `ValueError` changes nothing.
    fn set_state(&mut self, state: &Bound<'_, PyDict>) -> PyResult<()> {
        guard(|| {
            self.planted.check("VirtualBass", "set_state");
            let part = |name: &str| -> Result<PartitionedState, EngineError> {
                let dict = item(state, name)?
                    .cast_into::<PyDict>()
                    .map_err(|_| EngineError::Type(format!("state: {name} must be a dict")))?;
                partitioned_state(&dict)
            };
            let state = VirtualBassState {
                band: part("band")?,
                out: part("out")?,
            };
            self.inner.set_state(&state)?;
            Ok(())
        })
    }

    /// Makes the next `process`, `reset` or `set_state` panic inside its guard: the tests of the
    /// stage's failure path. Only in builds with `test-panic`.
    #[cfg(feature = "test-panic")]
    fn _panic_next(&mut self) {
        self.planted.arm();
    }
}
