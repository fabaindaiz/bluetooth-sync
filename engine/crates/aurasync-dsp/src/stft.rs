//! The streaming STFT the ambience extractor ([`crate::ambience`]) and the spatial upmix
//! ([`crate::spatial`]) both run: the root-Hann window, the input not yet consumed by a frame,
//! a frame's two forward transforms, and the inverse of a spectrum overlap-added into a buffer.
//!
//! Only what the two stages do operation by operation alike lives here. What differs stays in
//! each stage:
//!
//! - the smoothed auto spectra: the extractor squares numpy's `abs` (`np.abs(x) ** 2`, a `hypot`
//!   squared), the upmix takes `re * re + im * im` (see [`crate::complex::norm2`]);
//! - the overlap-add's normalisation: the extractor divides by the sum of squared windows where
//!   it is above its floor and leaves the sample undivided elsewhere; the upmix multiplies by
//!   `1 / max(sum, floor)` there and gives zero elsewhere;
//! - the buffers that grow with the block (one ready buffer, or two per speaker).

use std::f64::consts::PI;

use crate::Complex;
use crate::fft::{Forward, Inverse, inverse_real, plans};

/// numpy's `1e-20`, the regulariser of the coherence's and the energy ratio's denominators (and
/// the upmix's `psi`), in both stages.
pub(crate) const TINY: f64 = 1e-20;

/// numpy's `np.sqrt(np.hanning(n + 1)[:n])`.
pub(crate) fn root_hann(n: usize) -> Vec<f64> {
    let m = n + 1;
    (0..n)
        .map(|k| {
            let x = (1.0 - m as f64) + 2.0 * k as f64;
            (0.5 + 0.5 * (PI * x / (m as f64 - 1.0)).cos()).sqrt()
        })
        .collect()
}

/// `ambience.mapeo`: equation (12)'s `Gamma` of the ambience `index`, smooth on purpose, with
/// the knee at `threshold`, the floor `mu0`, the ceiling `mu1` and the slope `sigma`.
pub(crate) fn ambience_curve(index: f64, threshold: f64, mu0: f64, mu1: f64, sigma: f64) -> f64 {
    ((mu1 - mu0) / 2.0) * (sigma * PI * (index - threshold)).tanh() + ((mu1 + mu0) / 2.0)
}

/// How many output samples `held` input samples produce: one hop per full frame.
pub(crate) fn produced(held: usize, n_fft: usize, hop: usize) -> usize {
    if held >= n_fft {
        ((held - n_fft) / hop + 1) * hop
    } else {
        0
    }
}

/// The stereo input not yet consumed by a frame. Its buffers only grow ([`Pending::reserve`]);
/// pushing within what was reserved allocates nothing.
#[derive(Debug, Default)]
pub(crate) struct Pending {
    left: Vec<f64>,
    right: Vec<f64>,
    len: usize,
}

impl Pending {
    /// How many samples are held, per channel.
    pub(crate) fn len(&self) -> usize {
        self.len
    }

    /// The held left samples.
    pub(crate) fn left(&self) -> &[f64] {
        &self.left[..self.len]
    }

    /// The held right samples.
    pub(crate) fn right(&self) -> &[f64] {
        &self.right[..self.len]
    }

    /// Room for `capacity` samples per channel.
    pub(crate) fn reserve(&mut self, capacity: usize) {
        if self.left.len() < capacity {
            self.left.resize(capacity, 0.0);
            self.right.resize(capacity, 0.0);
        }
    }

    /// Nothing held.
    pub(crate) fn clear(&mut self) {
        self.len = 0;
    }

    /// `left` and `right` (the same length) appended; the room was reserved.
    pub(crate) fn push(&mut self, left: &[f64], right: &[f64]) {
        let (start, n) = (self.len, left.len());
        self.left[start..start + n].copy_from_slice(left);
        self.right[start..start + n].copy_from_slice(right);
        self.len += n;
    }

    /// The first `consumed` samples dropped, the rest moved to the front.
    pub(crate) fn consume(&mut self, consumed: usize) {
        self.left.copy_within(consumed..self.len, 0);
        self.right.copy_within(consumed..self.len, 0);
        self.len -= consumed;
    }

    /// `left` and `right` (the same length) held instead; the room was reserved.
    pub(crate) fn load(&mut self, left: &[f64], right: &[f64]) {
        self.len = 0;
        self.push(left, right);
    }
}

/// An STFT of `n_fft` points: its plans and window, a frame's two spectra, and the buffers of
/// its transforms, sized once.
pub(crate) struct Stft {
    window: Vec<f64>,
    window2: Vec<f64>,
    forward: Forward,
    inverse: Inverse,
    forward_scratch: Vec<Complex<f64>>,
    inverse_scratch: Vec<Complex<f64>>,
    frame_left: Vec<f64>,
    frame_right: Vec<f64>,
    /// The left channel's spectrum of the last frame analysed, `n_fft / 2 + 1` bins.
    pub(crate) spectrum_left: Vec<Complex<f64>>,
    /// The right channel's spectrum of the last frame analysed.
    pub(crate) spectrum_right: Vec<Complex<f64>>,
    time: Vec<f64>,
}

impl Stft {
    /// The STFT of `n_fft` points (at least 2), its plans from this thread's planner.
    pub(crate) fn new(n_fft: usize) -> Self {
        let (forward, inverse) = plans(n_fft);
        let forward_scratch = forward.make_scratch_vec();
        let inverse_scratch = inverse.make_scratch_vec();
        let bins = n_fft / 2 + 1;
        let window = root_hann(n_fft);
        let window2 = window.iter().map(|w| w * w).collect();
        Self {
            window,
            window2,
            forward,
            inverse,
            forward_scratch,
            inverse_scratch,
            frame_left: vec![0.0; n_fft],
            frame_right: vec![0.0; n_fft],
            spectrum_left: vec![Complex::new(0.0, 0.0); bins],
            spectrum_right: vec![Complex::new(0.0, 0.0); bins],
            time: vec![0.0; n_fft],
        }
    }

    /// The frame `pending[at .. at + n_fft]` of both channels, windowed, into
    /// [`Stft::spectrum_left`] and [`Stft::spectrum_right`] (numpy's `rfft(x * w)`).
    pub(crate) fn analyse(&mut self, pending: &Pending, at: usize) {
        for i in 0..self.window.len() {
            self.frame_left[i] = pending.left[at + i] * self.window[i];
            self.frame_right[i] = pending.right[at + i] * self.window[i];
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
    }

    /// numpy's `ola += irfft(spectrum, n_fft) * w`: the inverse (edge bins' imaginary parts
    /// ignored; `spectrum` is overwritten), each sample `t * (1 / n_fft) * w` in that order.
    pub(crate) fn overlap_add(&mut self, spectrum: &mut [Complex<f64>], ola: &mut [f64]) {
        inverse_real(
            &self.inverse,
            spectrum,
            &mut self.time,
            &mut self.inverse_scratch,
        );
        let scale = 1.0 / self.time.len() as f64;
        for ((o, &t), &w) in ola.iter_mut().zip(&self.time).zip(&self.window) {
            *o += t * scale * w;
        }
    }

    /// numpy's `norm += w ** 2`: one frame's squared window added to the window sum.
    pub(crate) fn add_window2(&self, norm: &mut [f64]) {
        for (n, w2) in norm.iter_mut().zip(&self.window2) {
            *n += w2;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn root_hann_squares_overlap_add_to_a_constant() {
        let n = 1024;
        let hop = n / 2;
        let w = root_hann(n);
        let frames = 8;
        let mut sum = vec![0.0; hop * (frames + 1)];
        for f in 0..frames {
            for (k, x) in w.iter().enumerate() {
                sum[f * hop + k] += x * x;
            }
        }
        // Away from the first and last frame, where fewer than two frames overlap.
        for (i, s) in sum[n..frames * hop].iter().enumerate() {
            assert!((s - sum[n]).abs() < 1e-12, "sample {}: {s}", n + i);
        }
        assert!((sum[n] - 1.0).abs() < 1e-12);
    }
}
