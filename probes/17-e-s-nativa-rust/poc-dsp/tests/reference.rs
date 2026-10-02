//! The engine against direct reference computations, and against itself with different
//! call sizes (the graph's quantum is not fixed).

use poc_dsp::engine::{DELAY_FADE_MS, GAIN_RAMP_MS, MUTE_FADE_MS, db_to_gain};
use poc_dsp::{Command, Engine, EngineConfig, SpeakerParams, SpeakerUpdate};

const RATE: u32 = 48_000;

/// A deterministic, non-periodic test signal (a small LCG), different per channel.
fn signal(n: usize, seed: u32) -> Vec<f32> {
    let mut x = seed.wrapping_mul(2_654_435_761).wrapping_add(1);
    (0..n)
        .map(|_| {
            x = x.wrapping_mul(1_664_525).wrapping_add(1_013_904_223);
            (x >> 8) as f32 / (1u32 << 24) as f32 * 2.0 - 1.0
        })
        .collect()
}

fn config(speakers: &[SpeakerParams]) -> EngineConfig {
    EngineConfig {
        sample_rate: RATE,
        max_delay_samples: 4800,
        speakers: speakers.to_vec(),
        volume_db: 0.0,
        command_capacity: 16,
    }
}

/// Runs the whole input through the engine in calls of the given sizes (cycled).
fn run(engine: &mut Engine, left: &[f32], right: &[f32], sizes: &[usize]) -> Vec<Vec<f32>> {
    let n = left.len();
    let mut out = vec![vec![0.0f32; n]; engine.speakers()];
    let mut start = 0;
    let mut k = 0;
    while start < n {
        let end = (start + sizes[k % sizes.len()]).min(n);
        let mut views: Vec<&mut [f32]> = out.iter_mut().map(|o| &mut o[start..end]).collect();
        let done = engine.process(&left[start..end], &right[start..end], &mut views);
        assert_eq!(done, end - start);
        start = end;
        k += 1;
    }
    out
}

fn three_speakers() -> Vec<SpeakerParams> {
    vec![
        SpeakerParams {
            pan: -1.0,
            delay_samples: 0,
            gain_db: 0.0,
            muted: false,
        },
        SpeakerParams {
            pan: 0.7,
            delay_samples: 650,
            gain_db: -6.0,
            muted: false,
        },
        SpeakerParams {
            pan: 0.0,
            delay_samples: 3,
            gain_db: 3.0,
            muted: false,
        },
    ]
}

#[test]
fn steady_state_matches_mix_delay_and_gain() {
    let n = 10_000;
    let (l, r) = (signal(n, 1), signal(n, 2));
    let speakers = three_speakers();
    let (mut engine, _tx) = Engine::new(&config(&speakers));
    let out = run(&mut engine, &l, &r, &[1024]);
    for (s, p) in speakers.iter().enumerate() {
        let g = db_to_gain(p.gain_db);
        let d = p.delay_samples as usize;
        for t in 0..n {
            let expected = if t >= d {
                ((1.0 - p.pan) / 2.0 * l[t - d] + (1.0 + p.pan) / 2.0 * r[t - d]) * g
            } else {
                0.0
            };
            assert!(
                (out[s][t] - expected).abs() <= 1e-6,
                "speaker {s}, sample {t}: {} vs {expected}",
                out[s][t]
            );
        }
    }
}

#[test]
fn call_size_does_not_change_the_output() {
    let n = 50_000;
    let (l, r) = (signal(n, 3), signal(n, 4));
    let speakers = three_speakers();
    let changes = [
        Command::Speaker {
            index: 1,
            update: SpeakerUpdate {
                pan: Some(-0.3),
                delay_samples: Some(1200),
                gain_db: Some(-12.0),
                muted: None,
            },
        },
        Command::Speaker {
            index: 2,
            update: SpeakerUpdate {
                muted: Some(true),
                ..Default::default()
            },
        },
        Command::Volume { db: -3.0 },
    ];
    let mut reference = None;
    for sizes in [&[64][..], &[1024], &[2048], &[3000], &[1, 7, 333, 4096, 2]] {
        let (mut engine, mut tx) = Engine::new(&config(&speakers));
        for c in changes {
            tx.push(c).unwrap();
        }
        let out = run(&mut engine, &l, &r, sizes);
        match &reference {
            None => reference = Some(out),
            Some(expected) => assert_eq!(&out, expected, "call sizes {sizes:?}"),
        }
    }
}

#[test]
fn gain_ramps_linearly_over_the_short_ramp() {
    let n = 4800;
    let ones = vec![1.0f32; n];
    let (mut engine, mut tx) = Engine::new(&config(&[SpeakerParams::default()]));
    tx.push(Command::Speaker {
        index: 0,
        update: SpeakerUpdate {
            gain_db: Some(-6.0),
            ..Default::default()
        },
    })
    .unwrap();
    let out = run(&mut engine, &ones, &ones, &[480]);
    let ramp = (GAIN_RAMP_MS * RATE as f32 / 1000.0) as usize;
    let target = db_to_gain(-6.0);
    let mut previous = 1.0;
    for (t, &v) in out[0][..ramp].iter().enumerate() {
        assert!(v < previous && v >= target, "sample {t}: {v}");
        previous = v;
    }
    assert!(
        out[0][ramp - 1..]
            .iter()
            .all(|&v| (v - target).abs() < 1e-6)
    );
}

#[test]
fn mute_fades_to_exact_silence_and_back() {
    let n = 9600;
    let ones = vec![1.0f32; n];
    let (mut engine, mut tx) = Engine::new(&config(&[SpeakerParams::default()]));
    let fade = (MUTE_FADE_MS * RATE as f32 / 1000.0) as usize;
    tx.push(Command::Speaker {
        index: 0,
        update: SpeakerUpdate {
            muted: Some(true),
            ..Default::default()
        },
    })
    .unwrap();
    let out = run(&mut engine, &ones[..fade * 2], &ones[..fade * 2], &[256]);
    assert!(out[0][fade / 2] > 0.0 && out[0][fade / 2] < 1.0, "fading");
    assert!(
        out[0][fade - 1..].iter().all(|&v| v == 0.0),
        "silent after the fade"
    );
    tx.push(Command::Speaker {
        index: 0,
        update: SpeakerUpdate {
            muted: Some(false),
            ..Default::default()
        },
    })
    .unwrap();
    let out = run(&mut engine, &ones[..fade * 2], &ones[..fade * 2], &[256]);
    assert!(
        out[0][fade - 1..].iter().all(|&v| v == 1.0),
        "back to full level"
    );
}

#[test]
fn delay_change_fades_out_jumps_and_fades_in() {
    let n = 20_000;
    let (l, r) = (signal(n, 5), signal(n, 6));
    let (mut engine, mut tx) = Engine::new(&config(&[SpeakerParams::default()]));
    let first = 5000;
    let _ = run(&mut engine, &l[..first], &r[..first], &[1024]);
    tx.push(Command::Speaker {
        index: 0,
        update: SpeakerUpdate {
            delay_samples: Some(100),
            ..Default::default()
        },
    })
    .unwrap();
    let out = run(&mut engine, &l[first..], &r[first..], &[1024]);
    let fade = (DELAY_FADE_MS * RATE as f32 / 1000.0) as usize;
    // Fully faded out at the jump, then the new delay, then full level again.
    assert_eq!(out[0][fade - 1], 0.0);
    let settled = 2 * fade + 10;
    for (t, &v) in out[0].iter().enumerate().skip(settled) {
        let src = first + t - 100;
        let expected = 0.5 * l[src] + 0.5 * r[src];
        assert!((v - expected).abs() <= 1e-6, "sample {t}");
    }
    assert_eq!(engine.delays().collect::<Vec<_>>(), vec![100]);
}

#[test]
fn pan_moves_at_two_units_per_second() {
    let n = 60_000;
    let (l, r) = (vec![1.0f32; n], vec![0.0f32; n]);
    let (mut engine, mut tx) = Engine::new(&config(&[SpeakerParams {
        pan: -1.0,
        ..Default::default()
    }]));
    tx.push(Command::Speaker {
        index: 0,
        update: SpeakerUpdate {
            pan: Some(1.0),
            ..Default::default()
        },
    })
    .unwrap();
    let out = run(&mut engine, &l, &r, &[1024]);
    // Left-only weight is (1 - pan) / 2: 1 at pan -1, 0 at pan +1. From -1 to +1 is 2
    // units, 1 s at 2 units/s: weight 0.75 at 0.25 s, 0.5 at 0.5 s, 0 from 1 s on.
    assert!((out[0][11_999] - 0.75).abs() < 1e-3, "{}", out[0][11_999]);
    assert!((out[0][23_999] - 0.5).abs() < 1e-3, "{}", out[0][23_999]);
    assert!(out[0][47_999].abs() < 1e-3);
    assert!(out[0][48_000..].iter().all(|&v| v.abs() < 1e-6));
}

#[test]
fn shorter_output_limits_the_frames_processed() {
    let (mut engine, _tx) = Engine::new(&config(&[SpeakerParams::default(); 2]));
    let l = vec![0.5f32; 100];
    let mut a = vec![0.0f32; 100];
    let mut b = vec![0.0f32; 40];
    let mut outs: [&mut [f32]; 2] = [&mut a, &mut b];
    assert_eq!(engine.process(&l, &l, &mut outs), 40);
}
