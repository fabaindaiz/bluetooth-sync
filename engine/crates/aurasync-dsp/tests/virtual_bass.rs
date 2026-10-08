//! The virtual bass's per-block work (`aurasync_dsp::virtual_bass`).
//!
//! The golden against numpy (within 1e-9) is in `host/tests/test_virtual_bass_rust.py`; these
//! tests check what can be said without numpy: the generator is exactly the composition of the
//! two partitioned filters it owns (band, `abs`, harmonics band, calibration, gain), the gain
//! ramps as numpy's does, `reset` is a fresh generator, the state moves exactly, a refused call
//! changes nothing, and `process` allocates nothing.
//!
//! The counting allocator below is the only `unsafe` here: `GlobalAlloc` is an unsafe trait. The
//! library itself is `#![forbid(unsafe_code)]`.

use std::alloc::{GlobalAlloc, Layout, System};
use std::cell::Cell;
use std::hint::black_box;

use aurasync_dsp::fir::{FirError, PartitionedFir};
use aurasync_dsp::virtual_bass::VirtualBass;

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

    /// Full scale: uniform in [-1, 1).
    fn signal(&mut self, n: usize) -> Vec<f64> {
        (0..n)
            .map(|_| 2.0 * ((self.next_u64() >> 11) as f64 / (1u64 << 53) as f64) - 1.0)
            .collect()
    }
}

/// A decaying random filter of `m` taps.
fn filter(m: usize, seed: u64) -> Vec<f64> {
    Rng(seed)
        .signal(m)
        .iter()
        .enumerate()
        .map(|(k, v)| v * (-(k as f64) / (m as f64 / 4.0)).exp())
        .collect()
}

/// The blocks' lengths: `sizes` cycled until `total` samples.
fn cut(total: usize, sizes: &[usize]) -> Vec<usize> {
    let (mut out, mut at, mut i) = (Vec::new(), 0, 0);
    while at < total {
        let n = sizes[i % sizes.len()].min(total - at);
        out.push(n);
        at += n;
        i += 1;
    }
    out
}

const BLOCK: usize = 512;
const CAL: f64 = 1.75;

fn generator() -> VirtualBass {
    VirtualBass::new(&filter(900, 1), &filter(700, 2), CAL, BLOCK).unwrap()
}

/// The same work written out with the two filters and numpy's order of operations.
fn reference(
    band: &mut PartitionedFir,
    out: &mut PartitionedFir,
    x: &[f64],
    current: f64,
    target: f64,
) -> Vec<f64> {
    let n = x.len();
    let mut bass = vec![0.0; n];
    band.process(x, &mut bass).unwrap();
    let rectified: Vec<f64> = bass.iter().map(|b| b.abs()).collect();
    let mut made = vec![0.0; n];
    out.process(&rectified, &mut made).unwrap();
    for (i, y) in made.iter_mut().enumerate() {
        *y *= CAL;
        if n > 0 && target != current {
            *y *= current + (target - current) * (i + 1) as f64 / n as f64;
        } else {
            *y *= target;
        }
    }
    made
}

fn run(
    g: &mut VirtualBass,
    x: &[f64],
    current: f64,
    target: f64,
) -> (Vec<f64>, aurasync_dsp::virtual_bass::Energies) {
    let mut out = vec![0.0; x.len()];
    let energies = g.process(x, current, target, &mut out).unwrap();
    (out, energies)
}

#[test]
fn it_is_the_composition_of_its_two_filters_for_any_block_and_any_gain_move() {
    let mut g = generator();
    let mut band = PartitionedFir::new(&filter(900, 1), BLOCK).unwrap();
    let mut out = PartitionedFir::new(&filter(700, 2), BLOCK).unwrap();
    let x = Rng(3).signal(20_000);
    let gains = [
        (0.0, 0.0),
        (0.0, 1.0),
        (1.0, 1.0),
        (1.0, 0.25),
        (0.25, 0.0),
        (0.0, 2.0),
    ];
    let mut at = 0;
    for (k, n) in cut(x.len(), &[1, 7, 100, 511, 512, 513, 1500, 3000])
        .into_iter()
        .enumerate()
    {
        let (current, target) = gains[k % gains.len()];
        let block = &x[at..at + n];
        let want = reference(&mut band, &mut out, block, current, target);
        let (got, energies) = run(&mut g, block, current, target);
        assert_eq!(got, want, "block {k} of {n}");
        assert!(energies.bass.is_finite());
        let made: f64 = want.iter().map(|y| y * y).sum();
        assert!((energies.made - made).abs() <= 1e-12 * made.max(1.0));
        at += n;
    }
}

#[test]
fn an_empty_block_changes_nothing() {
    let mut g = generator();
    let x = Rng(4).signal(2000);
    let (first, _) = run(&mut g, &x, 0.0, 1.0);
    let mut h = generator();
    let _ = run(&mut h, &[], 0.0, 1.0);
    let (again, energies) = run(&mut h, &x, 0.0, 1.0);
    assert_eq!(first, again);
    assert!(energies.made > 0.0);
    assert_eq!(run(&mut g, &[], 0.0, 1.0).1.bass, 0.0);
}

#[test]
fn reset_is_a_fresh_generator() {
    let mut used = generator();
    let x = Rng(5).signal(3000);
    let _ = run(&mut used, &x, 1.0, 1.0);
    used.reset();
    let mut fresh = generator();
    let y = Rng(6).signal(3000);
    assert_eq!(
        run(&mut used, &y, 1.0, 1.0).0,
        run(&mut fresh, &y, 1.0, 1.0).0
    );
}

#[test]
fn the_state_moves_exactly() {
    let x = Rng(7).signal(9000);
    let (head, tail) = x.split_at(4321);
    let mut a = generator();
    let _ = run(&mut a, head, 0.0, 1.0);
    let mut b = generator();
    b.set_state(&a.state()).unwrap();
    assert_eq!(b.state(), a.state());
    assert_eq!(run(&mut a, tail, 1.0, 0.5).0, run(&mut b, tail, 1.0, 0.5).0);
}

#[test]
fn a_refused_call_changes_nothing() {
    let mut g = generator();
    let x = Rng(8).signal(2000);
    let _ = run(&mut g, &x, 0.0, 1.0);
    let before = g.state();
    let mut short = vec![0.0; 10];
    assert_eq!(
        g.process(&x, 1.0, 1.0, &mut short),
        Err(FirError::OutputMismatch)
    );
    assert_eq!(g.state(), before);
    // A state whose second half does not fit: the first half is not kept either.
    let other = VirtualBass::new(&filter(900, 1), &filter(2000, 2), CAL, BLOCK).unwrap();
    let mut bad = g.state();
    bad.out = other.state().out;
    assert!(matches!(g.set_state(&bad), Err(FirError::BadShape(_))));
    assert_eq!(g.state(), before);
    assert!(VirtualBass::new(&[], &[1.0], CAL, BLOCK).is_err());
    assert!(VirtualBass::new(&[1.0], &[], CAL, BLOCK).is_err());
    assert!(VirtualBass::new(&[1.0], &[1.0], CAL, 0).is_err());
}

#[test]
fn process_and_reset_allocate_nothing_for_whole_and_chunked_blocks() {
    assert!(allocations_in(|| drop(black_box(Vec::<f64>::with_capacity(1)))) >= 1);
    let mut g = generator();
    let x = Rng(9).signal(3 * BLOCK);
    let mut out = vec![0.0; x.len()];
    let allocations = allocations_in(|| {
        // Whole filter blocks, a ramp, a block of three chunks, then a reset.
        g.process(&x[..BLOCK], 0.0, 1.0, &mut out[..BLOCK]).unwrap();
        g.process(&x[..BLOCK], 1.0, 1.0, &mut out[..BLOCK]).unwrap();
        g.process(&x, 1.0, 0.5, &mut out).unwrap();
        g.reset();
        g.process(&x[..BLOCK], 0.0, 0.0, &mut out[..BLOCK]).unwrap();
    });
    assert_eq!(allocations, 0);
}
