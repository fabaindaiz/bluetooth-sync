//! The true-peak limiter: a look-ahead limiter on the 4x-oversampled peak, with a raised-cosine
//! attack, a hold and an exponential release.
//!
//! A port of `TruePeakLimiter.process` in `host/src/aurasync/dsp/limiter.py`, which stays the
//! oracle (`host/tests/test_limiter_rust.py` holds this within 1e-9 of it, metrics included). The
//! design stays numpy: the host computes the look-ahead `latency`, the `attack` and `hold` lengths
//! and the interpolation kernels (`loudness.interpolation_kernels`) and passes them; the only
//! constants here are [`MARGIN_DB`] and [`NEAR_CEILING`], numpy's own.
//!
//! Each block follows numpy's steps in numpy's order:
//!
//! 1. the kept input and the block are one segment; the last `keep` samples are kept;
//! 2. **need** (`_needed`): unless every sample is under [`NEAR_CEILING`] of the target, each
//!    window of `2 * half_width` samples gives the three points between its two middle samples
//!    (a plain left-to-right dot per kernel row, run tap by tap over all the windows; numpy's is
//!    a BLAS product, whose order the golden's tolerance covers); a sample's peak is the largest
//!    of its magnitude and the points on both sides; its need is
//!    `min(1, target / max(peak, 1e-12))`;
//! 3. the shortcut: at unity gain with nothing needed, the block is the delayed input;
//! 4. **hold and attack**: a running minimum over `attack + hold + 1` needs (van Herk /
//!    Gil-Werman: prefix and suffix minima per run of that width, padded with infinity), then the
//!    raised-cosine average over `attack + 1` of them (`_hann_average`: running sums of `h`,
//!    `h cos(theta m)` and `h sin(theta m)` from the block's start, the angle tables grown once
//!    to the largest block), skipped when nothing is under unity; values above `1 - 1e-12`
//!    become 1 (the running sums leave `1 - 4e-16` where nothing is needed);
//! 5. **release** (`_release`): `1 - g` shrinks by `e` every `release_ms`, done as numpy does it,
//!    a running maximum in the log domain from the last block's gain;
//! 6. the metrics: the deepest reduction (dB) and the share of samples under unity.
//!
//! `min` and `max` here are numpy's `minimum` and `maximum` (a NaN wins), and `max(x, 1e-9)` is
//! Python's (the first argument unless the second is larger), so even a NaN input behaves as in
//! numpy.
//!
//! **Allocation.** `process` allocates nothing once its buffers fit the block: the first block
//! longer than any before grows them (and the angle tables) once, as numpy grows its tables.

use std::f64::consts::PI;
use std::fmt;

/// The limiter aims this far under the ceiling, dB (numpy's `MARGIN_DB`): the gain moves while it
/// acts, and the oversampled product of a moving gain and the signal is not exactly the gain
/// times the oversampled signal.
pub const MARGIN_DB: f64 = 0.01;
/// A block whose samples all stay under this fraction of the target is not oversampled (numpy's
/// `NEAR_CEILING`): no point between two such samples reaches the ceiling.
pub const NEAR_CEILING: f64 = 0.25;
/// The points between two samples that are interpolated: 1/4, 2/4 and 3/4 of the way.
pub const POINTS: usize = 3;

/// Why a call was refused. Nothing changes when it is.
#[derive(Debug, Clone, PartialEq)]
pub enum LimiterError {
    /// The look-ahead does not fit the interpolator: `half_width` and `attack` must be at least 1
    /// and `latency` must be `attack + half_width + 1` (numpy's "look-ahead leaves no attack").
    BadTiming {
        /// The look-ahead asked for, in samples.
        latency: usize,
        /// The attack asked for, in samples.
        attack: usize,
        /// The interpolator's half width asked for, in samples.
        half_width: usize,
    },
    /// A knob the limiter cannot run with: a `ceiling_db` that is not finite, a `release_ms` that
    /// is not finite and positive, or a sample rate (`sr`) of 0.
    InvalidConfig {
        /// The knob: `ceiling_db`, `release_ms` or `sr`.
        field: &'static str,
        /// The value it was given.
        got: f64,
    },
    /// Kernels or a state whose size does not fit this limiter.
    BadShape {
        /// `kernels` (`3 * 2 * half_width` values) or the state's `x` (the kept input).
        field: &'static str,
        /// The length given.
        got: usize,
        /// The length this limiter needs.
        expected: usize,
    },
    /// The output slice is not as long as the block.
    OutputMismatch,
}

impl fmt::Display for LimiterError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::BadTiming {
                latency,
                attack,
                half_width,
            } => write!(
                f,
                "the look-ahead leaves no attack: the limiter needs half_width >= 1, attack >= 1 \
                 and latency = attack + half_width + 1 (latency {latency}, attack {attack}, \
                 half_width {half_width})"
            ),
            Self::InvalidConfig { field, got } => write!(
                f,
                "the limiter needs a finite ceiling_db, a finite release_ms > 0 and sr > 0 \
                 ({field} {got})"
            ),
            Self::BadShape {
                field,
                got,
                expected,
            } => write!(f, "{field}: {got} values where {expected} are needed"),
            Self::OutputMismatch => write!(f, "the output must be as long as the block"),
        }
    }
}

impl std::error::Error for LimiterError {}

/// What moves between engines at a switch: numpy's `_x` (the kept input) and `gain` (the gain on
/// the last sample out).
#[derive(Debug, Clone, PartialEq)]
pub struct LimiterState {
    /// The input kept from before the next block: `latency + attack + hold + half_width + 2`
    /// samples.
    pub x: Vec<f64>,
    /// The gain on the last sample out.
    pub gain: f64,
}

/// One speaker's true-peak limiter (numpy's `TruePeakLimiter`, per block).
pub struct TruePeakLimiter {
    /// `POINTS` rows of `2 * half_width` taps; row p's tap m multiplies a window's sample m.
    kernels: Vec<f64>,
    half_width: usize,
    latency: usize,
    attack: usize,
    hold: usize,
    sr: f64,
    target: f64,
    rate: f64,
    theta: f64,
    norm: f64,
    gain: f64,
    max_reduction_db: f64,
    active_fraction: f64,
    /// numpy's `_x`.
    x: Vec<f64>,
    /// The longest block the buffers below fit.
    fits: usize,
    seg: Vec<f64>,
    needed: Vec<f64>,
    point: Vec<f64>,
    between: Vec<f64>,
    prefix: Vec<f64>,
    suffix: Vec<f64>,
    held: Vec<f64>,
    cos: Vec<f64>,
    sin: Vec<f64>,
    sum_box: Vec<f64>,
    sum_cos: Vec<f64>,
    sum_sin: Vec<f64>,
    gains: Vec<f64>,
}

impl fmt::Debug for TruePeakLimiter {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("TruePeakLimiter")
            .field("half_width", &self.half_width)
            .field("latency", &self.latency)
            .field("attack", &self.attack)
            .field("hold", &self.hold)
            .field("keep", &self.x.len())
            .field("target", &self.target)
            .field("rate", &self.rate)
            .field("gain", &self.gain)
            .finish_non_exhaustive()
    }
}

/// numpy's `np.maximum`: a NaN on either side wins.
fn maximum(a: f64, b: f64) -> f64 {
    if a.is_nan() || b.is_nan() {
        f64::NAN
    } else if a >= b {
        a
    } else {
        b
    }
}

/// numpy's `np.minimum`: a NaN on either side wins.
fn minimum(a: f64, b: f64) -> f64 {
    if a.is_nan() || b.is_nan() {
        f64::NAN
    } else if a <= b {
        a
    } else {
        b
    }
}

/// numpy's `pairwise_sum` (`loops_utils.h.src`), as `ndarray.sum()` runs it on a contiguous
/// vector: under 8 elements a plain loop, up to 128 eight running lanes combined as a tree plus a
/// plain remainder, above that a split at half the length rounded down to a multiple of 8.
fn pairwise_sum(a: &[f64]) -> f64 {
    let n = a.len();
    if n < 8 {
        let mut res = 0.0;
        for &x in a {
            res += x;
        }
        res
    } else if n <= 128 {
        let (chunks, rest) = a.as_chunks::<8>();
        let mut r = chunks[0];
        for chunk in &chunks[1..] {
            for (lane, &x) in r.iter_mut().zip(chunk) {
                *lane += x;
            }
        }
        let mut res = ((r[0] + r[1]) + (r[2] + r[3])) + ((r[4] + r[5]) + (r[6] + r[7]));
        for &x in rest {
            res += x;
        }
        res
    } else {
        let mut half = n / 2;
        half -= half % 8;
        pairwise_sum(&a[..half]) + pairwise_sum(&a[half..])
    }
}

/// numpy's `10 ** ((ceiling_db - MARGIN_DB) / 20)`, or a refusal of a ceiling that is not finite.
fn target_of(ceiling_db: f64) -> Result<f64, LimiterError> {
    if !ceiling_db.is_finite() {
        return Err(LimiterError::InvalidConfig {
            field: "ceiling_db",
            got: ceiling_db,
        });
    }
    Ok(10f64.powf((ceiling_db - MARGIN_DB) / 20.0))
}

/// numpy's `1.0 / (release_ms / 1000 * sr)`, or a refusal of a release that is not finite and
/// positive.
fn rate_of(release_ms: f64, sr: f64) -> Result<f64, LimiterError> {
    if !(release_ms.is_finite() && release_ms > 0.0) {
        return Err(LimiterError::InvalidConfig {
            field: "release_ms",
            got: release_ms,
        });
    }
    Ok(1.0 / (release_ms / 1000.0 * sr))
}

impl TruePeakLimiter {
    /// The limiter numpy's `TruePeakLimiter` builds, at rest (silence kept, unity gain).
    ///
    /// `kernels` are [`POINTS`] rows of `2 * half_width` taps, row-major, row p's tap m
    /// multiplying a window's sample m (numpy's `self._kernels.T`, i.e.
    /// `loudness.interpolation_kernels(half_width)[:, ::-1]`). `latency`, `attack` and `hold` are
    /// numpy's (samples), `ceiling_db` and `release_ms` the live knobs, `sr` the sample rate.
    ///
    /// # Errors
    ///
    /// [`LimiterError::BadTiming`] if `half_width` or `attack` is 0 or `latency` is not
    /// `attack + half_width + 1`; [`LimiterError::BadShape`] if `kernels` does not have
    /// `3 * 2 * half_width` values; [`LimiterError::InvalidConfig`] if `ceiling_db` is not finite,
    /// `release_ms` is not finite and positive, or `sr` is 0.
    #[expect(
        clippy::too_many_arguments,
        reason = "numpy's design values, passed as they are so no constant is duplicated"
    )]
    pub fn new(
        kernels: &[f64],
        half_width: usize,
        ceiling_db: f64,
        latency: usize,
        attack: usize,
        hold: usize,
        release_ms: f64,
        sr: u32,
    ) -> Result<Self, LimiterError> {
        if half_width == 0 || attack == 0 || latency != attack + half_width + 1 {
            return Err(LimiterError::BadTiming {
                latency,
                attack,
                half_width,
            });
        }
        let taps = POINTS * 2 * half_width;
        if kernels.len() != taps {
            return Err(LimiterError::BadShape {
                field: "kernels",
                got: kernels.len(),
                expected: taps,
            });
        }
        if sr == 0 {
            return Err(LimiterError::InvalidConfig {
                field: "sr",
                got: 0.0,
            });
        }
        let sr = f64::from(sr);
        let target = target_of(ceiling_db)?;
        let rate = rate_of(release_ms, sr)?;
        // numpy: weights = 0.5 - 0.5 cos(2 pi k / (attack + 2)), k = 1 ..= attack + 1; norm = sum.
        let span = (attack + 2) as f64;
        let weights: Vec<f64> = (1..=attack + 1)
            .map(|k| 0.5 - 0.5 * (2.0 * PI * k as f64 / span).cos())
            .collect();
        let keep = latency + attack + hold + half_width + 2;
        Ok(Self {
            kernels: kernels.to_vec(),
            half_width,
            latency,
            attack,
            hold,
            sr,
            target,
            rate,
            theta: 2.0 * PI / span,
            norm: pairwise_sum(&weights),
            gain: 1.0,
            max_reduction_db: 0.0,
            active_fraction: 0.0,
            x: vec![0.0; keep],
            fits: 0,
            seg: Vec::new(),
            needed: Vec::new(),
            point: Vec::new(),
            between: Vec::new(),
            prefix: Vec::new(),
            suffix: Vec::new(),
            held: Vec::new(),
            cos: Vec::new(),
            sin: Vec::new(),
            sum_box: Vec::new(),
            sum_cos: Vec::new(),
            sum_sin: Vec::new(),
            gains: Vec::new(),
        })
    }

    /// The look-ahead: samples of delay, the same on every speaker.
    #[must_use]
    pub fn latency(&self) -> usize {
        self.latency
    }

    /// The gain on the last sample out.
    #[must_use]
    pub fn gain(&self) -> f64 {
        self.gain
    }

    /// The deepest reduction in the last block, dB (0 when nothing was reduced).
    #[must_use]
    pub fn max_reduction_db(&self) -> f64 {
        self.max_reduction_db
    }

    /// The share of the last block's samples with the gain under unity.
    #[must_use]
    pub fn active_fraction(&self) -> f64 {
        self.active_fraction
    }

    /// Changes the live knobs (numpy's `configure`: the target and the release rate only), from
    /// the next block. Nothing is allocated.
    ///
    /// # Errors
    ///
    /// [`LimiterError::InvalidConfig`] if `ceiling_db` is not finite or `release_ms` is not
    /// finite and positive; then neither changes.
    pub fn configure(
        &mut self,
        ceiling_db: Option<f64>,
        release_ms: Option<f64>,
    ) -> Result<(), LimiterError> {
        let target = ceiling_db.map(target_of).transpose()?;
        let rate = release_ms.map(|r| rate_of(r, self.sr)).transpose()?;
        if let Some(target) = target {
            self.target = target;
        }
        if let Some(rate) = rate {
            self.rate = rate;
        }
        Ok(())
    }

    /// The buffers sized for blocks of up to `n` samples (they only grow).
    fn fit(&mut self, n: usize) {
        if n <= self.fits {
            return;
        }
        let a = self.attack;
        let needs = n + 2 * a + self.hold;
        let width = a + self.hold + 1;
        self.seg.resize(self.x.len() + n, 0.0);
        self.needed.resize(needs, 0.0);
        self.point.resize(needs + 1, 0.0);
        self.between.resize(needs + 1, 0.0);
        self.prefix.resize(needs.div_ceil(width) * width, 0.0);
        self.suffix.resize(needs.div_ceil(width) * width, 0.0);
        self.held.resize(n + a, 0.0);
        // numpy's tables: angle = theta * arange(...), cos and sin of it (each value depends only
        // on its index, so growing them keeps the old ones).
        for k in self.cos.len()..=n + a {
            let angle = self.theta * k as f64;
            self.cos.push(angle.cos());
            self.sin.push(angle.sin());
        }
        self.sum_box.resize(n + a + 1, 0.0);
        self.sum_cos.resize(n + a + 1, 0.0);
        self.sum_sin.resize(n + a + 1, 0.0);
        self.gains.resize(n, 0.0);
        self.fits = n;
    }

    /// The limited block of `x` into `out`: the input `latency` samples ago times its gain.
    /// Nothing is allocated once a block this long has been seen.
    ///
    /// # Errors
    ///
    /// [`LimiterError::OutputMismatch`] if `out` is not as long as `x`.
    pub fn process(&mut self, x: &[f64], out: &mut [f64]) -> Result<(), LimiterError> {
        if out.len() != x.len() {
            return Err(LimiterError::OutputMismatch);
        }
        let n = x.len();
        if n == 0 {
            return Ok(());
        }
        self.fit(n);
        let (a, w, keep) = (self.attack, self.half_width, self.x.len());
        // seg = concat(_x, x); _x = seg[-keep:].
        let seg = &mut self.seg[..keep + n];
        seg[..keep].copy_from_slice(&self.x);
        seg[keep..].copy_from_slice(x);
        self.x.copy_from_slice(&seg[n..]);
        let seg = &self.seg[..keep + n];
        // Output j is seg[o + j]; its gain looks at the need of
        // seg[o + j - hold .. o + j + attack].
        let o = keep - self.latency;
        let first = o - a - self.hold;
        let needs = n + 2 * a + self.hold;
        let needed = &mut self.needed[..needs];
        need(
            &seg[first - w..o + n + a + w],
            &self.kernels,
            w,
            self.target,
            &mut self.point[..=needs],
            &mut self.between[..=needs],
            needed,
        );
        let delayed = &seg[o..o + n];
        if self.gain >= 1.0 && smallest(needed) >= 1.0 {
            self.max_reduction_db = 0.0;
            self.active_fraction = 0.0;
            out.copy_from_slice(delayed);
            return Ok(());
        }
        // held[i] = min(needed[i .. i + attack + hold]); output j sits at
        // needed[j + attack + hold], and the raised-cosine average of held[j .. j + attack] is its
        // attack envelope.
        let held = &mut self.held[..n + a];
        running_min(
            needed,
            a + self.hold + 1,
            &mut self.prefix,
            &mut self.suffix,
            held,
        );
        let smooth = &mut self.gains[..n];
        if smallest(held) < 1.0 {
            let sums = Sums {
                cos: &self.cos,
                sin: &self.sin,
                by_one: &mut self.sum_box,
                by_cos: &mut self.sum_cos,
                by_sin: &mut self.sum_sin,
            };
            hann_average(&*held, a, self.norm, sums, smooth);
        } else {
            smooth.fill(1.0);
        }
        for s in smooth.iter_mut() {
            // Running sums leave 1 - 4e-16 where nothing is needed.
            if *s > 1.0 - 1e-12 {
                *s = 1.0;
            }
        }
        release(smooth, self.gain, self.rate);
        let gain = &*smooth;
        self.gain = gain[n - 1];
        let lowest = smallest(gain);
        // Python's max(lowest, 1e-9): the first argument unless the second is larger.
        let floor = if 1e-9 > lowest { 1e-9 } else { lowest };
        self.max_reduction_db = -20.0 * floor.log10();
        let active = gain.iter().filter(|&&g| g < 1.0 - 1e-9).count();
        self.active_fraction = active as f64 / n as f64;
        for ((y, &d), &g) in out.iter_mut().zip(delayed).zip(gain) {
            *y = d * g;
        }
        Ok(())
    }

    /// The state, copied (to move it to numpy's limiter).
    #[must_use]
    pub fn to_state(&self) -> LimiterState {
        LimiterState {
            x: self.x.clone(),
            gain: self.gain,
        }
    }

    /// Takes `state` as its own (from numpy's limiter). The metrics are not state: they describe
    /// the next block once it has run.
    ///
    /// # Errors
    ///
    /// [`LimiterError::BadShape`] if `state.x` is not `latency + attack + hold + half_width + 2`
    /// samples long; then nothing changes.
    pub fn set_state(&mut self, state: &LimiterState) -> Result<(), LimiterError> {
        if state.x.len() != self.x.len() {
            return Err(LimiterError::BadShape {
                field: "x",
                got: state.x.len(),
                expected: self.x.len(),
            });
        }
        self.x.copy_from_slice(&state.x);
        self.gain = state.gain;
        Ok(())
    }
}

/// The reductions below keep eight running lanes: `min` and `max` are exact whatever the order,
/// so the lanes give numpy's value and let the compiler vectorise. A NaN is noted apart and wins
/// at the end, as in numpy.
const LANES: usize = 8;

/// numpy's `values.min()`, a NaN winning (`values` is never empty here).
fn smallest(values: &[f64]) -> f64 {
    let (chunks, rest) = values.as_chunks::<LANES>();
    let mut low = [f64::INFINITY; LANES];
    let mut nan = false;
    for chunk in chunks {
        for (lane, &v) in low.iter_mut().zip(chunk) {
            nan |= v.is_nan();
            *lane = if v < *lane { v } else { *lane };
        }
    }
    for &v in rest {
        nan |= v.is_nan();
        low[0] = if v < low[0] { v } else { low[0] };
    }
    if nan {
        return f64::NAN;
    }
    low.into_iter().fold(f64::INFINITY, f64::min)
}

/// numpy's `np.abs(values).max()`, a NaN winning (`values` is never empty here).
fn loudest(values: &[f64]) -> f64 {
    let (chunks, rest) = values.as_chunks::<LANES>();
    let mut high = [0.0_f64; LANES];
    let mut nan = false;
    for chunk in chunks {
        for (lane, &v) in high.iter_mut().zip(chunk) {
            nan |= v.is_nan();
            let v = v.abs();
            *lane = if v > *lane { v } else { *lane };
        }
    }
    for &v in rest {
        nan |= v.is_nan();
        let v = v.abs();
        high[0] = if v > high[0] { v } else { high[0] };
    }
    if nan {
        return f64::NAN;
    }
    high.into_iter().fold(0.0, f64::max)
}

/// numpy's `_needed`: the gain each of `x[w .. len - w]` needs so that it and the points on both
/// sides of it stay under `target`. `between` takes the windows' points (one more than `needed`)
/// and `point` is scratch of the same length.
///
/// Each point is a plain left-to-right dot of a kernel row and a window, from 0. The loops run
/// tap by tap over all the windows: each window's sum still adds its taps in order, so the result
/// is the same to the bit as one window at a time, and in the limiter this was ~25 % faster than
/// one window at a time (MEDIDO, two runs, `docs/research/experimentos/20-…` §9).
fn need(
    x: &[f64],
    kernels: &[f64],
    w: usize,
    target: f64,
    point: &mut [f64],
    between: &mut [f64],
    needed: &mut [f64],
) {
    if loudest(x) < NEAR_CEILING * target {
        needed.fill(1.0);
        return;
    }
    // Window i is x[i .. i + 2w]; its points lie between x[i + w - 1] and x[i + w].
    let (taps, windows) = (2 * w, between.len());
    for (p, row) in kernels.chunks_exact(taps).enumerate() {
        point.fill(0.0);
        for (m, &k) in row.iter().enumerate() {
            for (sum, &v) in point.iter_mut().zip(&x[m..m + windows]) {
                *sum += k * v;
            }
        }
        // numpy's np.abs(...).max(axis=0): the first row, then the larger with each next one.
        for (b, &sum) in between.iter_mut().zip(point.iter()) {
            *b = if p == 0 {
                sum.abs()
            } else {
                maximum(*b, sum.abs())
            };
        }
    }
    for (i, slot) in needed.iter_mut().enumerate() {
        let peak = maximum(x[w + i].abs(), maximum(between[i], between[i + 1]));
        *slot = minimum(1.0, target / maximum(peak, 1e-12));
    }
}

/// numpy's `_running_min`: `out[i] = min(x[i .. i + width])` for every full window (van Herk /
/// Gil-Werman): the input padded with infinity to whole runs of `width`, each run's prefix and
/// suffix minima, and each window the minimum of a suffix and a prefix.
fn running_min(x: &[f64], width: usize, prefix: &mut [f64], suffix: &mut [f64], out: &mut [f64]) {
    let runs = x.len().div_ceil(width);
    let at = |k: usize| x.get(k).copied().unwrap_or(f64::INFINITY);
    for run in 0..runs {
        let start = run * width;
        prefix[start] = at(start);
        for k in start + 1..start + width {
            prefix[k] = minimum(prefix[k - 1], at(k));
        }
        let last = start + width - 1;
        suffix[last] = at(last);
        for k in (start..last).rev() {
            suffix[k] = minimum(suffix[k + 1], at(k));
        }
    }
    for (i, slot) in out.iter_mut().enumerate() {
        *slot = minimum(suffix[i], prefix[i + width - 1]);
    }
}

/// The angle tables and the running sums' buffers of [`hann_average`].
struct Sums<'a> {
    cos: &'a [f64],
    sin: &'a [f64],
    by_one: &'a mut [f64],
    by_cos: &'a mut [f64],
    by_sin: &'a mut [f64],
}

/// numpy's `_hann_average`: `out[j] = sum_k weight[k] h[j + k]`, the weights a raised cosine over
/// `a + 1` taps summing to 1, from running sums of `h`, `h cos(theta m)` and `h sin(theta m)`
/// (each from 0 at the block's start, numpy's `cumsum`), so it costs O(n) for any attack.
fn hann_average(h: &[f64], a: usize, norm: f64, sums: Sums<'_>, out: &mut [f64]) {
    let Sums {
        cos: cos_table,
        sin: sin_table,
        by_one,
        by_cos,
        by_sin,
    } = sums;
    let len = h.len();
    let (by_one, by_cos, by_sin) = (
        &mut by_one[..=len],
        &mut by_cos[..=len],
        &mut by_sin[..=len],
    );
    by_one[0] = 0.0;
    by_cos[0] = 0.0;
    by_sin[0] = 0.0;
    let (mut one, mut cos, mut sin) = (0.0, 0.0, 0.0);
    for (m, &v) in h.iter().enumerate() {
        one += v;
        cos += v * cos_table[m];
        sin += v * sin_table[m];
        by_one[m + 1] = one;
        by_cos[m + 1] = cos;
        by_sin[m + 1] = sin;
    }
    let (by_one, by_cos, by_sin) = (&*by_one, &*by_cos, &*by_sin);
    for (j, slot) in out.iter_mut().enumerate() {
        let window = |c: &[f64]| c[a + 1 + j] - c[j];
        let cos_part =
            cos_table[a + 1 + j] * window(by_cos) + sin_table[a + 1 + j] * window(by_sin);
        *slot = (0.5 * window(by_one) - 0.5 * cos_part) / norm;
    }
}

/// numpy's `_release`, in place: `g[k] = min(target[k], 1 - (1 - g[k-1]) e^{-rate})` from `last`
/// (the previous block's gain), as a running maximum in the log domain.
fn release(target: &mut [f64], last: f64, rate: f64) {
    let start = if last < 1.0 {
        (1.0 - last).ln()
    } else {
        f64::NEG_INFINITY
    };
    let mut carried = f64::NEG_INFINITY;
    for (k, slot) in target.iter_mut().enumerate() {
        let depth = 1.0 - minimum(*slot, 1.0);
        let steps = (k + 1) as f64 * rate;
        // ln(0) is -inf and exp(-inf) is 0: both shortcuts give numpy's value without the call.
        let log = if depth == 0.0 {
            f64::NEG_INFINITY
        } else {
            depth.ln()
        };
        let value = maximum(log + steps, start);
        carried = if k == 0 {
            value
        } else {
            maximum(carried, value)
        };
        *slot = if carried == f64::NEG_INFINITY {
            1.0
        } else {
            1.0 - (carried - steps).exp()
        };
    }
}
