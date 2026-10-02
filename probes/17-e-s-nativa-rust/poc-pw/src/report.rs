//! The log: one JSON object per line, one line per second plus a summary at the end.
//!
//! Durations are in microseconds with one decimal. Quantiles are the upper edge of their
//! histogram bucket (≤ 6.25 % above the true value, never below it) and never above the
//! exact maximum.

// Most of this module serves the live mode, which only exists on Linux.
#![cfg_attr(not(target_os = "linux"), allow(dead_code))]

use std::fs::{File, OpenOptions};
use std::io::{LineWriter, Write};
use std::path::Path;
use std::time::{SystemTime, UNIX_EPOCH};

use poc_dsp::{HistogramSnapshot, MetricsSnapshot};
use serde_json::{Value, json};

/// The real-time criterion of the protocol: the p99.9 of the callback under this fraction
/// of the (smallest) quantum.
pub const CALLBACK_BUDGET: f64 = 0.25;

pub fn unix_time() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0)
}

fn us(ns: u64) -> f64 {
    (ns as f64 / 100.0).round() / 10.0
}

/// A histogram as `{count, p50_us, p99_us, p999_us, max_us, buckets: [[lower_ns, count]…]}`.
pub fn histogram(h: &HistogramSnapshot) -> Value {
    let q = |p| h.quantile(p).map(us);
    json!({
        "count": h.count(),
        "p50_us": q(0.5),
        "p99_us": q(0.99),
        "p999_us": q(0.999),
        "max_us": if h.count() > 0 { Some(us(h.max)) } else { None },
        "buckets": h.nonzero(),
    })
}

/// The metrics of one interval (or of the whole run).
pub fn metrics(s: &MetricsSnapshot, rate: u32) -> Value {
    let min_quantum_ns = s.quantum.map(|(_, min, _)| min as f64 * 1e9 / rate as f64);
    let p999_fraction = match (s.callback_ns.quantile(0.999), min_quantum_ns) {
        (Some(p), Some(q)) if q > 0.0 => Some((p as f64 / q * 10_000.0).round() / 10_000.0),
        _ => None,
    };
    json!({
        "frames": s.frames,
        "callbacks": s.callbacks,
        "quantum": s.quantum.map(|(last, min, max)| json!({"last": last, "min": min, "max": max})),
        "callback": histogram(&s.callback_ns),
        "callback_p999_of_min_quantum": p999_fraction,
        "callback_within_budget": p999_fraction.map(|f| f < CALLBACK_BUDGET),
        "period": histogram(&s.period_ns),
        "wakeup": histogram(&s.wakeup_ns),
        "xruns": {
            "no_input": s.no_input,
            "no_output": s.no_output,
            "total": s.no_input + s.no_output,
        },
        "bad_buffer": s.bad_buffer,
        "trigger_failed": s.trigger_failed,
        "graph_xrun_flags": s.graph_xrun_flags,
        "rt_alloc_violations": s.rt_alloc_violations,
        "commands_applied": s.commands_applied,
    })
}

/// Appends JSON lines to a file, flushing each line (a crash loses at most the last one).
pub struct Logger {
    out: LineWriter<File>,
}

impl Logger {
    pub fn open(path: &Path) -> std::io::Result<Self> {
        let file = OpenOptions::new().create(true).append(true).open(path)?;
        Ok(Self {
            out: LineWriter::new(file),
        })
    }

    pub fn write(&mut self, line: &Value) {
        if let Err(e) = writeln!(self.out, "{line}") {
            eprintln!("poc-pw: cannot write the log: {e}");
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use poc_dsp::Metrics;

    #[test]
    fn budget_is_judged_against_the_smallest_quantum() {
        let m = Metrics::new();
        for _ in 0..1000 {
            m.callback_ns.record(50_000); // 50 µs
        }
        m.record_quantum(2048);
        m.record_quantum(256); // 5.33 ms: 50 µs is ~0.94 %
        let v = metrics(&m.take(), 48_000);
        let f = v["callback_p999_of_min_quantum"].as_f64().unwrap();
        assert!((0.009..0.0105).contains(&f), "{f}");
        assert_eq!(v["callback_within_budget"], json!(true));
        assert_eq!(v["callback"]["max_us"], json!(50.0));
    }

    #[test]
    fn empty_interval_has_nulls_not_zeros() {
        let v = metrics(&Metrics::new().take(), 48_000);
        assert_eq!(v["callback"]["p999_us"], Value::Null);
        assert_eq!(v["quantum"], Value::Null);
        assert_eq!(v["callback_within_budget"], Value::Null);
    }
}
