//! The band-limited read: the port of `host/src/aurasync/dsp/interpolation.py`.
//!
//! The formula strategy is the reference; the table strategy must stay within 1e-10 of it, and
//! both must match numpy (`matches_numpy_reference_values`, values embedded below).
//!
//! The counting allocator below is the only `unsafe` in the crate's tests: `GlobalAlloc` is an
//! unsafe trait. The library itself is `#![forbid(unsafe_code)]`.

use std::alloc::{GlobalAlloc, Layout, System};
use std::cell::Cell;
use std::hint::black_box;

use aurasync_dsp::interpolation::{FEW, HALF, ReadError, Reader, TAPS, kernel};

// --- An allocation counter, per thread so that tests running in parallel do not interfere.

struct Counting;

thread_local! {
    static COUNTING: Cell<bool> = const { Cell::new(false) };
    static ALLOCATIONS: Cell<usize> = const { Cell::new(0) };
}

fn note_allocation() {
    if COUNTING.try_with(Cell::get).unwrap_or(false) {
        let _ = ALLOCATIONS.try_with(|n| n.set(n.get() + 1));
    }
}

// SAFETY: every call is forwarded unchanged to the system allocator; the counter only touches
// const-initialised thread-locals, which never allocate.
unsafe impl GlobalAlloc for Counting {
    unsafe fn alloc(&self, layout: Layout) -> *mut u8 {
        note_allocation();
        // SAFETY: same contract as the caller's.
        unsafe { System.alloc(layout) }
    }

    unsafe fn alloc_zeroed(&self, layout: Layout) -> *mut u8 {
        note_allocation();
        // SAFETY: same contract as the caller's.
        unsafe { System.alloc_zeroed(layout) }
    }

    unsafe fn realloc(&self, ptr: *mut u8, layout: Layout, new_size: usize) -> *mut u8 {
        note_allocation();
        // SAFETY: same contract as the caller's.
        unsafe { System.realloc(ptr, layout, new_size) }
    }

    unsafe fn dealloc(&self, ptr: *mut u8, layout: Layout) {
        // SAFETY: same contract as the caller's.
        unsafe { System.dealloc(ptr, layout) }
    }
}

#[global_allocator]
static GLOBAL: Counting = Counting;

/// How many allocations `f` made on this thread.
fn allocations_in(f: impl FnOnce()) -> usize {
    ALLOCATIONS.with(|n| n.set(0));
    COUNTING.with(|c| c.set(true));
    f();
    COUNTING.with(|c| c.set(false));
    ALLOCATIONS.with(Cell::get)
}

// --- Helpers.

/// SplitMix64: a small deterministic generator, so the tests need no dependency.
struct Rng(u64);

impl Rng {
    fn next_u64(&mut self) -> u64 {
        self.0 = self.0.wrapping_add(0x9E37_79B9_7F4A_7C15);
        let mut z = self.0;
        z = (z ^ (z >> 30)).wrapping_mul(0xBF58_476D_1CE4_E5B9);
        z = (z ^ (z >> 27)).wrapping_mul(0x94D0_49BB_1331_11EB);
        z ^ (z >> 31)
    }

    /// Uniform in [0, 1).
    fn unit(&mut self) -> f64 {
        (self.next_u64() >> 11) as f64 / (1u64 << 53) as f64
    }

    /// Full scale: uniform in [-1, 1).
    fn signal(&mut self, n: usize) -> Vec<f64> {
        (0..n).map(|_| 2.0 * self.unit() - 1.0).collect()
    }
}

/// Each position read on its own: one distinct fraction, so always the formula strategy.
fn one_at_a_time(data: &[f64], position: &[f64]) -> Vec<f64> {
    let mut reader = Reader::new(1);
    position
        .iter()
        .map(|&p| {
            let mut out = [0.0];
            reader.read(data, &[p], &mut out).unwrap();
            out[0]
        })
        .collect()
}

fn max_abs_diff(a: &[f64], b: &[f64]) -> f64 {
    assert_eq!(a.len(), b.len());
    a.iter()
        .zip(b)
        .map(|(x, y)| (x - y).abs())
        .fold(0.0, f64::max)
}

fn distinct_fractions(position: &[f64]) -> usize {
    let mut fractions: Vec<f64> = position.iter().map(|p| p - p.floor()).collect();
    fractions.sort_by(f64::total_cmp);
    fractions.dedup();
    fractions.len()
}

// --- The tests the plan asks for.

#[test]
fn integer_positions_copy_exactly() {
    // The kernel is a single 1 at integer positions...
    assert_eq!(kernel(0.0), 1.0);
    for k in 1..=HALF + 1 {
        assert_eq!(kernel(k as f64), 0.0, "kernel({k})");
        assert_eq!(kernel(-(k as f64)), 0.0, "kernel(-{k})");
    }
    // ...so reading at an integer position returns the sample itself, bit for bit.
    let data = Rng(1).signal(512);
    let position: Vec<f64> = (HALF - 1..data.len() - HALF).map(|p| p as f64).collect();
    let mut out = vec![0.0; position.len()];
    Reader::new(position.len())
        .read(&data, &position, &mut out)
        .unwrap();
    for (&p, &got) in position.iter().zip(&out) {
        assert_eq!(got.to_bits(), data[p as usize].to_bits(), "position {p}");
    }
    // The same through the table strategy: integer positions among more than FEW fractions.
    let mut rng = Rng(2);
    let position: Vec<f64> = (0..400)
        .map(|i| {
            let p = (HALF + i) as f64;
            if i % 2 == 0 { p } else { p + rng.unit() }
        })
        .collect();
    assert!(distinct_fractions(&position) > FEW);
    let mut out = vec![0.0; position.len()];
    Reader::new(position.len())
        .read(&data, &position, &mut out)
        .unwrap();
    for (&p, &got) in position.iter().zip(&out).step_by(2) {
        assert_eq!(got.to_bits(), data[p as usize].to_bits(), "position {p}");
    }
}

#[test]
fn weights_sum_to_one() {
    let reader = Reader::new(0);
    let mut rng = Rng(3);
    let mut weights = [0.0; TAPS];
    for _ in 0..1000 {
        let frac = rng.unit();
        reader.weights_from_formula(frac, &mut weights);
        assert!(
            (weights.iter().sum::<f64>() - 1.0).abs() < 1e-14,
            "formula, {frac}"
        );
        reader.weights_from_table(frac, &mut weights);
        assert!(
            (weights.iter().sum::<f64>() - 1.0).abs() < 1e-14,
            "table, {frac}"
        );
    }
    // Through `read`: on a constant signal the output is the sum of the weights.
    let ones = vec![1.0; 256];
    let position: Vec<f64> = (0..1000).map(|_| 20.0 + 200.0 * rng.unit()).collect();
    let mut out = vec![0.0; position.len()];
    Reader::new(position.len())
        .read(&ones, &position, &mut out)
        .unwrap();
    assert!(max_abs_diff(&out, &vec![1.0; out.len()]) < 1e-14);
}

#[test]
fn table_matches_formula_within_1e_10() {
    let reader = Reader::new(0);
    let mut from_formula = [0.0; TAPS];
    let mut from_table = [0.0; TAPS];
    let mut worst: f64 = 0.0;
    for i in 0..10_000 {
        let frac = i as f64 / 10_000.0;
        reader.weights_from_formula(frac, &mut from_formula);
        reader.weights_from_table(frac, &mut from_table);
        worst = worst.max(max_abs_diff(&from_formula, &from_table));
    }
    assert!(worst < 1e-10, "worst tap difference {worst:e}");
    // On a full-scale signal, all 10 000 fractions in one block (table) against one at a time
    // (formula).
    let data = Rng(4).signal(10_200);
    let position: Vec<f64> = (0..10_000)
        .map(|i| (HALF + 50 + i) as f64 + (i as f64 * 7919.0 % 10_000.0) / 10_000.0)
        .collect();
    assert!(distinct_fractions(&position) > FEW);
    let mut out = vec![0.0; position.len()];
    Reader::new(position.len())
        .read(&data, &position, &mut out)
        .unwrap();
    let diff = max_abs_diff(&out, &one_at_a_time(&data, &position));
    assert!(diff < 1e-10, "worst output difference {diff:e}");
}

#[test]
fn strategy_boundary_64_65() {
    let data = Rng(5).signal(2048);
    for distinct in [FEW, FEW + 1] {
        // Multiples of 2^-20 survive `p - floor(p)` exactly, so the count is what it says, and
        // most fall between the table's points (every 2^-11), where the table differs.
        let fractions: Vec<f64> = (0..distinct)
            .map(|d| ((d * 40_503 + 12_345) % (1 << 20)) as f64 / (1 << 20) as f64)
            .collect();
        // 1000 positions that cycle over exactly `distinct` fractions.
        let position: Vec<f64> = (0..1000)
            .map(|i| (HALF + 300 + i) as f64 + fractions[(i * 7) % distinct])
            .collect();
        assert_eq!(distinct_fractions(&position), distinct);
        let mut out = vec![0.0; position.len()];
        Reader::new(position.len())
            .read(&data, &position, &mut out)
            .unwrap();
        let reference = one_at_a_time(&data, &position);
        let diff = max_abs_diff(&out, &reference);
        assert!(diff < 1e-10, "{distinct} fractions: {diff:e}");
        if distinct == FEW {
            // Up to FEW the weights come straight from the formula: identical output.
            assert_eq!(out, reference, "{distinct} fractions");
        } else {
            // Above FEW the table is used: close, but not bit-identical.
            assert_ne!(out, reference, "{distinct} fractions");
        }
    }
}

#[test]
fn empty_read() {
    let mut out: [f64; 0] = [];
    assert_eq!(Reader::new(0).read(&[], &[], &mut out), Ok(()));
    assert_eq!(Reader::new(16).read(&[0.0; 64], &[], &mut out), Ok(()));
}

#[test]
fn out_of_range_is_an_error() {
    let data = Rng(7).signal(100);
    let mut reader = Reader::new(4);
    let mut out = [0.0; 3];
    let first = (HALF - 1) as f64;
    let last = (data.len() - 1 - HALF) as f64;
    // The exact boundaries are in range, and so is anything up to the next integer.
    assert_eq!(
        reader.read(&data, &[first, last, last + 0.999], &mut out),
        Ok(())
    );
    for (bad, index) in [
        (first - 1e-9, 0),
        (last + 1.0, 1),
        (-3.5, 2),
        (f64::NAN, 1),
        (f64::INFINITY, 2),
        (f64::NEG_INFINITY, 0),
    ] {
        let mut position = [first + 0.5, last, 50.25];
        position[index] = bad;
        assert_eq!(
            reader.read(&data, &position, &mut out),
            Err(ReadError::OutOfRange { index }),
            "{bad}"
        );
    }
    // `2 * HALF` samples are the least a read needs; one fewer is too short for any position.
    assert_eq!(
        reader.read(&data[..2 * HALF], &[first], &mut out[..1]),
        Ok(())
    );
    assert_eq!(
        reader.read(&data[..2 * HALF - 1], &[first], &mut out[..1]),
        Err(ReadError::OutOfRange { index: 0 })
    );
    assert_eq!(
        reader.read(&data, &[50.0, 51.0], &mut out),
        Err(ReadError::LengthMismatch)
    );
    assert_eq!(
        reader.read(&data, &[50.0; 5], &mut [0.0; 5]),
        Err(ReadError::BlockTooLarge)
    );
}

#[test]
fn no_allocation_in_read() {
    // The control: the counter does see an allocation.
    assert!(allocations_in(|| drop(black_box(Vec::<f64>::with_capacity(1)))) >= 1);

    let max_block = 4096;
    let data = Rng(8).signal(8192);
    let mut reader = Reader::new(max_block);
    let mut out = vec![0.0; max_block];
    let still: Vec<f64> = (0..max_block).map(|i| (i + 40) as f64 + 0.37).collect();
    let ramp: Vec<f64> = still
        .iter()
        .enumerate()
        .map(|(i, p)| p + 1.7 * i as f64 / (max_block - 1) as f64)
        .collect();
    let few: Vec<f64> = (0..max_block)
        .map(|i| (i + 40) as f64 + (i % FEW) as f64 / FEW as f64)
        .collect();
    for position in [&still, &ramp, &few] {
        for n in [1, 63, 64, 65, max_block - 1, max_block] {
            let allocations = allocations_in(|| {
                reader
                    .read(black_box(&data), black_box(&position[..n]), &mut out[..n])
                    .unwrap();
            });
            assert_eq!(allocations, 0, "block of {n}");
        }
    }
}

// --- The absolute reference: numpy.

/// Values from numpy 2.5.3, `host/src/aurasync/dsp/interpolation.py` at 63c6445, generated by
/// `cd host && hatch run python ref.py` with:
///
/// ```python
/// import numpy as np
/// from aurasync.dsp import interpolation as it
/// n = np.arange(256)
/// data = ((n * 7919) % 2001) / 1000.0 - 1.0
/// still = np.arange(8) + 40 + 0.37             # 1 distinct fraction: formula
/// ramp = 100.0 + np.arange(70) * 1.37          # 70 distinct fractions: table
/// for t in [0.0, 0.25, -0.5, 1.37, -7.75, 15.5, 16.0, 16.5]:
///     print(t, float(it._kernel(np.array([t]))[0]))
/// print([float(v) for v in it.read(data, still)])
/// r = it.read(data, ramp); print([(i, float(r[i])) for i in range(0, 70, 7)])
/// ```
///
/// numpy's `np.sinc` gives ~1e-20 at nonzero integers (here `kernel(16.0)`), the port gives an
/// exact 0, which is what makes integer positions copy exactly.
const NUMPY_KERNEL: [(f64, f64); 8] = [
    (0.0, 1.0),
    (0.25, 0.8994943717920189),
    (-0.5, 0.6342977504150261),
    (1.37, -0.2074551944165279),
    (-7.75, -0.011439983958083145),
    (15.5, -0.00010829993715079084),
    (16.0, -9.117163225773903e-20),
    (16.5, 4.511956814008013e-05),
];

const NUMPY_STILL: [f64; 8] = [
    -0.44799187282186,
    -0.4878853203460338,
    -0.6373061165937691,
    -0.6305874910670235,
    -0.8481077223904754,
    -0.7323343115252239,
    -1.162277667457384,
    -0.32475398927903654,
];

const NUMPY_RAMP: [(usize, f64); 10] = [
    (0, 0.5049999999999999),
    (7, -0.29561285183659924),
    (14, 0.7850363449762725),
    (21, 0.06179112476817192),
    (28, -0.8318410407670431),
    (35, 0.42721513445876896),
    (42, -0.4052373843295972),
    (49, 0.8438733374013507),
    (56, -0.012775592934048612),
    (63, -0.7214993656685498),
];

#[test]
fn matches_numpy_reference_values() {
    for (t, expected) in NUMPY_KERNEL {
        assert!((kernel(t) - expected).abs() < 1e-14, "kernel({t})");
    }
    let data: Vec<f64> = (0..256u64)
        .map(|n| ((n * 7919) % 2001) as f64 / 1000.0 - 1.0)
        .collect();
    let mut reader = Reader::new(70);

    let still: Vec<f64> = (0..8).map(|k| (k as f64 + 40.0) + 0.37).collect();
    let mut out = vec![0.0; still.len()];
    reader.read(&data, &still, &mut out).unwrap();
    assert!(max_abs_diff(&out, &NUMPY_STILL) < 1e-12);

    let ramp: Vec<f64> = (0..70).map(|k| 100.0 + k as f64 * 1.37).collect();
    assert_eq!(distinct_fractions(&ramp), 70);
    let mut out = vec![0.0; ramp.len()];
    reader.read(&data, &ramp, &mut out).unwrap();
    for (i, expected) in NUMPY_RAMP {
        assert!((out[i] - expected).abs() < 1e-12, "ramp[{i}]");
    }
}

// --- Review fixes.

/// `kernel(NaN)` returns NaN instead of looping forever in the Bessel series (a NaN term never
/// falls under the stopping threshold).
#[test]
fn kernel_of_nan_is_nan() {
    assert!(kernel(f64::NAN).is_nan());
    // An infinite offset is far outside the window: zero, and it returns.
    assert_eq!(kernel(f64::INFINITY), 0.0);
}
