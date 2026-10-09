//! The FFT plans of every stage: one `realfft` planner per thread, so stages of the same size
//! share their plans (the planner caches them by length) and building one costs no twiddles.
//!
//! A plan for a given length is the same whichever planner instance makes it (`rustfft` chooses
//! the algorithm from the length and the CPU alone), so sharing the planner changes no output.

use std::cell::RefCell;
use std::sync::Arc;

use realfft::{ComplexToReal, RealFftPlanner, RealToComplex};

use crate::Complex;

/// A real-to-complex plan (numpy's `rfft`).
pub(crate) type Forward = Arc<dyn RealToComplex<f64>>;
/// A complex-to-real plan (numpy's `irfft`, unscaled).
pub(crate) type Inverse = Arc<dyn ComplexToReal<f64>>;

thread_local! {
    /// One planner per thread: plans are shared between stages (the planner caches them by
    /// length), so building a stage of a size already planned costs no twiddles.
    static PLANNER: RefCell<RealFftPlanner<f64>> = RefCell::new(RealFftPlanner::new());
}

/// The forward and inverse plans of length `size`, from this thread's planner.
pub(crate) fn plans(size: usize) -> (Forward, Inverse) {
    PLANNER.with_borrow_mut(|planner| {
        (
            planner.plan_fft_forward(size),
            planner.plan_fft_inverse(size),
        )
    })
}

/// The unscaled inverse of `spectrum` into `time` (`len(time)` points): numpy's `irfft` ignores
/// the edge bins' imaginary parts, `realfft` wants them zero, so they are zeroed first.
///
/// # Panics
///
/// If the buffers are not the plan's sizes (`spectrum` of `len(time) / 2 + 1` bins, `scratch` of
/// the plan's scratch length): a bug of the caller, which sizes them from the plan.
pub(crate) fn inverse_real(
    inverse: &Inverse,
    spectrum: &mut [Complex<f64>],
    time: &mut [f64],
    scratch: &mut [Complex<f64>],
) {
    spectrum[0].im = 0.0;
    if time.len().is_multiple_of(2) {
        spectrum[spectrum.len() - 1].im = 0.0;
    }
    inverse
        .process_with_scratch(spectrum, time, scratch)
        .expect("buffers sized by the plan, edge bins real");
}
