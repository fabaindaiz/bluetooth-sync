//! The engine: per speaker, pan → integer delay → gain × mute × master volume.
//!
//! Everything the callback touches is allocated in [`Engine::new`]. [`Engine::process`]
//! accepts any number of frames per call (the graph's quantum changes) and its output does
//! not depend on how the input is split into calls: every ramp advances once per sample.

use crate::command::{Command, SpeakerUpdate};
use crate::ramp::LinearRamp;

/// Pan speed, in units per second: the same as `motor.py` (`VELOCIDAD_SUAVE`, 0 to 1 in
/// 0.5 s), so a pan change is not a click.
pub const PAN_SPEED_PER_S: f32 = 2.0;
/// A gain or volume change is a linear ramp of this length.
pub const GAIN_RAMP_MS: f32 = 20.0;
/// Mute fade: the default of `mute_fade_ms` in `chain.py`.
pub const MUTE_FADE_MS: f32 = 50.0;
/// An integer delay cannot move smoothly, so a change fades out, jumps, and fades in.
pub const DELAY_FADE_MS: f32 = 20.0;
/// Frames per inner chunk: the master volume ramp is computed once per chunk into a
/// preallocated buffer and shared by every speaker.
const CHUNK: usize = 256;

/// The initial state of one speaker.
#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct SpeakerParams {
    /// -1 (left only) to +1 (right only).
    pub pan: f32,
    pub delay_samples: u32,
    pub gain_db: f32,
    pub muted: bool,
}

#[derive(Clone, Debug, PartialEq)]
pub struct EngineConfig {
    pub sample_rate: u32,
    /// The longest delay any speaker may take; sizes the delay lines.
    pub max_delay_samples: u32,
    pub speakers: Vec<SpeakerParams>,
    pub volume_db: f32,
    /// Slots in the command ring.
    pub command_capacity: usize,
}

impl Default for EngineConfig {
    fn default() -> Self {
        Self {
            sample_rate: 48_000,
            max_delay_samples: 48_000,
            speakers: Vec::new(),
            volume_db: 0.0,
            command_capacity: 256,
        }
    }
}

/// dB to a linear factor; `-inf` is exact silence.
pub fn db_to_gain(db: f32) -> f32 {
    if db == f32::NEG_INFINITY {
        0.0
    } else {
        10f32.powf(db / 20.0)
    }
}

fn ms_to_samples(ms: f32, rate: u32) -> u32 {
    (ms * rate as f32 / 1000.0).round() as u32
}

struct Speaker {
    pan: LinearRamp,
    gain: LinearRamp,
    mute: LinearRamp,
    /// Fades the speaker out and back in around a delay change.
    gate: LinearRamp,
    delay: usize,
    pending_delay: Option<usize>,
    line: Box<[f32]>,
    mask: usize,
    write: usize,
}

impl Speaker {
    fn new(p: &SpeakerParams, line_len: usize) -> Self {
        Self {
            pan: LinearRamp::new(p.pan),
            gain: LinearRamp::new(db_to_gain(p.gain_db)),
            mute: LinearRamp::new(if p.muted { 0.0 } else { 1.0 }),
            gate: LinearRamp::new(1.0),
            delay: p.delay_samples as usize,
            pending_delay: None,
            line: vec![0.0; line_len].into_boxed_slice(),
            mask: line_len - 1,
            write: 0,
        }
    }

    #[inline]
    fn process(&mut self, left: &[f32], right: &[f32], master: &[f32], out: &mut [f32], k: &Rates) {
        for ((&l, &r), (&m, o)) in left
            .iter()
            .zip(right)
            .zip(master.iter().zip(out.iter_mut()))
        {
            let pan = self.pan.advance();
            // The same expression as motor.py: (1 - pan) / 2 * L + (1 + pan) / 2 * R.
            let x = (1.0 - pan) / 2.0 * l + (1.0 + pan) / 2.0 * r;
            if let Some(d) = self.pending_delay
                && self.gate.is_settled()
                && self.gate.value() == 0.0
            {
                self.delay = d;
                self.pending_delay = None;
                self.gate.set(1.0, k.delay_fade);
            }
            self.line[self.write & self.mask] = x;
            let y = self.line[self.write.wrapping_sub(self.delay) & self.mask];
            self.write = self.write.wrapping_add(1);
            *o = y * self.gain.advance() * self.mute.advance() * self.gate.advance() * m;
        }
    }

    fn apply(&mut self, u: &SpeakerUpdate, k: &Rates, max_delay: usize) {
        if let Some(p) = u.pan {
            self.pan
                .set_rate_limited(p.clamp(-1.0, 1.0), k.pan_per_sample);
        }
        if let Some(g) = u.gain_db {
            self.gain.set(db_to_gain(g), k.gain_ramp);
        }
        if let Some(m) = u.muted {
            let target = if m { 0.0 } else { 1.0 };
            // A partial fade takes the proportional part of the full fade.
            let samples = ((target - self.mute.value()).abs() * k.mute_fade as f32).ceil() as u32;
            self.mute.set(target, samples);
        }
        if let Some(d) = u.delay_samples {
            let d = (d as usize).min(max_delay);
            if d != self.delay || self.pending_delay.is_some() {
                self.pending_delay = Some(d);
                let samples = (self.gate.value().abs() * k.delay_fade as f32).ceil() as u32;
                self.gate.set(0.0, samples);
            }
        }
    }
}

/// Ramp lengths in samples, computed once from the sample rate.
struct Rates {
    pan_per_sample: f32,
    gain_ramp: u32,
    mute_fade: u32,
    delay_fade: u32,
}

pub struct Engine {
    speakers: Vec<Speaker>,
    master: LinearRamp,
    master_buf: Box<[f32]>,
    commands: rtrb::Consumer<Command>,
    rates: Rates,
    max_delay: usize,
    applied: u64,
}

impl Engine {
    /// Builds the engine and the producer end of its command ring. Everything the
    /// real-time thread will touch is allocated here.
    pub fn new(config: &EngineConfig) -> (Self, rtrb::Producer<Command>) {
        let (producer, consumer) = rtrb::RingBuffer::new(config.command_capacity.max(1));
        let line_len = (config.max_delay_samples as usize + 1).next_power_of_two();
        let rate = config.sample_rate;
        let engine = Self {
            speakers: config
                .speakers
                .iter()
                .map(|p| {
                    let mut p = *p;
                    p.delay_samples = p.delay_samples.min(config.max_delay_samples);
                    Speaker::new(&p, line_len)
                })
                .collect(),
            master: LinearRamp::new(db_to_gain(config.volume_db)),
            master_buf: vec![0.0; CHUNK].into_boxed_slice(),
            commands: consumer,
            rates: Rates {
                pan_per_sample: PAN_SPEED_PER_S / rate as f32,
                gain_ramp: ms_to_samples(GAIN_RAMP_MS, rate),
                mute_fade: ms_to_samples(MUTE_FADE_MS, rate),
                delay_fade: ms_to_samples(DELAY_FADE_MS, rate),
            },
            max_delay: config.max_delay_samples as usize,
            applied: 0,
        };
        (engine, producer)
    }

    pub fn speakers(&self) -> usize {
        self.speakers.len()
    }

    pub fn max_delay_samples(&self) -> u32 {
        self.max_delay as u32
    }

    /// Commands applied since the start (for the log).
    pub fn commands_applied(&self) -> u64 {
        self.applied
    }

    /// The delay each speaker is using now, in samples.
    pub fn delays(&self) -> impl Iterator<Item = usize> + '_ {
        self.speakers.iter().map(|s| s.delay)
    }

    fn apply_commands(&mut self) {
        while let Ok(cmd) = self.commands.pop() {
            match cmd {
                Command::Speaker { index, update } => {
                    if let Some(s) = self.speakers.get_mut(index) {
                        s.apply(&update, &self.rates, self.max_delay);
                    }
                }
                Command::Volume { db } => {
                    let target = db_to_gain(db.min(crate::command::MAX_GAIN_DB));
                    self.master.set(target, self.rates.gain_ramp);
                }
            }
            self.applied += 1;
        }
    }

    /// Processes one call of the graph: a stereo input, one output per speaker.
    ///
    /// Returns the number of frames written: the shortest of the input channels and the
    /// outputs. Outputs beyond the number of speakers are left untouched. Never
    /// allocates, never blocks.
    pub fn process(&mut self, left: &[f32], right: &[f32], outputs: &mut [&mut [f32]]) -> usize {
        self.apply_commands();
        let n = outputs
            .iter()
            .take(self.speakers.len())
            .fold(left.len().min(right.len()), |m, o| m.min(o.len()));
        let mut start = 0;
        while start < n {
            let end = (start + CHUNK).min(n);
            let master = &mut self.master_buf[..end - start];
            for g in master.iter_mut() {
                *g = self.master.advance();
            }
            for (speaker, out) in self.speakers.iter_mut().zip(outputs.iter_mut()) {
                speaker.process(
                    &left[start..end],
                    &right[start..end],
                    master,
                    &mut out[start..end],
                    &self.rates,
                );
            }
            start = end;
        }
        n
    }
}
