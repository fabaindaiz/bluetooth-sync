//! The ambience extractor: the port of `host/src/aurasync/dsp/ambience.py` (`Extractor`).
//!
//! The golden against numpy (within 1e-9) is in `host/tests/test_ambience_rust.py`; these tests
//! check what can be said without numpy: the paper's three cases (mono has no ambience,
//! independent channels are all ambience, a source on one side is not ambience), silence stays
//! silence and the latency is exactly `n_fft`, the block size does not change the stream, the
//! state moves exactly, a reset is a new extractor, errors change nothing, and `process`
//! allocates nothing within the reserved block.
//!
//! The counting allocator below is the only `unsafe` here: `GlobalAlloc` is an unsafe trait. The
//! library itself is `#![forbid(unsafe_code)]`.

use std::alloc::{GlobalAlloc, Layout, System};
use std::cell::Cell;
use std::hint::black_box;

use aurasync_dsp::ambience::{AmbienceError, Extractor, HOP, N_FFT, Params};

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

const BLOCK: usize = 4096;

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

/// Partly correlated stereo: a centre plus independent room on each side.
fn music(n: usize, seed: u64) -> (Vec<f64>, Vec<f64>) {
    let mut rng = Rng(seed);
    let centre = rng.signal(n);
    let (a, b) = (rng.signal(n), rng.signal(n));
    let mix = |room: &[f64]| -> Vec<f64> {
        centre
            .iter()
            .zip(room)
            .map(|(c, x)| 0.6 * c + 0.4 * x)
            .collect()
    };
    (mix(&a), mix(&b))
}

fn extractor() -> Extractor {
    Extractor::new(N_FFT, HOP, BLOCK)
}

/// The whole stream through `ex` in blocks of `sizes` (cycled).
fn stream(ex: &mut Extractor, left: &[f64], right: &[f64], sizes: &[usize]) -> Vec<f64> {
    let mut out = Vec::new();
    let (mut at, mut i) = (0, 0);
    while at < left.len() {
        let n = sizes[i % sizes.len()].min(left.len() - at);
        let mut block = vec![0.0; n];
        ex.process(&left[at..at + n], &right[at..at + n], &mut block)
            .unwrap();
        out.extend_from_slice(&block);
        at += n;
        i += 1;
    }
    out
}

fn rms(x: &[f64]) -> f64 {
    (x.iter().map(|v| v * v).sum::<f64>() / x.len() as f64).sqrt()
}

// --- The tests.

#[test]
fn the_paper_s_three_cases() {
    let n = 48_000;
    let settle = 2 * N_FFT..n;
    let mut rng = Rng(1);
    let (left, right) = (rng.signal(n), rng.signal(n));
    let mid: Vec<f64> = left
        .iter()
        .zip(&right)
        .map(|(l, r)| (l + r) / 2.0)
        .collect();

    // Mono (L = R): coherence 1, index 0, almost nothing comes out.
    let mono = stream(&mut extractor(), &left, &left, &[BLOCK]);
    assert!(rms(&mono[settle.clone()]) < 0.01 * rms(&left));

    // Independent channels: coherence near 0, most of the mid comes out.
    let independent = stream(&mut extractor(), &left, &right, &[BLOCK]);
    assert!(rms(&independent[settle.clone()]) > 0.5 * rms(&mid));

    // One channel only: low coherence too, but not ambience (the energy criterion).
    let zeros = vec![0.0; n];
    let one_sided = stream(&mut extractor(), &left, &zeros, &[BLOCK]);
    assert!(rms(&one_sided[settle.clone()]) < 0.01 * rms(&left));
    // Without the criterion it would be taken for ambience.
    let mut without = extractor();
    without.set_params(Params {
        min_energy: 0.0,
        ..Params::default()
    });
    let one_sided = stream(&mut without, &left, &zeros, &[BLOCK]);
    assert!(rms(&one_sided[settle]) > 0.2 * rms(&left));
}

#[test]
fn silence_stays_silence_and_a_signal_comes_out_after_the_latency() {
    let zeros = vec![0.0; 20_000];
    let out = stream(&mut extractor(), &zeros, &zeros, &[BLOCK]);
    assert!(out.iter().all(|&x| x == 0.0));

    let (left, right) = music(20_000, 2);
    let out = stream(&mut extractor(), &left, &right, &[BLOCK]);
    // The first N_FFT samples are the latency: exact zeros.
    assert!(out[..N_FFT].iter().all(|&x| x == 0.0));
    assert!(out[N_FFT..].iter().any(|&x| x != 0.0));
    assert!(out.iter().all(|x| x.is_finite()));
}

#[test]
fn the_block_size_does_not_change_the_stream() {
    // The output is a function of the input stream only: frames fall on fixed hops. Bit for
    // bit, whatever the blocks.
    let (left, right) = music(30_000, 3);
    let reference = stream(&mut extractor(), &left, &right, &[BLOCK]);
    for sizes in [
        &[1usize, 777, 10_000][..],
        &[512],
        &[2047, 2049],
        &[0, 5000],
    ] {
        let got = stream(&mut extractor(), &left, &right, sizes);
        assert_eq!(got, reference, "blocks {sizes:?}");
    }
}

#[test]
fn the_state_moves_exactly() {
    let (left, right) = music(30_000, 4);
    let mut a = extractor();
    let _ = stream(&mut a, &left[..10_001], &right[..10_001], &[BLOCK]);
    let mut b = extractor();
    b.set_state(&a.state()).unwrap();
    assert_eq!(a.state(), b.state());
    let rest_a = stream(&mut a, &left[10_001..], &right[10_001..], &[3000]);
    let rest_b = stream(&mut b, &left[10_001..], &right[10_001..], &[3000]);
    assert_eq!(rest_a, rest_b);
}

#[test]
fn a_reset_is_a_new_extractor_with_the_same_params() {
    let params = Params {
        lam: 0.7,
        sigma: 6.0,
        ..Params::default()
    };
    let (left, right) = music(20_000, 5);
    let mut used = extractor();
    used.set_params(params);
    let _ = stream(&mut used, &left, &right, &[BLOCK]);
    used.reset();
    assert_eq!(used.params(), params);
    let mut fresh = extractor();
    fresh.set_params(params);
    assert_eq!(used.state(), fresh.state());
    assert_eq!(
        stream(&mut used, &left, &right, &[BLOCK]),
        stream(&mut fresh, &left, &right, &[BLOCK])
    );
}

#[test]
fn a_refused_call_changes_nothing() {
    let (left, right) = music(10_000, 6);
    let mut ex = extractor();
    let _ = stream(&mut ex, &left, &right, &[BLOCK]);
    let before = ex.state();

    let mut out = vec![0.0; 10];
    assert_eq!(
        ex.process(&left[..10], &right[..11], &mut out),
        Err(AmbienceError::LengthMismatch {
            left: 10,
            right: 11
        })
    );
    let mut short = vec![0.0; 9];
    assert_eq!(
        ex.process(&left[..10], &right[..10], &mut short),
        Err(AmbienceError::OutputMismatch)
    );
    let mut bad = ex.state();
    bad.norm.pop();
    assert!(matches!(ex.set_state(&bad), Err(AmbienceError::BadShape(m)) if m.contains("norm")));
    let mut bad = ex.state();
    bad.pending_right.push(0.0);
    assert!(
        matches!(ex.set_state(&bad), Err(AmbienceError::BadShape(m)) if m.contains("pending_right"))
    );
    let mut bad = ex.state();
    bad.acc12.pop();
    assert!(matches!(ex.set_state(&bad), Err(AmbienceError::BadShape(m)) if m.contains("acc12")));
    assert_eq!(ex.state(), before);
}

#[test]
fn no_allocation_in_process() {
    // The control: the counter does see an allocation.
    assert!(allocations_in(|| drop(black_box(Vec::<f64>::with_capacity(1)))) >= 1);

    let (left, right) = music(BLOCK, 7);
    let mut out = vec![0.0; BLOCK];
    for (n_fft, hop) in [(N_FFT, HOP), (1000, 250), (513, 171)] {
        let mut ex = Extractor::new(n_fft, hop, BLOCK);
        // From a new extractor (its first frames), then past the latency.
        for n in [1, 511, BLOCK, 2049, BLOCK - 1, BLOCK, 0, BLOCK, 7] {
            let allocations = allocations_in(|| {
                ex.process(black_box(&left[..n]), black_box(&right[..n]), &mut out[..n])
                    .unwrap();
            });
            assert_eq!(allocations, 0, "n_fft {n_fft}, block of {n}");
        }
        // A change of params and a reset do not allocate either, nor the blocks after them.
        let allocations = allocations_in(|| {
            ex.set_params(Params {
                lam: 0.5,
                ..Params::default()
            });
            ex.reset();
            ex.process(&left, &right, &mut out).unwrap();
        });
        assert_eq!(allocations, 0, "n_fft {n_fft}, after a reset");
    }
}
