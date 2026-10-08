//! FIR filters by FFT convolution: [`StreamingFir`] (overlap-add with a tail) and
//! [`PartitionedFir`] (uniform partitioned overlap-save, for long filters, with no latency).
//!
//! A port of `host/src/aurasync/dsp/eq.py` (`StreamingFIR`, `PartitionedFIR`), which stays the
//! oracle: the host's golden (`host/tests/test_eq_rust.py`) holds this within 1e-9 of it. Every
//! user of those classes (the per-speaker EQ, the crossover's branches, the bass protection and
//! its all-pass, the virtual bass, the diffuse tail) gets these through the numpy classes, which
//! own one when the engine is Rust; Rust code can own them directly (the later ports of the
//! crossover and the virtual bass).
//!
//! The arithmetic follows numpy's, in numpy's order:
//!
//! - the FFT size of a block of `n` against `m` taps is the next power of two at or above
//!   `n + m - 1` (numpy's `1 << int(np.ceil(np.log2(n + m - 1)))`, identical below 2^50);
//! - the taps' spectrum is computed once per FFT size and kept until the taps change; the input's
//!   spectrum times it is numpy's complex product, and the inverse is scaled by `1 / size`;
//! - the partitioned filter's sum over partitions accumulates from zero in partition order, as
//!   numpy's `einsum("kf,kf->f", ...)` does;
//! - the FFTs are `realfft` (on `rustfft`) instead of numpy's pocketfft: the only difference that
//!   is not a matter of order, at the level of 1e-16 of the signal. Nothing feeds back (the tail
//!   and the history are sums of fresh products), so it does not accumulate.
//!
//! **Allocation.** `process` and `skip` allocate nothing once the FFT plan, the buffers and the
//! taps' spectrum for that block's FFT size exist. The first block of a new size builds them
//! (numpy does the same with its spectrum cache); [`StreamingFir::prepare`] builds them ahead. A
//! session sees one size per block length: the service's 4096 and, at most, a short last block
//! of a file. The partitioned filter's full blocks (`n == block`) never allocate; only its exact
//! path for a short block keeps a per-size cache. A change of taps to the same length refreshes
//! the kept spectra in place; to another length the tail is resized (once). The FFT plans come
//! from one planner per thread, so many filters of the same size share their twiddles.

use std::cell::RefCell;
use std::fmt;
use std::sync::Arc;

pub use realfft::num_complex::Complex;
use realfft::{ComplexToReal, RealFftPlanner, RealToComplex};

/// Why a call was refused. Nothing changes when it is.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum FirError {
    /// A filter needs at least one tap.
    NoTaps,
    /// A partitioned filter needs partitions of at least one sample.
    ZeroBlock,
    /// The output slice is not as long as the block.
    OutputMismatch,
    /// The kept tail is longer than this block's convolution: numpy's broadcast error, reached
    /// only after `replace_taps` (numpy's `taps = ...`) with fewer taps.
    TailTooLong { tail: usize, full: usize },
    /// A state whose size does not fit this filter.
    BadShape(String),
}

impl fmt::Display for FirError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::NoTaps => write!(f, "a FIR needs at least one tap"),
            Self::ZeroBlock => write!(f, "a partitioned FIR needs a block of at least one sample"),
            Self::OutputMismatch => write!(f, "the output must be as long as the block"),
            Self::TailTooLong { tail, full } => write!(
                f,
                "the kept tail ({tail} samples) is longer than this block's convolution ({full})"
            ),
            Self::BadShape(what) => write!(f, "{what}"),
        }
    }
}

impl std::error::Error for FirError {}

type Forward = Arc<dyn RealToComplex<f64>>;
type Inverse = Arc<dyn ComplexToReal<f64>>;

thread_local! {
    /// One planner per thread: plans are shared between filters (the planner caches them by
    /// length), so building a filter of a size already planned costs no twiddles.
    static PLANNER: RefCell<RealFftPlanner<f64>> = RefCell::new(RealFftPlanner::new());
}

fn plans(size: usize) -> (Forward, Inverse) {
    PLANNER.with_borrow_mut(|planner| {
        (
            planner.plan_fft_forward(size),
            planner.plan_fft_inverse(size),
        )
    })
}

/// numpy's `1 << int(np.ceil(np.log2(k)))`, for `k >= 1`.
fn fft_size(k: usize) -> usize {
    k.next_power_of_two()
}

/// One FFT size: its plans, its buffers and the taps' spectrum at that size.
struct Size {
    size: usize,
    forward: Forward,
    inverse: Inverse,
    taps_spectrum: Vec<Complex<f64>>,
    /// The taps' spectrum is that of the present taps.
    fresh: bool,
    time: Vec<f64>,
    spectrum: Vec<Complex<f64>>,
    scratch: Vec<Complex<f64>>,
}

impl Size {
    fn new(size: usize) -> Self {
        let (forward, inverse) = plans(size);
        let bins = size / 2 + 1;
        let scratch = forward.get_scratch_len().max(inverse.get_scratch_len());
        Self {
            size,
            forward,
            inverse,
            taps_spectrum: vec![Complex::new(0.0, 0.0); bins],
            fresh: false,
            time: vec![0.0; size],
            spectrum: vec![Complex::new(0.0, 0.0); bins],
            scratch: vec![Complex::new(0.0, 0.0); scratch],
        }
    }

    /// numpy's `np.fft.rfft(taps, size)`, when the taps changed since it was last computed.
    fn refresh(&mut self, taps: &[f64]) {
        if self.fresh {
            return;
        }
        self.time[..taps.len()].copy_from_slice(taps);
        self.time[taps.len()..].fill(0.0);
        self.forward
            .process_with_scratch(&mut self.time, &mut self.taps_spectrum, &mut self.scratch)
            .expect("buffers sized by the plan");
        self.fresh = true;
    }

    /// `time` (the input, zero-padded to `size`) becomes numpy's
    /// `np.fft.irfft(np.fft.rfft(time) * taps_spectrum, size)`.
    fn convolve(&mut self) {
        self.forward
            .process_with_scratch(&mut self.time, &mut self.spectrum, &mut self.scratch)
            .expect("buffers sized by the plan");
        for (s, h) in self.spectrum.iter_mut().zip(&self.taps_spectrum) {
            *s *= *h;
        }
        inverse_scaled(
            &self.inverse,
            &mut self.spectrum,
            &mut self.time,
            &mut self.scratch,
        );
    }
}

/// numpy's `np.fft.irfft(spectrum, len(time))`: the edge bins' imaginary parts ignored (realfft
/// wants them zero), the result scaled by `1 / len`.
fn inverse_scaled(
    inverse: &Inverse,
    spectrum: &mut [Complex<f64>],
    time: &mut [f64],
    scratch: &mut [Complex<f64>],
) {
    let size = time.len();
    spectrum[0].im = 0.0;
    if size.is_multiple_of(2) {
        spectrum[spectrum.len() - 1].im = 0.0;
    }
    inverse
        .process_with_scratch(spectrum, time, scratch)
        .expect("buffers sized by the plan, edge bins real");
    let scale = 1.0 / size as f64;
    for t in time.iter_mut() {
        *t *= scale;
    }
}

/// The per-size caches of a filter: found by size, built on the first block of a new size.
#[derive(Default)]
struct Sizes(Vec<Size>);

impl Sizes {
    /// The entry for `size`, with the spectrum of `taps`.
    fn get(&mut self, size: usize, taps: &[f64]) -> &mut Size {
        let at = match self.0.iter().position(|s| s.size == size) {
            Some(at) => at,
            None => {
                self.0.push(Size::new(size));
                self.0.len() - 1
            }
        };
        let entry = &mut self.0[at];
        entry.refresh(taps);
        entry
    }

    /// The taps changed: every kept spectrum is recomputed on its next use (in place).
    fn stale(&mut self) {
        for size in &mut self.0 {
            size.fresh = false;
        }
    }

    fn len(&self) -> usize {
        self.0.len()
    }
}

/// The state of a [`StreamingFir`]: what moves between engines at a cut's bottom.
#[derive(Debug, Clone, PartialEq)]
pub struct StreamingState {
    pub taps: Vec<f64>,
    /// The convolution's part that falls after the blocks given so far (`len(taps) - 1` samples,
    /// except right after `replace_taps` to another length).
    pub tail: Vec<f64>,
}

/// FFT overlap-add convolution that keeps its state between blocks (numpy's `StreamingFIR`):
/// each block's output is its convolution with the taps plus what the previous blocks left.
pub struct StreamingFir {
    taps: Vec<f64>,
    tail: Vec<f64>,
    sizes: Sizes,
}

impl StreamingFir {
    /// A filter with `taps` (at least one) and a silent tail.
    pub fn new(taps: &[f64]) -> Result<Self, FirError> {
        if taps.is_empty() {
            return Err(FirError::NoTaps);
        }
        Ok(Self {
            taps: taps.to_vec(),
            tail: vec![0.0; taps.len() - 1],
            sizes: Sizes::default(),
        })
    }

    pub fn taps(&self) -> &[f64] {
        &self.taps
    }

    pub fn tail(&self) -> &[f64] {
        &self.tail
    }

    /// How many FFT sizes are cached (one per block length seen, at most).
    pub fn cached_sizes(&self) -> usize {
        self.sizes.len()
    }

    /// numpy's `set_taps`: new taps; the tail is reset to silence only when the length changes.
    /// The caller does it at a cut's bottom, so the jump is never heard.
    pub fn set_taps(&mut self, taps: &[f64]) -> Result<(), FirError> {
        if taps.is_empty() {
            return Err(FirError::NoTaps);
        }
        if taps.len() != self.taps.len() {
            self.tail.clear();
            self.tail.resize(taps.len() - 1, 0.0);
        }
        self.load_taps(taps);
        Ok(())
    }

    /// numpy's `taps = ...` (the property's setter): new taps, the tail kept as it is.
    pub fn replace_taps(&mut self, taps: &[f64]) -> Result<(), FirError> {
        if taps.is_empty() {
            return Err(FirError::NoTaps);
        }
        self.load_taps(taps);
        Ok(())
    }

    fn load_taps(&mut self, taps: &[f64]) {
        self.taps.clear();
        self.taps.extend_from_slice(taps);
        self.sizes.stale();
    }

    /// Builds the plan, the buffers and the taps' spectrum for blocks of `block` samples now, so
    /// the first such block allocates nothing either.
    pub fn prepare(&mut self, block: usize) {
        if block > 0 {
            let size = fft_size(block + self.taps.len() - 1);
            self.sizes.get(size, &self.taps);
        }
    }

    /// One block in; as many samples out, into `out`. Allocates nothing once its FFT size is
    /// cached (and the tail is the taps' length less one).
    pub fn process(&mut self, x: &[f64], out: &mut [f64]) -> Result<(), FirError> {
        let n = x.len();
        if out.len() != n {
            return Err(FirError::OutputMismatch);
        }
        if n == 0 {
            return Ok(());
        }
        let m = self.taps.len();
        let full = n + m - 1;
        if self.tail.len() > full {
            return Err(FirError::TailTooLong {
                tail: self.tail.len(),
                full,
            });
        }
        let size = self.sizes.get(fft_size(full), &self.taps);
        size.time[..n].copy_from_slice(x);
        size.time[n..].fill(0.0);
        size.convolve();
        for (t, &kept) in size.time.iter_mut().zip(&self.tail) {
            *t += kept;
        }
        out.copy_from_slice(&size.time[..n]);
        self.tail.clear();
        self.tail.extend_from_slice(&size.time[n..full]);
        Ok(())
    }

    /// The state, copied (to move it to numpy's filter).
    pub fn state(&self) -> StreamingState {
        StreamingState {
            taps: self.taps.clone(),
            tail: self.tail.clone(),
        }
    }

    /// Takes `state` as its own (from numpy's filter). Any tail length is taken, as numpy keeps
    /// it; on an error nothing changes.
    pub fn set_state(&mut self, state: &StreamingState) -> Result<(), FirError> {
        if state.taps.is_empty() {
            return Err(FirError::NoTaps);
        }
        if state.taps != self.taps {
            self.load_taps(&state.taps);
        }
        self.tail.clear();
        self.tail.extend_from_slice(&state.tail);
        Ok(())
    }
}

/// The state of a [`PartitionedFir`]: what moves between engines at a cut's bottom. The
/// partitions' spectra and the exact path's caches follow from the taps and are rebuilt.
#[derive(Debug, Clone, PartialEq)]
pub struct PartitionedState {
    /// The last `(partitions + 1) * block` input samples.
    pub history: Vec<f64>,
    /// The frequency-domain delay line: `partitions` rows of `block + 1` bins.
    pub fdl: Vec<Vec<Complex<f64>>>,
    /// The row of the newest input frame.
    pub head: usize,
    /// The delay line follows the history (false after a skip or a short block: the next full
    /// block rebuilds it from the history).
    pub fdl_valid: bool,
}

/// Uniform partitioned convolution (overlap-save) with no latency (numpy's `PartitionedFIR`).
///
/// The taps are cut into partitions of `block` samples; a block of `block` input samples costs
/// one FFT pair of `2 * block` plus one product per partition. Longer blocks are cut; a shorter
/// one goes through an exact convolution from the kept history, after which the delay line is
/// rebuilt from that history on the next full block.
pub struct PartitionedFir {
    taps: Vec<f64>,
    block: usize,
    parts: usize,
    bins: usize,
    /// The partitions' spectra, `parts` rows of `bins`.
    part_spectra: Vec<Complex<f64>>,
    history: Vec<f64>,
    /// The delay line, `parts` rows of `bins`.
    fdl: Vec<Complex<f64>>,
    head: usize,
    fdl_valid: bool,
    forward: Forward,
    inverse: Inverse,
    frame: Vec<f64>,
    total: Vec<Complex<f64>>,
    scratch: Vec<Complex<f64>>,
    /// The exact path's caches, per FFT size.
    exact: Sizes,
}

impl PartitionedFir {
    /// A filter with `taps` (at least one) cut into partitions of `block` (at least one) samples,
    /// at rest.
    pub fn new(taps: &[f64], block: usize) -> Result<Self, FirError> {
        if taps.is_empty() {
            return Err(FirError::NoTaps);
        }
        if block == 0 {
            return Err(FirError::ZeroBlock);
        }
        let parts = taps.len().div_ceil(block);
        let bins = block + 1;
        let (forward, inverse) = plans(2 * block);
        let scratch = forward.get_scratch_len().max(inverse.get_scratch_len());
        let mut fir = Self {
            taps: taps.to_vec(),
            block,
            parts,
            bins,
            part_spectra: vec![Complex::new(0.0, 0.0); parts * bins],
            history: vec![0.0; (parts + 1) * block],
            fdl: vec![Complex::new(0.0, 0.0); parts * bins],
            head: 0,
            fdl_valid: true,
            forward,
            inverse,
            frame: vec![0.0; 2 * block],
            total: vec![Complex::new(0.0, 0.0); bins],
            scratch: vec![Complex::new(0.0, 0.0); scratch],
            exact: Sizes::default(),
        };
        // numpy's `np.fft.rfft(padded.reshape(parts, block), 2 * block, axis=1)`.
        for k in 0..parts {
            let start = k * block;
            let end = (start + block).min(taps.len());
            fir.frame[..end - start].copy_from_slice(&taps[start..end]);
            fir.frame[end - start..].fill(0.0);
            fir.forward
                .process_with_scratch(
                    &mut fir.frame,
                    &mut fir.part_spectra[k * bins..(k + 1) * bins],
                    &mut fir.scratch,
                )
                .expect("buffers sized by the plan");
        }
        Ok(fir)
    }

    pub fn taps(&self) -> &[f64] {
        &self.taps
    }

    pub fn block(&self) -> usize {
        self.block
    }

    pub fn partitions(&self) -> usize {
        self.parts
    }

    /// How many FFT sizes the exact path for short blocks has cached.
    pub fn cached_sizes(&self) -> usize {
        self.exact.len()
    }

    /// numpy's `_push`: `x` appended to the history, which keeps its length.
    fn push(&mut self, x: &[f64]) {
        let (n, len) = (x.len(), self.history.len());
        if n >= len {
            self.history.copy_from_slice(&x[n - len..]);
        } else {
            self.history.copy_within(n.., 0);
            self.history[len - n..].copy_from_slice(x);
        }
    }

    /// Takes `x` as input without computing its output (numpy's `skip`). Allocates nothing.
    pub fn skip(&mut self, x: &[f64]) {
        self.push(x);
        self.fdl_valid = false;
    }

    /// Back at rest, as a new filter is: history and delay line zero, nothing skipped. The
    /// partitions' spectra and the exact path's caches follow from the taps and stay. Allocates
    /// nothing.
    pub fn reset(&mut self) {
        self.history.fill(0.0);
        self.fdl.fill(Complex::new(0.0, 0.0));
        self.head = 0;
        self.fdl_valid = true;
    }

    /// `history[end - 2 * block .. end]`'s spectrum into the delay line's `row`.
    fn transform_into(&mut self, row: usize, end: usize) {
        let p = self.block;
        self.frame.copy_from_slice(&self.history[end - 2 * p..end]);
        self.forward
            .process_with_scratch(
                &mut self.frame,
                &mut self.fdl[row * self.bins..(row + 1) * self.bins],
                &mut self.scratch,
            )
            .expect("buffers sized by the plan");
    }

    /// numpy's `_one`: a block of at most `block` samples.
    fn one(&mut self, x: &[f64], out: &mut [f64]) {
        let (n, p, parts, bins) = (x.len(), self.block, self.parts, self.bins);
        self.push(x);
        let len = self.history.len();
        if n == p {
            if self.fdl_valid {
                self.head = (self.head + 1) % parts;
                self.transform_into(self.head, len);
            } else {
                for k in 0..parts {
                    self.transform_into((self.head + parts - k) % parts, len - k * p);
                }
                self.fdl_valid = true;
            }
            self.total.fill(Complex::new(0.0, 0.0));
            for k in 0..parts {
                let row = (self.head + parts - k) % parts;
                let fdl = &self.fdl[row * bins..(row + 1) * bins];
                let part = &self.part_spectra[k * bins..(k + 1) * bins];
                for ((t, &a), &b) in self.total.iter_mut().zip(fdl).zip(part) {
                    *t += a * b;
                }
            }
            inverse_scaled(
                &self.inverse,
                &mut self.total,
                &mut self.frame,
                &mut self.scratch,
            );
            out.copy_from_slice(&self.frame[p..]);
            return;
        }
        self.fdl_valid = false;
        let m = self.taps.len();
        let span = len.min(m - 1 + n);
        let size = self.exact.get(fft_size(span + m - 1), &self.taps);
        size.time[..span].copy_from_slice(&self.history[len - span..]);
        size.time[span..].fill(0.0);
        size.convolve();
        out.copy_from_slice(&size.time[span - n..span]);
    }

    /// One block in (any length; longer than `block` is cut); as many samples out, into `out`.
    /// Allocates nothing for full blocks, nor for a short one whose FFT size is cached.
    pub fn process(&mut self, x: &[f64], out: &mut [f64]) -> Result<(), FirError> {
        if out.len() != x.len() {
            return Err(FirError::OutputMismatch);
        }
        for (x, out) in x.chunks(self.block).zip(out.chunks_mut(self.block)) {
            self.one(x, out);
        }
        Ok(())
    }

    /// The state, copied (to move it to numpy's filter).
    pub fn state(&self) -> PartitionedState {
        PartitionedState {
            history: self.history.clone(),
            fdl: self.fdl.chunks(self.bins).map(<[_]>::to_vec).collect(),
            head: self.head,
            fdl_valid: self.fdl_valid,
        }
    }

    /// Takes `state` as its own (from numpy's filter). Every size is checked first; on an error
    /// nothing changes.
    pub fn set_state(&mut self, state: &PartitionedState) -> Result<(), FirError> {
        let bad = |what: String| Err(FirError::BadShape(format!("state: {what}")));
        if state.history.len() != self.history.len() {
            return bad(format!(
                "history has length {}, expected {}",
                state.history.len(),
                self.history.len()
            ));
        }
        if state.fdl.len() != self.parts {
            return bad(format!(
                "fdl has {} rows, expected {}",
                state.fdl.len(),
                self.parts
            ));
        }
        if let Some(row) = state.fdl.iter().find(|row| row.len() != self.bins) {
            return bad(format!(
                "fdl has a row of {} bins, expected {}",
                row.len(),
                self.bins
            ));
        }
        if state.head >= self.parts {
            return bad(format!(
                "head {} is not a row of the fdl ({} rows)",
                state.head, self.parts
            ));
        }
        self.history.copy_from_slice(&state.history);
        for (to, from) in self.fdl.chunks_mut(self.bins).zip(&state.fdl) {
            to.copy_from_slice(from);
        }
        self.head = state.head;
        self.fdl_valid = state.fdl_valid;
        Ok(())
    }
}
