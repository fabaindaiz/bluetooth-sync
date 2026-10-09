//! `Reader`: `aurasync.dsp.interpolation.read`, the band-limited read, as an object that owns its
//! table and scratch buffers.

use aurasync_dsp::interpolation;
use numpy::PyArray1;
use pyo3::prelude::*;

use crate::convert::{float64_vector, samples};
use crate::error::guard;
use crate::planted::Planted;

/// The block a reader is built for by default: twice the service's 4096 samples. A larger block
/// replaces the table with one that fits (a few milliseconds, once).
const FIRST_MAX_BLOCK: usize = 8192;

/// The band-limited read. Its table costs about 70 000 kernel evaluations, too many to repeat per
/// block, so the host keeps one reader and calls `read` on it. Every method's whole body runs
/// inside `guard`: a panic is an `EnginePanic`, after which the host drops this object (its
/// scratch buffers may be half-written) and builds another.
#[pyclass(name = "Reader", module = "aurasync_engine")]
pub(crate) struct Reader {
    inner: interpolation::Reader,
    planted: Planted,
}

#[pymethods]
impl Reader {
    /// A reader for blocks of up to `max_block` positions (larger blocks rebuild it, once).
    #[new]
    #[pyo3(signature = (max_block = FIRST_MAX_BLOCK))]
    fn new(max_block: usize) -> PyResult<Self> {
        guard(|| {
            Ok(Self {
                inner: interpolation::Reader::new(max_block),
                planted: Planted::default(),
            })
        })
    }

    /// `data` evaluated at each (fractional) `position`, band-limited: a new float64 array.
    fn read<'py>(
        &mut self,
        py: Python<'py>,
        data: &Bound<'py, PyAny>,
        position: &Bound<'py, PyAny>,
    ) -> PyResult<Bound<'py, PyArray1<f64>>> {
        guard(|| {
            self.planted.check("Reader", "read");
            let data = float64_vector("data", data)?;
            let position = float64_vector("position", position)?;
            let data = samples(&data);
            let position = samples(&position);
            if position.len() > self.inner.max_block() {
                self.inner = interpolation::Reader::new(
                    position
                        .len()
                        .checked_next_power_of_two()
                        .unwrap_or(position.len())
                        .max(FIRST_MAX_BLOCK),
                );
            }
            let mut out = vec![0.0; position.len()];
            self.inner.read(&data, &position, &mut out)?;
            Ok(PyArray1::from_vec(py, out))
        })
    }

    /// Makes the next `read` panic inside its guard: the tests of the reader's failure path.
    /// Only in builds with `test-panic`.
    #[cfg(feature = "test-panic")]
    fn _panic_next(&mut self) {
        self.planted.arm();
    }
}
