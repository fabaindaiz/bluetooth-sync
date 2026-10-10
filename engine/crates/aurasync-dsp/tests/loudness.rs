//! The loudness meter's per-block work (`aurasync_dsp::loudness`).
//!
//! The golden against numpy (within 1e-9) is in `host/tests/test_loudness_rust.py`; these tests
//! check what can be said without numpy: the steps and the peak do not depend on how the stream is
//! cut into blocks, a step's energy is Parseval's with a flat weighting, a sine phased between
//! samples has its true peak found, the state moves exactly, a refused call changes nothing, and
//! `push` allocates nothing once warm.
//!
//! The counting allocator and the generator are in `common`.

use std::f64::consts::PI;
use std::hint::black_box;

use aurasync_dsp::loudness::{LoudnessError, LoudnessMeter, MeterState};

mod common;
use common::{Rng, allocations_in};

const W: usize = 12;
const STEP: usize = 4800;

/// Windowed-sinc kernels in the window's order (row p's tap m multiplies `x[i + m]` and gives the
/// point `(p + 1) / 4` of the way from `x[i + W - 1]` to `x[i + W]`), each summing to 1.
fn kernels() -> Vec<f64> {
    let mut out = Vec::with_capacity(3 * 2 * W);
    for p in 1..=3 {
        let row: Vec<f64> = (0..2 * W)
            .map(|m| {
                let t = (W - 1) as f64 + f64::from(p) / 4.0 - m as f64;
                let sinc = if t == 0.0 {
                    1.0
                } else {
                    (PI * t).sin() / (PI * t)
                };
                sinc * (0.5 + 0.5 * (PI * t / W as f64).cos())
            })
            .collect();
        let total: f64 = row.iter().sum();
        out.extend(row.iter().map(|k| k / total));
    }
    out
}

/// A flat weighting with Parseval's factors: a step's energy is then its sum of squares.
fn flat_power() -> Vec<f64> {
    let bins = STEP / 2 + 1;
    (0..bins)
        .map(|k| if k == 0 || k == bins - 1 { 1.0 } else { 2.0 } / STEP as f64)
        .collect()
}

fn meter(channels: usize) -> LoudnessMeter {
    LoudnessMeter::new(&kernels(), W, &flat_power(), &vec![1.0; channels], STEP).unwrap()
}

/// `frames` through `meter` in blocks of `sizes` (cycled): every step and the highest peak.
fn run(meter: &mut LoudnessMeter, frames: &[f64], sizes: &[usize]) -> (Vec<f64>, f64) {
    let ch = meter.channels();
    let (mut steps, mut peak, mut at, mut k) = (Vec::new(), 0.0_f64, 0, 0);
    while at < frames.len() {
        let n = (sizes[k % sizes.len()] * ch).min(frames.len() - at);
        peak = peak.max(meter.push(&frames[at..at + n]).unwrap());
        steps.extend_from_slice(meter.new_steps());
        at += n;
        k += 1;
    }
    (steps, peak)
}

#[test]
fn the_block_size_changes_neither_the_steps_nor_the_peak() {
    let frames = Rng(5).signal(2 * 30_000);
    let (want_steps, want_peak) = run(&mut meter(2), &frames, &[4096]);
    assert_eq!(want_steps.len(), 30_000 / STEP);
    for sizes in [&[1, 7, 4799][..], &[4800], &[20_000, 3]] {
        let (steps, peak) = run(&mut meter(2), &frames, sizes);
        assert_eq!(steps.len(), want_steps.len());
        for (a, b) in steps.iter().zip(&want_steps) {
            assert!((a - b).abs() <= 1e-9 * b.abs(), "{a} != {b}");
        }
        assert_eq!(peak, want_peak);
    }
}

#[test]
fn a_flat_weighting_gives_each_step_its_sum_of_squares() {
    let frames = Rng(9).signal(3 * STEP + 100);
    let (steps, _) = run(&mut meter(1), &frames, &[4096]);
    assert_eq!(steps.len(), 3);
    for (s, step) in steps.iter().zip(frames.as_chunks::<STEP>().0) {
        let squares: f64 = step.iter().map(|v| v * v).sum();
        assert!((s - squares).abs() <= 1e-9 * squares, "{s} != {squares}");
    }
}

#[test]
fn a_sine_at_a_quarter_of_the_rate_has_its_peak_between_samples() {
    // Phased 45 degrees: every sample sits at 0.707 of the sine's peak, which lies between them
    // (the abrupt start overshoots a little with these short kernels).
    let frames: Vec<f64> = (0..8192_i32)
        .map(|k| (PI / 2.0 * f64::from(k) + PI / 4.0).sin())
        .collect();
    let (_, peak) = run(&mut meter(1), &frames, &[4096]);
    assert!(peak > 0.99 && peak < 1.03, "{peak}");
}

#[test]
fn silence_peaks_at_zero_and_a_short_block_completes_no_step() {
    let mut m = meter(1);
    assert_eq!(m.push(&[0.0; 100]).unwrap(), 0.0);
    assert_eq!(m.new_steps(), &[] as &[f64]);
    assert_eq!(m.push(&[]).unwrap(), 0.0);
}

#[test]
fn the_state_moves_exactly() {
    let frames = Rng(3).signal(2 * 7000);
    let mut a = meter(2);
    a.push(&frames[..2 * 5000]).unwrap();
    let state = a.to_state();
    assert_eq!(state.context.len(), 2 * 2 * W);
    assert_eq!(state.pending.len(), 2 * 200);
    let mut b = meter(2);
    b.set_state(&state).unwrap();
    assert_eq!(b.to_state(), state);
    let pa = a.push(&frames[2 * 5000..]).unwrap();
    let pb = b.push(&frames[2 * 5000..]).unwrap();
    assert_eq!(pa, pb);
    assert_eq!(a.new_steps(), b.new_steps());
}

#[test]
fn refused_calls_change_nothing() {
    assert_eq!(
        LoudnessMeter::new(&kernels(), W, &flat_power(), &[], STEP).unwrap_err(),
        LoudnessError::InvalidConfig { field: "weights" }
    );
    assert!(matches!(
        LoudnessMeter::new(&kernels()[1..], W, &flat_power(), &[1.0], STEP),
        Err(LoudnessError::BadShape {
            field: "kernels",
            ..
        })
    ));
    assert!(matches!(
        LoudnessMeter::new(&kernels(), W, &flat_power()[1..], &[1.0], STEP),
        Err(LoudnessError::BadShape {
            field: "k_power",
            ..
        })
    ));
    let mut m = meter(2);
    m.push(&Rng(1).signal(2 * 300)).unwrap();
    let before = m.to_state();
    assert!(m.push(&[0.5; 3]).is_err());
    let bad = MeterState {
        context: vec![0.0; 3],
        pending: Vec::new(),
    };
    assert!(m.set_state(&bad).is_err());
    let too_long = MeterState {
        context: before.context.clone(),
        pending: vec![0.0; 2 * STEP],
    };
    assert!(m.set_state(&too_long).is_err());
    assert_eq!(m.to_state(), before);
}

#[test]
fn push_allocates_nothing_once_warm() {
    let frames = Rng(7).signal(2 * 4096 * 6);
    let mut m = meter(2);
    let mut blocks = frames.as_chunks::<{ 2 * 4096 }>().0.iter();
    // Two blocks: the segment fits 4096 frames and the steps' buffer has held a step.
    for block in blocks.by_ref().take(2) {
        m.push(block).unwrap();
    }
    for block in blocks {
        assert_eq!(
            allocations_in(|| {
                black_box(m.push(black_box(block)).unwrap());
            }),
            0
        );
    }
}
