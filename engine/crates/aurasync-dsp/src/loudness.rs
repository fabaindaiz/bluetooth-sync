//! The loudness meter's per-block work: the K-weighted energy of every 100 ms step and the true
//! peak (BS.1770-5).
//!
//! A port of the part of `LoudnessMeter.push` in `host/src/aurasync/dsp/loudness.py` that touches
//! the samples; numpy stays the oracle (`host/tests/test_loudness_rust.py` holds this within 1e-9
//! of it). The design stays numpy: the host computes the step length, the K-weighting's power
//! response at the step's bins (`k_power`, with Parseval's factors and `1 / step_n` folded in),
//! the channel weights and the interpolation kernels (`loudness.interpolation_kernels`) and passes
//! them; the only constant here is [`NEAR_PEAK`], numpy's own. What is read from the steps
//! (momentary, short-term, integrated, PSR) stays in the host: it is a few sums over at most
//! 30 numbers, done when read.
//!
//! Each block follows numpy's steps in numpy's order:
//!
//! 1. **true peak** (`_true_peak`): per channel, the kept context (`2 * half_width` samples) and
//!    the block are one segment, whose last `2 * half_width` samples are kept. The positions `j`
//!    between `seg[j]` and `seg[j + 1]` that have `half_width` samples on both sides are looked
//!    at; the block's sample peak is the largest magnitude of the samples after them, over every
//!    channel; only a position next to a sample at least [`NEAR_PEAK`] of that is interpolated
//!    (three points, the dot of the window with each kernel row in four lanes; numpy's is a BLAS
//!    product, in its own order, and the golden's tolerance covers both). The peak is Python's `max(sample_peak, points)`:
//!    the sample peak unless the loudest point is larger;
//! 2. **steps**: the samples are appended to the pending ones, and every whole step of `step_n`
//!    samples gives one energy: per channel, the power spectrum of the step (`|rfft|^2`, as
//!    `re^2 + im^2`) dotted with `k_power`, then the channels summed with their weights.
//!
//! **Allocation.** `push` allocates nothing once its buffers fit the block: the first block
//! longer than any before grows them once.

use std::fmt;

use crate::Complex;
use crate::fft::{Forward, forward};

/// True peak: only the positions next to a sample at least this fraction of the block's sample
/// peak are interpolated (numpy's `NEAR_PEAK`).
pub const NEAR_PEAK: f64 = 0.5;
/// The points between two samples that are interpolated: 1/4, 2/4 and 3/4 of the way.
pub const POINTS: usize = 3;

/// Why a call was refused. Nothing changes when it is.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum LoudnessError {
    /// A design the meter cannot run with: `half_width`, `step_n` or the number of channels (the
    /// length of `weights`) is 0.
    InvalidConfig {
        /// `half_width`, `step_n` or `weights`.
        field: &'static str,
    },
    /// Kernels, a power response, a block or a state whose size does not fit this meter.
    BadShape {
        /// `kernels` (`3 * 2 * half_width` values), `k_power` (`step_n / 2 + 1`), `frames` (a
        /// multiple of the channels), the state's `context` (`channels * 2 * half_width`) or its
        /// `pending` (a multiple of the channels, under `channels * step_n`).
        field: &'static str,
        /// The length given.
        got: usize,
        /// The length (or, for `frames` and `pending`, the multiple) this meter needs.
        expected: usize,
    },
}

impl fmt::Display for LoudnessError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::InvalidConfig { field } => write!(
                f,
                "the loudness meter needs half_width, step_n and the channels (weights) to be at \
                 least 1 ({field} is 0)"
            ),
            Self::BadShape {
                field,
                got,
                expected,
            } => write!(
                f,
                "{field}: {got} values where {expected} (or a fitting multiple) are needed"
            ),
        }
    }
}

impl std::error::Error for LoudnessError {}

/// What moves between engines at a switch: numpy's `_context` and `_pending`, each flattened
/// channel by channel (row-major, as numpy's `(channels, k)` arrays).
#[derive(Debug, Clone, PartialEq)]
pub struct MeterState {
    /// The last `2 * half_width` samples of every channel: the true peak's look back.
    pub context: Vec<f64>,
    /// The samples of every channel that do not yet make a whole step (fewer than `step_n`).
    pub pending: Vec<f64>,
}

/// One meter's per-block work (numpy's `LoudnessMeter.push`, without the readings).
pub struct LoudnessMeter {
    channels: usize,
    half_width: usize,
    step_n: usize,
    /// `POINTS` rows of `2 * half_width` taps; row p's tap m multiplies a window's sample m.
    kernels: Vec<f64>,
    k_power: Vec<f64>,
    weights: Vec<f64>,
    /// numpy's `_context`, channel by channel.
    context: Vec<f64>,
    /// numpy's `_pending`: `channels` rows of `step_n` slots, the first `pending` of each used.
    pending_rows: Vec<f64>,
    pending: usize,
    /// The energies of the steps the last `push` completed.
    steps: Vec<f64>,
    plan: Forward,
    time: Vec<f64>,
    spectrum: Vec<Complex<f64>>,
    scratch: Vec<Complex<f64>>,
    /// Per-channel energies of one step.
    power: Vec<f64>,
    /// The segments (context and block) of every channel, `2 * half_width + fits` each.
    seg: Vec<f64>,
    /// The longest block `seg` fits.
    fits: usize,
}

impl fmt::Debug for LoudnessMeter {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("LoudnessMeter")
            .field("channels", &self.channels)
            .field("half_width", &self.half_width)
            .field("step_n", &self.step_n)
            .field("pending", &self.pending)
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

/// Channel `c` of the interleaved `frames` (`channels` samples each) into `out`, one per frame.
fn deinterleave(frames: &[f64], channels: usize, c: usize, out: &mut [f64]) {
    if channels == 1 {
        out.copy_from_slice(frames);
        return;
    }
    for (slot, frame) in out.iter_mut().zip(frames.chunks_exact(channels)) {
        *slot = frame[c];
    }
}

/// `kernel @ window` in four running lanes (taps m, m + 4, ...) added as `(l0 + l1) + (l2 + l3)`,
/// then any remainder: four independent chains the CPU overlaps, where one chain waits on each
/// add. numpy's product is BLAS's, in its own order; the golden's tolerance covers both.
fn dot(kernel: &[f64], window: &[f64]) -> f64 {
    let (kernel4, kernel_rest) = kernel.as_chunks::<4>();
    let (window4, window_rest) = window.as_chunks::<4>();
    let mut lanes = [0.0; 4];
    for (k, v) in kernel4.iter().zip(window4) {
        for ((lane, &k), &v) in lanes.iter_mut().zip(k).zip(v) {
            *lane += v * k;
        }
    }
    let mut sum = (lanes[0] + lanes[1]) + (lanes[2] + lanes[3]);
    for (&k, &v) in kernel_rest.iter().zip(window_rest) {
        sum += v * k;
    }
    sum
}

impl LoudnessMeter {
    /// The meter numpy's `LoudnessMeter` builds, at rest (silent context, nothing pending).
    ///
    /// `kernels` are [`POINTS`] rows of `2 * half_width` taps, row-major, row p's tap m
    /// multiplying a window's sample m (numpy's `_kernels_ascending.T`, i.e.
    /// `loudness.interpolation_kernels(half_width)[:, ::-1]`). `k_power` is numpy's `_k_power`
    /// (`step_n / 2 + 1` bins), `weights` one weight per channel, `step_n` the step's samples.
    ///
    /// # Errors
    ///
    /// [`LoudnessError::InvalidConfig`] if `half_width`, `step_n` or `weights` is empty or 0;
    /// [`LoudnessError::BadShape`] if `kernels` does not have `3 * 2 * half_width` values or
    /// `k_power` not `step_n / 2 + 1`.
    pub fn new(
        kernels: &[f64],
        half_width: usize,
        k_power: &[f64],
        weights: &[f64],
        step_n: usize,
    ) -> Result<Self, LoudnessError> {
        for (field, value) in [
            ("half_width", half_width),
            ("step_n", step_n),
            ("weights", weights.len()),
        ] {
            if value == 0 {
                return Err(LoudnessError::InvalidConfig { field });
            }
        }
        let taps = POINTS * 2 * half_width;
        if kernels.len() != taps {
            return Err(LoudnessError::BadShape {
                field: "kernels",
                got: kernels.len(),
                expected: taps,
            });
        }
        let bins = step_n / 2 + 1;
        if k_power.len() != bins {
            return Err(LoudnessError::BadShape {
                field: "k_power",
                got: k_power.len(),
                expected: bins,
            });
        }
        let channels = weights.len();
        let plan = forward(step_n);
        let scratch = plan.make_scratch_vec();
        Ok(Self {
            channels,
            half_width,
            step_n,
            kernels: kernels.to_vec(),
            k_power: k_power.to_vec(),
            weights: weights.to_vec(),
            context: vec![0.0; channels * 2 * half_width],
            pending_rows: vec![0.0; channels * step_n],
            pending: 0,
            steps: Vec::new(),
            time: vec![0.0; step_n],
            spectrum: vec![Complex::new(0.0, 0.0); bins],
            scratch,
            plan,
            power: vec![0.0; channels],
            seg: Vec::new(),
            fits: 0,
        })
    }

    /// The channels this meter takes.
    #[must_use]
    pub fn channels(&self) -> usize {
        self.channels
    }

    /// The energies (channel-weighted sums) of the steps the last [`push`](Self::push)
    /// completed, oldest first: numpy's new entries of `_steps`.
    #[must_use]
    pub fn new_steps(&self) -> &[f64] {
        &self.steps
    }

    /// One block of `frames` (interleaved: `n` frames of `channels` samples, numpy's `(n,
    /// channels)` C order): its true peak is returned and the steps it completes are in
    /// [`new_steps`](Self::new_steps). An empty block does nothing and gives 0 (numpy returns
    /// before measuring). Nothing is allocated once a block this long has been seen and the
    /// steps' buffer has held this many steps.
    ///
    /// # Errors
    ///
    /// [`LoudnessError::BadShape`] if `frames` is not a whole number of frames; then nothing
    /// changes.
    pub fn push(&mut self, frames: &[f64]) -> Result<f64, LoudnessError> {
        let ch = self.channels;
        if !frames.len().is_multiple_of(ch) {
            return Err(LoudnessError::BadShape {
                field: "frames",
                got: frames.len(),
                expected: ch,
            });
        }
        self.steps.clear();
        let n = frames.len() / ch;
        if n == 0 {
            return Ok(0.0);
        }
        let peak = self.true_peak(frames, n);
        self.take_steps(frames, n);
        Ok(peak)
    }

    /// numpy's `_true_peak`: the highest 4x-oversampled value of the positions that now have
    /// samples on both sides; the context moves on to the block's last samples.
    fn true_peak(&mut self, frames: &[f64], n: usize) -> f64 {
        let (ch, w) = (self.channels, self.half_width);
        let len = 2 * w + n;
        if n > self.fits {
            self.seg.resize(ch * len, 0.0);
            self.fits = n;
        }
        let seg = &mut self.seg[..ch * len];
        for (c, row) in seg.chunks_exact_mut(len).enumerate() {
            row[..2 * w].copy_from_slice(&self.context[c * 2 * w..(c + 1) * 2 * w]);
            deinterleave(frames, ch, c, &mut row[2 * w..]);
            self.context[c * 2 * w..(c + 1) * 2 * w].copy_from_slice(&row[len - 2 * w..]);
        }
        // Positions j (between seg[j] and seg[j + 1]) from w - 1 to len - w - 1; the sample peak
        // is over seg[w ..= len - w], every channel (numpy's `.max()`: a NaN wins).
        let (start, stop) = (w - 1, len - w);
        let mut sample_peak = 0.0_f64;
        let mut nan = false;
        for row in seg.chunks_exact(len) {
            for &v in &row[start + 1..=stop] {
                nan |= v.is_nan();
                let v = v.abs();
                sample_peak = if v > sample_peak { v } else { sample_peak };
            }
        }
        if nan {
            // numpy: no position compares as near a NaN, so the NaN is returned as it is.
            return f64::NAN;
        }
        if sample_peak == 0.0 {
            return 0.0;
        }
        let near = NEAR_PEAK * sample_peak;
        let taps = 2 * w;
        // numpy's np.abs(between).max(): a NaN wins; None while no position was near.
        let mut points: Option<f64> = None;
        for row in seg.chunks_exact(len) {
            for j in start..stop {
                // numpy's `np.maximum(...) >= near`: a NaN on either side is never near.
                let side = maximum(row[j].abs(), row[j + 1].abs());
                if side.is_nan() || side < near {
                    continue;
                }
                // The window of position j: seg[j - w + 1 ..= j + w], oldest first.
                let window = &row[j + 1 - w..=j + w];
                for kernel in self.kernels.chunks_exact(taps) {
                    let value = dot(kernel, window).abs();
                    points = Some(points.map_or(value, |p| maximum(p, value)));
                }
            }
        }
        match points {
            // Python's max(sample_peak, points): the first argument unless the second is larger.
            Some(p) if p > sample_peak => p,
            _ => sample_peak,
        }
    }

    /// numpy's step loop: the pending samples and the block, cut into whole steps; each step's
    /// K-weighted energy goes to `steps`, the rest stays pending.
    fn take_steps(&mut self, frames: &[f64], n: usize) {
        let (ch, step_n) = (self.channels, self.step_n);
        let mut at = 0;
        while at < n {
            let take = (step_n - self.pending).min(n - at);
            for c in 0..ch {
                let row = &mut self.pending_rows[c * step_n..(c + 1) * step_n];
                deinterleave(
                    &frames[at * ch..(at + take) * ch],
                    ch,
                    c,
                    &mut row[self.pending..self.pending + take],
                );
            }
            self.pending += take;
            at += take;
            if self.pending == step_n {
                let energy = self.step_energy();
                self.steps.push(energy);
                self.pending = 0;
            }
        }
    }

    /// One whole step's energy: per channel, `|rfft(step)|^2 @ k_power`, then the weighted sum
    /// of the channels (numpy's `weights @ power`).
    fn step_energy(&mut self) -> f64 {
        let step_n = self.step_n;
        for c in 0..self.channels {
            // realfft leaves its input as scratch: the step is copied in first.
            self.time
                .copy_from_slice(&self.pending_rows[c * step_n..(c + 1) * step_n]);
            self.plan
                .process_with_scratch(&mut self.time, &mut self.spectrum, &mut self.scratch)
                .expect("buffers sized by the plan");
            let mut sum = 0.0;
            for (z, &k) in self.spectrum.iter().zip(&self.k_power) {
                sum += z.norm_sqr() * k;
            }
            self.power[c] = sum;
        }
        let mut energy = 0.0;
        for (&w, &p) in self.weights.iter().zip(&self.power) {
            energy += w * p;
        }
        energy
    }

    /// The state, copied (to move it to numpy's meter).
    #[must_use]
    pub fn to_state(&self) -> MeterState {
        let step_n = self.step_n;
        let pending = (0..self.channels)
            .flat_map(|c| &self.pending_rows[c * step_n..c * step_n + self.pending])
            .copied()
            .collect();
        MeterState {
            context: self.context.clone(),
            pending,
        }
    }

    /// Takes `state` as its own (from numpy's meter). The steps of the last push are dropped:
    /// they described a block numpy already counted.
    ///
    /// # Errors
    ///
    /// [`LoudnessError::BadShape`] if `state.context` is not `channels * 2 * half_width` samples
    /// or `state.pending` is not a whole number of rows of fewer than `step_n` samples; then
    /// nothing changes.
    pub fn set_state(&mut self, state: &MeterState) -> Result<(), LoudnessError> {
        let ch = self.channels;
        if state.context.len() != self.context.len() {
            return Err(LoudnessError::BadShape {
                field: "context",
                got: state.context.len(),
                expected: self.context.len(),
            });
        }
        if !state.pending.len().is_multiple_of(ch) || state.pending.len() / ch >= self.step_n {
            return Err(LoudnessError::BadShape {
                field: "pending",
                got: state.pending.len(),
                expected: ch,
            });
        }
        let pending = state.pending.len() / ch;
        self.context.copy_from_slice(&state.context);
        if pending > 0 {
            for (c, row) in state.pending.chunks_exact(pending).enumerate() {
                let start = c * self.step_n;
                self.pending_rows[start..start + pending].copy_from_slice(row);
            }
        }
        self.pending = pending;
        self.steps.clear();
        Ok(())
    }
}
