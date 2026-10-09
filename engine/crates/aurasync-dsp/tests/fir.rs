//! The FIR filters by FFT convolution: the port of `host/src/aurasync/dsp/eq.py`
//! (`StreamingFIR`, `PartitionedFIR`).
//!
//! The golden against numpy (within 1e-9) is in `host/tests/test_eq_rust.py`; these tests check
//! what can be said without numpy: both filters equal a direct convolution for any block size,
//! `set_taps` and `replace_taps` keep or reset the tail as numpy does, `skip` resumes exactly,
//! the state moves exactly, a refused call changes nothing, and `process` / `skip` allocate
//! nothing once the block's FFT size is cached.
//!
//! The counting allocator and the generator are in `common`.

use std::hint::black_box;

use aurasync_dsp::fir::{FirError, PartitionedFir, PartitionedState, StreamingFir};

mod common;
use common::{Rng, allocations_in};

// --- Helpers.

/// A decaying random filter of `m` taps.
fn filter(m: usize, seed: u64) -> Vec<f64> {
    Rng(seed)
        .signal(m)
        .iter()
        .enumerate()
        .map(|(k, v)| v * (-(k as f64) / (m as f64 / 4.0)).exp())
        .collect()
}

/// The first `x.len()` samples of the direct convolution `x * h`.
fn direct(x: &[f64], h: &[f64]) -> Vec<f64> {
    (0..x.len())
        .map(|i| {
            (0..h.len().min(i + 1))
                .map(|k| h[k] * x[i - k])
                .sum::<f64>()
        })
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

fn streamed(f: &mut StreamingFir, x: &[f64], sizes: &[usize]) -> Vec<f64> {
    let mut out = vec![0.0; x.len()];
    let mut at = 0;
    for n in cut(x.len(), sizes) {
        f.process(&x[at..at + n], &mut out[at..at + n]).unwrap();
        at += n;
    }
    out
}

fn partitioned(f: &mut PartitionedFir, x: &[f64], sizes: &[usize]) -> Vec<f64> {
    let mut out = vec![0.0; x.len()];
    let mut at = 0;
    for n in cut(x.len(), sizes) {
        f.process(&x[at..at + n], &mut out[at..at + n]).unwrap();
        at += n;
    }
    out
}

fn max_diff(a: &[f64], b: &[f64]) -> f64 {
    assert_eq!(a.len(), b.len());
    a.iter()
        .zip(b)
        .map(|(x, y)| (x - y).abs())
        .fold(0.0, f64::max)
}

// --- StreamingFir.

#[test]
fn streaming_equals_a_direct_convolution_for_any_block() {
    let x = Rng(1).signal(12_000);
    for m in [1, 2, 5, 300, 2048] {
        let h = filter(m, 2);
        let expected = direct(&x, &h);
        for sizes in [
            &[4096][..],
            &[1, 7, 333, 2047, 2048, 2049],
            &[10_000, 3],
            &[0, 1500],
        ] {
            let got = streamed(&mut StreamingFir::new(&h).unwrap(), &x, sizes);
            let diff = max_diff(&got, &expected);
            assert!(diff < 1e-12, "m {m}, sizes {sizes:?}: {diff}");
        }
    }
}

#[test]
fn set_taps_resets_the_tail_only_when_the_length_changes() {
    let x = Rng(3).signal(8192);
    let (h1, h2, short) = (filter(512, 4), filter(512, 5), filter(100, 6));

    // Same length: the tail goes on, so the output is the old filter's tail plus the new one.
    let mut f = StreamingFir::new(&h1).unwrap();
    streamed(&mut f, &x[..4096], &[4096]);
    let tail = f.tail().to_vec();
    assert!(tail.iter().any(|&v| v != 0.0));
    f.set_taps(&h2).unwrap();
    assert_eq!(f.tail(), &tail[..]);
    let mut fresh = StreamingFir::new(&h2).unwrap();
    let got = streamed(&mut f, &x[4096..], &[4096]);
    let alone = streamed(&mut fresh, &x[4096..], &[4096]);
    let with_tail: Vec<f64> = alone
        .iter()
        .enumerate()
        .map(|(i, v)| v + tail.get(i).copied().unwrap_or(0.0))
        .collect();
    assert!(max_diff(&got, &with_tail) < 1e-12);

    // Another length: the tail is silence.
    f.set_taps(&short).unwrap();
    assert_eq!(f.tail(), &vec![0.0; 99][..]);
}

#[test]
fn replace_taps_keeps_the_tail_and_refuses_a_block_it_cannot_cover() {
    let x = Rng(7).signal(4096);
    let mut f = StreamingFir::new(&filter(512, 8)).unwrap();
    streamed(&mut f, &x, &[4096]);
    let tail = f.tail().to_vec();
    f.replace_taps(&filter(4, 9)).unwrap();
    assert_eq!(f.tail(), &tail[..]);
    // numpy's broadcast error: a tail of 511 against a block of 10 with 4 taps (13 samples).
    let mut out = vec![0.0; 10];
    assert_eq!(
        f.process(&x[..10], &mut out),
        Err(FirError::TailTooLong {
            tail: 511,
            full: 13
        })
    );
    assert_eq!(f.tail(), &tail[..]);
    // A block long enough takes it, and the tail is the taps' length less one from then on.
    let mut out = vec![0.0; 600];
    f.process(&Rng(10).signal(600), &mut out).unwrap();
    assert_eq!(f.tail().len(), 3);
}

#[test]
fn the_streaming_state_moves_exactly() {
    let x = Rng(11).signal(20_000);
    let h = filter(2048, 12);
    let mut a = StreamingFir::new(&h).unwrap();
    streamed(&mut a, &x[..7000], &[4096, 333]);
    let mut b = StreamingFir::new(&[1.0]).unwrap();
    b.set_state(&a.to_state()).unwrap();
    assert_eq!(b.to_state(), a.to_state());
    let rest_a = streamed(&mut a, &x[7000..], &[4096]);
    let rest_b = streamed(&mut b, &x[7000..], &[4096]);
    assert_eq!(rest_a, rest_b);
}

#[test]
fn a_refused_streaming_call_changes_nothing() {
    assert!(matches!(StreamingFir::new(&[]), Err(FirError::NoTaps)));
    let mut f = StreamingFir::new(&filter(64, 13)).unwrap();
    streamed(&mut f, &Rng(14).signal(1000), &[1000]);
    let before = f.to_state();
    assert_eq!(f.set_taps(&[]), Err(FirError::NoTaps));
    assert_eq!(f.replace_taps(&[]), Err(FirError::NoTaps));
    let mut out = vec![0.0; 3];
    assert_eq!(
        f.process(&[1.0, 2.0], &mut out),
        Err(FirError::OutputMismatch)
    );
    let mut empty = before.clone();
    empty.taps.clear();
    assert_eq!(f.set_state(&empty), Err(FirError::NoTaps));
    assert_eq!(f.to_state(), before);
}

// --- PartitionedFir.

#[test]
fn partitioned_equals_a_direct_convolution_for_any_block() {
    let x = Rng(15).signal(12_000);
    for (m, block) in [
        (5000, 4096),
        (5000, 1024),
        (5000, 512),
        (100, 1024),
        (1024, 1024),
        (3000, 1000),
        (1, 64),
    ] {
        let h = filter(m, 16);
        let expected = direct(&x, &h);
        for sizes in [
            vec![block],
            vec![block, block, 300, block, 5000],
            vec![700],
            vec![1, block - 1, block + 1, 0, 2 * block],
        ] {
            let got = partitioned(&mut PartitionedFir::new(&h, block).unwrap(), &x, &sizes);
            let diff = max_diff(&got, &expected);
            assert!(
                diff < 1e-11,
                "m {m}, block {block}, sizes {sizes:?}: {diff}"
            );
        }
    }
}

#[test]
fn partitioned_skips_input_and_resumes_exactly() {
    let x = Rng(17).signal(20_000);
    let h = filter(3000, 18);
    let expected = direct(&x, &h);
    for skipped in [4096, 100, 30_000] {
        let mut f = PartitionedFir::new(&h, 1024).unwrap();
        partitioned(&mut f, &x[..4096], &[1024]);
        let end = (4096 + skipped).min(16_000);
        if skipped == 30_000 {
            // Longer than the whole history: only its last part is kept.
            let mut long = vec![0.0; skipped];
            long[skipped - (end - 4096)..].copy_from_slice(&x[4096..end]);
            f.skip(&long);
        } else {
            f.skip(&x[4096..end]);
        }
        let got = partitioned(&mut f, &x[end..], &[1024]);
        let diff = max_diff(&got, &expected[end..]);
        assert!(diff < 1e-11, "skipped {skipped}: {diff}");
    }
}

#[test]
fn the_partitioned_state_moves_exactly() {
    let x = Rng(19).signal(20_000);
    let h = filter(5000, 20);
    for sizes in [&[1024][..], &[1024, 300]] {
        let mut a = PartitionedFir::new(&h, 1024).unwrap();
        partitioned(&mut a, &x[..6444], sizes);
        let mut b = PartitionedFir::new(&h, 1024).unwrap();
        b.set_state(&a.to_state()).unwrap();
        assert_eq!(b.to_state(), a.to_state());
        let rest_a = partitioned(&mut a, &x[6444..], &[1024, 77]);
        let rest_b = partitioned(&mut b, &x[6444..], &[1024, 77]);
        assert_eq!(rest_a, rest_b);
    }
}

/// A way to break a partitioned filter's state.
type Break = fn(&mut PartitionedState);

#[test]
fn a_refused_partitioned_call_changes_nothing() {
    assert!(matches!(
        PartitionedFir::new(&[], 64),
        Err(FirError::NoTaps)
    ));
    assert!(matches!(
        PartitionedFir::new(&[1.0], 0),
        Err(FirError::ZeroBlock)
    ));
    let mut f = PartitionedFir::new(&filter(300, 21), 128).unwrap();
    partitioned(&mut f, &Rng(22).signal(1000), &[128, 50]);
    let before = f.to_state();
    let mut out = vec![0.0; 3];
    assert_eq!(
        f.process(&[1.0, 2.0], &mut out),
        Err(FirError::OutputMismatch)
    );
    // 300 taps in partitions of 128: 3 partitions of 129 bins, a history of 4 * 128 samples.
    let bad_shape = |field, got, expected| FirError::BadShape {
        field,
        got,
        expected,
    };
    let breaks: [(Break, FirError); 4] = [
        (
            |s| {
                s.history.pop();
            },
            bad_shape("history", 511, 512),
        ),
        (
            |s| {
                s.fdl.pop();
            },
            bad_shape("fdl rows", 2, 3),
        ),
        (
            |s| {
                s.fdl[1].pop();
            },
            bad_shape("fdl row", 128, 129),
        ),
        (
            |s| s.head = 3,
            FirError::OutOfRange {
                field: "head",
                got: 3,
                max: 2,
            },
        ),
    ];
    for (broken, error) in breaks {
        let mut bad = before.clone();
        broken(&mut bad);
        assert_eq!(f.set_state(&bad), Err(error));
        assert_eq!(f.to_state(), before);
    }
}

// --- Allocation.

#[test]
fn no_allocation_once_the_block_s_size_is_cached() {
    // The control: the counter does see an allocation.
    assert!(allocations_in(|| drop(black_box(Vec::<f64>::with_capacity(1)))) >= 1);

    let x = Rng(23).signal(4096);
    let mut out = vec![0.0; 4096];
    let (h, h2) = (filter(2048, 24), filter(2048, 25));
    let mut f = StreamingFir::new(&h).unwrap();
    f.prepare(4096);
    let allocations = allocations_in(|| {
        for _ in 0..3 {
            f.process(&x, &mut out).unwrap();
        }
        // New taps of the same length: their spectrum is recomputed in place.
        f.set_taps(&h2).unwrap();
        f.process(&x, &mut out).unwrap();
        f.replace_taps(&h).unwrap();
        f.process(&x, &mut out).unwrap();
    });
    assert_eq!(allocations, 0, "streaming");
    // A short block of a size seen once allocates nothing the second time.
    f.process(&x[..333], &mut out[..333]).unwrap();
    let cached = f.cached_sizes();
    let allocations = allocations_in(|| f.process(&x[..333], &mut out[..333]).unwrap());
    assert_eq!(allocations, 0, "streaming, short block");
    assert_eq!(f.cached_sizes(), cached);

    // The partitioned filter: full blocks never allocate, from the first, nor does a skip.
    let mut p = PartitionedFir::new(&filter(10_000, 26), 4096).unwrap();
    let allocations = allocations_in(|| {
        p.process(&x, &mut out).unwrap();
        p.skip(&x[..1000]);
        p.process(&x, &mut out).unwrap();
        p.process(&x, &mut out).unwrap();
    });
    assert_eq!(allocations, 0, "partitioned");
    p.process(&x[..1000], &mut out[..1000]).unwrap();
    let allocations = allocations_in(|| {
        p.process(&x[..1000], &mut out[..1000]).unwrap();
        p.process(&x, &mut out).unwrap();
    });
    assert_eq!(allocations, 0, "partitioned, short block cached");
}
