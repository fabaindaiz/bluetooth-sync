//! The ambience extractor: the mono ambience of a stereo stream, by inter-channel coherence
//! (Avendaño and Jot 2002, equations (11) and (12)).
//!
//! A port of `host/src/aurasync/dsp/ambience.py` (`Extractor`), which stays the oracle: the
//! host's golden (`host/tests/test_ambience_rust.py`) holds this within 1e-9 of it. Per frame of
//! a streaming STFT (root-Hann, `n_fft` points, hop `hop`, latency `n_fft`) and per bin: the
//! cross and auto spectra smoothed with the forgetting factor `lam`, the coherence
//! `|P12| / sqrt(P11 P22)`, the ambience index `1 - coherence` (zero where the weaker channel has
//! less than `min_energy` of the stronger's energy), the gain `Gamma` of equation (12), and the
//! mid `(L + R) / 2` weighted by it, back to time by overlap-add. This file follows numpy
//! operation by operation, in numpy's order, so the two round alike:
//!
//! - `abs` of a complex number is `hypot` (numpy's `npy_cabs`), and `abs(z) ** 2` squares it;
//! - a real scalar times a complex array is a componentwise product (numpy's complex product with
//!   a zero imaginary part gives the same bits), and `/ 2` halves both parts exactly;
//! - the overlap-add is divided by the sum of squared windows (a division, as numpy's `/=`), and
//!   left undivided where that sum is not above [`FLOOR`];
//! - the FFTs are `realfft` (on `rustfft`) instead of numpy's pocketfft: the only difference that
//!   is not a matter of order, at the level of 1e-16 of the signal.
//!
//! The extractor runs once per input stream, not per speaker. All the state lives in
//! [`Extractor`]; [`Extractor::process`] allocates nothing for blocks up to the size reserved
//! ([`Extractor::reserve`]). [`Extractor::state`] and [`Extractor::set_state`] move the whole
//! state to and from the numpy extractor at a cut's bottom (the live switch of engine), exactly.

use std::f64::consts::PI;
use std::fmt;
use std::sync::Arc;

pub use realfft::num_complex::Complex;
use realfft::{ComplexToReal, RealFftPlanner, RealToComplex};

/// The STFT's length (`ambience.N_FFT`): ~43 ms at 48 kHz, and the extractor's latency.
pub const N_FFT: usize = 2048;
/// The STFT's hop (`ambience.SALTO`): 75 % overlap.
pub const HOP: usize = N_FFT / 4;
/// Below this sum of squared windows the overlap-add is not divided (`ambience._PISO_NORMA`).
pub const FLOOR: f64 = 1e-8;
/// The regulariser of the coherence's and the energy ratio's denominators (numpy's `1e-20`).
const TINY: f64 = 1e-20;

/// The knobs (`ambience.Parametros`): equation (12)'s curve and the smoothing.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Params {
    /// The forgetting factor of the smoothing (`lam`).
    pub lam: f64,
    /// Where the curve's knee is (`umbral`, the paper's `Phi_0`).
    pub threshold: f64,
    /// The curve's floor (`mu0`).
    pub mu0: f64,
    /// The curve's ceiling (`mu1`).
    pub mu1: f64,
    /// The curve's slope (`sigma`).
    pub sigma: f64,
    /// The least share of the stronger channel's energy the weaker must have for a bin to count
    /// as ambience (`energia_minima`).
    pub min_energy: f64,
}

impl Default for Params {
    fn default() -> Self {
        Self {
            lam: 0.9,
            threshold: 0.5,
            mu0: 0.0,
            mu1: 1.0,
            sigma: 2.0,
            min_energy: 0.25,
        }
    }
}

/// Why a call was refused. Nothing changes when it is.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum AmbienceError {
    /// `left` and `right` differ in length.
    LengthMismatch { left: usize, right: usize },
    /// The output slice is not as long as the block.
    OutputMismatch,
    /// A state whose size does not fit this extractor.
    BadShape(String),
}

impl fmt::Display for AmbienceError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::LengthMismatch { left, right } => {
                write!(f, "the channels have different lengths: {left} and {right}")
            }
            Self::OutputMismatch => write!(f, "the output must be as long as the block"),
            Self::BadShape(what) => write!(f, "{what}"),
        }
    }
}

impl std::error::Error for AmbienceError {}

/// The whole state of the extractor: what moves between engines at a cut's bottom.
#[derive(Debug, Clone, PartialEq)]
pub struct State {
    /// The smoothed cross spectrum, `n_fft / 2 + 1` bins.
    pub acc12: Vec<Complex<f64>>,
    /// The smoothed auto spectra.
    pub acc11: Vec<f64>,
    pub acc22: Vec<f64>,
    /// Input not yet consumed by a frame (the same length on both sides).
    pub pending_left: Vec<f64>,
    pub pending_right: Vec<f64>,
    /// The overlap-add and its sum of squared windows, `n_fft` samples each.
    pub ola: Vec<f64>,
    pub norm: Vec<f64>,
    /// Output computed but not yet given.
    pub ready: Vec<f64>,
}

/// The streaming extractor: [`Extractor::process`] takes a stereo block and gives as many
/// samples of mono ambience, `n_fft` samples late.
pub struct Extractor {
    n_fft: usize,
    hop: usize,
    bins: usize,
    params: Params,
    window: Vec<f64>,
    window2: Vec<f64>,
    forward: Arc<dyn RealToComplex<f64>>,
    inverse: Arc<dyn ComplexToReal<f64>>,
    forward_scratch: Vec<Complex<f64>>,
    inverse_scratch: Vec<Complex<f64>>,

    // State.
    acc12: Vec<Complex<f64>>,
    acc11: Vec<f64>,
    acc22: Vec<f64>,
    pending_left: Vec<f64>,
    pending_right: Vec<f64>,
    pending_len: usize,
    ola: Vec<f64>,
    norm: Vec<f64>,
    ready: Vec<f64>,
    ready_len: usize,

    // Scratch, sized once.
    frame_left: Vec<f64>,
    frame_right: Vec<f64>,
    spectrum_left: Vec<Complex<f64>>,
    spectrum_right: Vec<Complex<f64>>,
    mid: Vec<Complex<f64>>,
    time: Vec<f64>,
    /// The block the buffers were sized for.
    max_block: usize,
}

/// numpy's `np.sqrt(np.hanning(n + 1)[:n])`.
fn root_hann(n: usize) -> Vec<f64> {
    let m = n + 1;
    (0..n)
        .map(|k| {
            let x = (1.0 - m as f64) + 2.0 * k as f64;
            (0.5 + 0.5 * (PI * x / (m as f64 - 1.0)).cos()).sqrt()
        })
        .collect()
}

/// `ambience.mapeo`: equation (12)'s `Gamma`, smooth on purpose.
fn curve(index: f64, p: &Params) -> f64 {
    ((p.mu1 - p.mu0) / 2.0) * (p.sigma * PI * (index - p.threshold)).tanh()
        + ((p.mu1 + p.mu0) / 2.0)
}

fn scaled(z: Complex<f64>, s: f64) -> Complex<f64> {
    Complex::new(z.re * s, z.im * s)
}

/// `z * conj(w)` as numpy multiplies complex numbers.
fn times_conj(z: Complex<f64>, w: Complex<f64>) -> Complex<f64> {
    let (br, bi) = (w.re, -w.im);
    Complex::new(z.re * br - z.im * bi, z.re * bi + z.im * br)
}

/// numpy's `abs` of a complex number.
fn magnitude(z: Complex<f64>) -> f64 {
    z.re.hypot(z.im)
}

impl Extractor {
    /// An extractor with an STFT of `n_fft` points and hop `hop` (numpy's defaults: [`N_FFT`],
    /// [`HOP`]), its buffers sized for blocks of `max_block`. Default params.
    pub fn new(n_fft: usize, hop: usize, max_block: usize) -> Self {
        assert!(
            n_fft >= 2 && hop >= 1 && hop <= n_fft,
            "n_fft {n_fft}, hop {hop}"
        );
        let mut planner = RealFftPlanner::<f64>::new();
        let forward = planner.plan_fft_forward(n_fft);
        let inverse = planner.plan_fft_inverse(n_fft);
        let forward_scratch = forward.make_scratch_vec();
        let inverse_scratch = inverse.make_scratch_vec();
        let bins = n_fft / 2 + 1;
        let window = root_hann(n_fft);
        let window2 = window.iter().map(|w| w * w).collect();
        let mut extractor = Self {
            n_fft,
            hop,
            bins,
            params: Params::default(),
            window,
            window2,
            forward,
            inverse,
            forward_scratch,
            inverse_scratch,
            acc12: vec![Complex::new(0.0, 0.0); bins],
            acc11: vec![0.0; bins],
            acc22: vec![0.0; bins],
            pending_left: Vec::new(),
            pending_right: Vec::new(),
            pending_len: 0,
            ola: vec![0.0; n_fft],
            norm: vec![0.0; n_fft],
            ready: Vec::new(),
            ready_len: 0,
            frame_left: vec![0.0; n_fft],
            frame_right: vec![0.0; n_fft],
            spectrum_left: vec![Complex::new(0.0, 0.0); bins],
            spectrum_right: vec![Complex::new(0.0, 0.0); bins],
            mid: vec![Complex::new(0.0, 0.0); bins],
            time: vec![0.0; n_fft],
            max_block: 0,
        };
        extractor.reset();
        extractor.reserve(max_block);
        extractor
    }

    /// The STFT's length (and the extractor's latency).
    pub fn n_fft(&self) -> usize {
        self.n_fft
    }

    /// The params in use.
    pub fn params(&self) -> Params {
        self.params
    }

    /// New knobs (numpy's `p = ...`); they apply from the next frame.
    pub fn set_params(&mut self, params: Params) {
        self.params = params;
    }

    /// Back to a new extractor's state (numpy's `reiniciar`): `n_fft` samples of silence ready,
    /// nothing pending, the smoothing at zero. The params stay.
    pub fn reset(&mut self) {
        self.acc12.fill(Complex::new(0.0, 0.0));
        self.acc11.fill(0.0);
        self.acc22.fill(0.0);
        self.pending_len = 0;
        self.ola.fill(0.0);
        self.norm.fill(0.0);
        self.grow(0, self.n_fft);
        self.ready[..self.n_fft].fill(0.0);
        self.ready_len = self.n_fft;
    }

    /// Sizes the buffers for blocks of up to `max_block` samples (they only grow): after it,
    /// [`Extractor::process`] allocates nothing for such blocks, from any state that numpy's
    /// extractor reaches.
    pub fn reserve(&mut self, max_block: usize) {
        self.max_block = self.max_block.max(max_block);
        let pending = self.n_fft + self.max_block;
        let ready = self.n_fft + self.max_block + self.hop;
        self.grow(pending, ready);
    }

    fn grow(&mut self, pending: usize, ready: usize) {
        if self.pending_left.len() < pending {
            self.pending_left.resize(pending, 0.0);
            self.pending_right.resize(pending, 0.0);
        }
        if self.ready.len() < ready {
            self.ready.resize(ready, 0.0);
        }
    }

    /// Room for a block of `n` from the present state; allocates only past what was reserved.
    fn make_room(&mut self, n: usize) {
        if n > self.max_block {
            self.reserve(n);
        }
        let held = self.pending_len + n;
        let produced = if held >= self.n_fft {
            ((held - self.n_fft) / self.hop + 1) * self.hop
        } else {
            0
        };
        self.grow(held, self.ready_len + produced);
    }

    /// One stereo block in; its mono ambience, as many samples, into `out`.
    pub fn process(
        &mut self,
        left: &[f64],
        right: &[f64],
        out: &mut [f64],
    ) -> Result<(), AmbienceError> {
        if left.len() != right.len() {
            return Err(AmbienceError::LengthMismatch {
                left: left.len(),
                right: right.len(),
            });
        }
        let n = left.len();
        if out.len() != n {
            return Err(AmbienceError::OutputMismatch);
        }
        self.make_room(n);

        let start = self.pending_len;
        self.pending_left[start..start + n].copy_from_slice(left);
        self.pending_right[start..start + n].copy_from_slice(right);
        self.pending_len += n;
        let mut at = 0;
        while self.pending_len - at >= self.n_fft {
            self.frame(at);
            at += self.hop;
        }
        self.pending_left.copy_within(at..self.pending_len, 0);
        self.pending_right.copy_within(at..self.pending_len, 0);
        self.pending_len -= at;

        // numpy pads the ready samples with zeros when there are fewer than the block.
        let available = self.ready_len.min(n);
        out[..available].copy_from_slice(&self.ready[..available]);
        out[available..].fill(0.0);
        self.ready.copy_within(available..self.ready_len, 0);
        self.ready_len -= available;
        Ok(())
    }

    /// One STFT frame from `pending[at .. at + n_fft]`: the smoothing, the gain, the overlap-add,
    /// and one hop of output appended to the ready buffer.
    fn frame(&mut self, at: usize) {
        let n_fft = self.n_fft;
        for i in 0..n_fft {
            self.frame_left[i] = self.pending_left[at + i] * self.window[i];
            self.frame_right[i] = self.pending_right[at + i] * self.window[i];
        }
        self.forward
            .process_with_scratch(
                &mut self.frame_left,
                &mut self.spectrum_left,
                &mut self.forward_scratch,
            )
            .expect("buffers sized by the plan");
        self.forward
            .process_with_scratch(
                &mut self.frame_right,
                &mut self.spectrum_right,
                &mut self.forward_scratch,
            )
            .expect("buffers sized by the plan");

        let p = self.params;
        let lam = p.lam;
        let rest = 1.0 - lam;
        for b in 0..self.bins {
            let (fl, fr) = (self.spectrum_left[b], self.spectrum_right[b]);
            let cross = times_conj(scaled(fl, rest), fr);
            let acc = scaled(self.acc12[b], lam);
            self.acc12[b] = Complex::new(acc.re + cross.re, acc.im + cross.im);
            let (ml, mr) = (magnitude(fl), magnitude(fr));
            self.acc11[b] = lam * self.acc11[b] + rest * (ml * ml);
            self.acc22[b] = lam * self.acc22[b] + rest * (mr * mr);
            let (p11, p22) = (self.acc11[b], self.acc22[b]);

            let coherence = magnitude(self.acc12[b]) / (p11 * p22 + TINY).sqrt();
            let mut index = (1.0 - coherence).clamp(0.0, 1.0);
            let (weak, strong) = (p11.min(p22), p11.max(p22) + TINY);
            if weak / strong < p.min_energy {
                index = 0.0;
            }
            let gain = curve(index, &p);
            let mid = Complex::new(fl.re + fr.re, fl.im + fr.im);
            self.mid[b] = scaled(scaled(mid, 0.5), gain);
        }

        // numpy's irfft ignores these imaginary parts; realfft wants them zero.
        self.mid[0].im = 0.0;
        if n_fft.is_multiple_of(2) {
            self.mid[self.bins - 1].im = 0.0;
        }
        self.inverse
            .process_with_scratch(&mut self.mid, &mut self.time, &mut self.inverse_scratch)
            .expect("buffers sized by the plan, edge bins real");
        let scale = 1.0 / n_fft as f64;
        for ((o, &t), &w) in self.ola.iter_mut().zip(&self.time).zip(&self.window) {
            *o += t * scale * w;
        }
        for (n, w2) in self.norm.iter_mut().zip(&self.window2) {
            *n += w2;
        }

        let hop = self.hop;
        let ready_at = self.ready_len;
        for ((r, &o), &n) in self.ready[ready_at..ready_at + hop]
            .iter_mut()
            .zip(&self.ola[..hop])
            .zip(&self.norm[..hop])
        {
            *r = if n > FLOOR { o / n } else { o };
        }
        self.ready_len += hop;
        self.ola.copy_within(hop.., 0);
        self.ola[n_fft - hop..].fill(0.0);
        self.norm.copy_within(hop.., 0);
        self.norm[n_fft - hop..].fill(0.0);
    }

    /// The whole state, copied (to move it to numpy's extractor).
    pub fn state(&self) -> State {
        State {
            acc12: self.acc12.clone(),
            acc11: self.acc11.clone(),
            acc22: self.acc22.clone(),
            pending_left: self.pending_left[..self.pending_len].to_vec(),
            pending_right: self.pending_right[..self.pending_len].to_vec(),
            ola: self.ola.clone(),
            norm: self.norm.clone(),
            ready: self.ready[..self.ready_len].to_vec(),
        }
    }

    /// Takes `state` as its own (from numpy's extractor). Every size is checked first; on an
    /// error nothing changes.
    pub fn set_state(&mut self, state: &State) -> Result<(), AmbienceError> {
        let check = |name: &str, got: usize, want: usize| {
            if got == want {
                Ok(())
            } else {
                Err(AmbienceError::BadShape(format!(
                    "state: {name} has length {got}, expected {want}"
                )))
            }
        };
        check("acc12", state.acc12.len(), self.bins)?;
        check("acc11", state.acc11.len(), self.bins)?;
        check("acc22", state.acc22.len(), self.bins)?;
        check(
            "pending_right",
            state.pending_right.len(),
            state.pending_left.len(),
        )?;
        check("ola", state.ola.len(), self.n_fft)?;
        check("norm", state.norm.len(), self.n_fft)?;

        let pending = state.pending_left.len();
        let ready = state.ready.len();
        self.grow(
            pending + self.max_block,
            ready + pending + self.max_block + self.hop,
        );
        self.acc12.copy_from_slice(&state.acc12);
        self.acc11.copy_from_slice(&state.acc11);
        self.acc22.copy_from_slice(&state.acc22);
        self.pending_left[..pending].copy_from_slice(&state.pending_left);
        self.pending_right[..pending].copy_from_slice(&state.pending_right);
        self.pending_len = pending;
        self.ola.copy_from_slice(&state.ola);
        self.norm.copy_from_slice(&state.norm);
        self.ready[..ready].copy_from_slice(&state.ready);
        self.ready_len = ready;
        Ok(())
    }
}
