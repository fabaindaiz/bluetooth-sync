//! Minimal real-time engine for the native-I/O proof of concept (d-7c8794-36dde5).
//!
//! Per speaker it does what `aurasync/motor.py` does with the ambience, the decorrelator
//! and the EQ switched off: mix L/R with `pan` (`(1-pan)/2·L + (1+pan)/2·R`), delay by an
//! **integer** number of samples, apply a gain and a mute, each change with a short ramp.
//!
//! The rules of the real-time callback (PipeWire `stream.h` l. 150) hold in
//! [`Engine::process`]: it never allocates, locks or does I/O. Parameters arrive through a
//! wait-free SPSC ring ([`rtrb`]) from a non-real-time thread, and the timing metrics
//! leave through atomics ([`metrics::Metrics`]).

pub mod command;
pub mod engine;
pub mod metrics;
pub mod ramp;

/// The ring the commands travel through, re-exported so callers use the same version.
pub use rtrb;

pub use command::{Command, CommandError, SpeakerUpdate};
pub use engine::{Engine, EngineConfig, SpeakerParams};
pub use metrics::{Histogram, HistogramSnapshot, Metrics, MetricsSnapshot};
