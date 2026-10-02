//! `--dry-run`: the same engine and configuration, from a stereo WAV to an N-channel WAV,
//! without PipeWire. It checks the engine on a machine with no speakers and doubles as a
//! benchmark there (it prints the cost per call).

use std::path::{Path, PathBuf};
use std::time::Instant;

use hound::{SampleFormat, WavReader, WavSpec, WavWriter};
use poc_dsp::{Engine, Histogram};
use serde_json::{Value, json};

use crate::config::PocConfig;
use crate::report;

/// Call sizes cycled through by default: the quantums the graph may use, and an odd one.
pub const DEFAULT_SIZES: [usize; 4] = [64, 1024, 2048, 3000];

pub struct DryRunOptions {
    pub input: PathBuf,
    pub output: PathBuf,
    pub sizes: Vec<usize>,
}

/// Reads a mono or stereo WAV as two f32 channels (mono is duplicated).
pub fn read_stereo(path: &Path, rate: u32) -> Result<(Vec<f32>, Vec<f32>), String> {
    let mut reader = WavReader::open(path).map_err(|e| format!("{}: {e}", path.display()))?;
    let spec = reader.spec();
    if spec.sample_rate != rate {
        return Err(format!(
            "{}: {} Hz, the engine runs at {rate} Hz (resample it first)",
            path.display(),
            spec.sample_rate
        ));
    }
    let samples: Vec<f32> = match spec.sample_format {
        SampleFormat::Float => reader
            .samples::<f32>()
            .collect::<Result<_, _>>()
            .map_err(|e| e.to_string())?,
        SampleFormat::Int => {
            let scale = 1.0 / (1u64 << (spec.bits_per_sample - 1)) as f32;
            reader
                .samples::<i32>()
                .map(|s| s.map(|v| v as f32 * scale))
                .collect::<Result<_, _>>()
                .map_err(|e| e.to_string())?
        }
    };
    match spec.channels {
        1 => Ok((samples.clone(), samples)),
        2 => Ok((
            samples.iter().step_by(2).copied().collect(),
            samples.iter().skip(1).step_by(2).copied().collect(),
        )),
        n => Err(format!("{}: {n} channels, expected 1 or 2", path.display())),
    }
}

/// Runs the engine over the whole signal in calls of the given sizes (cycled), timing
/// each call into `timing`.
pub fn process_offline(
    engine: &mut Engine,
    left: &[f32],
    right: &[f32],
    sizes: &[usize],
    timing: &Histogram,
) -> Vec<Vec<f32>> {
    let n = left.len().min(right.len());
    let mut out = vec![vec![0.0f32; n]; engine.speakers()];
    let (mut start, mut k) = (0, 0);
    while start < n {
        let end = (start + sizes[k % sizes.len()].max(1)).min(n);
        let mut views: Vec<&mut [f32]> = out.iter_mut().map(|o| &mut o[start..end]).collect();
        let t = Instant::now();
        engine.process(&left[start..end], &right[start..end], &mut views);
        timing.record(t.elapsed().as_nanos() as u64);
        start = end;
        k += 1;
    }
    out
}

pub fn run(config: &PocConfig, opts: &DryRunOptions) -> Result<Value, String> {
    let (left, right) = read_stereo(&opts.input, config.rate)?;
    let (mut engine, _commands) = Engine::new(&config.engine_config());
    let timing = Histogram::new();
    let started = Instant::now();
    let out = process_offline(&mut engine, &left, &right, &opts.sizes, &timing);
    let busy = started.elapsed().as_secs_f64();

    let spec = WavSpec {
        channels: out.len() as u16,
        sample_rate: config.rate,
        bits_per_sample: 32,
        sample_format: SampleFormat::Float,
    };
    let mut writer = WavWriter::create(&opts.output, spec)
        .map_err(|e| format!("{}: {e}", opts.output.display()))?;
    for i in 0..left.len().min(right.len()) {
        for channel in &out {
            writer.write_sample(channel[i]).map_err(|e| e.to_string())?;
        }
    }
    writer.finalize().map_err(|e| e.to_string())?;

    let seconds = left.len() as f64 / config.rate as f64;
    let snapshot = timing.take();
    Ok(json!({
        "mode": "dry-run",
        "config": config.source,
        "input": opts.input.display().to_string(),
        "output": opts.output.display().to_string(),
        "speakers": config.names(),
        "delay_samples": engine.delays().collect::<Vec<_>>(),
        "audio_s": seconds,
        "call_sizes": opts.sizes,
        "process": report::histogram(&snapshot),
        "realtime_factor": if busy > 0.0 { Some((seconds / busy).round()) } else { None },
    }))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::config::from_config_json;

    #[test]
    fn a_wav_goes_through_with_pan_and_delay() {
        let dir = std::env::temp_dir().join(format!("poc-pw-dry-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let input = dir.join("in.wav");
        let output = dir.join("out.wav");
        let spec = WavSpec {
            channels: 2,
            sample_rate: 48_000,
            bits_per_sample: 16,
            sample_format: SampleFormat::Int,
        };
        let mut w = WavWriter::create(&input, spec).unwrap();
        for i in 0..4800i32 {
            w.write_sample((i % 100) as i16 * 100).unwrap(); // left: a ramp
            w.write_sample(0i16).unwrap(); // right: silence
        }
        w.finalize().unwrap();
        let config = from_config_json(
            r#"{"speakers": [{"name": "L", "pan": -1, "delay_samples": 10},
                             {"name": "R", "pan": 1}]}"#,
            "test",
        )
        .unwrap();
        let summary = run(
            &config,
            &DryRunOptions {
                input: input.clone(),
                output: output.clone(),
                sizes: DEFAULT_SIZES.to_vec(),
            },
        )
        .unwrap();
        assert_eq!(summary["speakers"], json!(["L", "R"]));
        let mut r = WavReader::open(&output).unwrap();
        assert_eq!(r.spec().channels, 2);
        let s: Vec<f32> = r.samples::<f32>().map(Result::unwrap).collect();
        let left_out: Vec<f32> = s.iter().step_by(2).copied().collect();
        let right_out: Vec<f32> = s.iter().skip(1).step_by(2).copied().collect();
        // Speaker L: the left input, 10 samples late. Speaker R: right only, silence.
        assert!(left_out[..10].iter().all(|&v| v == 0.0));
        assert!((left_out[10 + 57] - 57.0 * 100.0 / 32768.0).abs() < 1e-6);
        assert!(right_out.iter().all(|&v| v == 0.0));
        std::fs::remove_dir_all(&dir).ok();
    }
}
