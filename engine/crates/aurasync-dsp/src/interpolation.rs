//! Band-limited reading between samples, for the fractional delay line.
//!
//! A port of `host/src/aurasync/dsp/interpolation.py`, with both of its strategies:
//!
//! - **Few distinct fractions** (a still delay, at most [`FEW`] in a block): the weights are
//!   computed once per distinct fraction with the formula, the Kaiser-windowed sinc of [`TAPS`]
//!   taps ([`kernel`]).
//! - **More** (a moving delay): the kernel comes from a table sampled every 1/[`STEPS`] of a
//!   sample on `[-HALF - 1, HALF + 1]`, read with 4-point Lagrange interpolation; within 1e-10 of
//!   the formula.
//!
//! Either way the weights are normalised to sum 1, and a position `p` reads
//! `data[floor(p) - HALF + 1 ..= floor(p) + HALF]`. Sums use numpy's order for a 32-element
//! reduction (eight running lanes, then a pairwise tree), so the output follows numpy's rounding.
//!
//! One deliberate difference: the sinc is an exact 0 at nonzero integers (numpy's `np.sinc` gives
//! about 1e-17 there), so an integer position returns its sample bit for bit.

use std::f64::consts::PI;
use std::fmt;

/// Taps on each side of the reading point; a read needs `HALF - 1` samples before and `HALF`
/// after it.
pub const HALF: usize = 16;
/// Kaiser window shape.
pub const BETA: f64 = 8.0;
/// Taps of the kernel: offsets `-HALF + 1 ..= HALF`.
pub const TAPS: usize = 2 * HALF;
/// Up to this many distinct fractions in a block, the weights come straight from the formula.
pub const FEW: usize = 64;
/// Table points per sample for a moving delay.
pub const STEPS: usize = 2048;

/// The table covers `t` in `[GRID_START, -GRID_START]`: one extra sample each side of the
/// kernel's support, for the cubic.
const GRID_START: f64 = -(HALF as f64) - 1.0;
const TABLE_LEN: usize = (2 * HALF + 2) * STEPS + 1;
/// The first offset, `-HALF + 1`.
const FIRST_OFFSET: f64 = 1.0 - HALF as f64;
const I0_BETA: f64 = bessel_i0(BETA);

/// The modified Bessel function of the first kind, order 0, by its power series
/// `sum_k ((x/2)^2)^k / (k!)^2`, summed until a term is under 1e-16 of the total.
///
/// NaN gives NaN, and the series stops after [`I0_MAX_TERMS`] terms in any case: a NaN or
/// infinite term never falls under the threshold, and the loop would not end. The kernel only
/// asks for `x` in `[0, BETA]`, which converges in about 20 terms.
const fn bessel_i0(x: f64) -> f64 {
    if x.is_nan() {
        return x;
    }
    let q = (x / 2.0) * (x / 2.0);
    let mut term = 1.0;
    let mut sum = 1.0;
    let mut k = 1;
    while k <= I0_MAX_TERMS {
        let kf = k as f64;
        term = term * q / (kf * kf);
        sum += term;
        if term < 1e-16 * sum {
            break;
        }
        k += 1;
    }
    sum
}

/// The most terms [`bessel_i0`] sums; enough for any `x` whose I0 is finite (about 713).
const I0_MAX_TERMS: u32 = 1000;

/// `sin(pi t) / (pi t)`: 1 at 0 and an exact 0 at the other integers.
fn sinc(t: f64) -> f64 {
    if t == 0.0 {
        1.0
    } else if t == t.trunc() {
        0.0
    } else {
        let y = PI * t;
        y.sin() / y
    }
}

/// The windowed sinc at offset `t`, not normalised: numpy's `_kernel`.
#[must_use]
pub fn kernel(t: f64) -> f64 {
    let r = t / HALF as f64;
    let window = bessel_i0(BETA * (1.0 - r * r).clamp(0.0, 1.0).sqrt()) / I0_BETA;
    sinc(t) * window
}

/// The sum of 32 values in numpy's order (`pairwise_sum` for a short contiguous run: eight
/// running lanes, then a pairwise tree).
#[inline]
fn sum32(a: &[f64; TAPS]) -> f64 {
    let (chunks, _) = a.as_chunks::<8>();
    let mut r = chunks[0];
    for chunk in &chunks[1..] {
        for (lane, &x) in r.iter_mut().zip(chunk) {
            *lane += x;
        }
    }
    ((r[0] + r[1]) + (r[2] + r[3])) + ((r[4] + r[5]) + (r[6] + r[7]))
}

/// `sum(data * weights)` in numpy's order: the products, then [`sum32`]'s lanes and tree.
#[inline]
fn dot32(data: &[f64; TAPS], weights: &[f64; TAPS]) -> f64 {
    let (data, _) = data.as_chunks::<8>();
    let (weights, _) = weights.as_chunks::<8>();
    let mut r = [0.0; 8];
    for (lane, (&d, &w)) in r.iter_mut().zip(data[0].iter().zip(&weights[0])) {
        *lane = d * w;
    }
    for (data, weights) in data[1..].iter().zip(&weights[1..]) {
        for (lane, (&d, &w)) in r.iter_mut().zip(data.iter().zip(weights)) {
            *lane += d * w;
        }
    }
    ((r[0] + r[1]) + (r[2] + r[3])) + ((r[4] + r[5]) + (r[6] + r[7]))
}

fn normalise(weights: &mut [f64; TAPS]) {
    let total = sum32(weights);
    for w in weights.iter_mut() {
        *w /= total;
    }
}

/// The `TAPS` samples read by a position whose integer part is `floor` (already range-checked).
#[inline]
fn window(data: &[f64], floor: f64) -> &[f64; TAPS] {
    let start = floor as usize - (HALF - 1);
    data[start..start + TAPS]
        .try_into()
        .expect("the range check leaves TAPS samples around every position")
}

/// Why a read was refused. Nothing is written to `out` when it is.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ReadError {
    /// `position[index]` does not have `HALF - 1` samples before it and `HALF` after it in
    /// `data`, or is not finite.
    OutOfRange {
        /// The index of the first position that is out of range.
        index: usize,
    },
    /// `out` and `position` differ in length.
    LengthMismatch,
    /// More positions than the `max_block` the reader was built for.
    BlockTooLarge,
}

impl fmt::Display for ReadError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::OutOfRange { index } => write!(
                f,
                "position {index} needs {} samples before it and {HALF} after it",
                HALF - 1
            ),
            Self::LengthMismatch => write!(f, "out and position differ in length"),
            Self::BlockTooLarge => write!(f, "more positions than the reader's max_block"),
        }
    }
}

impl std::error::Error for ReadError {}

/// The band-limited read, with its table and scratch buffers allocated once.
///
/// [`Reader::read`] allocates nothing for blocks of up to `max_block` positions and refuses
/// larger ones.
pub struct Reader {
    max_block: usize,
    /// The kernel at `GRID_START + j / STEPS`, `j` in `0..TABLE_LEN`.
    table: Box<[f64]>,
    /// The block's fractions, sorted to find the distinct ones.
    sorted: Box<[f64]>,
    /// The block's distinct fractions, ascending, when there are at most `FEW`.
    distinct: [f64; FEW],
    /// The normalised weights of each distinct fraction.
    weights: Box<[[f64; TAPS]; FEW]>,
}

impl fmt::Debug for Reader {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("Reader")
            .field("max_block", &self.max_block)
            .field("table_len", &self.table.len())
            .finish_non_exhaustive()
    }
}

impl Reader {
    /// A reader for blocks of up to `max_block` positions.
    #[must_use]
    pub fn new(max_block: usize) -> Self {
        let table = (0..TABLE_LEN)
            .map(|j| kernel(GRID_START + j as f64 / STEPS as f64))
            .collect();
        Self {
            max_block,
            table,
            sorted: vec![0.0; max_block].into_boxed_slice(),
            distinct: [0.0; FEW],
            weights: Box::new([[0.0; TAPS]; FEW]),
        }
    }

    /// The largest block [`Reader::read`] accepts.
    #[must_use]
    pub fn max_block(&self) -> usize {
        self.max_block
    }

    /// The normalised weights for fraction `frac` from the formula (numpy's strategy for few
    /// distinct fractions). Tap `j` is offset `j - HALF + 1`.
    pub fn weights_from_formula(frac: f64, weights: &mut [f64; TAPS]) {
        for (j, w) in weights.iter_mut().enumerate() {
            *w = kernel(frac - (FIRST_OFFSET + j as f64));
        }
        normalise(weights);
    }

    /// The normalised weights for fraction `frac` (in `[0, 1]`) from the table, by 4-point
    /// Lagrange interpolation (numpy's `_kernel_from_table`). The offsets are whole samples, so
    /// the position between table points, and the four coefficients, are the same for every tap.
    ///
    /// A fraction far outside `[0, 1]` reads past the table and panics; debug builds check
    /// the range first.
    pub fn weights_from_table(&self, frac: f64, weights: &mut [f64; TAPS]) {
        debug_assert!(
            (0.0..=1.0).contains(&frac),
            "weights_from_table needs a fraction in [0, 1]"
        );
        let x = (frac - GRID_START) * STEPS as f64;
        let base = x.floor();
        let u = x - base;
        let (um1, up1, um2) = (u - 1.0, u + 1.0, u - 2.0);
        let c0 = -u * um1 * um2 / 6.0;
        let c1 = up1 * um1 * um2 / 2.0;
        let c2 = up1 * u * um2 / 2.0;
        let c3 = up1 * u * um1 / 6.0;
        // The tap at offset `o = j - HALF + 1` reads around table index `base - o * STEPS`.
        let first = base as usize + (HALF - 1) * STEPS;
        for (j, w) in weights.iter_mut().enumerate() {
            let k = first - j * STEPS;
            let t = &self.table[k - 1..k + 3];
            *w = c0 * t[0] + c1 * t[1] - c2 * t[2] + c3 * t[3];
        }
        normalise(weights);
    }

    /// `data` evaluated at each (fractional) `position`, band-limited, into `out`.
    ///
    /// Every position must have `HALF - 1` samples before and `HALF` after it in `data`;
    /// otherwise the read is refused with the index of the first that does not, and `out` is
    /// left as it was.
    ///
    /// # Errors
    ///
    /// [`ReadError::LengthMismatch`] if `out` and `position` differ in length;
    /// [`ReadError::BlockTooLarge`] if there are more positions than the reader's `max_block`;
    /// [`ReadError::OutOfRange`] if a position has too few samples around it, or is not finite.
    ///
    /// # Panics
    ///
    /// Never in practice: the range check leaves every position with the samples it reads.
    pub fn read(
        &mut self,
        data: &[f64],
        position: &[f64],
        out: &mut [f64],
    ) -> Result<(), ReadError> {
        if out.len() != position.len() {
            return Err(ReadError::LengthMismatch);
        }
        if position.len() > self.max_block {
            return Err(ReadError::BlockTooLarge);
        }
        // `floor(p)` in [HALF - 1, len - 1 - HALF]; NaN and infinities fail the comparison.
        let lowest = (HALF - 1) as f64;
        let highest = data.len() as f64 - 1.0 - HALF as f64;
        if let Some(index) = position.iter().position(|p| {
            let floor = p.floor();
            !(floor >= lowest && floor <= highest)
        }) {
            return Err(ReadError::OutOfRange { index });
        }

        let distinct = self.distinct_fractions(position);
        if distinct <= FEW {
            for d in 0..distinct {
                let mut weights = [0.0; TAPS];
                Self::weights_from_formula(self.distinct[d], &mut weights);
                self.weights[d] = weights;
            }
            let fractions = &self.distinct[..distinct];
            let mut which = 0;
            for (o, &p) in out.iter_mut().zip(position) {
                let floor = p.floor();
                let frac = p - floor;
                if fractions[which] != frac {
                    which = fractions
                        .binary_search_by(|f| f.total_cmp(&frac))
                        .expect("every fraction of the block is among the distinct ones");
                }
                *o = dot32(window(data, floor), &self.weights[which]);
            }
        } else {
            let mut weights = [0.0; TAPS];
            for (o, &p) in out.iter_mut().zip(position) {
                let floor = p.floor();
                self.weights_from_table(p - floor, &mut weights);
                *o = dot32(window(data, floor), &weights);
            }
        }
        Ok(())
    }

    /// How many distinct fractions `position` has, counting up to `FEW + 1`. When there are at
    /// most `FEW`, they are left in `self.distinct`, ascending.
    fn distinct_fractions(&mut self, position: &[f64]) -> usize {
        let sorted = &mut self.sorted[..position.len()];
        for (s, &p) in sorted.iter_mut().zip(position) {
            *s = p - p.floor();
        }
        // An unstable sort works in place: no allocation.
        sorted.sort_unstable_by(f64::total_cmp);
        let mut count = 0;
        for &frac in sorted.iter() {
            if count == 0 || frac != self.distinct[count - 1] {
                if count == FEW {
                    return FEW + 1;
                }
                self.distinct[count] = frac;
                count += 1;
            }
        }
        count
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A tiny deterministic generator (`SplitMix64`) with a wide dynamic range, so that the order
    /// of the additions changes the rounding and an equality check means something.
    fn wide(seed: u64, n: usize) -> Vec<f64> {
        let mut state = seed;
        (0..n)
            .map(|_| {
                state = state.wrapping_add(0x9E37_79B9_7F4A_7C15);
                let mut z = state;
                z = (z ^ (z >> 30)).wrapping_mul(0xBF58_476D_1CE4_E5B9);
                z = (z ^ (z >> 27)).wrapping_mul(0x94D0_49BB_1331_11EB);
                z ^= z >> 31;
                let unit = (z >> 11) as f64 / (1u64 << 53) as f64;
                (2.0 * unit - 1.0) * 10f64.powi((z & 7) as i32 - 3)
            })
            .collect()
    }

    /// numpy's `pairwise_sum` (`loops_utils.h.src`), restated independently: under 8 elements a
    /// plain loop, up to 128 eight running lanes combined as a tree plus a plain remainder, above
    /// that a split at half the length rounded down to a multiple of 8.
    fn numpy_pairwise(a: &[f64]) -> f64 {
        let n = a.len();
        if n < 8 {
            let mut res = 0.0;
            for &x in a {
                res += x;
            }
            res
        } else if n <= 128 {
            let mut r: [f64; 8] = a[..8].try_into().unwrap();
            let mut i = 8;
            while i < n - n % 8 {
                for j in 0..8 {
                    r[j] += a[i + j];
                }
                i += 8;
            }
            let mut res = ((r[0] + r[1]) + (r[2] + r[3])) + ((r[4] + r[5]) + (r[6] + r[7]));
            while i < n {
                res += a[i];
                i += 1;
            }
            res
        } else {
            let mut n2 = n / 2;
            n2 -= n2 % 8;
            numpy_pairwise(&a[..n2]) + numpy_pairwise(&a[n2..])
        }
    }

    #[test]
    fn the_reference_has_numpys_shape_on_every_length() {
        // The lengths the kernel does not use are here so the restatement itself is checked
        // against an exactly summable input: small integers add exactly in any order.
        for n in [0, 1, 7, 8, 9, 31, 33, 1000] {
            let ints: Vec<f64> = (0..n).map(|k| f64::from(k as u32 % 13) - 6.0).collect();
            let exact: f64 = ints.iter().sum();
            assert_eq!(numpy_pairwise(&ints), exact, "length {n}");
        }
    }

    #[test]
    fn sum32_equals_numpys_pairwise_order_exactly() {
        for seed in 0..50 {
            let v = wide(seed, TAPS);
            let a: [f64; TAPS] = v.as_slice().try_into().unwrap();
            assert_eq!(sum32(&a), numpy_pairwise(&v), "seed {seed}");
        }
    }

    #[test]
    fn dot32_equals_numpys_sum_of_products_exactly() {
        for seed in 0..50 {
            let (d, w) = (wide(2 * seed, TAPS), wide(2 * seed + 1, TAPS));
            let products: Vec<f64> = d.iter().zip(&w).map(|(x, y)| x * y).collect();
            let (d, w): ([f64; TAPS], [f64; TAPS]) = (
                d.as_slice().try_into().unwrap(),
                w.as_slice().try_into().unwrap(),
            );
            assert_eq!(dot32(&d, &w), numpy_pairwise(&products), "seed {seed}");
        }
    }

    #[test]
    fn bessel_i0_known_values() {
        assert_eq!(bessel_i0(0.0), 1.0);
        assert!((bessel_i0(1.0) - 1.266_065_877_752_008_2).abs() < 1e-15);
    }
}
