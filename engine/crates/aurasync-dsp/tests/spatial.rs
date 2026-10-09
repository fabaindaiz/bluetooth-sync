//! The spatial / front upmix: the port of `host/src/aurasync/dsp/spatial.py`.
//!
//! The golden against numpy (within 1e-9) is in `host/tests/test_spatial_rust.py`; these tests
//! check what can be said without numpy: the front render gives its input back, silence stays
//! silence, the block size does not change the stream, the state moves exactly, errors change
//! nothing, and `process` allocates nothing within the reserved block.
//!
//! The counting allocator and the generator are in `common`.

use std::hint::black_box;

use aurasync_dsp::spatial::{N_FFT, Params, SpatialError, SpatialUpmix};

mod common;
use common::{Rng, allocations_in};

// --- Helpers.

const SR: u32 = 48_000;
const BLOCK: usize = 4096;

/// Partly correlated stereo: a centre plus independent room on each side.
fn music(n: usize, seed: u64) -> (Vec<f64>, Vec<f64>) {
    let mut rng = Rng(seed);
    let centre = rng.signal(n);
    let (a, b) = (rng.signal(n), rng.signal(n));
    let left = centre
        .iter()
        .zip(&a)
        .map(|(c, x)| 0.6 * c + 0.4 * x)
        .collect();
    let right = centre
        .iter()
        .zip(&b)
        .map(|(c, x)| 0.6 * c + 0.4 * x)
        .collect();
    (left, right)
}

/// Three principals at -60, 60 and 180 degrees and one ambient speaker.
fn ring(params: Params) -> SpatialUpmix {
    let mut up = SpatialUpmix::new(4, SR, N_FFT, N_FFT / 4, BLOCK).unwrap();
    up.set_params(params);
    up.set_layout(
        &[Some(-60.0), Some(60.0), Some(180.0), None],
        &[false, false, false, true],
        None,
    )
    .unwrap();
    up
}

/// The whole stream through `up` in blocks of `sizes` (cycled): (direct, ambience) per speaker.
fn stream(
    up: &mut SpatialUpmix,
    left: &[f64],
    right: &[f64],
    sizes: &[usize],
) -> Vec<(Vec<f64>, Vec<f64>)> {
    let k = up.speakers();
    let mut out = vec![(Vec::new(), Vec::new()); k];
    let (mut at, mut i) = (0, 0);
    while at < left.len() {
        let n = sizes[i % sizes.len()].min(left.len() - at);
        let (mut direct, mut ambience) = (vec![0.0; k * n], vec![0.0; k * n]);
        up.process(
            &left[at..at + n],
            &right[at..at + n],
            &mut direct,
            &mut ambience,
        )
        .unwrap();
        for (s, (d, a)) in out.iter_mut().enumerate() {
            d.extend_from_slice(&direct[s * n..(s + 1) * n]);
            a.extend_from_slice(&ambience[s * n..(s + 1) * n]);
        }
        at += n;
        i += 1;
    }
    out
}

// --- The tests.

#[test]
fn the_front_render_gives_its_input_back_to_the_front_pair() {
    let (left, right) = music(48_000, 1);
    let mut up = ring(Params {
        front_intact: true,
        ..Params::default()
    });
    let out = stream(&mut up, &left, &right, &[BLOCK]);
    // Past the latency (N_FFT) and the entry fade, the front pair plays L and R as they came.
    let settle = 24_000;
    for (speaker, input) in [(0, &left), (1, &right)] {
        let worst = (settle..left.len())
            .map(|i| (out[speaker].0[i] - input[i - N_FFT]).abs())
            .fold(0.0, f64::max);
        assert!(worst < 1e-12, "speaker {speaker}: {worst}");
        // Nothing of the ambience path on the front.
        assert!(out[speaker].1.iter().all(|&x| x == 0.0));
    }
    // The rear and the ambient speaker get only ambience.
    for (direct, ambience) in &out[2..] {
        assert!(direct.iter().all(|&x| x == 0.0));
        assert!(ambience[settle..].iter().any(|&x| x != 0.0));
    }
}

#[test]
fn silence_stays_silence_and_a_signal_comes_out_after_the_latency() {
    let zeros = vec![0.0; 20_000];
    let mut up = ring(Params::default());
    let out = stream(&mut up, &zeros, &zeros, &[BLOCK]);
    assert!(
        out.iter()
            .all(|(d, a)| d.iter().chain(a).all(|&x| x == 0.0))
    );

    let (left, right) = music(20_000, 2);
    let mut up = ring(Params::default());
    let out = stream(&mut up, &left, &right, &[BLOCK]);
    for (d, a) in &out {
        // The first N_FFT samples are the latency: exact zeros.
        assert!(d[..N_FFT].iter().chain(&a[..N_FFT]).all(|&x| x == 0.0));
        assert!(d.iter().chain(a).all(|x| x.is_finite()));
    }
    assert!(
        out.iter()
            .any(|(d, _)| d[N_FFT..].iter().any(|&x| x != 0.0))
    );
}

#[test]
fn the_block_size_does_not_change_the_stream() {
    // Without a live change, the output is a function of the input stream only: frames fall on
    // fixed hops and the fade counts samples. Bit for bit, whatever the blocks.
    let (left, right) = music(30_000, 3);
    let reference = stream(&mut ring(Params::default()), &left, &right, &[BLOCK]);
    for sizes in [
        &[1usize, 777, 10_000][..],
        &[512],
        &[2047, 2049],
        &[0, 5000],
    ] {
        let got = stream(&mut ring(Params::default()), &left, &right, sizes);
        assert_eq!(got, reference, "blocks {sizes:?}");
    }
}

#[test]
fn the_state_moves_exactly() {
    let (left, right) = music(30_000, 4);
    let mut a = ring(Params::default());
    let _ = stream(&mut a, &left[..10_001], &right[..10_001], &[BLOCK]);
    let mut b = ring(Params::default());
    b.set_state(&a.to_state()).unwrap();
    assert_eq!(a.to_state(), b.to_state());
    let rest_a = stream(&mut a, &left[10_001..], &right[10_001..], &[3000]);
    let rest_b = stream(&mut b, &left[10_001..], &right[10_001..], &[3000]);
    assert_eq!(rest_a, rest_b);
}

#[test]
fn a_refused_call_changes_nothing() {
    let (left, right) = music(10_000, 5);
    let mut up = ring(Params::default());
    let _ = stream(&mut up, &left, &right, &[BLOCK]);
    let before = up.to_state();

    let mut out = vec![0.0; 4 * 10];
    assert_eq!(
        up.process(&left[..10], &right[..11], &mut out.clone(), &mut out),
        Err(SpatialError::LengthMismatch {
            left: 10,
            right: 11
        })
    );
    let mut short = vec![0.0; 4 * 10 - 1];
    assert_eq!(
        up.process(&left[..10], &right[..10], &mut short, &mut out),
        Err(SpatialError::OutputMismatch)
    );
    assert_eq!(
        up.set_layout(&[Some(0.0)], &[false; 4], None),
        Err(SpatialError::BadShape {
            field: "angles",
            got: 1,
            expected: 4
        })
    );
    assert_eq!(
        up.set_layout(&[None; 4], &[false], None),
        Err(SpatialError::BadShape {
            field: "ambient",
            got: 1,
            expected: 4
        })
    );
    let mut bad = up.to_state();
    bad.norm.pop();
    assert_eq!(
        up.set_state(&bad),
        Err(SpatialError::BadShape {
            field: "norm",
            got: N_FFT - 1,
            expected: N_FFT
        })
    );
    let mut bad = up.to_state();
    bad.haas_read[0] = up.haas_len() + 1;
    assert_eq!(
        up.set_state(&bad),
        Err(SpatialError::OutOfRange {
            field: "haas_read",
            got: up.haas_len() + 1,
            max: up.haas_len()
        })
    );
    let mut bad = up.to_state();
    let ready = bad.ready_direct[0].len();
    bad.ready_ambience[1].push(0.0);
    assert_eq!(
        up.set_state(&bad),
        Err(SpatialError::BadShape {
            field: "ready_ambience row",
            got: ready + 1,
            expected: ready
        })
    );
    let mut bad = up.to_state();
    bad.ola_direct.pop();
    assert_eq!(
        up.set_state(&bad),
        Err(SpatialError::BadShape {
            field: "ola_direct rows",
            got: 3,
            expected: 4
        })
    );
    assert_eq!(up.to_state(), before);
}

#[test]
fn a_bad_configuration_is_refused() {
    let error = SpatialUpmix::new(2, 48_000, 1024, 0, 8192).unwrap_err();
    assert_eq!(
        error,
        SpatialError::InvalidConfig {
            n_fft: 1024,
            hop: 0,
            sr: 48_000
        }
    );
    let message = error.to_string();
    assert!(
        message.contains("n_fft 1024") && message.contains("hop 0") && message.contains("sr 48000"),
        "{message}"
    );
    for (n_fft, hop, sr) in [(1, 1, SR), (0, 1, SR), (8, 9, SR), (1024, 256, 0)] {
        assert_eq!(
            SpatialUpmix::new(2, sr, n_fft, hop, 8).unwrap_err(),
            SpatialError::InvalidConfig { n_fft, hop, sr }
        );
    }
    // The edges that are allowed: the shortest STFT, and a hop of the whole frame or of 1.
    for (n_fft, hop) in [(2, 1), (2, 2), (8, 8), (8, 1)] {
        assert!(
            SpatialUpmix::new(2, SR, n_fft, hop, 8).is_ok(),
            "n_fft {n_fft}, hop {hop}"
        );
    }
}

#[test]
fn the_haas_delay_is_clamped_and_rounded_like_python() {
    let mut up = ring(Params::default());
    up.set_params(Params {
        haas_ms: 45.0,
        ..Params::default()
    });
    assert_eq!(up.params().haas_ms, 30.0);
    up.set_params(Params {
        haas_ms: -1.0,
        ..Params::default()
    });
    assert_eq!(up.params().haas_ms, 0.0);
    // 30 ms at 48 kHz: a line of 1440 samples.
    assert_eq!(up.haas_len(), 1440);
}

#[test]
fn no_allocation_in_process() {
    // The control: the counter does see an allocation.
    assert!(allocations_in(|| drop(black_box(Vec::<f64>::with_capacity(1)))) >= 1);

    let (left, right) = music(BLOCK, 6);
    let k = 4;
    let (mut direct, mut ambience) = (vec![0.0; k * BLOCK], vec![0.0; k * BLOCK]);
    for front_intact in [false, true] {
        let mut up = ring(Params {
            front_intact,
            ..Params::default()
        });
        // Past the latency and the fade, then a live Haas change (the crossfade path).
        for _ in 0..4 {
            up.process(&left, &right, &mut direct, &mut ambience)
                .unwrap();
        }
        up.set_params(Params {
            front_intact,
            haas_ms: 25.0,
            ..Params::default()
        });
        for n in [BLOCK, 1, 511, 2049, BLOCK - 1, BLOCK, BLOCK] {
            let allocations = allocations_in(|| {
                up.process(
                    black_box(&left[..n]),
                    black_box(&right[..n]),
                    &mut direct[..k * n],
                    &mut ambience[..k * n],
                )
                .unwrap();
            });
            assert_eq!(allocations, 0, "front {front_intact}, block of {n}");
        }
    }
}
