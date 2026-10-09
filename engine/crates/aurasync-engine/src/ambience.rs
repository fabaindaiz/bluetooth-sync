//! `AmbienceExtractor`: `aurasync.dsp.ambience.Extractor`'s work (the mono ambience of a stereo
//! stream).

use aurasync_dsp::ambience;
use numpy::PyArray1;
use pyo3::prelude::*;
use pyo3::types::PyDict;

use crate::convert::{get_complex, owned, set_complex, vector};
use crate::error::guard;
use crate::planted::Planted;

/// `aurasync.dsp.ambience.Extractor`'s work: the numpy extractor owns one when the engine is
/// Rust. Every method's whole body runs inside `guard`: a panic is an `EnginePanic`, after which
/// the host never calls this object again (its state may be half-written).
#[pyclass(name = "AmbienceExtractor", module = "aurasync_engine")]
pub(crate) struct AmbienceExtractor {
    inner: ambience::Extractor,
    planted: Planted,
}

#[pymethods]
impl AmbienceExtractor {
    /// An extractor with an STFT of `n_fft` points and hop `hop`, its buffers sized for blocks of
    /// `max_block` (larger blocks grow them, once).
    #[new]
    #[pyo3(signature = (n_fft, hop, max_block = 8192))]
    fn new(n_fft: usize, hop: usize, max_block: usize) -> PyResult<Self> {
        guard(|| {
            Ok(Self {
                inner: ambience::Extractor::new(n_fft, hop, max_block)?,
                planted: Planted::default(),
            })
        })
    }

    /// The knobs (`Parametros`'s `lam`, `umbral`, `mu0`, `mu1`, `sigma`, `energia_minima`).
    #[pyo3(signature = (*, lam, threshold, mu0, mu1, sigma, min_energy))]
    fn set_params(
        &mut self,
        lam: f64,
        threshold: f64,
        mu0: f64,
        mu1: f64,
        sigma: f64,
        min_energy: f64,
    ) -> PyResult<()> {
        guard(|| {
            self.planted.check("AmbienceExtractor", "set_params");
            self.inner.set_params(ambience::Params {
                lam,
                threshold,
                mu0,
                mu1,
                sigma,
                min_energy,
            });
            Ok(())
        })
    }

    /// Back to a new extractor's state (numpy's `reiniciar`); the params stay.
    fn reset(&mut self) -> PyResult<()> {
        guard(|| {
            self.planted.check("AmbienceExtractor", "reset");
            self.inner.reset();
            Ok(())
        })
    }

    /// One stereo block: its mono ambience, a new float64 array of `len(left)` samples.
    fn process<'py>(
        &mut self,
        py: Python<'py>,
        left: &Bound<'py, PyAny>,
        right: &Bound<'py, PyAny>,
    ) -> PyResult<Bound<'py, PyArray1<f64>>> {
        guard(|| {
            self.planted.check("AmbienceExtractor", "process");
            let left = owned("left", left)?;
            let right = owned("right", right)?;
            let inner = &mut self.inner;
            // Without the interpreter while it works (`convert::owned`): the inputs are copies.
            let out = py.detach(|| {
                let mut out = vec![0.0; left.len()];
                inner.process(&left, &right, &mut out).map(|()| out)
            })?;
            Ok(PyArray1::from_vec(py, out))
        })
    }

    /// The whole state, as numpy's extractor keeps it: a dict of 1-D float64 arrays.
    fn state<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
        guard(|| {
            let state = self.inner.to_state();
            let out = PyDict::new(py);
            set_complex(py, &out, "acc12", &state.acc12)?;
            out.set_item("acc11", PyArray1::from_vec(py, state.acc11))?;
            out.set_item("acc22", PyArray1::from_vec(py, state.acc22))?;
            out.set_item("pending_left", PyArray1::from_vec(py, state.pending_left))?;
            out.set_item("pending_right", PyArray1::from_vec(py, state.pending_right))?;
            out.set_item("ola", PyArray1::from_vec(py, state.ola))?;
            out.set_item("norm", PyArray1::from_vec(py, state.norm))?;
            out.set_item("ready", PyArray1::from_vec(py, state.ready))?;
            Ok(out)
        })
    }

    /// Takes a state from numpy's extractor (the keys of `state()`). Every size is checked first;
    /// a `ValueError` changes nothing.
    fn set_state(&mut self, state: &Bound<'_, PyDict>) -> PyResult<()> {
        guard(|| {
            self.planted.check("AmbienceExtractor", "set_state");
            let state = ambience::State {
                acc12: get_complex(state, "acc12")?,
                acc11: vector(state, "acc11")?,
                acc22: vector(state, "acc22")?,
                pending_left: vector(state, "pending_left")?,
                pending_right: vector(state, "pending_right")?,
                ola: vector(state, "ola")?,
                norm: vector(state, "norm")?,
                ready: vector(state, "ready")?,
            };
            self.inner.set_state(&state)?;
            Ok(())
        })
    }

    /// Makes the next `process`, `set_params`, `reset` or `set_state` panic inside its guard:
    /// the tests of the extractor's failure path. Only in builds with `test-panic`.
    #[cfg(feature = "test-panic")]
    fn _panic_next(&mut self) {
        self.planted.arm();
    }
}
