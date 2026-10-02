//! `Engine::process` and the metrics never touch the allocator, including while applying
//! commands. `assert_no_alloc` (with `warn_debug`) counts allocations made inside the
//! guarded closure on this thread; a control test shows that the counter does see one.
#![cfg(debug_assertions)]

use assert_no_alloc::{AllocDisabler, assert_no_alloc, violation_count};
use poc_dsp::{Command, Engine, EngineConfig, Metrics, SpeakerParams, SpeakerUpdate};

#[global_allocator]
static A: AllocDisabler = AllocDisabler;

#[test]
fn the_counter_sees_an_allocation() {
    let before = violation_count();
    let v = assert_no_alloc(|| Vec::<u8>::with_capacity(64));
    drop(v);
    assert!(
        violation_count() > before,
        "the guard must notice an allocation"
    );
}

#[test]
fn process_with_commands_does_not_allocate() {
    let config = EngineConfig {
        speakers: vec![SpeakerParams::default(); 4],
        ..EngineConfig::default()
    };
    let (mut engine, mut tx) = Engine::new(&config);
    let metrics = Metrics::new();
    let n = 3000;
    let left = vec![0.25f32; n];
    let right = vec![-0.25f32; n];
    let mut bufs = vec![vec![0.0f32; n]; 4];
    let before = violation_count();
    for round in 0..200u32 {
        tx.push(Command::Speaker {
            index: (round % 4) as usize,
            update: SpeakerUpdate {
                pan: Some((round % 3) as f32 - 1.0),
                delay_samples: Some(round * 37 % 4800),
                gain_db: Some(-(round as f32 % 20.0)),
                muted: Some(round % 5 == 0),
            },
        })
        .unwrap();
        let len = [64usize, 1024, 2048, 3000][round as usize % 4];
        let (a, rest) = bufs.split_at_mut(1);
        let (b, rest) = rest.split_at_mut(1);
        let (c, d) = rest.split_at_mut(1);
        assert_no_alloc(|| {
            let mut outs: [&mut [f32]; 4] = [
                &mut a[0][..len],
                &mut b[0][..len],
                &mut c[0][..len],
                &mut d[0][..len],
            ];
            let frames = engine.process(&left[..len], &right[..len], &mut outs);
            metrics.record_quantum(frames as u64);
            metrics.callback_ns.record(12_345);
            metrics.period_ns.record(21_333_333);
        });
    }
    assert_eq!(violation_count(), before, "process allocated");
}
