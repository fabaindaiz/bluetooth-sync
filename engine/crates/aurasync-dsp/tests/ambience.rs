//! The ambience extractor: the port of `host/src/aurasync/dsp/ambience.py` (`Extractor`).
//!
//! The golden against numpy (within 1e-9) is in `host/tests/test_ambience_rust.py`; these tests
//! check what can be said without numpy: the paper's three cases (mono has no ambience,
//! independent channels are all ambience, a source on one side is not ambience), silence stays
//! silence and the latency is exactly `n_fft`, the block size does not change the stream, the
//! state moves exactly, a reset is a new extractor, errors change nothing, and `process`
//! allocates nothing within the reserved block.
//!
//! The counting allocator and the generator are in `common`.

use std::hint::black_box;

use aurasync_dsp::ambience::{AmbienceError, Extractor, HOP, N_FFT, Params};

mod common;
use common::{Rng, allocations_in};

// --- Helpers.

const BLOCK: usize = 4096;

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
    Extractor::new(N_FFT, HOP, BLOCK).unwrap()
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
    b.set_state(&a.to_state()).unwrap();
    assert_eq!(a.to_state(), b.to_state());
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
    assert_eq!(used.to_state(), fresh.to_state());
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
    let before = ex.to_state();

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
    let mut bad = ex.to_state();
    bad.norm.pop();
    assert_eq!(
        ex.set_state(&bad),
        Err(AmbienceError::BadShape {
            field: "norm",
            got: N_FFT - 1,
            expected: N_FFT
        })
    );
    let mut bad = ex.to_state();
    bad.pending_right.push(0.0);
    let pending = bad.pending_left.len();
    assert_eq!(
        ex.set_state(&bad),
        Err(AmbienceError::BadShape {
            field: "pending_right",
            got: pending + 1,
            expected: pending
        })
    );
    let mut bad = ex.to_state();
    bad.acc12.pop();
    assert_eq!(
        ex.set_state(&bad),
        Err(AmbienceError::BadShape {
            field: "acc12",
            got: N_FFT / 2,
            expected: N_FFT / 2 + 1
        })
    );
    assert_eq!(ex.to_state(), before);
}

#[test]
fn a_bad_shape_says_which_field_and_both_lengths() {
    let error = AmbienceError::BadShape {
        field: "norm",
        got: 2047,
        expected: 2048,
    };
    assert_eq!(error.to_string(), "norm: 2047 values where 2048 are needed");
}

#[test]
fn a_bad_configuration_is_refused() {
    for (n_fft, hop) in [(1, 1), (0, 1), (8, 0), (8, 9)] {
        let error = Extractor::new(n_fft, hop, 8).unwrap_err();
        assert_eq!(error, AmbienceError::InvalidConfig { n_fft, hop });
        let message = error.to_string();
        assert!(
            message.contains(&format!("n_fft {n_fft}")) && message.contains(&format!("hop {hop}")),
            "{message}"
        );
    }
    // The edges that are allowed: the shortest STFT, and a hop of the whole frame or of 1.
    for (n_fft, hop) in [(2, 1), (2, 2), (8, 8), (8, 1)] {
        assert!(
            Extractor::new(n_fft, hop, 8).is_ok(),
            "n_fft {n_fft}, hop {hop}"
        );
    }
}

#[test]
fn no_allocation_in_process() {
    // The control: the counter does see an allocation.
    assert!(allocations_in(|| drop(black_box(Vec::<f64>::with_capacity(1)))) >= 1);

    let (left, right) = music(BLOCK, 7);
    let mut out = vec![0.0; BLOCK];
    for (n_fft, hop) in [(N_FFT, HOP), (1000, 250), (513, 171)] {
        let mut ex = Extractor::new(n_fft, hop, BLOCK).unwrap();
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
