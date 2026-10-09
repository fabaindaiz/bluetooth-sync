//! `TruePeakLimiter`: `aurasync.dsp.limiter.TruePeakLimiter`'s per-block work (the 4x-oversampled
//! need, the hold and raised-cosine attack, the release and the metrics).

use aurasync_dsp::limiter::{self, LimiterState};
use numpy::PyArray1;
use pyo3::prelude::*;
use pyo3::types::PyDict;

use crate::convert::{item, owned, taps_of, vector};
use crate::error::guard;
use crate::planted::Planted;

/// `aurasync.dsp.limiter.TruePeakLimiter`'s per-block work: the numpy limiter owns one when the
/// engine is Rust. Every method's whole body runs inside `guard`: a panic is an `EnginePanic`,
/// after which the host never calls this object again (its state may be half-written).
#[pyclass(name = "TruePeakLimiter", module = "aurasync_engine")]
pub(crate) struct TruePeakLimiter {
    inner: limiter::TruePeakLimiter,
    planted: Planted,
}

#[pymethods]
impl TruePeakLimiter {
    /// The limiter numpy's `TruePeakLimiter` builds, at rest: `kernels` (a 1-D float64 array of
    /// three rows of `2 * half_width` taps, numpy's `_kernels.T` flattened), the look-ahead
    /// `latency`, `attack` and `hold` in samples, and the knobs `ceiling_db` and `release_ms` at
    /// `sr` Hz.
    #[new]
    #[expect(
        clippy::too_many_arguments,
        reason = "numpy's design values, passed as they are so no constant is duplicated"
    )]
    fn new(
        kernels: &Bound<'_, PyAny>,
        half_width: usize,
        ceiling_db: f64,
        latency: usize,
        attack: usize,
        hold: usize,
        release_ms: f64,
        sr: u32,
    ) -> PyResult<Self> {
        guard(|| {
            Ok(Self {
                inner: limiter::TruePeakLimiter::new(
                    &taps_of(kernels)?,
                    half_width,
                    ceiling_db,
                    latency,
                    attack,
                    hold,
                    release_ms,
                    sr,
                )?,
                planted: Planted::default(),
            })
        })
    }

    /// The live knobs (numpy's `configure`), from the next block; a `ValueError` changes neither.
    #[pyo3(signature = (*, ceiling_db = None, release_ms = None))]
    fn configure(&mut self, ceiling_db: Option<f64>, release_ms: Option<f64>) -> PyResult<()> {
        guard(|| {
            self.planted.check("TruePeakLimiter", "configure");
            self.inner.configure(ceiling_db, release_ms)?;
            Ok(())
        })
    }

    /// One block: `(out, gain, max_reduction_db, active_fraction)`, the output a new float64
    /// array of `len(x)` samples, then the gain on its last sample, the deepest reduction (dB)
    /// and the share of samples under unity.
    fn process<'py>(
        &mut self,
        py: Python<'py>,
        x: &Bound<'py, PyAny>,
    ) -> PyResult<(Bound<'py, PyArray1<f64>>, f64, f64, f64)> {
        guard(|| {
            self.planted.check("TruePeakLimiter", "process");
            let x = owned("x", x)?;
            let inner = &mut self.inner;
            // Without the interpreter while it works (`convert::owned`): the input is a copy.
            let out = py.detach(|| {
                let mut out = vec![0.0; x.len()];
                inner.process(&x, &mut out).map(|()| out)
            })?;
            Ok((
                PyArray1::from_vec(py, out),
                self.inner.gain(),
                self.inner.max_reduction_db(),
                self.inner.active_fraction(),
            ))
        })
    }

    /// The whole state, as numpy's limiter keeps it: `{"x": ..., "gain": ...}` (`_x`, the kept
    /// input, and the gain on the last sample out).
    fn state<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
        guard(|| {
            let state = self.inner.to_state();
            let out = PyDict::new(py);
            out.set_item("x", PyArray1::from_vec(py, state.x))?;
            out.set_item("gain", state.gain)?;
            Ok(out)
        })
    }

    /// Takes a state from numpy's limiter (the keys of `state()`); a `ValueError` changes nothing.
    fn set_state(&mut self, state: &Bound<'_, PyDict>) -> PyResult<()> {
        guard(|| {
            self.planted.check("TruePeakLimiter", "set_state");
            let state = LimiterState {
                x: vector(state, "x")?,
                gain: item(state, "gain")?.extract()?,
            };
            self.inner.set_state(&state)?;
            Ok(())
        })
    }

    /// Makes the next `process`, `configure` or `set_state` panic inside its guard: the tests of
    /// the limiter's failure path. Only in builds with `test-panic`.
    #[cfg(feature = "test-panic")]
    fn _panic_next(&mut self) {
        self.planted.arm();
    }
}
