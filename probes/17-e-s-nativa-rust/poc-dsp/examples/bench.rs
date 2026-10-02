//! Cost of `Engine::process`: `cargo run --release -p poc-dsp --example bench [speakers]`.
//!
//! Times many calls at the quantums seen in the graph and prints, per quantum, the median,
//! p99 and max time per call, the percentage of the quantum's real time it takes, and
//! how many times faster than real time it runs.

use std::time::Instant;

use poc_dsp::{Engine, EngineConfig, SpeakerParams};

const RATE: f64 = 48_000.0;

fn main() {
    let speakers: usize = std::env::args()
        .nth(1)
        .and_then(|s| s.parse().ok())
        .unwrap_or(3);
    let config = EngineConfig {
        speakers: (0..speakers)
            .map(|i| SpeakerParams {
                pan: i as f32 / speakers as f32 * 2.0 - 1.0,
                delay_samples: 100 + 311 * i as u32,
                gain_db: -1.0,
                muted: false,
            })
            .collect(),
        ..EngineConfig::default()
    };
    println!("speakers: {speakers}");
    for quantum in [256usize, 1024, 2048, 4096] {
        let (mut engine, _tx) = Engine::new(&config);
        let left: Vec<f32> = (0..quantum).map(|i| (i as f32 * 0.01).sin()).collect();
        let right: Vec<f32> = (0..quantum).map(|i| (i as f32 * 0.013).cos()).collect();
        let mut bufs = vec![vec![0.0f32; quantum]; speakers];
        let calls = (20.0 * RATE / quantum as f64) as usize; // 20 s of audio
        let mut times = Vec::with_capacity(calls);
        for _ in 0..calls {
            let mut outs: Vec<&mut [f32]> = bufs.iter_mut().map(|b| b.as_mut_slice()).collect();
            let t = Instant::now();
            let n = engine.process(&left, &right, &mut outs);
            times.push(t.elapsed().as_nanos() as f64);
            std::hint::black_box(n);
            std::hint::black_box(&bufs);
        }
        times.sort_by(f64::total_cmp);
        let p = |q: f64| times[((times.len() - 1) as f64 * q) as usize];
        let budget_ns = quantum as f64 / RATE * 1e9;
        println!(
            "quantum {quantum:>5}: median {:>8.1} µs, p99 {:>8.1} µs, max {:>8.1} µs \
             = {:.3} % of the quantum (median), {:.0}x real time",
            p(0.5) / 1e3,
            p(0.99) / 1e3,
            times[times.len() - 1] / 1e3,
            p(0.5) / budget_ns * 100.0,
            budget_ns / p(0.5),
        );
    }
}
