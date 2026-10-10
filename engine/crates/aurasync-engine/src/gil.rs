//! When a call lets go of the interpreter (`Python::detach`): only when it is long.
//!
//! Letting go of the GIL is free while no other Python thread wants it, but taking it back is
//! not when one is busy: the waiting thread asks the busy one to yield and waits up to
//! `sys.getswitchinterval()` (5 ms by default) for it. A real-time block makes dozens of short
//! calls (each well under a millisecond), so it would pay that wait dozens of times: the convoy
//! effect, measured in `docs/research/experimentos/20-…` §15. Holding the GIL through a short
//! call costs the other threads at most that call, which is less than a switch interval.
//!
//! So a call lets go only when its input has at least `detach_min_samples` samples (by default
//! [`DEFAULT_MIN_SAMPLES`], 16 blocks of 4096): offline renders and long analyses still free
//! the other threads, the engine's per-block calls keep the GIL. `set_detach_min_samples`
//! changes it for the whole process (0: always let go, as before; a huge value: never), for the
//! probes and the tests that compare them.

use std::sync::atomic::{AtomicUsize, Ordering};

use pyo3::marker::Ungil;
use pyo3::prelude::*;

use crate::error::guard;

/// The default threshold, in samples of a call's input: 16 blocks of 4096.
pub const DEFAULT_MIN_SAMPLES: usize = 1 << 16;

static MIN_SAMPLES: AtomicUsize = AtomicUsize::new(DEFAULT_MIN_SAMPLES);

/// The current threshold, in samples.
pub fn min_samples() -> usize {
    MIN_SAMPLES.load(Ordering::Relaxed)
}

/// `work()`, without the interpreter when `samples` (the length of the call's input) reaches the
/// threshold, with it otherwise. Its inputs must be copies (`convert::owned`) either way, since
/// the threshold can change between two calls.
pub fn run<T, F>(py: Python<'_>, samples: usize, work: F) -> T
where
    F: Ungil + FnOnce() -> T,
    T: Ungil,
{
    if samples >= min_samples() {
        py.detach(work)
    } else {
        work()
    }
}

/// Sets the threshold for the whole process and gives the previous one: a call whose input has
/// at least `samples` samples lets go of the interpreter while it works.
#[pyfunction]
pub fn set_detach_min_samples(samples: usize) -> PyResult<usize> {
    guard(|| Ok(MIN_SAMPLES.swap(samples, Ordering::Relaxed)))
}
