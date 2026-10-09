//! The test-panic switch: a panic planted on purpose, to test that the guard turns it into
//! `EnginePanic` and that the host recovers.
//!
//! Without the `test-panic` feature [`Planted`] is an empty struct whose `check` does nothing, so
//! the production build carries neither the switch nor the `_panic*` functions.

#[cfg(feature = "test-panic")]
use pyo3::prelude::*;

#[cfg(feature = "test-panic")]
use crate::error::guard;

/// A pyclass's armed/disarmed switch; each class keeps one and calls [`check`](Self::check) at
/// the top of the calls its tests plant a panic in.
#[derive(Debug, Default)]
pub struct Planted {
    /// `_panic_next()` was called: the next `check` panics.
    #[cfg(feature = "test-panic")]
    armed: bool,
}

impl Planted {
    /// Arms the switch: the next [`check`](Self::check) panics (and disarms it).
    #[cfg(feature = "test-panic")]
    pub fn arm(&mut self) {
        self.armed = true;
    }

    /// Panics if armed (builds with `test-panic`); nothing otherwise.
    #[cfg(feature = "test-panic")]
    pub fn check(&mut self, class: &str, call: &str) {
        assert!(
            !std::mem::take(&mut self.armed),
            "planted panic in {class}.{call} (feature test-panic)"
        );
    }

    /// Panics if armed (builds with `test-panic`); nothing otherwise.
    #[cfg(not(feature = "test-panic"))]
    #[expect(clippy::unused_self, reason = "only the test-panic build reads self")]
    pub fn check(&mut self, _class: &str, _call: &str) {}
}

/// Panics before any argument is looked at: the test that the guard covers the whole body, not
/// only the read. Only in builds with `test-panic`.
#[cfg(feature = "test-panic")]
#[pyfunction]
pub fn _panic_outside_the_read(value: &Bound<'_, PyAny>) -> PyResult<()> {
    guard(|| {
        panic!(
            "planted panic before the arguments ({})",
            value.get_type().name()?
        )
    })
}
