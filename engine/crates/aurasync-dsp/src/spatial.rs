//! The spatial and "frente intacto" upmix: each bin's direct part to the principal speakers at its
//! angle, its ambience to the ambient ones.
//!
//! A port of `host/src/aurasync/dsp/spatial.py` (`SpatialUpmix`), which stays the oracle: the
//! host's golden (`host/tests/test_spatial_rust.py`) holds this within 1e-9 of it. The algorithm,
//! per frame of a streaming STFT (root-Hann, `n_fft` points, hop `n_fft / 4`, latency `n_fft`)
//! and per bin, is described in the numpy module; this file follows it operation by operation,
//! in numpy's order, so the two round alike:
//!
//! - a real scalar times a complex array is a componentwise product (numpy's complex product with
//!   a zero imaginary part gives the same bits), and a complex array divided by a real scalar is
//!   multiplied by its reciprocal (numpy's complex division does exactly that);
//! - the FFTs are `realfft` (on `rustfft`) instead of numpy's pocketfft: the only difference that
//!   is not a matter of order, at the level of 1e-16 of the signal.
//!
//! The layout (which speaker is principal, the ring, the front pair, the back gap, the classic
//! loudness target) is derived here from each speaker's angle and role, as numpy's `set_layout`
//! does. All the state lives in [`SpatialUpmix`]; [`SpatialUpmix::process`] allocates nothing
//! for blocks up to the size reserved ([`SpatialUpmix::reserve`]). [`SpatialUpmix::to_state`] and
//! [`SpatialUpmix::set_state`] move the whole state to and from the numpy stage at a cut's
//! bottom (the live switch of engine), exactly.

use std::f64::consts::PI;
use std::fmt;

use crate::Complex;
use crate::complex::{magnitude, norm2, scaled, times_conj};
use crate::stft::{Pending, Stft, TINY, ambience_curve, produced};

/// The STFT's length (`spatial.N_FFT`), the same as the ambience extractor's.
pub const N_FFT: usize = 2048;
/// The STFT's hop (`spatial.HOP`).
pub const HOP: usize = N_FFT / 4;
/// The longest Haas delay of the ambience, and the length of its line.
pub const MAX_HAAS_MS: f64 = 30.0;
/// Samples of a new renderer's entry ramp.
pub const FADE_IN: usize = 4096;
/// Below this sum of squared windows the overlap-add is not divided (the edges).
pub const FLOOR: f64 = 1e-8;
/// A bin with less energy than this is silence: it gets no scale.
pub const SILENT: f64 = 1e-30;
/// In "frente intacto" the ambience is lifted by this much (`FRONT_AMBIENCE_BOOST_DB`).
pub const FRONT_BOOST_DB: f64 = 6.0;
/// `ambience.Parametros().energia_minima`: the weaker channel must have at least this share of
/// the stronger one's energy for a bin to count as ambience.
pub const MIN_ENERGY_RATIO: f64 = 0.25;
/// `ambience.Parametros()`'s curve (`ambience.mapeo`): the floor.
pub const MU0: f64 = 0.0;
/// `ambience.Parametros()`'s curve (`ambience.mapeo`): the ceiling.
pub const MU1: f64 = 1.0;
/// `ambience.Parametros()`'s curve (`ambience.mapeo`): the slope.
pub const SIGMA: f64 = 2.0;

const HALF_TURN: f64 = 180.0;
/// numpy's `et > 0.01 * energy`: below this share of the bin's energy, `L + R` nearly cancels and
/// the direct part takes the louder channel's phase instead.
const MIN_ENERGY_SHARE: f64 = 0.01;
/// numpy's `np.maximum(ep, 1e-40)`: the floor of the phase source's energy under the direct
/// part's square root.
const MIN_PHASE_ENERGY: f64 = 1e-40;
/// numpy's `np.maximum(out_energy, 1e-30)`: the floor of the render's energy under the loudness
/// scale's square root.
const MIN_OUT_ENERGY: f64 = 1e-30;
/// numpy's `np.maximum(hi - lo, 1e-9)`: the narrowest arc, in degrees, between two principals
/// that the direct part's pan divides by.
const MIN_ARC_DEG: f64 = 1e-9;
/// numpy's `hi - lo >= 180.0 - 1e-9`: the slack, in degrees, of the back gap's "180° or more".
const BACK_GAP_SLACK_DEG: f64 = 1e-9;

/// The knobs (`spatial.SpatialParams`).
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Params {
    /// The arc the principal speakers spread over, in degrees.
    pub arc_deg: f64,
    /// How much ambience goes to the ambient speakers.
    pub ambience: f64,
    /// The ambience's level, in dB.
    pub ambient_level_db: f64,
    /// Clamped to `[0, MAX_HAAS_MS]` by [`SpatialUpmix::set_params`].
    pub haas_ms: f64,
    /// The ambience curve's threshold.
    pub threshold: f64,
    /// The smoothing of the spectra between frames.
    pub lam: f64,
    /// "Frente intacto": the front pair is left as it is.
    pub front_intact: bool,
}

impl Default for Params {
    fn default() -> Self {
        Self {
            arc_deg: 105.0,
            ambience: 0.5,
            ambient_level_db: 3.0,
            haas_ms: 14.0,
            threshold: 0.5,
            lam: 0.9,
            front_intact: false,
        }
    }
}

/// Why a call was refused. Nothing changes when it is.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SpatialError {
    /// A stage that cannot run: `n_fft` below 2, a hop of 0 or longer than `n_fft`, or a sample
    /// rate of 0.
    InvalidConfig {
        /// The STFT's length asked for.
        n_fft: usize,
        /// The hop asked for.
        hop: usize,
        /// The sample rate asked for, in Hz.
        sr: u32,
    },
    /// `left` and `right` differ in length.
    LengthMismatch {
        /// The length of `left`.
        left: usize,
        /// The length of `right`.
        right: usize,
    },
    /// The output slices are not `speakers * block` long.
    OutputMismatch,
    /// A layout or state whose size does not fit this stage.
    BadShape {
        /// The layout's or state's field: a 1-D one by its name (`angles`, `ambient`, `acc12`,
        /// `norm`, ...); of a per-speaker one, `<name> rows` is its number of rows and
        /// `<name> row` the length of one of them (`ola_direct row`, ...).
        field: &'static str,
        /// The length (or number of rows) the argument has.
        got: usize,
        /// The length (or number of rows) this stage needs.
        expected: usize,
    },
    /// A state whose read point is past what this stage has.
    OutOfRange {
        /// The state's field: `haas_read`, a read point of the Haas line.
        field: &'static str,
        /// The value the state has.
        got: usize,
        /// The largest value this stage allows (the line's length).
        max: usize,
    },
}

impl fmt::Display for SpatialError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::InvalidConfig { n_fft, hop, sr } => write!(
                f,
                "the spatial upmix needs n_fft >= 2, 0 < hop <= n_fft and sr > 0 (n_fft {n_fft}, hop {hop}, sr {sr})"
            ),
            Self::LengthMismatch { left, right } => {
                write!(f, "the channels have different lengths: {left} and {right}")
            }
            Self::OutputMismatch => write!(f, "the outputs must be speakers x block long"),
            Self::BadShape {
                field,
                got,
                expected,
            } => write!(f, "{field}: {got} values where {expected} are needed"),
            Self::OutOfRange { field, got, max } => {
                write!(f, "{field}: {got} is past {max}, the largest allowed")
            }
        }
    }
}

impl std::error::Error for SpatialError {}

/// Who plays what, derived from the angles and roles (numpy's `set_layout`).
#[derive(Debug, Clone, Default)]
struct Layout {
    /// Ambient speakers, in speaker order.
    ambient: Vec<usize>,
    /// Principal speakers, sorted by angle (stable: ties keep speaker order).
    principal: Vec<usize>,
    /// The principals' angles, ascending, and the first again plus a turn.
    ext: Vec<f64>,
    /// The principal nearest the front on each side.
    front: Option<(usize, usize)>,
    /// The gap between principals that holds 180°, when it is 180° or more.
    back: Option<(f64, f64)>,
    /// The classic mix's energy terms `(cl·cl, cr·cr, cl·cr, amb·amb)`.
    classic: Option<[f64; 4]>,
}

/// The whole state of the stage: what moves between engines at a cut's bottom.
#[derive(Debug, Clone, PartialEq)]
pub struct State {
    /// The smoothed cross spectrum.
    pub acc12: Vec<Complex<f64>>,
    /// The smoothed auto spectrum of the left channel.
    pub acc11: Vec<f64>,
    /// The smoothed auto spectrum of the right channel.
    pub acc22: Vec<f64>,
    /// Left input not yet consumed by a frame.
    pub pending_left: Vec<f64>,
    /// Right input not yet consumed by a frame.
    pub pending_right: Vec<f64>,
    /// The direct overlap-add: per speaker, `n_fft` samples each.
    pub ola_direct: Vec<Vec<f64>>,
    /// The ambience overlap-add: per speaker, `n_fft` samples each.
    pub ola_ambience: Vec<Vec<f64>>,
    /// The overlap-add's sum of squared windows.
    pub norm: Vec<f64>,
    /// The direct output computed but not yet given: per speaker, the same length for all.
    pub ready_direct: Vec<Vec<f64>>,
    /// The ambience output computed but not yet given: per speaker, the same length for all.
    pub ready_ambience: Vec<Vec<f64>>,
    /// Per speaker, the Haas line (`round(MAX_HAAS_MS * sr / 1000)` samples).
    pub haas: Vec<Vec<f64>>,
    /// Per speaker, the Haas line's current read point, in samples.
    pub haas_read: Vec<usize>,
    /// Samples given so far (for the entry fade).
    pub emitted: u64,
}

/// The streaming upmix: [`SpatialUpmix::process`] takes a stereo block and gives, per speaker,
/// a direct and an ambience block.
pub struct SpatialUpmix {
    speakers: usize,
    sr: f64,
    n_fft: usize,
    hop: usize,
    bins: usize,
    params: Params,
    layout: Layout,
    stft: Stft,
    haas_len: usize,

    // State.
    acc12: Vec<Complex<f64>>,
    acc11: Vec<f64>,
    acc22: Vec<f64>,
    pending: Pending,
    ola_direct: Vec<Vec<f64>>,
    ola_ambience: Vec<Vec<f64>>,
    norm: Vec<f64>,
    ready_direct: Vec<Vec<f64>>,
    ready_ambience: Vec<Vec<f64>>,
    ready_len: usize,
    haas: Vec<Vec<f64>>,
    haas_read: Vec<usize>,
    emitted: u64,

    // Scratch, sized once.
    mask: Vec<f64>,
    psi: Vec<f64>,
    direct: Vec<Complex<f64>>,
    ambience_left: Vec<Complex<f64>>,
    ambience_right: Vec<Complex<f64>>,
    spectra_direct: Vec<Vec<Complex<f64>>>,
    spectra_ambience: Vec<Vec<Complex<f64>>>,
    has_direct: Vec<bool>,
    has_ambience: Vec<bool>,
    inverse_norm: Vec<f64>,
    fade: Vec<f64>,
    ambience_block: Vec<f64>,
    /// The block the buffers were sized for.
    max_block: usize,
}

/// Python's `round`: halves to even.
fn round_samples(x: f64) -> usize {
    x.round_ties_even().max(0.0) as usize
}

/// `ambience.mapeo` with `ambience.Parametros(umbral=threshold)`.
fn curve(index: f64, threshold: f64) -> f64 {
    ambience_curve(index, threshold, MU0, MU1, SIGMA)
}

impl fmt::Debug for SpatialUpmix {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("SpatialUpmix")
            .field("speakers", &self.speakers)
            .field("sr", &self.sr)
            .field("n_fft", &self.n_fft)
            .field("hop", &self.hop)
            .field("params", &self.params)
            .finish_non_exhaustive()
    }
}

impl SpatialUpmix {
    /// A stage for `speakers` outputs at `sr` Hz, with an STFT of `n_fft` points and hop `hop`
    /// (numpy's defaults: [`N_FFT`], [`HOP`]), its buffers sized for blocks of `max_block`.
    /// Default params; no principal and no ambient until [`SpatialUpmix::set_layout`].
    ///
    /// # Errors
    ///
    /// [`SpatialError::InvalidConfig`] if `n_fft < 2`, `hop == 0`, `hop > n_fft` or `sr == 0`.
    pub fn new(
        speakers: usize,
        sr: u32,
        n_fft: usize,
        hop: usize,
        max_block: usize,
    ) -> Result<Self, SpatialError> {
        if n_fft < 2 || hop == 0 || hop > n_fft || sr == 0 {
            return Err(SpatialError::InvalidConfig { n_fft, hop, sr });
        }
        let bins = n_fft / 2 + 1;
        let sr = f64::from(sr);
        let haas_len = round_samples(MAX_HAAS_MS * sr / 1000.0);
        let zeros = |n: usize| vec![0.0; n];
        let czeros = |n: usize| vec![Complex::new(0.0, 0.0); n];
        let mut stage = Self {
            speakers,
            sr,
            n_fft,
            hop,
            bins,
            params: Params::default(),
            layout: Layout::default(),
            stft: Stft::new(n_fft),
            haas_len,
            acc12: czeros(bins),
            acc11: zeros(bins),
            acc22: zeros(bins),
            pending: Pending::default(),
            ola_direct: vec![zeros(n_fft); speakers],
            ola_ambience: vec![zeros(n_fft); speakers],
            norm: zeros(n_fft),
            ready_direct: vec![Vec::new(); speakers],
            ready_ambience: vec![Vec::new(); speakers],
            ready_len: n_fft,
            haas: vec![zeros(haas_len); speakers],
            haas_read: Vec::new(),
            emitted: 0,
            mask: zeros(bins),
            psi: zeros(bins),
            direct: czeros(bins),
            ambience_left: czeros(bins),
            ambience_right: czeros(bins),
            spectra_direct: vec![czeros(bins); speakers],
            spectra_ambience: vec![czeros(bins); speakers],
            has_direct: vec![false; speakers],
            has_ambience: vec![false; speakers],
            inverse_norm: zeros(hop),
            fade: Vec::new(),
            ambience_block: Vec::new(),
            max_block: 0,
        };
        let read = stage.haas_samples();
        stage.haas_read = vec![read; speakers];
        stage.reserve(max_block);
        Ok(stage)
    }

    /// How many outputs.
    #[must_use]
    pub fn speakers(&self) -> usize {
        self.speakers
    }

    /// The STFT's length (and the stage's latency).
    #[must_use]
    pub fn n_fft(&self) -> usize {
        self.n_fft
    }

    /// The Haas line's length in samples.
    #[must_use]
    pub fn haas_len(&self) -> usize {
        self.haas_len
    }

    /// The params in use (the Haas delay clamped).
    #[must_use]
    pub fn params(&self) -> Params {
        self.params
    }

    /// Sizes the buffers for blocks of up to `max_block` samples (they only grow): after it,
    /// [`SpatialUpmix::process`] allocates nothing for such blocks, from any state that numpy's
    /// stage can reach.
    pub fn reserve(&mut self, max_block: usize) {
        self.max_block = self.max_block.max(max_block);
        let pending = self.n_fft + self.max_block;
        let ready = self.n_fft + self.max_block + self.hop;
        self.grow(pending, ready);
        if self.fade.len() < self.max_block {
            self.fade.resize(self.max_block, 0.0);
            self.ambience_block.resize(self.max_block, 0.0);
        }
    }

    fn grow(&mut self, pending: usize, ready: usize) {
        self.pending.reserve(pending);
        for buffer in self.ready_direct.iter_mut().chain(&mut self.ready_ambience) {
            if buffer.len() < ready {
                buffer.resize(ready, 0.0);
            }
        }
    }

    /// New knobs, live (numpy's `set_params`): the Haas delay crossfades over the next block.
    pub fn set_params(&mut self, params: Params) {
        self.params = Params {
            haas_ms: params.haas_ms.clamp(0.0, MAX_HAAS_MS),
            ..params
        };
    }

    /// Which speakers are principal (with their angle in degrees) and which ambient, live
    /// (numpy's `set_layout`): only the ring changes, the overlap-add crossfades it.
    ///
    /// `angles[s]` is speaker `s`'s angle, `None` when it has none; `ambient[s]` its role.
    /// `classic`, when given and not empty, is each speaker's `(pan, ambience)` of the classic
    /// mix, whose loudness the output then keeps bin by bin.
    ///
    /// # Errors
    ///
    /// [`SpatialError::BadShape`] if `angles` or `ambient` is not `speakers` long (its field is
    /// `angles` or `ambient`).
    pub fn set_layout(
        &mut self,
        angles: &[Option<f64>],
        ambient: &[bool],
        classic: Option<&[(f64, f64)]>,
    ) -> Result<(), SpatialError> {
        for (field, got) in [("angles", angles.len()), ("ambient", ambient.len())] {
            if got != self.speakers {
                return Err(SpatialError::BadShape {
                    field,
                    got,
                    expected: self.speakers,
                });
            }
        }
        let ambient_list: Vec<usize> = (0..self.speakers).filter(|&s| ambient[s]).collect();
        let mut principal: Vec<usize> = (0..self.speakers)
            .filter(|&s| !ambient[s] && angles[s].is_some())
            .collect();
        let angle = |s: usize| angles[s].unwrap_or(f64::NAN);
        // Stable, like Python's `sort`: ties keep speaker order. `total_cmp` orders -0.0 before
        // 0.0, which Python takes as equal: `+ 0.0` turns -0.0 into 0.0 first (the golden's
        // "signed-zero-tie" layout; plain `total_cmp` put the speakers the other way round).
        principal.sort_by(|&a, &b| (angle(a) + 0.0).total_cmp(&(angle(b) + 0.0)));
        let ring: Vec<f64> = principal.iter().map(|&s| angle(s)).collect();
        let mut ext = ring.clone();
        if let Some(&first) = ring.first() {
            ext.push(first + 360.0);
        }

        // `front_pair`: Python's `max`/`min` keep the first of equal ones, in ring order.
        let mut left: Option<usize> = None;
        let mut right: Option<usize> = None;
        for &s in &principal {
            let a = angle(s);
            if -HALF_TURN < a && a < 0.0 && left.is_none_or(|l| a > angle(l)) {
                left = Some(s);
            }
            if 0.0 < a && a < HALF_TURN && right.is_none_or(|r| a < angle(r)) {
                right = Some(s);
            }
        }
        let front = left.zip(right);

        let mut back = None;
        if ring.len() >= 2 {
            for i in 0..ring.len() {
                let (lo, hi) = (ext[i], ext[i + 1]);
                if lo <= HALF_TURN && HALF_TURN < hi && hi - lo >= HALF_TURN - BACK_GAP_SLACK_DEG {
                    back = Some((lo, hi));
                    break;
                }
            }
        }

        let classic = classic.filter(|c| !c.is_empty()).map(|pairs| {
            let mut terms = [0.0; 4];
            for &(pan, amb) in pairs {
                let cl = (1.0 - pan) / 2.0 * (1.0 - amb);
                let cr = (1.0 + pan) / 2.0 * (1.0 - amb);
                terms[0] += cl * cl;
                terms[1] += cr * cr;
                terms[2] += cl * cr;
                terms[3] += amb * amb;
            }
            terms
        });

        self.layout = Layout {
            ambient: ambient_list,
            principal,
            ext,
            front,
            back,
            classic,
        };
        Ok(())
    }

    fn haas_samples(&self) -> usize {
        round_samples(self.params.haas_ms * self.sr / 1000.0)
    }

    /// One stereo block in; per speaker `s`, its direct block into `direct[s * n .. (s + 1) * n]`
    /// and its ambience block into `ambience[..]` likewise (`n` = the block's length).
    ///
    /// # Errors
    ///
    /// [`SpatialError::LengthMismatch`] if `left` and `right` differ in length;
    /// [`SpatialError::OutputMismatch`] if an output is not `speakers * n` long.
    pub fn process(
        &mut self,
        left: &[f64],
        right: &[f64],
        direct: &mut [f64],
        ambience: &mut [f64],
    ) -> Result<(), SpatialError> {
        if left.len() != right.len() {
            return Err(SpatialError::LengthMismatch {
                left: left.len(),
                right: right.len(),
            });
        }
        let n = left.len();
        if direct.len() != self.speakers * n || ambience.len() != self.speakers * n {
            return Err(SpatialError::OutputMismatch);
        }
        self.make_room(n);

        self.pending.push(left, right);
        let mut at = 0;
        while self.pending.len() - at >= self.n_fft {
            self.frame(at);
            at += self.hop;
        }
        self.pending.consume(at);

        self.fade_in(n);
        let available = self.ready_len.min(n);
        for s in 0..self.speakers {
            let out = &mut direct[s * n..(s + 1) * n];
            for (i, o) in out.iter_mut().enumerate() {
                let x = if i < available {
                    self.ready_direct[s][i]
                } else {
                    0.0
                };
                *o = x * self.fade[i];
            }
            for i in 0..n {
                let x = if i < available {
                    self.ready_ambience[s][i]
                } else {
                    0.0
                };
                self.ambience_block[i] = x * self.fade[i];
            }
            self.delay(s, n, &mut ambience[s * n..(s + 1) * n]);
        }
        if self.ready_len > n {
            for s in 0..self.speakers {
                self.ready_direct[s].copy_within(n..self.ready_len, 0);
                self.ready_ambience[s].copy_within(n..self.ready_len, 0);
            }
            self.ready_len -= n;
        } else {
            self.ready_len = 0;
        }
        self.emitted += n as u64;
        Ok(())
    }

    /// Room for a block of `n` from the present state; allocates only past what was reserved.
    fn make_room(&mut self, n: usize) {
        if n > self.max_block {
            self.reserve(n);
        }
        let held = self.pending.len() + n;
        self.grow(held, self.ready_len + produced(held, self.n_fft, self.hop));
    }

    /// The entry fade into `self.fade[..n]` (numpy's `_fade_in`).
    fn fade_in(&mut self, n: usize) {
        let start = i128::from(self.emitted) - self.n_fft as i128;
        if start >= FADE_IN as i128 {
            self.fade[..n].fill(1.0);
            return;
        }
        for (i, f) in self.fade[..n].iter_mut().enumerate() {
            let k = (start + i as i128) as f64;
            *f = if k < 0.0 {
                0.0
            } else {
                0.5 - 0.5 * (PI * (k / FADE_IN as f64).clamp(0.0, 1.0)).cos()
            };
        }
    }

    /// The Haas delay of speaker `s` on `self.ambience_block[..n]`, into `out` (numpy's
    /// `_delay`): a change of delay crossfades over the block between the two read points.
    fn delay(&mut self, s: usize, n: usize, out: &mut [f64]) {
        let length = self.haas_len;
        let new = self.haas_samples();
        let old = self.haas_read[s];
        let line = &self.haas[s];
        let block = &self.ambience_block[..n];
        let at = |j: usize| {
            if j < length {
                line[j]
            } else {
                block[j - length]
            }
        };
        let start = length - new;
        for (i, o) in out.iter_mut().enumerate() {
            *o = at(start + i);
        }
        if old != new && n > 0 {
            // numpy's `linspace(0, 1, n)`: `i * (1 / (n - 1))`, the last exactly 1.
            let step = if n > 1 { 1.0 / (n - 1) as f64 } else { 0.0 };
            for (i, o) in out.iter_mut().enumerate() {
                let ramp = if n > 1 && i == n - 1 {
                    1.0
                } else {
                    i as f64 * step + 0.0
                };
                let before = at(length - old + i);
                *o = (1.0 - ramp) * before + ramp * *o;
            }
        }
        let line = &mut self.haas[s];
        if n >= length {
            line.copy_from_slice(&block[n - length..]);
        } else {
            line.copy_within(n.., 0);
            line[length - n..].copy_from_slice(block);
        }
        self.haas_read[s] = new;
    }

    /// One STFT frame from `pending[at .. at + n_fft]`: its spectra per speaker, their
    /// overlap-add, and one hop of output per speaker appended to the ready buffers.
    fn frame(&mut self, at: usize) {
        let n_fft = self.n_fft;
        self.stft.analyse(&self.pending, at);

        self.has_direct.fill(false);
        self.has_ambience.fill(false);
        self.analyse();
        if self.params.front_intact && self.layout.front.is_some() {
            self.front_frame();
        } else {
            self.spatial_frame();
        }

        self.stft.add_window2(&mut self.norm);
        // A reciprocal where the window sum is above the floor, zero elsewhere (the extractor
        // divides and leaves the rest undivided: not shared).
        for (inv, &n) in self.inverse_norm.iter_mut().zip(&self.norm) {
            *inv = if n > FLOOR { 1.0 / n.max(FLOOR) } else { 0.0 };
        }
        let hop = self.hop;
        let ready_at = self.ready_len;
        for s in 0..self.speakers {
            for which in [false, true] {
                let (ola, spectrum, has, ready) = if which {
                    (
                        &mut self.ola_ambience[s],
                        &mut self.spectra_ambience[s],
                        self.has_ambience[s],
                        &mut self.ready_ambience[s],
                    )
                } else {
                    (
                        &mut self.ola_direct[s],
                        &mut self.spectra_direct[s],
                        self.has_direct[s],
                        &mut self.ready_direct[s],
                    )
                };
                if has {
                    self.stft.overlap_add(spectrum, ola);
                }
                for ((r, &o), &inv) in ready[ready_at..ready_at + hop]
                    .iter_mut()
                    .zip(&ola[..hop])
                    .zip(&self.inverse_norm)
                {
                    *r = o * inv;
                }
                ola.copy_within(hop.., 0);
                ola[n_fft - hop..].fill(0.0);
            }
        }
        self.norm.copy_within(hop.., 0);
        self.norm[n_fft - hop..].fill(0.0);
        self.ready_len += hop;
    }

    /// The smoothed powers and the ambience mask of every bin (the first half of numpy's
    /// `_frame`, common to both renders).
    fn analyse(&mut self) {
        let lam = self.params.lam;
        let rest = 1.0 - lam;
        for b in 0..self.bins {
            let (fl, fr) = (self.stft.spectrum_left[b], self.stft.spectrum_right[b]);
            // numpy's `re * re + im * im`, not the extractor's `np.abs(x) ** 2`.
            let el = norm2(fl);
            let er = norm2(fr);
            let cross = times_conj(scaled(fl, rest), fr);
            let acc = scaled(self.acc12[b], lam);
            self.acc12[b] = Complex::new(acc.re + cross.re, acc.im + cross.im);
            self.acc11[b] = lam * self.acc11[b] + rest * el;
            self.acc22[b] = lam * self.acc22[b] + rest * er;
            let (p11, p22) = (self.acc11[b], self.acc22[b]);

            let coherence = magnitude(self.acc12[b]) / (p11 * p22 + TINY).sqrt();
            let mut index = (1.0 - coherence).clamp(0.0, 1.0);
            let (weak, strong) = (p11.min(p22), p11.max(p22) + TINY);
            if weak / strong < MIN_ENERGY_RATIO {
                index = 0.0;
            }
            self.mask[b] = self.params.ambience * curve(index, self.params.threshold);
            self.psi[b] = (p22 - p11) / (p11 + p22 + TINY);
        }
    }

    /// The spatial render (the rest of numpy's `_frame`).
    fn spatial_frame(&mut self) {
        let level = 10f64.powf(self.params.ambient_level_db / 20.0);
        let classic = self.layout.classic;
        for b in 0..self.bins {
            let (fl, fr) = (self.stft.spectrum_left[b], self.stft.spectrum_right[b]);
            let mask = self.mask[b];
            let el = norm2(fl);
            let er = norm2(fr);
            let energy = el + er;
            let total = Complex::new(fl.re + fr.re, fl.im + fr.im);
            let et = norm2(total);
            // The phase of L + R, or of the louder channel where L + R nearly cancels.
            let phase = if et > MIN_ENERGY_SHARE * energy {
                total
            } else if el >= er {
                fl
            } else {
                fr
            };
            let ep = norm2(phase);
            let direct = scaled(
                phase,
                (1.0 - mask) * (energy / ep.max(MIN_PHASE_ENERGY)).sqrt(),
            );
            let lm = level * mask;
            let amb_l = scaled(fl, lm);
            let amb_r = scaled(fr, lm);
            let m2 = lm * lm;
            let out_energy = (1.0 - mask) * (1.0 - mask) * energy + m2 * energy;
            let target = match classic {
                None => energy,
                Some([a, bb, c, d]) => {
                    let cross = times_conj(fl, fr).re;
                    let t = a * el + bb * er + 2.0 * c * cross + d * (mask * mask) * energy / 2.0;
                    t.max(0.0)
                }
            };
            let mut scale = (target / out_energy.max(MIN_OUT_ENERGY)).sqrt();
            if energy < SILENT {
                scale = 0.0;
            }
            self.direct[b] = scaled(direct, scale);
            self.ambience_left[b] = scaled(amb_l, scale);
            self.ambience_right[b] = scaled(amb_r, scale);
        }

        if !self.layout.principal.is_empty() {
            self.place_direct();
        }
        let layout = &self.layout;
        if layout.principal.is_empty() && !layout.ambient.is_empty() {
            let share = 1.0 / (layout.ambient.len() as f64).sqrt();
            for &s in &layout.ambient {
                for (o, &d) in self.spectra_direct[s].iter_mut().zip(&self.direct) {
                    *o = scaled(d, share);
                }
                self.has_direct[s] = true;
            }
        }
        let receivers = if layout.ambient.is_empty() {
            &layout.principal
        } else {
            &layout.ambient
        };
        spread_ambience(
            receivers.iter().copied(),
            receivers.len(),
            &self.ambience_left,
            &self.ambience_right,
            &mut self.spectra_ambience,
            &mut self.has_ambience,
        );
    }

    /// Each bin's direct part to the two principals around its angle `psi * arc_deg`, with
    /// constant-power gains (numpy's `_gains`; a single principal takes it all).
    fn place_direct(&mut self) {
        let layout = &self.layout;
        let k = layout.principal.len();
        for &s in &layout.principal {
            self.has_direct[s] = true;
        }
        if k == 1 {
            let s = layout.principal[0];
            for (o, &d) in self.spectra_direct[s].iter_mut().zip(&self.direct) {
                *o = scaled(d, 1.0);
            }
            return;
        }
        for &s in &layout.principal {
            self.spectra_direct[s].fill(Complex::new(0.0, 0.0));
        }
        let ext = &layout.ext;
        for b in 0..self.bins {
            let mut theta = self.psi[b] * self.params.arc_deg;
            if let Some((lo, hi)) = layout.back {
                // An angle in the back gap goes to the nearer edge principal.
                let t = if theta < lo { theta + 360.0 } else { theta };
                if t > lo && t < hi {
                    let snapped = if (t - lo) <= (hi - t) { lo } else { hi };
                    theta = if snapped > HALF_TURN {
                        snapped - 360.0
                    } else {
                        snapped
                    };
                }
            }
            let t = if theta < ext[0] { theta + 360.0 } else { theta };
            // `searchsorted(ext, t, side="right") - 1`, clipped to `[0, k - 1]`.
            let above = ext.partition_point(|&e| e <= t);
            let i = above.saturating_sub(1).min(k - 1);
            let (lo, hi) = (ext[i], ext[i + 1]);
            let frac = ((t - lo) / (hi - lo).max(MIN_ARC_DEG)).clamp(0.0, 1.0);
            let d = self.direct[b];
            let near = layout.principal[i % k];
            let far = layout.principal[(i + 1) % k];
            self.spectra_direct[near][b] = scaled(d, (frac * PI / 2.0).cos());
            self.spectra_direct[far][b] = scaled(d, (frac * PI / 2.0).sin());
        }
    }

    /// "Frente intacto" (numpy's `_front_frame`): L and R as they come to the front pair, and
    /// to everyone else only the ambience, boosted, on top.
    fn front_frame(&mut self) {
        let (front_left, front_right) = self.layout.front.expect("checked by the caller");
        let level = 10f64.powf((self.params.ambient_level_db + FRONT_BOOST_DB) / 20.0);
        for b in 0..self.bins {
            let lm = level * self.mask[b];
            self.ambience_left[b] = scaled(self.stft.spectrum_left[b], lm);
            self.ambience_right[b] = scaled(self.stft.spectrum_right[b], lm);
        }
        self.spectra_direct[front_left].copy_from_slice(&self.stft.spectrum_left);
        self.spectra_direct[front_right].copy_from_slice(&self.stft.spectrum_right);
        self.has_direct[front_left] = true;
        self.has_direct[front_right] = true;
        let receivers = (0..self.speakers).filter(|&s| s != front_left && s != front_right);
        let count = self.speakers - 2;
        spread_ambience(
            receivers,
            count,
            &self.ambience_left,
            &self.ambience_right,
            &mut self.spectra_ambience,
            &mut self.has_ambience,
        );
    }

    /// The whole state, copied (to move it to numpy's stage).
    #[must_use]
    pub fn to_state(&self) -> State {
        let ready = |buffers: &[Vec<f64>]| {
            buffers
                .iter()
                .map(|b| b[..self.ready_len].to_vec())
                .collect()
        };
        State {
            acc12: self.acc12.clone(),
            acc11: self.acc11.clone(),
            acc22: self.acc22.clone(),
            pending_left: self.pending.left().to_vec(),
            pending_right: self.pending.right().to_vec(),
            ola_direct: self.ola_direct.clone(),
            ola_ambience: self.ola_ambience.clone(),
            norm: self.norm.clone(),
            ready_direct: ready(&self.ready_direct),
            ready_ambience: ready(&self.ready_ambience),
            haas: self.haas.clone(),
            haas_read: self.haas_read.clone(),
            emitted: self.emitted,
        }
    }

    /// Takes `state` as its own (from numpy's stage). Every size is checked first; on an error
    /// nothing changes.
    ///
    /// # Errors
    ///
    /// [`SpatialError::BadShape`] if a size of `state` does not fit this stage;
    /// [`SpatialError::OutOfRange`] if a `haas_read` is past the Haas line.
    pub fn set_state(&mut self, state: &State) -> Result<(), SpatialError> {
        let k = self.speakers;
        let check = |field, got, expected| {
            if got == expected {
                Ok(())
            } else {
                Err(SpatialError::BadShape {
                    field,
                    got,
                    expected,
                })
            }
        };
        // A per-speaker buffer: `k` rows (`<name> rows`), each `want` long, or as long as the
        // first when `want` is `None` (`<name> row`). Its row length.
        let rows =
            |names: (&'static str, &'static str), buffers: &[Vec<f64>], want: Option<usize>| {
                check(names.0, buffers.len(), k)?;
                let length = want.unwrap_or_else(|| buffers.first().map_or(0, Vec::len));
                for row in buffers {
                    check(names.1, row.len(), length)?;
                }
                Ok::<usize, SpatialError>(length)
            };
        check("acc12", state.acc12.len(), self.bins)?;
        check("acc11", state.acc11.len(), self.bins)?;
        check("acc22", state.acc22.len(), self.bins)?;
        check(
            "pending_right",
            state.pending_right.len(),
            state.pending_left.len(),
        )?;
        rows(
            ("ola_direct rows", "ola_direct row"),
            &state.ola_direct,
            Some(self.n_fft),
        )?;
        rows(
            ("ola_ambience rows", "ola_ambience row"),
            &state.ola_ambience,
            Some(self.n_fft),
        )?;
        check("norm", state.norm.len(), self.n_fft)?;
        let ready = rows(
            ("ready_direct rows", "ready_direct row"),
            &state.ready_direct,
            None,
        )?;
        rows(
            ("ready_ambience rows", "ready_ambience row"),
            &state.ready_ambience,
            Some(ready),
        )?;
        rows(("haas rows", "haas row"), &state.haas, Some(self.haas_len))?;
        check("haas_read", state.haas_read.len(), k)?;
        if let Some(&bad) = state.haas_read.iter().find(|&&r| r > self.haas_len) {
            return Err(SpatialError::OutOfRange {
                field: "haas_read",
                got: bad,
                max: self.haas_len,
            });
        }

        let pending = state.pending_left.len();
        self.grow(
            pending + self.max_block,
            ready + pending + self.max_block + self.hop,
        );
        self.acc12.copy_from_slice(&state.acc12);
        self.acc11.copy_from_slice(&state.acc11);
        self.acc22.copy_from_slice(&state.acc22);
        self.pending.load(&state.pending_left, &state.pending_right);
        for s in 0..k {
            self.ola_direct[s].copy_from_slice(&state.ola_direct[s]);
            self.ola_ambience[s].copy_from_slice(&state.ola_ambience[s]);
            self.ready_direct[s][..ready].copy_from_slice(&state.ready_direct[s]);
            self.ready_ambience[s][..ready].copy_from_slice(&state.ready_ambience[s]);
            self.haas[s].copy_from_slice(&state.haas[s]);
        }
        self.ready_len = ready;
        self.norm.copy_from_slice(&state.norm);
        self.haas_read.copy_from_slice(&state.haas_read);
        self.emitted = state.emitted;
        Ok(())
    }
}

/// The ambience to its receivers (numpy's last lines of `_frame` and `_front_frame`): one gets
/// `(L + R) / sqrt(2)`; with more, they alternate L and R, each side shared at constant power.
fn spread_ambience(
    receivers: impl Iterator<Item = usize> + Clone,
    count: usize,
    left: &[Complex<f64>],
    right: &[Complex<f64>],
    spectra: &mut [Vec<Complex<f64>>],
    has: &mut [bool],
) {
    if count == 0 {
        return;
    }
    if count == 1 {
        let s = receivers.clone().next().expect("count is 1");
        let share = 1.0 / 2f64.sqrt();
        for ((o, &l), &r) in spectra[s].iter_mut().zip(left).zip(right) {
            *o = scaled(Complex::new(l.re + r.re, l.im + r.im), share);
        }
        has[s] = true;
        return;
    }
    let lefts = count.div_ceil(2);
    let rights = count / 2;
    for (j, s) in receivers.enumerate() {
        let (source, share) = if j % 2 == 0 {
            (left, 1.0 / (lefts as f64).sqrt())
        } else {
            (right, 1.0 / (rights as f64).sqrt())
        };
        for (o, &x) in spectra[s].iter_mut().zip(source) {
            *o = scaled(x, share);
        }
        has[s] = true;
    }
}
