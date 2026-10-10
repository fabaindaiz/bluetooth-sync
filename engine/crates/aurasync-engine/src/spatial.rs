//! `SpatialUpmix`: `aurasync.dsp.spatial.SpatialUpmix`'s work (the spatial and "frente intacto"
//! renders).

use aurasync_dsp::spatial::{self, Params, State};
use numpy::{PyArray1, PyArray2, PyArrayMethods};
use pyo3::prelude::*;
use pyo3::types::PyDict;

use crate::convert::{float64_rows, get_complex, item, matrix, owned, set_complex, vector};
use crate::error::guard;
use crate::planted::Planted;

/// The direct and the ambience blocks of every speaker, `(speakers, block)` each.
type Blocks<'py> = (Bound<'py, PyArray2<f64>>, Bound<'py, PyArray2<f64>>);

/// `aurasync.dsp.spatial.SpatialUpmix`'s work: the numpy stage owns one when the engine is Rust.
/// Speakers are indices, in the numpy stage's `names` order. Every method's whole body runs
/// inside `guard`: a panic is an `EnginePanic`, after which the host never calls this object
/// again (its state may be half-written).
#[pyclass(name = "SpatialUpmix", module = "aurasync_engine")]
pub(crate) struct SpatialUpmix {
    inner: spatial::SpatialUpmix,
    planted: Planted,
}

#[pymethods]
impl SpatialUpmix {
    /// A stage for `speakers` outputs at `sr` Hz, with an STFT of `n_fft` points and hop `hop`,
    /// its buffers sized for blocks of `max_block` (larger blocks grow them, once).
    #[new]
    #[pyo3(signature = (speakers, sr, n_fft, hop, max_block = 8192))]
    fn new(speakers: usize, sr: u32, n_fft: usize, hop: usize, max_block: usize) -> PyResult<Self> {
        guard(|| {
            Ok(Self {
                inner: spatial::SpatialUpmix::new(speakers, sr, n_fft, hop, max_block)?,
                planted: Planted::default(),
            })
        })
    }

    /// The knobs, live (`SpatialParams`'s fields, in its order).
    #[pyo3(signature = (*, arc_deg, ambience, ambient_level_db, haas_ms, threshold, lam, front_intact))]
    #[expect(
        clippy::too_many_arguments,
        reason = "mirrors the seven fields of Params, as keyword-only arguments"
    )]
    fn set_params(
        &mut self,
        arc_deg: f64,
        ambience: f64,
        ambient_level_db: f64,
        haas_ms: f64,
        threshold: f64,
        lam: f64,
        front_intact: bool,
    ) -> PyResult<()> {
        guard(|| {
            self.inner.set_params(Params {
                arc_deg,
                ambience,
                ambient_level_db,
                haas_ms,
                threshold,
                lam,
                front_intact,
            });
            Ok(())
        })
    }

    /// The layout, live: each speaker's angle (`None` without one), whether it is ambient, and
    /// the classic mix's `(pan, ambience)` pairs or `None`.
    #[pyo3(signature = (angles, ambient, classic = None))]
    #[expect(
        clippy::needless_pass_by_value,
        reason = "PyO3 extracts the arguments as owned values"
    )]
    fn set_layout(
        &mut self,
        angles: Vec<Option<f64>>,
        ambient: Vec<bool>,
        classic: Option<Vec<(f64, f64)>>,
    ) -> PyResult<()> {
        guard(|| {
            self.inner
                .set_layout(&angles, &ambient, classic.as_deref())?;
            Ok(())
        })
    }

    /// One stereo block: `(direct, ambience)`, each a new `(speakers, len(left))` float64 array.
    fn process<'py>(
        &mut self,
        py: Python<'py>,
        left: &Bound<'py, PyAny>,
        right: &Bound<'py, PyAny>,
    ) -> PyResult<Blocks<'py>> {
        guard(|| {
            self.planted.check("SpatialUpmix", "process");
            let left = owned("left", left)?;
            let right = owned("right", right)?;
            let n = left.len();
            let shape = [self.inner.speakers(), n];
            let inner = &mut self.inner;
            // Without the interpreter while it works (`convert::owned`): the inputs are copies and
            // the outputs Rust's own buffers, handed to numpy afterwards without a copy.
            let (direct, ambience) = py.detach(|| {
                let mut direct = vec![0.0; shape[0] * n];
                let mut ambience = vec![0.0; shape[0] * n];
                inner
                    .process(&left, &right, &mut direct, &mut ambience)
                    .map(|()| (direct, ambience))
            })?;
            Ok((
                PyArray1::from_vec(py, direct).reshape(shape)?,
                PyArray1::from_vec(py, ambience).reshape(shape)?,
            ))
        })
    }

    /// The whole state, as numpy's stage keeps it: a dict of float64 arrays (2-D per speaker),
    /// `haas_read` a list of ints and `emitted` an int.
    fn state<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
        guard(|| {
            let state = self.inner.to_state();
            let ready = state.ready_direct.first().map_or(0, Vec::len);
            let n_fft = self.inner.n_fft();
            let out = PyDict::new(py);
            set_complex(py, &out, "acc12", &state.acc12)?;
            out.set_item("acc11", PyArray1::from_vec(py, state.acc11))?;
            out.set_item("acc22", PyArray1::from_vec(py, state.acc22))?;
            out.set_item("pending_left", PyArray1::from_vec(py, state.pending_left))?;
            out.set_item("pending_right", PyArray1::from_vec(py, state.pending_right))?;
            out.set_item("ola_direct", matrix(py, &state.ola_direct, n_fft)?)?;
            out.set_item("ola_ambience", matrix(py, &state.ola_ambience, n_fft)?)?;
            out.set_item("norm", PyArray1::from_vec(py, state.norm))?;
            out.set_item("ready_direct", matrix(py, &state.ready_direct, ready)?)?;
            out.set_item("ready_ambience", matrix(py, &state.ready_ambience, ready)?)?;
            out.set_item("haas", matrix(py, &state.haas, self.inner.haas_len())?)?;
            out.set_item("haas_read", state.haas_read)?;
            out.set_item("emitted", state.emitted)?;
            Ok(out)
        })
    }

    /// Takes a state from numpy's stage (the keys of `state()`). Every size is checked first;
    /// a `ValueError` changes nothing.
    fn set_state(&mut self, state: &Bound<'_, PyDict>) -> PyResult<()> {
        guard(|| {
            let state = State {
                acc12: get_complex(state, "acc12")?,
                acc11: vector(state, "acc11")?,
                acc22: vector(state, "acc22")?,
                pending_left: vector(state, "pending_left")?,
                pending_right: vector(state, "pending_right")?,
                ola_direct: float64_rows("ola_direct", &item(state, "ola_direct")?)?,
                ola_ambience: float64_rows("ola_ambience", &item(state, "ola_ambience")?)?,
                norm: vector(state, "norm")?,
                ready_direct: float64_rows("ready_direct", &item(state, "ready_direct")?)?,
                ready_ambience: float64_rows("ready_ambience", &item(state, "ready_ambience")?)?,
                haas: float64_rows("haas", &item(state, "haas")?)?,
                haas_read: item(state, "haas_read")?.extract()?,
                emitted: item(state, "emitted")?.extract()?,
            };
            self.inner.set_state(&state)?;
            Ok(())
        })
    }

    /// Makes the next `process` panic inside its guard: the tests of the stage's failure path.
    /// Only in builds with `test-panic`.
    #[cfg(feature = "test-panic")]
    fn _panic_next(&mut self) {
        self.planted.arm();
    }
}
