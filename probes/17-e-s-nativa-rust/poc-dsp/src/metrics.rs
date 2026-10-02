//! Timing histograms and counters shared between the real-time thread and the logger.
//!
//! The real-time side only does relaxed atomic adds, maxes and mins: no locks, no
//! allocation. The logger takes a snapshot once per second by swapping each atomic with
//! its reset value; a sample recorded during the swap may land in either interval, never
//! in both and never lost.

use std::sync::atomic::{AtomicU64, Ordering::Relaxed};

/// Linear sub-buckets per power of two: values are resolved to 1/16 of their magnitude
/// (6.25 %), exactly below 16.
const SUB_BITS: u32 = 4;
const SUB: usize = 1 << SUB_BITS;
/// Enough buckets for any `u64`.
pub const BUCKETS: usize = (64 - SUB_BITS as usize + 1) * SUB;

/// The bucket of a value (log-linear, like HdrHistogram with 4 significant bits).
pub fn bucket_index(v: u64) -> usize {
    if v < SUB as u64 {
        return v as usize;
    }
    let e = 63 - v.leading_zeros(); // position of the highest set bit, >= SUB_BITS
    let shift = e - SUB_BITS;
    let sub = ((v >> shift) as usize) & (SUB - 1);
    (shift as usize + 1) * SUB + sub
}

/// The smallest value that falls in bucket `i`.
pub fn bucket_lower(i: usize) -> u64 {
    if i < SUB {
        return i as u64;
    }
    let group = i / SUB;
    let sub = (i % SUB) as u64;
    (SUB as u64 + sub) << (group - 1)
}

/// The largest value that falls in bucket `i`.
pub fn bucket_upper(i: usize) -> u64 {
    if i + 1 >= BUCKETS {
        return u64::MAX;
    }
    bucket_lower(i + 1) - 1
}

/// A histogram of nanosecond durations, written from the real-time thread.
pub struct Histogram {
    buckets: Box<[AtomicU64]>,
    max: AtomicU64,
}

impl Default for Histogram {
    fn default() -> Self {
        Self::new()
    }
}

impl Histogram {
    pub fn new() -> Self {
        Self {
            buckets: (0..BUCKETS).map(|_| AtomicU64::new(0)).collect(),
            max: AtomicU64::new(0),
        }
    }

    #[inline]
    pub fn record(&self, v: u64) {
        self.buckets[bucket_index(v)].fetch_add(1, Relaxed);
        self.max.fetch_max(v, Relaxed);
    }

    /// Everything recorded since the previous `take`, and resets. Not for the RT thread.
    pub fn take(&self) -> HistogramSnapshot {
        HistogramSnapshot {
            counts: self.buckets.iter().map(|b| b.swap(0, Relaxed)).collect(),
            max: self.max.swap(0, Relaxed),
        }
    }
}

/// A plain copy of a histogram, to merge and to read percentiles from.
#[derive(Clone, Debug, PartialEq)]
pub struct HistogramSnapshot {
    pub counts: Vec<u64>,
    /// The exact largest value.
    pub max: u64,
}

impl Default for HistogramSnapshot {
    fn default() -> Self {
        Self {
            counts: vec![0; BUCKETS],
            max: 0,
        }
    }
}

impl HistogramSnapshot {
    pub fn count(&self) -> u64 {
        self.counts.iter().sum()
    }

    pub fn merge(&mut self, other: &Self) {
        for (a, b) in self.counts.iter_mut().zip(&other.counts) {
            *a += b;
        }
        self.max = self.max.max(other.max);
    }

    /// The `q` quantile (0..=1), reported as the **upper** edge of its bucket (so it never
    /// understates), and never above the exact maximum. `None` if empty.
    pub fn quantile(&self, q: f64) -> Option<u64> {
        let total = self.count();
        if total == 0 {
            return None;
        }
        let rank = ((q.clamp(0.0, 1.0) * total as f64).ceil() as u64).max(1);
        let mut seen = 0;
        for (i, &c) in self.counts.iter().enumerate() {
            seen += c;
            if seen >= rank {
                return Some(bucket_upper(i).min(self.max));
            }
        }
        Some(self.max)
    }

    /// Non-empty buckets as `(lower_edge, count)`, for the log.
    pub fn nonzero(&self) -> Vec<(u64, u64)> {
        self.counts
            .iter()
            .enumerate()
            .filter(|(_, c)| **c > 0)
            .map(|(i, c)| (bucket_lower(i), *c))
            .collect()
    }
}

/// Everything the callback reports. One instance, shared through an `Arc`.
pub struct Metrics {
    /// Wall time of one `process` call (dequeue, DSP, queue).
    pub callback_ns: Histogram,
    /// Time between the starts of two consecutive calls: the jitter.
    pub period_ns: Histogram,
    /// Start of the call minus the start of the graph cycle (`spa_io_position.clock.nsec`).
    pub wakeup_ns: Histogram,
    pub frames: AtomicU64,
    pub callbacks: AtomicU64,
    /// Calls that found no input buffer: an own xrun.
    pub no_input: AtomicU64,
    /// Calls that found no output buffer: an own xrun.
    pub no_output: AtomicU64,
    /// Buffers with an unexpected layout (channel count, alignment): output silenced.
    pub bad_buffer: AtomicU64,
    /// Capture calls whose trigger of the playback stream failed.
    pub trigger_failed: AtomicU64,
    /// Cycles flagged `SPA_IO_CLOCK_FLAG_XRUN_RECOVER` by the graph.
    pub graph_xrun_flags: AtomicU64,
    /// Allocations seen inside the callback (must stay 0).
    pub rt_alloc_violations: AtomicU64,
    pub commands_applied: AtomicU64,
    quantum_last: AtomicU64,
    quantum_min: AtomicU64,
    quantum_max: AtomicU64,
}

impl Default for Metrics {
    fn default() -> Self {
        Self::new()
    }
}

/// One interval of [`Metrics`], taken by the logger.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct MetricsSnapshot {
    pub callback_ns: HistogramSnapshot,
    pub period_ns: HistogramSnapshot,
    pub wakeup_ns: HistogramSnapshot,
    pub frames: u64,
    pub callbacks: u64,
    pub no_input: u64,
    pub no_output: u64,
    pub bad_buffer: u64,
    pub trigger_failed: u64,
    pub graph_xrun_flags: u64,
    pub rt_alloc_violations: u64,
    pub commands_applied: u64,
    /// `(last, min, max)` frames per call in the interval; `None` with no calls.
    pub quantum: Option<(u64, u64, u64)>,
}

impl MetricsSnapshot {
    /// Adds an interval to a running total. `commands_applied` and
    /// `rt_alloc_violations` are cumulative gauges, so the latest value is kept.
    pub fn merge(&mut self, o: &Self) {
        self.callback_ns.merge(&o.callback_ns);
        self.period_ns.merge(&o.period_ns);
        self.wakeup_ns.merge(&o.wakeup_ns);
        self.frames += o.frames;
        self.callbacks += o.callbacks;
        self.no_input += o.no_input;
        self.no_output += o.no_output;
        self.bad_buffer += o.bad_buffer;
        self.trigger_failed += o.trigger_failed;
        self.graph_xrun_flags += o.graph_xrun_flags;
        self.rt_alloc_violations = self.rt_alloc_violations.max(o.rt_alloc_violations);
        self.commands_applied = self.commands_applied.max(o.commands_applied);
        self.quantum = match (self.quantum, o.quantum) {
            (None, q) | (q, None) => q,
            (Some((_, a_min, a_max)), Some((last, b_min, b_max))) => {
                Some((last, a_min.min(b_min), a_max.max(b_max)))
            }
        };
    }
}

impl Metrics {
    pub fn new() -> Self {
        Self {
            callback_ns: Histogram::new(),
            period_ns: Histogram::new(),
            wakeup_ns: Histogram::new(),
            frames: AtomicU64::new(0),
            callbacks: AtomicU64::new(0),
            no_input: AtomicU64::new(0),
            no_output: AtomicU64::new(0),
            bad_buffer: AtomicU64::new(0),
            trigger_failed: AtomicU64::new(0),
            graph_xrun_flags: AtomicU64::new(0),
            rt_alloc_violations: AtomicU64::new(0),
            commands_applied: AtomicU64::new(0),
            quantum_last: AtomicU64::new(0),
            quantum_min: AtomicU64::new(u64::MAX),
            quantum_max: AtomicU64::new(0),
        }
    }

    /// Records the frames of one call.
    #[inline]
    pub fn record_quantum(&self, frames: u64) {
        self.frames.fetch_add(frames, Relaxed);
        self.quantum_last.store(frames, Relaxed);
        self.quantum_min.fetch_min(frames, Relaxed);
        self.quantum_max.fetch_max(frames, Relaxed);
    }

    #[inline]
    pub fn bump(counter: &AtomicU64) {
        counter.fetch_add(1, Relaxed);
    }

    /// The interval since the previous call, and resets the per-interval values.
    pub fn take(&self) -> MetricsSnapshot {
        let callbacks = self.callbacks.swap(0, Relaxed);
        let min = self.quantum_min.swap(u64::MAX, Relaxed);
        let max = self.quantum_max.swap(0, Relaxed);
        let last = self.quantum_last.load(Relaxed);
        MetricsSnapshot {
            callback_ns: self.callback_ns.take(),
            period_ns: self.period_ns.take(),
            wakeup_ns: self.wakeup_ns.take(),
            frames: self.frames.swap(0, Relaxed),
            callbacks,
            no_input: self.no_input.swap(0, Relaxed),
            no_output: self.no_output.swap(0, Relaxed),
            bad_buffer: self.bad_buffer.swap(0, Relaxed),
            trigger_failed: self.trigger_failed.swap(0, Relaxed),
            graph_xrun_flags: self.graph_xrun_flags.swap(0, Relaxed),
            rt_alloc_violations: self.rt_alloc_violations.load(Relaxed),
            commands_applied: self.commands_applied.load(Relaxed),
            quantum: (min != u64::MAX).then_some((last, min, max)),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn buckets_are_contiguous_and_monotonic() {
        let mut previous_upper = None;
        for i in 0..BUCKETS - 1 {
            let lo = bucket_lower(i);
            if let Some(up) = previous_upper {
                assert_eq!(lo, up + 1, "gap before bucket {i}");
            }
            assert_eq!(bucket_index(lo), i);
            assert_eq!(bucket_index(bucket_upper(i)), i);
            previous_upper = Some(bucket_upper(i));
        }
        assert_eq!(bucket_index(u64::MAX), BUCKETS - 1);
    }

    #[test]
    fn relative_error_is_bounded() {
        for v in [17u64, 1_000, 21_333_333, 42_666_666, 1 << 40] {
            let i = bucket_index(v);
            let width = bucket_upper(i) - bucket_lower(i) + 1;
            assert!(width as f64 <= v as f64 / SUB as f64 + 1.0, "{v}");
        }
    }

    #[test]
    fn quantiles_never_understate_and_cap_at_max() {
        let h = Histogram::new();
        for v in 1..=1000u64 {
            h.record(v * 1000);
        }
        let s = h.take();
        assert_eq!(s.count(), 1000);
        assert_eq!(s.max, 1_000_000);
        let p50 = s.quantile(0.5).unwrap();
        assert!((500_000..=500_000 + 500_000 / 16).contains(&p50), "{p50}");
        assert_eq!(s.quantile(1.0), Some(1_000_000));
        assert!(s.quantile(0.999).unwrap() >= 999_000);
        assert_eq!(h.take().count(), 0, "take resets");
    }

    #[test]
    fn snapshot_reports_quantum_range() {
        let m = Metrics::new();
        assert_eq!(m.take().quantum, None);
        for q in [1024, 2048, 512] {
            m.record_quantum(q);
        }
        let s = m.take();
        assert_eq!(s.quantum, Some((512, 512, 2048)));
        assert_eq!(s.frames, 3584);
    }
}
