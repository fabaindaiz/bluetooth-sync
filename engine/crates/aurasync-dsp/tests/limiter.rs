//! The true-peak limiter (`aurasync_dsp::limiter`).
//!
//! The golden against numpy (within 1e-9) is in `host/tests/test_limiter_rust.py`; these tests
//! check what can be said without numpy: the output does not depend on how the stream is cut into
//! blocks, a quiet stream comes out only delayed, the samples stay under the target, the state
//! moves exactly, a refused call changes nothing, and `process` allocates nothing once warm.
//!
//! The counting allocator and the generator are in `common`.

use std::f64::consts::PI;
use std::hint::black_box;

use aurasync_dsp::limiter::{LimiterError, LimiterState, MARGIN_DB, TruePeakLimiter};

mod common;
use common::{Rng, allocations_in};

// --- Helpers: the numpy class's design at 48 kHz (3 ms look-ahead, 15 ms hold, 250 ms release).

const SR: u32 = 48_000;
const W: usize = 12;
const LATENCY: usize = 144;
const ATTACK: usize = LATENCY - W - 1;
const HOLD: usize = 720;
const CEILING_DB: f64 = -1.0;
const RELEASE_MS: f64 = 250.0;

/// Interpolation kernels in the window's order: row p's tap m multiplies `x[i + m]` and gives the
/// point `(p + 1) / 4` of the way from `x[i + W - 1]` to `x[i + W]` (a Hann-windowed sinc, summing
/// to 1; the host passes `loudness`'s Kaiser kernels, which these tests do not need).
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

fn limiter() -> TruePeakLimiter {
    TruePeakLimiter::new(
        &kernels(),
        W,
        CEILING_DB,
        LATENCY,
        ATTACK,
        HOLD,
        RELEASE_MS,
        SR,
    )
    .unwrap()
}

/// A stream with every path in it: quiet passages (the shortcut that skips oversampling), loud
/// bursts well over the ceiling, a lone spike, and silence for the release.
fn stream(n: usize) -> Vec<f64> {
    let mut rng = Rng(11);
    (0..n)
        .map(|k| {
            let t = k as f64 / f64::from(SR);
            let tone = (2.0 * PI * 60.0 * t).sin();
            let noise = 2.0 * rng.unit() - 1.0;
            match (k / 3000) % 5 {
                0 => 0.1 * tone,
                1 => 1.8 * tone + 0.3 * noise,
                2 => 0.0,
                3 => {
                    if k % 3000 == 1500 {
                        2.5
                    } else {
                        0.05 * noise
                    }
                }
                _ => 1.2 * noise,
            }
        })
        .collect()
}

/// `x` through `lim` in blocks of `block` samples.
fn run(lim: &mut TruePeakLimiter, x: &[f64], block: usize) -> Vec<f64> {
    let mut out = vec![0.0; x.len()];
    for (x, out) in x.chunks(block).zip(out.chunks_mut(block)) {
        lim.process(x, out).unwrap();
    }
    out
}

fn max_diff(a: &[f64], b: &[f64]) -> f64 {
    a.iter()
        .zip(b)
        .map(|(a, b)| (a - b).abs())
        .fold(0.0, f64::max)
}

// --- Tests.

#[test]
fn the_output_does_not_depend_on_the_block_size() {
    let x = stream(20_000);
    let want = run(&mut limiter(), &x, 4096);
    assert!(want.iter().any(|v| *v != 0.0));
    // Something was limited: the loud bursts leave the output well under the input.
    let target = 10f64.powf((CEILING_DB - MARGIN_DB) / 20.0);
    assert!(x.iter().map(|v| v.abs()).fold(0.0, f64::max) > 2.0);
    assert!(want.iter().map(|v| v.abs()).fold(0.0, f64::max) <= target + 1e-12);
    // The output is causal: blocks of 1 over the first 11 000 samples (the quiet start, a loud
    // passage, its release and the spike) give the start of the same output.
    for (block, len) in [(1024, x.len()), (7, x.len()), (1, 11_000)] {
        let got = run(&mut limiter(), &x[..len], block);
        let diff = max_diff(&got, &want[..len]);
        assert!(diff <= 1e-12, "blocks of {block}: max |diff| = {diff:e}");
    }
}

#[test]
fn a_quiet_stream_comes_out_only_delayed_and_reports_nothing() {
    let x: Vec<f64> = (0..10_000)
        .map(|k| 0.5 * (f64::from(k) / 10.0).sin())
        .collect();
    let mut lim = limiter();
    let out = run(&mut lim, &x, 4096);
    assert!(out[..LATENCY].iter().all(|v| *v == 0.0));
    assert_eq!(&out[LATENCY..], &x[..x.len() - LATENCY]);
    assert_eq!(lim.gain(), 1.0);
    assert_eq!(lim.max_reduction_db(), 0.0);
    assert_eq!(lim.active_fraction(), 0.0);
    assert_eq!(lim.latency(), LATENCY);
}

#[test]
fn the_metrics_describe_the_last_block() {
    let mut lim = limiter();
    let loud: Vec<f64> = (0..4096)
        .map(|k| 2.0 * (2.0 * PI * 60.0 * f64::from(k) / f64::from(SR)).sin())
        .collect();
    let _ = run(&mut lim, &loud, 4096);
    assert!(lim.gain() < 0.5);
    assert!(lim.max_reduction_db() > 6.0);
    assert!(lim.active_fraction() > 0.5 && lim.active_fraction() <= 1.0);
    // Silence: the release is exponential (1 - g shrinks by e every 250 ms), so after 40 blocks
    // (3.4 s, 13.6 time constants) the reduction is a few millionths of a dB but not 0, as numpy's.
    let silence = vec![0.0; 4096];
    for _ in 0..40 {
        let _ = run(&mut lim, &silence, 4096);
    }
    assert!(
        lim.gain() < 1.0 && lim.gain() > 1.0 - 1e-5,
        "{}",
        lim.gain()
    );
    assert!(lim.max_reduction_db() < 1e-4, "{}", lim.max_reduction_db());
}

#[test]
fn configure_lowers_the_ceiling_from_the_next_block() {
    let x: Vec<f64> = (0..8192)
        .map(|k| 0.85 * (2.0 * PI * 100.0 * f64::from(k) / f64::from(SR)).sin())
        .collect();
    let mut lim = limiter();
    let _ = run(&mut lim, &x[..4096], 4096);
    assert_eq!(lim.max_reduction_db(), 0.0);
    lim.configure(Some(-6.0), Some(50.0)).unwrap();
    let out = run(&mut lim, &x[4096..], 4096);
    assert!(lim.max_reduction_db() > 4.0);
    let target = 10f64.powf((-6.0 - MARGIN_DB) / 20.0);
    assert!(out.iter().map(|v| v.abs()).fold(0.0, f64::max) <= target + 1e-12);
    // A refused knob changes nothing, the other one included.
    let before = lim.clone_state_and_knobs();
    assert_eq!(
        lim.configure(Some(-3.0), Some(0.0)),
        Err(LimiterError::InvalidConfig {
            field: "release_ms",
            got: 0.0
        })
    );
    assert_eq!(lim.clone_state_and_knobs(), before);
}

#[test]
fn the_state_moves_exactly() {
    let x = stream(15_000);
    let (head, tail) = x.split_at(7_321);
    let mut a = limiter();
    let _ = run(&mut a, head, 1000);
    let mut b = limiter();
    b.set_state(&a.to_state()).unwrap();
    assert_eq!(b.to_state(), a.to_state());
    assert!(a.to_state().gain < 1.0, "the state carries a reduction");
    assert_eq!(run(&mut a, tail, 4096), run(&mut b, tail, 4096));
}

#[test]
fn a_refused_call_changes_nothing() {
    let mut lim = limiter();
    let x = stream(5000);
    let _ = run(&mut lim, &x, 4096);
    let before = lim.to_state();
    let mut short = vec![0.0; 10];
    assert_eq!(
        lim.process(&x, &mut short),
        Err(LimiterError::OutputMismatch)
    );
    assert_eq!(lim.to_state(), before);
    let keep = before.x.len();
    let error = lim
        .set_state(&LimiterState {
            x: vec![0.0; keep + 1],
            gain: 0.5,
        })
        .unwrap_err();
    assert_eq!(
        error,
        LimiterError::BadShape {
            field: "x",
            got: keep + 1,
            expected: keep
        }
    );
    assert_eq!(
        error.to_string(),
        format!("x: {} values where {keep} are needed", keep + 1)
    );
    assert_eq!(lim.to_state(), before);
    // An empty block changes nothing either.
    lim.process(&[], &mut []).unwrap();
    assert_eq!(lim.to_state(), before);
}

#[test]
fn the_constructor_refuses_what_cannot_run() {
    let k = kernels();
    let new = |kernels: &[f64], w, latency, attack, release_ms, sr| {
        TruePeakLimiter::new(
            kernels, w, CEILING_DB, latency, attack, HOLD, release_ms, sr,
        )
    };
    assert_eq!(
        new(&k[1..], W, LATENCY, ATTACK, RELEASE_MS, SR).unwrap_err(),
        LimiterError::BadShape {
            field: "kernels",
            got: 3 * 2 * W - 1,
            expected: 3 * 2 * W
        }
    );
    // numpy's "look-ahead leaves no attack", and a latency that is not attack + w + 1.
    for (latency, attack) in [(W + 1, 0), (LATENCY, ATTACK + 1)] {
        let error = new(&k, W, latency, attack, RELEASE_MS, SR).unwrap_err();
        assert_eq!(
            error,
            LimiterError::BadTiming {
                latency,
                attack,
                half_width: W
            }
        );
        assert!(error.to_string().contains("attack"), "{error}");
    }
    assert_eq!(
        new(&[], 0, 2, 1, RELEASE_MS, SR).unwrap_err(),
        LimiterError::BadTiming {
            latency: 2,
            attack: 1,
            half_width: 0
        }
    );
    assert_eq!(
        new(&k, W, LATENCY, ATTACK, -1.0, SR).unwrap_err(),
        LimiterError::InvalidConfig {
            field: "release_ms",
            got: -1.0
        }
    );
    assert_eq!(
        new(&k, W, LATENCY, ATTACK, RELEASE_MS, 0).unwrap_err(),
        LimiterError::InvalidConfig {
            field: "sr",
            got: 0.0
        }
    );
    let error =
        TruePeakLimiter::new(&k, W, f64::NAN, LATENCY, ATTACK, HOLD, RELEASE_MS, SR).unwrap_err();
    assert!(matches!(
        error,
        LimiterError::InvalidConfig {
            field: "ceiling_db",
            ..
        }
    ));
}

#[test]
fn debug_shows_sizes_and_knobs_only() {
    let text = format!("{:?}", limiter());
    assert!(text.starts_with("TruePeakLimiter"), "{text}");
    assert!(text.contains("latency: 144"), "{text}");
    assert!(text.len() < 400, "{text}");
}

#[test]
fn process_and_configure_allocate_nothing_once_warm() {
    assert!(allocations_in(|| drop(black_box(Vec::<f64>::with_capacity(1)))) >= 1);
    let mut lim = limiter();
    let x = stream(4096 * 5);
    let mut out = vec![0.0; 4096];
    // The first block of the largest size grows the buffers.
    lim.process(&x[..4096], &mut out).unwrap();
    let allocations = allocations_in(|| {
        for (k, block) in x.chunks(4096).enumerate() {
            lim.process(block, &mut out[..block.len()]).unwrap();
            if k == 2 {
                lim.configure(Some(-3.0), Some(100.0)).unwrap();
            }
        }
        // Shorter blocks, the quiet shortcut and silence.
        lim.process(&x[..7], &mut out[..7]).unwrap();
        lim.process(&[0.0; 1000], &mut out[..1000]).unwrap();
        lim.process(&x[..1], &mut out[..1]).unwrap();
    });
    assert_eq!(allocations, 0);
}

// --- What `configure` must not touch, for the refused-call checks.

trait Snapshot {
    fn clone_state_and_knobs(&self) -> (LimiterState, String);
}

impl Snapshot for TruePeakLimiter {
    fn clone_state_and_knobs(&self) -> (LimiterState, String) {
        (self.to_state(), format!("{self:?}"))
    }
}
