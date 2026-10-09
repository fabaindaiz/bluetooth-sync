//! `LoudnessMeter`: `aurasync.dsp.loudness.LoudnessMeter`'s per-block work (the K-weighted energy
//! of every 100 ms step and the true peak).

use aurasync_dsp::loudness::{self, MeterState};
use numpy::PyArray2;
use pyo3::prelude::*;
use pyo3::types::PyDict;

use crate::convert::{float64_rows, float64_vector, item, matrix, owned, samples, taps_of};
use crate::error::{EngineError, guard};
use crate::planted::Planted;

/// `aurasync.dsp.loudness.LoudnessMeter`'s per-block work: the numpy meter owns one when the engine
/// is Rust and keeps the readings (momentary, short-term, integrated, PSR) itself. Every method's
/// whole body runs inside `guard`: a panic is an `EnginePanic`, after which the host never calls
/// this object again (its state may be half-written).
#[pyclass(name = "LoudnessMeter", module = "aurasync_engine")]
pub(crate) struct LoudnessMeter {
    inner: loudness::LoudnessMeter,
    planted: Planted,
}

/// `rows` flattened, each checked to be `columns` long (numpy's `(channels, columns)` array).
fn flat(name: &str, rows: Vec<Vec<f64>>, channels: usize) -> Result<Vec<f64>, EngineError> {
    if rows.len() != channels {
        return Err(EngineError::Invalid(format!(
            "state: {name} has {} rows where the meter has {channels} channels",
            rows.len()
        )));
    }
    Ok(rows.into_iter().flatten().collect())
}

#[pymethods]
impl LoudnessMeter {
    /// The meter numpy's `LoudnessMeter` builds, at rest: `kernels` (a 1-D float64 array of three
    /// rows of `2 * half_width` taps, numpy's `_kernels_ascending.T` flattened), `k_power` (numpy's
    /// `_k_power`, `step_n // 2 + 1` bins), `weights` (one per channel) and the step's `step_n`
    /// samples.
    #[new]
    #[pyo3(signature = (*, kernels, half_width, k_power, weights, step_n))]
    fn new(
        kernels: &Bound<'_, PyAny>,
        half_width: usize,
        k_power: &Bound<'_, PyAny>,
        weights: &Bound<'_, PyAny>,
        step_n: usize,
    ) -> PyResult<Self> {
        guard(|| {
            let k_power = float64_vector("k_power", k_power)?;
            let weights = float64_vector("weights", weights)?;
            Ok(Self {
                inner: loudness::LoudnessMeter::new(
                    &taps_of(kernels)?,
                    half_width,
                    &samples(&k_power),
                    &samples(&weights),
                    step_n,
                )?,
                planted: Planted::default(),
            })
        })
    }

    /// One block of `frames` (a 1-D float64 array: `n` frames of the meter's channels,
    /// interleaved, numpy's `(n, channels)` block flattened): `(true_peak, steps)`, the block's
    /// true peak (linear) and the energies of the 100 ms steps it completed, oldest first.
    fn push(&mut self, py: Python<'_>, frames: &Bound<'_, PyAny>) -> PyResult<(f64, Vec<f64>)> {
        guard(|| {
            self.planted.check("LoudnessMeter", "push");
            let frames = owned("frames", frames)?;
            let inner = &mut self.inner;
            // Without the interpreter while it measures (`convert::owned`): the input is a copy.
            let (peak, steps) = py.detach(|| {
                inner
                    .push(&frames)
                    .map(|peak| (peak, inner.new_steps().to_vec()))
            })?;
            Ok((peak, steps))
        })
    }

    /// The whole state, as numpy's meter keeps it: `{"context": ..., "pending": ...}`, two
    /// `(channels, k)` float64 arrays (`_context` and `_pending`).
    fn state<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
        guard(|| {
            let state = self.inner.to_state();
            let channels = self.inner.channels();
            let rows = |values: &[f64]| -> Result<Bound<'py, PyArray2<f64>>, EngineError> {
                let columns = values.len() / channels;
                let rows: Vec<Vec<f64>> = (0..channels)
                    .map(|c| values[c * columns..(c + 1) * columns].to_vec())
                    .collect();
                matrix(py, &rows, columns)
            };
            let out = PyDict::new(py);
            out.set_item("context", rows(&state.context)?)?;
            out.set_item("pending", rows(&state.pending)?)?;
            Ok(out)
        })
    }

    /// Takes a state from numpy's meter (the keys of `state()`); a `ValueError` changes nothing.
    fn set_state(&mut self, state: &Bound<'_, PyDict>) -> PyResult<()> {
        guard(|| {
            self.planted.check("LoudnessMeter", "set_state");
            let channels = self.inner.channels();
            let state = MeterState {
                context: flat(
                    "context",
                    float64_rows("context", &item(state, "context")?)?,
                    channels,
                )?,
                pending: flat(
                    "pending",
                    float64_rows("pending", &item(state, "pending")?)?,
                    channels,
                )?,
            };
            self.inner.set_state(&state)?;
            Ok(())
        })
    }

    /// Makes the next `push` or `set_state` panic inside its guard: the tests of the meter's
    /// failure path. Only in builds with `test-panic`.
    #[cfg(feature = "test-panic")]
    fn _panic_next(&mut self) {
        self.planted.arm();
    }
}
