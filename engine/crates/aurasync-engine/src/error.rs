//! The binding's one error type and the Python exceptions it becomes.
//!
//! Every exported function runs inside [`guard`], returns `Result<_, EngineError>` internally and
//! converts once, at the boundary, with `impl From<EngineError> for PyErr`:
//!
//! | `EngineError`          | Python                                                          |
//! |------------------------|-----------------------------------------------------------------|
//! | `Panic`                | `EnginePanic` (a caught Rust panic)                             |
//! | `Internal`             | `EngineError` (a broken invariant of this crate, never the caller's) |
//! | `Invalid`              | `ValueError` (a configuration, argument or state that is refused)    |
//! | `Type`                 | `TypeError` (an argument of the wrong type)                     |
//! | `Python`               | the Python error itself, unchanged                              |
//!
//! `EnginePanic` derives from `EngineError`, which derives from `RuntimeError`: the host's
//! dispatcher (`aurasync/dsp/backend.py`) takes a `RuntimeError` as the engine failing.

use std::fmt;
use std::panic::{self, AssertUnwindSafe};

use aurasync_dsp::ambience::AmbienceError;
use aurasync_dsp::fir::FirError;
use aurasync_dsp::interpolation::ReadError;
use aurasync_dsp::spatial::SpatialError;
use aurasync_dsp::virtual_bass::VirtualBassError;
use numpy::{AsSliceError, BorrowError};
use pyo3::PyErr;
use pyo3::PyResult;
use pyo3::exceptions::{PyRuntimeError, PyTypeError, PyValueError};

/// The Python exceptions of the module (exported from `lib.rs`).
pub mod exceptions {
    use super::PyRuntimeError;

    pyo3::create_exception!(
        aurasync_engine,
        EngineError,
        PyRuntimeError,
        "The engine failed in a way that is not the caller's fault: a broken invariant of the \
         extension. The host takes it (it is a `RuntimeError`) as the engine failing."
    );

    pyo3::create_exception!(
        aurasync_engine,
        EnginePanic,
        EngineError,
        "A Rust panic, caught at the boundary. The object that raised it may be half-written and \
         must not be used again."
    );
}

/// Why an exported call failed, before it becomes a Python exception.
#[derive(Debug)]
pub enum EngineError {
    /// A Rust panic, with its message.
    Panic(String),
    /// A broken invariant of this crate (not the caller's argument).
    Internal(String),
    /// A configuration, argument or state that is refused: a `ValueError`.
    Invalid(String),
    /// An argument of the wrong type: a `TypeError`.
    Type(String),
    /// A Python error raised while converting (a failed `extract`, `set_item`...), kept as it is.
    Python(PyErr),
}

impl fmt::Display for EngineError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Panic(message) => write!(f, "aurasync_engine panicked: {message}"),
            Self::Internal(message) | Self::Invalid(message) | Self::Type(message) => {
                f.write_str(message)
            }
            Self::Python(error) => write!(f, "{error}"),
        }
    }
}

impl std::error::Error for EngineError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            Self::Python(error) => Some(error),
            _ => None,
        }
    }
}

impl From<EngineError> for PyErr {
    fn from(error: EngineError) -> Self {
        match error {
            EngineError::Panic(_) => exceptions::EnginePanic::new_err(error.to_string()),
            EngineError::Internal(message) => exceptions::EngineError::new_err(message),
            EngineError::Invalid(message) => PyValueError::new_err(message),
            EngineError::Type(message) => PyTypeError::new_err(message),
            EngineError::Python(error) => error,
        }
    }
}

impl From<PyErr> for EngineError {
    fn from(error: PyErr) -> Self {
        Self::Python(error)
    }
}

impl From<FirError> for EngineError {
    fn from(error: FirError) -> Self {
        Self::Invalid(error.to_string())
    }
}

impl From<AmbienceError> for EngineError {
    fn from(error: AmbienceError) -> Self {
        Self::Invalid(error.to_string())
    }
}

impl From<SpatialError> for EngineError {
    fn from(error: SpatialError) -> Self {
        Self::Invalid(error.to_string())
    }
}

impl From<VirtualBassError> for EngineError {
    /// The filter's own error is the message: its `Display` names what was refused, and the
    /// virtual bass's own says only that a filter refused (it is the `source`).
    fn from(error: VirtualBassError) -> Self {
        match error {
            VirtualBassError::Filter(error) => error.into(),
        }
    }
}

impl From<ReadError> for EngineError {
    fn from(error: ReadError) -> Self {
        match error {
            ReadError::OutOfRange { .. } | ReadError::LengthMismatch => {
                Self::Invalid(error.to_string())
            }
            // `Reader::read` sizes the table for the block, so this is a bug here, not the caller's.
            ReadError::BlockTooLarge => Self::Internal(error.to_string()),
        }
    }
}

impl From<BorrowError> for EngineError {
    fn from(error: BorrowError) -> Self {
        Self::Internal(error.to_string())
    }
}

impl From<AsSliceError> for EngineError {
    fn from(error: AsSliceError) -> Self {
        Self::Internal(error.to_string())
    }
}

/// Runs `body`, turning a panic anywhere inside it into `EnginePanic` and any error into its
/// exception. Every exported function is one call to this, so nothing it does can unwind into
/// `PyO3` (whose `PanicException` is a `BaseException`, which the service's loop would not catch).
pub fn guard<T>(body: impl FnOnce() -> Result<T, EngineError>) -> PyResult<T> {
    match panic::catch_unwind(AssertUnwindSafe(body)) {
        Ok(result) => result.map_err(PyErr::from),
        Err(payload) => {
            let message = payload
                .downcast_ref::<&str>()
                .copied()
                .or_else(|| payload.downcast_ref::<String>().map(String::as_str))
                .unwrap_or("(no message)");
            Err(EngineError::Panic(message.to_owned()).into())
        }
    }
}
