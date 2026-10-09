//! `StreamingFIR` and `PartitionedFIR`: `aurasync.dsp.eq`'s FFT convolutions (overlap-add with a
//! tail; uniform partitioned overlap-save).

use aurasync_dsp::Complex;
use aurasync_dsp::fir;
use numpy::PyArray1;
use pyo3::prelude::*;
use pyo3::types::PyDict;

use crate::convert::{float64_rows, float64_vector, item, matrix, owned, samples, taps_of, vector};
use crate::error::{EngineError, guard};
use crate::planted::Planted;

/// A [`fir::PartitionedState`] as numpy's `PartitionedFIR` keeps it: `history` (1-D), `fdl_re`
/// and `fdl_im` (`(partitions, bins)`), `head` (an int) and `fdl_valid` (a bool).
pub fn partitioned_dict<'py>(
    py: Python<'py>,
    state: &fir::PartitionedState,
    bins: usize,
) -> Result<Bound<'py, PyDict>, EngineError> {
    let re: Vec<Vec<f64>> = state
        .fdl
        .iter()
        .map(|row| row.iter().map(|z| z.re).collect())
        .collect();
    let im: Vec<Vec<f64>> = state
        .fdl
        .iter()
        .map(|row| row.iter().map(|z| z.im).collect())
        .collect();
    let out = PyDict::new(py);
    out.set_item("history", PyArray1::from_vec(py, state.history.clone()))?;
    out.set_item("fdl_re", matrix(py, &re, bins)?)?;
    out.set_item("fdl_im", matrix(py, &im, bins)?)?;
    out.set_item("head", state.head)?;
    out.set_item("fdl_valid", state.fdl_valid)?;
    Ok(out)
}

/// The inverse of [`partitioned_dict`]; a `ValueError` for a missing key or shapes that differ.
pub fn partitioned_state(state: &Bound<'_, PyDict>) -> Result<fir::PartitionedState, EngineError> {
    let re = float64_rows("fdl_re", &item(state, "fdl_re")?)?;
    let im = float64_rows("fdl_im", &item(state, "fdl_im")?)?;
    if re.len() != im.len() || re.iter().zip(&im).any(|(a, b)| a.len() != b.len()) {
        return Err(EngineError::Invalid(
            "state: fdl_re and fdl_im have different shapes".to_owned(),
        ));
    }
    Ok(fir::PartitionedState {
        history: vector(state, "history")?,
        fdl: re
            .iter()
            .zip(&im)
            .map(|(re, im)| {
                re.iter()
                    .zip(im)
                    .map(|(&re, &im)| Complex::new(re, im))
                    .collect()
            })
            .collect(),
        head: item(state, "head")?.extract()?,
        fdl_valid: item(state, "fdl_valid")?.extract()?,
    })
}

/// `aurasync.dsp.eq.StreamingFIR`'s work: the numpy filter owns one when the engine is Rust.
/// Every method's whole body runs inside `guard`: a panic is an `EnginePanic`, after which the
/// host never calls this object again (its state may be half-written).
#[pyclass(name = "StreamingFIR", module = "aurasync_engine")]
pub(crate) struct StreamingFir {
    inner: fir::StreamingFir,
    planted: Planted,
}

#[pymethods]
impl StreamingFir {
    /// A filter with `taps` (a 1-D float64 array, at least one tap) and a silent tail; the FFT
    /// size for blocks of `block` samples is built now (others on their first block).
    #[new]
    #[pyo3(signature = (taps, block = 4096))]
    fn new(taps: &Bound<'_, PyAny>, block: usize) -> PyResult<Self> {
        guard(|| {
            let mut inner = fir::StreamingFir::new(&taps_of(taps)?)?;
            inner.prepare(block);
            Ok(Self {
                inner,
                planted: Planted::default(),
            })
        })
    }

    /// numpy's `set_taps`: the tail is reset only when the length changes.
    fn set_taps(&mut self, taps: &Bound<'_, PyAny>) -> PyResult<()> {
        guard(|| {
            self.planted.check("StreamingFIR", "set_taps");
            self.inner.set_taps(&taps_of(taps)?)?;
            Ok(())
        })
    }

    /// numpy's `taps = ...`: the tail is kept as it is.
    fn replace_taps(&mut self, taps: &Bound<'_, PyAny>) -> PyResult<()> {
        guard(|| {
            self.planted.check("StreamingFIR", "replace_taps");
            self.inner.replace_taps(&taps_of(taps)?)?;
            Ok(())
        })
    }

    /// One block: a new float64 array of `len(x)` samples.
    fn process<'py>(
        &mut self,
        py: Python<'py>,
        x: &Bound<'py, PyAny>,
    ) -> PyResult<Bound<'py, PyArray1<f64>>> {
        guard(|| {
            self.planted.check("StreamingFIR", "process");
            let x = owned("x", x)?;
            let inner = &mut self.inner;
            // Without the interpreter while it filters (`convert::owned`): the input is a copy.
            let out = py.detach(|| {
                let mut out = vec![0.0; x.len()];
                inner.process(&x, &mut out).map(|()| out)
            })?;
            Ok(PyArray1::from_vec(py, out))
        })
    }

    /// The whole state, as numpy's filter keeps it: `{"taps": ..., "tail": ...}`.
    fn state<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
        guard(|| {
            let state = self.inner.to_state();
            let out = PyDict::new(py);
            out.set_item("taps", PyArray1::from_vec(py, state.taps))?;
            out.set_item("tail", PyArray1::from_vec(py, state.tail))?;
            Ok(out)
        })
    }

    /// Takes a state from numpy's filter (the keys of `state()`); a `ValueError` changes nothing.
    fn set_state(&mut self, state: &Bound<'_, PyDict>) -> PyResult<()> {
        guard(|| {
            self.planted.check("StreamingFIR", "set_state");
            let state = fir::StreamingState {
                taps: vector(state, "taps")?,
                tail: vector(state, "tail")?,
            };
            self.inner.set_state(&state)?;
            Ok(())
        })
    }

    /// Makes the next `process`, `set_taps`, `replace_taps` or `set_state` panic inside its
    /// guard: the tests of the filter's failure path. Only in builds with `test-panic`.
    #[cfg(feature = "test-panic")]
    fn _panic_next(&mut self) {
        self.planted.arm();
    }
}

/// `aurasync.dsp.eq.PartitionedFIR`'s work: the numpy filter owns one when the engine is Rust.
/// Every method's whole body runs inside `guard`: a panic is an `EnginePanic`, after which the
/// host never calls this object again (its state may be half-written).
#[pyclass(name = "PartitionedFIR", module = "aurasync_engine")]
pub(crate) struct PartitionedFir {
    inner: fir::PartitionedFir,
    planted: Planted,
}

#[pymethods]
impl PartitionedFir {
    /// A filter with `taps` (a 1-D float64 array, at least one tap) cut into partitions of
    /// `block` samples, at rest.
    #[new]
    fn new(taps: &Bound<'_, PyAny>, block: usize) -> PyResult<Self> {
        guard(|| {
            Ok(Self {
                inner: fir::PartitionedFir::new(&taps_of(taps)?, block)?,
                planted: Planted::default(),
            })
        })
    }

    /// One block (any length): a new float64 array of `len(x)` samples.
    fn process<'py>(
        &mut self,
        py: Python<'py>,
        x: &Bound<'py, PyAny>,
    ) -> PyResult<Bound<'py, PyArray1<f64>>> {
        guard(|| {
            self.planted.check("PartitionedFIR", "process");
            let x = owned("x", x)?;
            let inner = &mut self.inner;
            // Without the interpreter while it filters (`convert::owned`): the input is a copy.
            let out = py.detach(|| {
                let mut out = vec![0.0; x.len()];
                inner.process(&x, &mut out).map(|()| out)
            })?;
            Ok(PyArray1::from_vec(py, out))
        })
    }

    /// Takes `x` as input without computing its output (numpy's `skip`).
    fn skip(&mut self, x: &Bound<'_, PyAny>) -> PyResult<()> {
        guard(|| {
            self.planted.check("PartitionedFIR", "skip");
            let x = float64_vector("x", x)?;
            self.inner.skip(&samples(&x));
            Ok(())
        })
    }

    /// The whole state, as numpy's filter keeps it: `history` (1-D), `fdl_re` and `fdl_im`
    /// (`(partitions, block + 1)`), `head` (an int) and `fdl_valid` (a bool).
    fn state<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
        guard(|| partitioned_dict(py, &self.inner.to_state(), self.inner.block() + 1))
    }

    /// Takes a state from numpy's filter (the keys of `state()`). Every size is checked first;
    /// a `ValueError` changes nothing.
    fn set_state(&mut self, state: &Bound<'_, PyDict>) -> PyResult<()> {
        guard(|| {
            self.planted.check("PartitionedFIR", "set_state");
            self.inner.set_state(&partitioned_state(state)?)?;
            Ok(())
        })
    }

    /// Makes the next `process`, `skip` or `set_state` panic inside its guard: the tests of the
    /// filter's failure path. Only in builds with `test-panic`.
    #[cfg(feature = "test-panic")]
    fn _panic_next(&mut self) {
        self.planted.arm();
    }
}
