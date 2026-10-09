//! The virtual bass's per-block work: the harmonics of a bass band (a non-linear device).
//!
//! A port of `VirtualBass.process` in `host/src/aurasync/dsp/virtual_bass.py`, which stays the
//! oracle (`host/tests/test_virtual_bass_rust.py` holds this within 1e-9 of it). The filters'
//! design (`_filters`: the two impulse responses and the calibration gain) is design-time and
//! stays numpy; this owns the two [`PartitionedFir`]s directly, so a block is one call:
//! band filter, full-wave rectifier (`abs`), harmonics filter, calibration, then the gain,
//! constant or ramped linearly across the block when the level changes live.
//!
//! The rectifier is continuous (`|x|` has no threshold or sign decision on a near-zero input that
//! a 1-ulp change could flip), so nothing here is ill-conditioned: the only differences from numpy
//! are the FFTs' (see [`crate::fir`]) and the order of the two energy sums.
//!
//! **Allocation.** `process` allocates nothing: the block is cut into chunks of the filters'
//! `block` (the filters cut anyway, and the chain is causal, so band-chunk, harmonics-chunk,
//! next chunk is the same signal as band-all, harmonics-all) and two scratch chunks are made once.

use std::fmt;

use crate::fir::{FirError, PartitionedFir, PartitionedState};

/// Why a call was refused. Nothing changes when it is.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum VirtualBassError {
    /// One of the two filters refused it; the filter's error is the [`source`](std::error::Error::source).
    Filter(FirError),
}

impl fmt::Display for VirtualBassError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Filter(_) => write!(f, "a filter of the virtual bass refused the call"),
        }
    }
}

impl std::error::Error for VirtualBassError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            Self::Filter(error) => Some(error),
        }
    }
}

impl From<FirError> for VirtualBassError {
    fn from(error: FirError) -> Self {
        Self::Filter(error)
    }
}

/// What moves between engines at a cut's bottom: the two filters' states. The gain's position is
/// the numpy stage's (`_current`), passed to every call, so it is not kept here.
#[derive(Debug, Clone, PartialEq)]
pub struct VirtualBassState {
    /// The bass band filter.
    pub band: PartitionedState,
    /// The harmonics band filter.
    pub out: PartitionedState,
}

/// The harmonic generator of one speaker (numpy's `VirtualBass`, per block).
pub struct VirtualBass {
    band: PartitionedFir,
    out: PartitionedFir,
    calibration: f64,
    block: usize,
    bass: Vec<f64>,
    rectified: Vec<f64>,
}

/// The energies of one block: of the bass band and of the harmonics made from it.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Energies {
    /// The sum of squares of the bass band's output.
    pub bass: f64,
    /// The sum of squares of the harmonics out.
    pub made: f64,
}

impl fmt::Debug for VirtualBass {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("VirtualBass")
            .field("band", &self.band)
            .field("out", &self.out)
            .field("calibration", &self.calibration)
            .field("block", &self.block)
            .finish_non_exhaustive()
    }
}

impl VirtualBass {
    /// The generator for filters `band` and `out` (at least one tap each) cut into partitions of
    /// `block` (at least one) samples, with the `calibration` gain, at rest.
    ///
    /// # Errors
    ///
    /// [`VirtualBassError::Filter`] with [`FirError::NoTaps`] if a filter has no taps, with
    /// [`FirError::ZeroBlock`] if `block` is 0.
    pub fn new(
        band: &[f64],
        out: &[f64],
        calibration: f64,
        block: usize,
    ) -> Result<Self, VirtualBassError> {
        Ok(Self {
            band: PartitionedFir::new(band, block)?,
            out: PartitionedFir::new(out, block)?,
            calibration,
            block,
            bass: vec![0.0; block],
            rectified: vec![0.0; block],
        })
    }

    /// The filters' partition length in samples (the `block` it was built with).
    #[must_use]
    pub fn block(&self) -> usize {
        self.block
    }

    /// Both filters back at rest (a fresh generator's state).
    pub fn reset(&mut self) {
        self.band.reset();
        self.out.reset();
    }

    /// The harmonics of `x` into `out`, with the gain moving linearly from `current` to `target`
    /// across the block when they differ (numpy: `current + (target - current) * k / n`, k from 1
    /// to n), else `target`. Returns the energies of the bass band and of the harmonics. Nothing
    /// is allocated.
    ///
    /// # Errors
    ///
    /// [`VirtualBassError::Filter`] with [`FirError::OutputMismatch`] if `out` is not as long as
    /// `x`.
    pub fn process(
        &mut self,
        x: &[f64],
        current: f64,
        target: f64,
        out: &mut [f64],
    ) -> Result<Energies, VirtualBassError> {
        if out.len() != x.len() {
            return Err(FirError::OutputMismatch.into());
        }
        let n = x.len();
        let ramp = n > 0 && target != current;
        let mut energies = Energies {
            bass: 0.0,
            made: 0.0,
        };
        let mut done = 0;
        for (x, out) in x.chunks(self.block).zip(out.chunks_mut(self.block)) {
            let len = x.len();
            let (bass, rectified) = (&mut self.bass[..len], &mut self.rectified[..len]);
            self.band.process(x, bass)?;
            for (r, b) in rectified.iter_mut().zip(bass.iter()) {
                *r = b.abs();
            }
            self.out.process(rectified, out)?;
            for (i, y) in out.iter_mut().enumerate() {
                *y *= self.calibration;
                if ramp {
                    let k = (done + i + 1) as f64;
                    *y *= current + (target - current) * k / n as f64;
                } else {
                    *y *= target;
                }
            }
            energies.bass += bass.iter().map(|b| b * b).sum::<f64>();
            energies.made += out.iter().map(|y| y * y).sum::<f64>();
            done += len;
        }
        Ok(energies)
    }

    /// The state, copied (to move it to numpy's stage).
    #[must_use]
    pub fn to_state(&self) -> VirtualBassState {
        VirtualBassState {
            band: self.band.to_state(),
            out: self.out.to_state(),
        }
    }

    /// Takes `state` as its own (from numpy's stage). Both are checked first; on an error
    /// nothing changes.
    ///
    /// # Errors
    ///
    /// [`VirtualBassError::Filter`] with the filter's [`FirError::BadShape`] or
    /// [`FirError::OutOfRange`] if `state` does not fit these filters.
    ///
    /// # Panics
    ///
    /// Never in practice: the state it restores on an error is the one it just held.
    pub fn set_state(&mut self, state: &VirtualBassState) -> Result<(), VirtualBassError> {
        let band = self.band.to_state();
        self.band.set_state(&state.band)?;
        if let Err(error) = self.out.set_state(&state.out) {
            self.band
                .set_state(&band)
                .expect("the state it just held fits");
            return Err(error.into());
        }
        Ok(())
    }
}
