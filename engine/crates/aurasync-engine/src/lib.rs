//! `aurasync_engine`: the Rust DSP of `aurasync-dsp` as a Python module.
//!
//! - `read(data, position)`: `aurasync.dsp.interpolation.read`, the band-limited read, within
//!   1e-9 of numpy (host/tests/test_engine_rust.py). Both arguments must be 1-D float64 numpy
//!   arrays; a strided view is copied first, any other dtype is a `TypeError` (never converted
//!   silently). A position without `HALF - 1` samples before it and `HALF` after it is a
//!   `ValueError`.
//! - `SpatialUpmix`: `aurasync.dsp.spatial.SpatialUpmix`'s work in Rust (the spatial and "frente
//!   intacto" renders), within 1e-9 of numpy (host/tests/test_spatial_rust.py). The numpy stage
//!   owns one when the engine is Rust, and moves its state in and out (`state`, `set_state`) at a
//!   cut's bottom. Inputs are 1-D float64 arrays (a strided view is copied); `process` gives two
//!   new `(speakers, block)` float64 arrays, the direct and the ambience blocks.
//! - `AmbienceExtractor`: `aurasync.dsp.ambience.Extractor`'s work in Rust (the mono ambience of
//!   a stereo stream), within 1e-9 of numpy (host/tests/test_ambience_rust.py). The numpy
//!   extractor owns one when the engine is Rust, and moves its state in and out (`state`,
//!   `set_state`) at a cut's bottom. Inputs are 1-D float64 arrays (a strided view is copied);
//!   `process` gives a new 1-D float64 array as long as the block.
//! - `StreamingFIR` and `PartitionedFIR`: `aurasync.dsp.eq`'s FFT convolutions in Rust (overlap-add
//!   with a tail; uniform partitioned overlap-save), within 1e-9 of numpy
//!   (host/tests/test_eq_rust.py). The numpy filters own one when the engine is Rust (so the EQ,
//!   the crossover, the bass protection, the virtual bass and the diffuse tail all get it), and
//!   move their state in and out (`state`, `set_state`) at a cut's bottom. Inputs are 1-D float64
//!   arrays (a strided view is copied); `process` gives a new 1-D float64 array as long as the
//!   block.
//! - `VirtualBass`: `aurasync.dsp.virtual_bass.VirtualBass`'s per-block work in Rust (the bass
//!   band, the rectifier, the harmonics band, the calibration and the gain's ramp), within 1e-9
//!   of numpy (host/tests/test_virtual_bass_rust.py). It owns its two partitioned filters
//!   directly, so a block is one call: `process(x, current, target)` gives the harmonics and the
//!   two energies. The numpy stage owns one when the engine is Rust and moves the filters'
//!   states in and out (`state`, `set_state`, `reset`) at a cut's bottom.
//! - `capabilities()`: the constants each stage was built with, for the host to check against its
//!   own.
//!
//! A panic never reaches Python as PyO3's `PanicException`, which derives from `BaseException`
//! and that the service's loop would not catch: the whole body of every exported function (the
//! argument checks, the read and building the result) runs inside [`guard`], which catches it and
//! raises `RuntimeError`. The host's dispatcher (`aurasync/dsp/backend.py`) takes a
//! `RuntimeError` as the engine failing: that block is silence and numpy reads from the next cut.
#![forbid(unsafe_code)]

use std::borrow::Cow;
use std::panic::{self, AssertUnwindSafe};
use std::sync::{Mutex, MutexGuard};

use aurasync_dsp::ambience::{self, AmbienceError};
use aurasync_dsp::fir::{self, FirError};
use aurasync_dsp::interpolation::{self, ReadError, Reader};
use aurasync_dsp::spatial::{self, Params, SpatialError, State};
use aurasync_dsp::virtual_bass;
use numpy::{PyArray1, PyArray2, PyArrayMethods, PyReadonlyArray1};
use pyo3::exceptions::{PyRuntimeError, PyTypeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::PyDict;

/// The block the first reader is built for: twice the service's 4096 samples. A larger block
/// replaces the reader with one that fits (rebuilding its table, a few milliseconds, once).
const FIRST_MAX_BLOCK: usize = 8192;

/// The reader, built on the first call: its table costs about 70 000 kernel evaluations, too
/// many to repeat per block. Every call holds the GIL, so the lock is never contended.
static READER: Mutex<Option<Reader>> = Mutex::new(None);

/// The reader's lock. After a panic while it was held the lock is poisoned and the scratch
/// buffers may be half-written: the reader is dropped and the next call builds a new one.
fn lock_reader() -> MutexGuard<'static, Option<Reader>> {
    READER.lock().unwrap_or_else(|poisoned| {
        READER.clear_poison();
        let mut guard = poisoned.into_inner();
        *guard = None;
        guard
    })
}

/// Runs `body`, turning a panic anywhere inside it into `RuntimeError`. Every exported function
/// is one call to this, so nothing it does can unwind into PyO3 (whose `PanicException` is a
/// `BaseException`).
fn guard<T>(body: impl FnOnce() -> PyResult<T>) -> PyResult<T> {
    panic::catch_unwind(AssertUnwindSafe(body)).unwrap_or_else(|payload| {
        let message = payload
            .downcast_ref::<&str>()
            .copied()
            .or_else(|| payload.downcast_ref::<String>().map(String::as_str))
            .unwrap_or("(no message)");
        Err(PyRuntimeError::new_err(format!(
            "aurasync_engine panicked: {message}"
        )))
    })
}

/// Runs `f` with a reader for blocks of `block` positions. Called inside [`guard`]: a panic in
/// `f` poisons the lock, and [`lock_reader`] drops that reader on the next call.
fn with_reader<T>(block: usize, f: impl FnOnce(&mut Reader) -> T) -> T {
    let mut slot = lock_reader();
    let reader = match slot.take() {
        Some(reader) if reader.max_block() >= block => reader,
        _ => Reader::new(
            block
                .checked_next_power_of_two()
                .unwrap_or(block)
                .max(FIRST_MAX_BLOCK),
        ),
    };
    f(slot.insert(reader))
}

fn read_error(error: ReadError) -> PyErr {
    match error {
        ReadError::OutOfRange { .. } | ReadError::LengthMismatch => {
            PyValueError::new_err(error.to_string())
        }
        // `with_reader` sizes the reader for the block, so this is a bug here, not the caller's.
        ReadError::BlockTooLarge => PyRuntimeError::new_err(error.to_string()),
    }
}

/// `value` as a 1-D float64 numpy array, or a `TypeError` that says what it is instead (the
/// automatic one reads "'ndarray' object is not an instance of 'ndarray'"). Nothing is converted:
/// float32, integers, another byte order or a list are refused.
fn float64_vector<'py>(
    name: &str,
    value: &Bound<'py, PyAny>,
) -> PyResult<PyReadonlyArray1<'py, f64>> {
    let Ok(array) = value.cast::<PyArray1<f64>>() else {
        let what = match (value.getattr("dtype"), value.getattr("ndim")) {
            (Ok(dtype), Ok(ndim)) => {
                format!("an array of {} with {ndim} dimension(s)", dtype.str()?)
            }
            _ => format!("{}", value.get_type().name()?),
        };
        return Err(PyTypeError::new_err(format!(
            "{name} must be a 1-D float64 numpy array in native byte order, not {what}"
        )));
    };
    array
        .try_readonly()
        .map_err(|error| PyRuntimeError::new_err(error.to_string()))
}

/// The array's samples in order: borrowed when contiguous, copied from a strided view.
fn samples<'a>(array: &'a PyReadonlyArray1<'_, f64>) -> Cow<'a, [f64]> {
    match array.as_slice() {
        Ok(slice) => Cow::Borrowed(slice),
        Err(_) => Cow::Owned(array.as_array().iter().copied().collect()),
    }
}

/// `data` evaluated at each (fractional) `position`, band-limited: a new float64 array.
#[pyfunction]
fn read<'py>(
    py: Python<'py>,
    data: &Bound<'py, PyAny>,
    position: &Bound<'py, PyAny>,
) -> PyResult<Bound<'py, PyArray1<f64>>> {
    guard(|| {
        let data = float64_vector("data", data)?;
        let position = float64_vector("position", position)?;
        let data = samples(&data);
        let position = samples(&position);
        let mut out = vec![0.0; position.len()];
        with_reader(position.len(), |reader| {
            reader.read(&data, &position, &mut out)
        })
        .map_err(read_error)?;
        Ok(PyArray1::from_vec(py, out))
    })
}

/// The constants each stage was built with: `{"interpolation": {"half": 16, "beta": 8.0,
/// "steps": 2048}, "spatial": {...}, "ambience": {"floor": 1e-8}, "fir": {}, "virtual_bass": {}}`. The FIR
/// filters and the virtual bass share no constant with numpy; their keys say this build has them, so the host refuses an
/// older build at load instead of failing on the first filter.
#[pyfunction]
fn capabilities(py: Python<'_>) -> PyResult<Bound<'_, PyDict>> {
    guard(|| {
        let read = PyDict::new(py);
        read.set_item("half", interpolation::HALF)?;
        read.set_item("beta", interpolation::BETA)?;
        read.set_item("steps", interpolation::STEPS)?;
        let upmix = PyDict::new(py);
        upmix.set_item("max_haas_ms", spatial::MAX_HAAS_MS)?;
        upmix.set_item("fade_in", spatial::FADE_IN)?;
        upmix.set_item("floor", spatial::FLOOR)?;
        upmix.set_item("silent", spatial::SILENT)?;
        upmix.set_item("front_boost_db", spatial::FRONT_BOOST_DB)?;
        upmix.set_item("min_energy_ratio", spatial::MIN_ENERGY_RATIO)?;
        upmix.set_item("mu0", spatial::MU0)?;
        upmix.set_item("mu1", spatial::MU1)?;
        upmix.set_item("sigma", spatial::SIGMA)?;
        let extractor = PyDict::new(py);
        extractor.set_item("floor", ambience::FLOOR)?;
        let all = PyDict::new(py);
        all.set_item("interpolation", read)?;
        all.set_item("spatial", upmix)?;
        all.set_item("ambience", extractor)?;
        // No constant shared with numpy: `version` is bumped (here and in backend.py) whenever
        // the Rust behaviour of the stage changes, so a stale build is refused.
        let fir = PyDict::new(py);
        fir.set_item("version", 1)?;
        let bass = PyDict::new(py);
        bass.set_item("version", 1)?;
        all.set_item("fir", fir)?;
        all.set_item("virtual_bass", bass)?;
        Ok(all)
    })
}

/// Panics inside the read's guard, with the reader locked: the test that a panic arrives as
/// `RuntimeError` and that reading goes on afterwards. Only in builds with `test-panic`.
#[cfg(feature = "test-panic")]
#[pyfunction]
fn _panic() -> PyResult<()> {
    guard(|| with_reader(0, |_| panic!("planted panic (feature test-panic)")))
}

/// Panics before any argument is looked at: the test that the guard covers the whole body, not
/// only the read. Only in builds with `test-panic`.
#[cfg(feature = "test-panic")]
#[pyfunction]
fn _panic_outside_the_read(value: &Bound<'_, PyAny>) -> PyResult<()> {
    guard(|| {
        panic!(
            "planted panic before the arguments ({})",
            value.get_type().name()?
        )
    })
}

fn spatial_error(error: SpatialError) -> PyErr {
    PyValueError::new_err(error.to_string())
}

/// `value` as a 2-D float64 numpy array's rows, or a `TypeError` (as [`float64_vector`]).
fn float64_rows(name: &str, value: &Bound<'_, PyAny>) -> PyResult<Vec<Vec<f64>>> {
    let Ok(array) = value.cast::<PyArray2<f64>>() else {
        return Err(PyTypeError::new_err(format!(
            "{name} must be a 2-D float64 numpy array in native byte order"
        )));
    };
    let array = array
        .try_readonly()
        .map_err(|error| PyRuntimeError::new_err(error.to_string()))?;
    Ok(array
        .as_array()
        .rows()
        .into_iter()
        .map(|row| row.to_vec())
        .collect())
}

/// `state[name]`, or a `ValueError` naming the missing key.
fn item<'py>(state: &Bound<'py, PyDict>, name: &str) -> PyResult<Bound<'py, PyAny>> {
    state
        .get_item(name)?
        .ok_or_else(|| PyValueError::new_err(format!("state: {name} is missing")))
}

fn vector(state: &Bound<'_, PyDict>, name: &str) -> PyResult<Vec<f64>> {
    let array = float64_vector(name, &item(state, name)?)?;
    Ok(samples(&array).into_owned())
}

/// `rows` as a new `(rows, columns)` float64 array (`columns` is needed when there are none).
fn matrix<'py>(
    py: Python<'py>,
    rows: &[Vec<f64>],
    columns: usize,
) -> PyResult<Bound<'py, PyArray2<f64>>> {
    let flat: Vec<f64> = rows.iter().flatten().copied().collect();
    PyArray1::from_vec(py, flat).reshape([rows.len(), columns])
}

/// The direct and the ambience blocks of every speaker, `(speakers, block)` each.
type Blocks<'py> = (Bound<'py, PyArray2<f64>>, Bound<'py, PyArray2<f64>>);

/// `aurasync.dsp.spatial.SpatialUpmix`'s work: the numpy stage owns one when the engine is Rust.
/// Speakers are indices, in the numpy stage's `names` order. Every method's whole body runs
/// inside [`guard`]: a panic is a `RuntimeError`, after which the host never calls this object
/// again (its state may be half-written).
#[pyclass(name = "SpatialUpmix", module = "aurasync_engine")]
struct SpatialUpmix {
    inner: spatial::SpatialUpmix,
    /// `_panic_next()` was called: the next `process` panics. Only in builds with `test-panic`.
    #[cfg(feature = "test-panic")]
    panic_next: bool,
}

#[pymethods]
impl SpatialUpmix {
    /// A stage for `speakers` outputs at `sr` Hz, with an STFT of `n_fft` points and hop `hop`,
    /// its buffers sized for blocks of `max_block` (larger blocks grow them, once).
    #[new]
    #[pyo3(signature = (speakers, sr, n_fft, hop, max_block = 8192))]
    fn new(speakers: usize, sr: u32, n_fft: usize, hop: usize, max_block: usize) -> PyResult<Self> {
        guard(|| {
            if n_fft < 2 || hop == 0 || hop > n_fft || sr == 0 {
                return Err(PyValueError::new_err(format!(
                    "SpatialUpmix needs n_fft >= 2, 0 < hop <= n_fft and sr > 0 (n_fft {n_fft}, hop {hop}, sr {sr})"
                )));
            }
            Ok(Self {
                inner: spatial::SpatialUpmix::new(speakers, sr, n_fft, hop, max_block),
                #[cfg(feature = "test-panic")]
                panic_next: false,
            })
        })
    }

    /// The knobs, live (`SpatialParams`'s fields, in its order).
    #[allow(clippy::too_many_arguments)]
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
    fn set_layout(
        &mut self,
        angles: Vec<Option<f64>>,
        ambient: Vec<bool>,
        classic: Option<Vec<(f64, f64)>>,
    ) -> PyResult<()> {
        guard(|| {
            self.inner
                .set_layout(&angles, &ambient, classic.as_deref())
                .map_err(spatial_error)
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
            #[cfg(feature = "test-panic")]
            if std::mem::take(&mut self.panic_next) {
                panic!("planted panic in SpatialUpmix.process (feature test-panic)");
            }
            let left = float64_vector("left", left)?;
            let right = float64_vector("right", right)?;
            let (left, right) = (samples(&left), samples(&right));
            let n = left.len();
            let shape = [self.inner.speakers(), n];
            let direct = PyArray2::<f64>::zeros(py, shape, false);
            let ambience = PyArray2::<f64>::zeros(py, shape, false);
            {
                let mut direct_out = direct
                    .try_readwrite()
                    .map_err(|error| PyRuntimeError::new_err(error.to_string()))?;
                let mut ambience_out = ambience
                    .try_readwrite()
                    .map_err(|error| PyRuntimeError::new_err(error.to_string()))?;
                let direct_out = direct_out
                    .as_slice_mut()
                    .map_err(|error| PyRuntimeError::new_err(error.to_string()))?;
                let ambience_out = ambience_out
                    .as_slice_mut()
                    .map_err(|error| PyRuntimeError::new_err(error.to_string()))?;
                self.inner
                    .process(&left, &right, direct_out, ambience_out)
                    .map_err(spatial_error)?;
            }
            Ok((direct, ambience))
        })
    }

    /// The whole state, as numpy's stage keeps it: a dict of float64 arrays (2-D per speaker),
    /// `haas_read` a list of ints and `emitted` an int.
    fn state<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
        guard(|| {
            let state = self.inner.state();
            let ready = state.ready_direct.first().map_or(0, Vec::len);
            let n_fft = self.inner.n_fft();
            let out = PyDict::new(py);
            let re: Vec<f64> = state.acc12.iter().map(|z| z.re).collect();
            let im: Vec<f64> = state.acc12.iter().map(|z| z.im).collect();
            out.set_item("acc12_re", PyArray1::from_vec(py, re))?;
            out.set_item("acc12_im", PyArray1::from_vec(py, im))?;
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
            let re = vector(state, "acc12_re")?;
            let im = vector(state, "acc12_im")?;
            if re.len() != im.len() {
                return Err(PyValueError::new_err(format!(
                    "state: acc12_re has length {} and acc12_im {}",
                    re.len(),
                    im.len()
                )));
            }
            let state = State {
                acc12: re
                    .iter()
                    .zip(&im)
                    .map(|(&re, &im)| spatial::Complex::new(re, im))
                    .collect(),
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
            self.inner.set_state(&state).map_err(spatial_error)
        })
    }

    /// Makes the next `process` panic inside its guard: the tests of the stage's failure path.
    /// Only in builds with `test-panic`.
    #[cfg(feature = "test-panic")]
    fn _panic_next(&mut self) {
        self.panic_next = true;
    }
}

fn ambience_error(error: AmbienceError) -> PyErr {
    PyValueError::new_err(error.to_string())
}

/// `aurasync.dsp.ambience.Extractor`'s work: the numpy extractor owns one when the engine is
/// Rust. Every method's whole body runs inside [`guard`]: a panic is a `RuntimeError`, after
/// which the host never calls this object again (its state may be half-written).
#[pyclass(name = "AmbienceExtractor", module = "aurasync_engine")]
struct AmbienceExtractor {
    inner: ambience::Extractor,
    /// `_panic_next()` was called: the next call panics. Only in builds with `test-panic`.
    #[cfg(feature = "test-panic")]
    panic_next: bool,
}

impl AmbienceExtractor {
    /// Panics if `_panic_next()` was called (builds with `test-panic`); nothing otherwise.
    fn planted_panic(&mut self, _call: &str) {
        #[cfg(feature = "test-panic")]
        if std::mem::take(&mut self.panic_next) {
            panic!("planted panic in AmbienceExtractor.{_call} (feature test-panic)");
        }
    }
}

#[pymethods]
impl AmbienceExtractor {
    /// An extractor with an STFT of `n_fft` points and hop `hop`, its buffers sized for blocks of
    /// `max_block` (larger blocks grow them, once).
    #[new]
    #[pyo3(signature = (n_fft, hop, max_block = 8192))]
    fn new(n_fft: usize, hop: usize, max_block: usize) -> PyResult<Self> {
        guard(|| {
            if n_fft < 2 || hop == 0 || hop > n_fft {
                return Err(PyValueError::new_err(format!(
                    "AmbienceExtractor needs n_fft >= 2 and 0 < hop <= n_fft (n_fft {n_fft}, hop {hop})"
                )));
            }
            Ok(Self {
                inner: ambience::Extractor::new(n_fft, hop, max_block),
                #[cfg(feature = "test-panic")]
                panic_next: false,
            })
        })
    }

    /// The knobs (`Parametros`'s `lam`, `umbral`, `mu0`, `mu1`, `sigma`, `energia_minima`).
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
            self.planted_panic("set_params");
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
            self.planted_panic("reset");
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
            self.planted_panic("process");
            let left = float64_vector("left", left)?;
            let right = float64_vector("right", right)?;
            let (left, right) = (samples(&left), samples(&right));
            let mut out = vec![0.0; left.len()];
            self.inner
                .process(&left, &right, &mut out)
                .map_err(ambience_error)?;
            Ok(PyArray1::from_vec(py, out))
        })
    }

    /// The whole state, as numpy's extractor keeps it: a dict of 1-D float64 arrays.
    fn state<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
        guard(|| {
            let state = self.inner.state();
            let out = PyDict::new(py);
            let re: Vec<f64> = state.acc12.iter().map(|z| z.re).collect();
            let im: Vec<f64> = state.acc12.iter().map(|z| z.im).collect();
            out.set_item("acc12_re", PyArray1::from_vec(py, re))?;
            out.set_item("acc12_im", PyArray1::from_vec(py, im))?;
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
            self.planted_panic("set_state");
            let re = vector(state, "acc12_re")?;
            let im = vector(state, "acc12_im")?;
            if re.len() != im.len() {
                return Err(PyValueError::new_err(format!(
                    "state: acc12_re has length {} and acc12_im {}",
                    re.len(),
                    im.len()
                )));
            }
            let state = ambience::State {
                acc12: re
                    .iter()
                    .zip(&im)
                    .map(|(&re, &im)| ambience::Complex::new(re, im))
                    .collect(),
                acc11: vector(state, "acc11")?,
                acc22: vector(state, "acc22")?,
                pending_left: vector(state, "pending_left")?,
                pending_right: vector(state, "pending_right")?,
                ola: vector(state, "ola")?,
                norm: vector(state, "norm")?,
                ready: vector(state, "ready")?,
            };
            self.inner.set_state(&state).map_err(ambience_error)
        })
    }

    /// Makes the next `process`, `set_params`, `reset` or `set_state` panic inside its guard:
    /// the tests of the extractor's failure path. Only in builds with `test-panic`.
    #[cfg(feature = "test-panic")]
    fn _panic_next(&mut self) {
        self.panic_next = true;
    }
}

fn fir_error(error: FirError) -> PyErr {
    PyValueError::new_err(error.to_string())
}

/// `taps` as a 1-D float64 numpy array's samples, owned.
fn taps_of(taps: &Bound<'_, PyAny>) -> PyResult<Vec<f64>> {
    let taps = float64_vector("taps", taps)?;
    Ok(samples(&taps).into_owned())
}

/// `aurasync.dsp.eq.StreamingFIR`'s work: the numpy filter owns one when the engine is Rust.
/// Every method's whole body runs inside [`guard`]: a panic is a `RuntimeError`, after which the
/// host never calls this object again (its state may be half-written).
#[pyclass(name = "StreamingFIR", module = "aurasync_engine")]
struct StreamingFir {
    inner: fir::StreamingFir,
    /// `_panic_next()` was called: the next call panics. Only in builds with `test-panic`.
    #[cfg(feature = "test-panic")]
    panic_next: bool,
}

impl StreamingFir {
    /// Panics if `_panic_next()` was called (builds with `test-panic`); nothing otherwise.
    fn planted_panic(&mut self, _call: &str) {
        #[cfg(feature = "test-panic")]
        if std::mem::take(&mut self.panic_next) {
            panic!("planted panic in StreamingFIR.{_call} (feature test-panic)");
        }
    }
}

#[pymethods]
impl StreamingFir {
    /// A filter with `taps` (a 1-D float64 array, at least one tap) and a silent tail; the FFT
    /// size for blocks of `block` samples is built now (others on their first block).
    #[new]
    #[pyo3(signature = (taps, block = 4096))]
    fn new(taps: &Bound<'_, PyAny>, block: usize) -> PyResult<Self> {
        guard(|| {
            let mut inner = fir::StreamingFir::new(&taps_of(taps)?).map_err(fir_error)?;
            inner.prepare(block);
            Ok(Self {
                inner,
                #[cfg(feature = "test-panic")]
                panic_next: false,
            })
        })
    }

    /// numpy's `set_taps`: the tail is reset only when the length changes.
    fn set_taps(&mut self, taps: &Bound<'_, PyAny>) -> PyResult<()> {
        guard(|| {
            self.planted_panic("set_taps");
            self.inner.set_taps(&taps_of(taps)?).map_err(fir_error)
        })
    }

    /// numpy's `taps = ...`: the tail is kept as it is.
    fn replace_taps(&mut self, taps: &Bound<'_, PyAny>) -> PyResult<()> {
        guard(|| {
            self.planted_panic("replace_taps");
            self.inner.replace_taps(&taps_of(taps)?).map_err(fir_error)
        })
    }

    /// One block: a new float64 array of `len(x)` samples.
    fn process<'py>(
        &mut self,
        py: Python<'py>,
        x: &Bound<'py, PyAny>,
    ) -> PyResult<Bound<'py, PyArray1<f64>>> {
        guard(|| {
            self.planted_panic("process");
            let x = float64_vector("x", x)?;
            let x = samples(&x);
            let mut out = vec![0.0; x.len()];
            self.inner.process(&x, &mut out).map_err(fir_error)?;
            Ok(PyArray1::from_vec(py, out))
        })
    }

    /// The whole state, as numpy's filter keeps it: `{"taps": ..., "tail": ...}`.
    fn state<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
        guard(|| {
            let state = self.inner.state();
            let out = PyDict::new(py);
            out.set_item("taps", PyArray1::from_vec(py, state.taps))?;
            out.set_item("tail", PyArray1::from_vec(py, state.tail))?;
            Ok(out)
        })
    }

    /// Takes a state from numpy's filter (the keys of `state()`); a `ValueError` changes nothing.
    fn set_state(&mut self, state: &Bound<'_, PyDict>) -> PyResult<()> {
        guard(|| {
            self.planted_panic("set_state");
            let state = fir::StreamingState {
                taps: vector(state, "taps")?,
                tail: vector(state, "tail")?,
            };
            self.inner.set_state(&state).map_err(fir_error)
        })
    }

    /// Makes the next `process`, `set_taps`, `replace_taps` or `set_state` panic inside its
    /// guard: the tests of the filter's failure path. Only in builds with `test-panic`.
    #[cfg(feature = "test-panic")]
    fn _panic_next(&mut self) {
        self.panic_next = true;
    }
}

/// A [`fir::PartitionedState`] as numpy's `PartitionedFIR` keeps it: `history` (1-D), `fdl_re`
/// and `fdl_im` (`(partitions, bins)`), `head` (an int) and `fdl_valid` (a bool).
fn partitioned_dict<'py>(
    py: Python<'py>,
    state: &fir::PartitionedState,
    bins: usize,
) -> PyResult<Bound<'py, PyDict>> {
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
fn partitioned_state(state: &Bound<'_, PyDict>) -> PyResult<fir::PartitionedState> {
    let re = float64_rows("fdl_re", &item(state, "fdl_re")?)?;
    let im = float64_rows("fdl_im", &item(state, "fdl_im")?)?;
    if re.len() != im.len() || re.iter().zip(&im).any(|(a, b)| a.len() != b.len()) {
        return Err(PyValueError::new_err(
            "state: fdl_re and fdl_im have different shapes",
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
                    .map(|(&re, &im)| fir::Complex::new(re, im))
                    .collect()
            })
            .collect(),
        head: item(state, "head")?.extract()?,
        fdl_valid: item(state, "fdl_valid")?.extract()?,
    })
}

/// `aurasync.dsp.eq.PartitionedFIR`'s work: the numpy filter owns one when the engine is Rust.
/// Every method's whole body runs inside [`guard`]: a panic is a `RuntimeError`, after which the
/// host never calls this object again (its state may be half-written).
#[pyclass(name = "PartitionedFIR", module = "aurasync_engine")]
struct PartitionedFir {
    inner: fir::PartitionedFir,
    /// `_panic_next()` was called: the next call panics. Only in builds with `test-panic`.
    #[cfg(feature = "test-panic")]
    panic_next: bool,
}

impl PartitionedFir {
    /// Panics if `_panic_next()` was called (builds with `test-panic`); nothing otherwise.
    fn planted_panic(&mut self, _call: &str) {
        #[cfg(feature = "test-panic")]
        if std::mem::take(&mut self.panic_next) {
            panic!("planted panic in PartitionedFIR.{_call} (feature test-panic)");
        }
    }
}

#[pymethods]
impl PartitionedFir {
    /// A filter with `taps` (a 1-D float64 array, at least one tap) cut into partitions of
    /// `block` samples, at rest.
    #[new]
    fn new(taps: &Bound<'_, PyAny>, block: usize) -> PyResult<Self> {
        guard(|| {
            Ok(Self {
                inner: fir::PartitionedFir::new(&taps_of(taps)?, block).map_err(fir_error)?,
                #[cfg(feature = "test-panic")]
                panic_next: false,
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
            self.planted_panic("process");
            let x = float64_vector("x", x)?;
            let x = samples(&x);
            let mut out = vec![0.0; x.len()];
            self.inner.process(&x, &mut out).map_err(fir_error)?;
            Ok(PyArray1::from_vec(py, out))
        })
    }

    /// Takes `x` as input without computing its output (numpy's `skip`).
    fn skip(&mut self, x: &Bound<'_, PyAny>) -> PyResult<()> {
        guard(|| {
            self.planted_panic("skip");
            let x = float64_vector("x", x)?;
            self.inner.skip(&samples(&x));
            Ok(())
        })
    }

    /// The whole state, as numpy's filter keeps it: `history` (1-D), `fdl_re` and `fdl_im`
    /// (`(partitions, block + 1)`), `head` (an int) and `fdl_valid` (a bool).
    fn state<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
        guard(|| partitioned_dict(py, &self.inner.state(), self.inner.block() + 1))
    }

    /// Takes a state from numpy's filter (the keys of `state()`). Every size is checked first;
    /// a `ValueError` changes nothing.
    fn set_state(&mut self, state: &Bound<'_, PyDict>) -> PyResult<()> {
        guard(|| {
            self.planted_panic("set_state");
            self.inner
                .set_state(&partitioned_state(state)?)
                .map_err(fir_error)
        })
    }

    /// Makes the next `process`, `skip` or `set_state` panic inside its guard: the tests of the
    /// filter's failure path. Only in builds with `test-panic`.
    #[cfg(feature = "test-panic")]
    fn _panic_next(&mut self) {
        self.panic_next = true;
    }
}

/// `aurasync.dsp.virtual_bass.VirtualBass`'s per-block work: the numpy stage owns one when the
/// engine is Rust. It owns the two partitioned filters (one call per block). Every method's whole
/// body runs inside [`guard`]: a panic is a `RuntimeError`, after which the host never calls this
/// object again (its state may be half-written).
#[pyclass(name = "VirtualBass", module = "aurasync_engine")]
struct VirtualBass {
    inner: virtual_bass::VirtualBass,
    block: usize,
    /// `_panic_next()` was called: the next call panics. Only in builds with `test-panic`.
    #[cfg(feature = "test-panic")]
    panic_next: bool,
}

impl VirtualBass {
    /// Panics if `_panic_next()` was called (builds with `test-panic`); nothing otherwise.
    fn planted_panic(&mut self, _call: &str) {
        #[cfg(feature = "test-panic")]
        if std::mem::take(&mut self.panic_next) {
            panic!("planted panic in VirtualBass.{_call} (feature test-panic)");
        }
    }
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
                )
                .map_err(fir_error)?,
                block,
                #[cfg(feature = "test-panic")]
                panic_next: false,
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
            self.planted_panic("process");
            let x = float64_vector("x", x)?;
            let x = samples(&x);
            let mut out = vec![0.0; x.len()];
            let energies = self
                .inner
                .process(&x, current, target, &mut out)
                .map_err(fir_error)?;
            Ok((PyArray1::from_vec(py, out), energies.bass, energies.made))
        })
    }

    /// Both filters back at rest.
    fn reset(&mut self) -> PyResult<()> {
        guard(|| {
            self.planted_panic("reset");
            self.inner.reset();
            Ok(())
        })
    }

    /// The whole state, as numpy's stage keeps it: `{"band": ..., "out": ...}`, each the state of
    /// a numpy `PartitionedFIR` (`PartitionedFIR.state`'s keys).
    fn state<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
        guard(|| {
            let state = self.inner.state();
            let bins = self.block + 1;
            let out = PyDict::new(py);
            out.set_item("band", partitioned_dict(py, &state.band, bins)?)?;
            out.set_item("out", partitioned_dict(py, &state.out, bins)?)?;
            Ok(out)
        })
    }

    /// Takes a state from numpy's stage (the keys of `state()`); a `ValueError` changes nothing.
    fn set_state(&mut self, state: &Bound<'_, PyDict>) -> PyResult<()> {
        guard(|| {
            self.planted_panic("set_state");
            let part = |name: &str| -> PyResult<fir::PartitionedState> {
                let dict = item(state, name)?
                    .cast_into::<PyDict>()
                    .map_err(|_| PyTypeError::new_err(format!("state: {name} must be a dict")))?;
                partitioned_state(&dict)
            };
            let state = virtual_bass::VirtualBassState {
                band: part("band")?,
                out: part("out")?,
            };
            self.inner.set_state(&state).map_err(fir_error)
        })
    }

    /// Makes the next `process`, `reset` or `set_state` panic inside its guard: the tests of the
    /// stage's failure path. Only in builds with `test-panic`.
    #[cfg(feature = "test-panic")]
    fn _panic_next(&mut self) {
        self.panic_next = true;
    }
}

#[pymodule]
fn aurasync_engine(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(read, module)?)?;
    module.add_function(wrap_pyfunction!(capabilities, module)?)?;
    module.add_class::<SpatialUpmix>()?;
    module.add_class::<AmbienceExtractor>()?;
    module.add_class::<StreamingFir>()?;
    module.add_class::<PartitionedFir>()?;
    module.add_class::<VirtualBass>()?;
    #[cfg(feature = "test-panic")]
    module.add_function(wrap_pyfunction!(_panic, module)?)?;
    #[cfg(feature = "test-panic")]
    module.add_function(wrap_pyfunction!(_panic_outside_the_read, module)?)?;
    Ok(())
}
