//! Pure-Rust DSP for aurasync: the band-limited fractional delay read ([`interpolation`]), the
//! spatial upmix ([`spatial`]), the ambience extractor ([`ambience`]), the FIR filters by FFT
//! convolution ([`fir`]), the virtual bass ([`virtual_bass`]), the true-peak limiter
//! ([`limiter`]) and the loudness meter's per-block work ([`loudness`]).
//!
//! Every stage is checked against the numpy original it replaces, within 1e-9, and keeps the
//! order of numpy's arithmetic. A stage allocates nothing per block once it has warmed up.
//!
//! ```
//! use aurasync_dsp::fir::StreamingFir;
//!
//! let mut fir = StreamingFir::new(&[0.5, 0.25]).unwrap();
//! let mut out = [0.0; 3];
//! fir.process(&[1.0, 0.0, 0.0], &mut out).unwrap();
//! assert_eq!(out, [0.5, 0.25, 0.0]);
//! ```
#![forbid(unsafe_code)]

pub mod ambience;
mod complex;
mod fft;
pub mod fir;
pub mod interpolation;
pub mod limiter;
pub mod loudness;
pub mod spatial;
mod stft;
pub mod virtual_bass;

/// The complex number of every spectrum here (the ambience and spatial states' `acc12`, the
/// partitioned filter's delay line): `num_complex`'s, as `realfft` gives it.
pub use realfft::num_complex::Complex;
