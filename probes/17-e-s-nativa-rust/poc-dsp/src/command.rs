//! The messages that travel from the control thread to the real-time thread.
//!
//! They are small `Copy` values, so pushing one into the ring and popping it in the
//! callback never allocates. Validation happens on the sending side ([`Command::validate`]):
//! the real-time thread only clamps, it never rejects.

use std::fmt;

/// Upper bound for a gain or the master volume, in dB.
pub const MAX_GAIN_DB: f32 = 12.0;

/// A partial update of one speaker: `None` leaves the field as it is.
#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct SpeakerUpdate {
    pub pan: Option<f32>,
    pub delay_samples: Option<u32>,
    pub gain_db: Option<f32>,
    pub muted: Option<bool>,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub enum Command {
    Speaker {
        index: usize,
        update: SpeakerUpdate,
    },
    /// The master volume, applied to every speaker after its own gain.
    Volume {
        db: f32,
    },
}

#[derive(Clone, Debug, PartialEq)]
pub enum CommandError {
    NoSuchSpeaker { index: usize, count: usize },
    PanOutOfRange(f32),
    DelayTooLong { requested: u32, max: u32 },
    GainOutOfRange(f32),
}

impl fmt::Display for CommandError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::NoSuchSpeaker { index, count } => {
                write!(f, "no speaker {index}: there are {count}")
            }
            Self::PanOutOfRange(p) => write!(f, "pan {p} is outside -1..1"),
            Self::DelayTooLong { requested, max } => {
                write!(f, "delay of {requested} samples exceeds the line ({max})")
            }
            Self::GainOutOfRange(g) => {
                write!(f, "gain {g} dB is not finite or above +{MAX_GAIN_DB} dB")
            }
        }
    }
}

impl std::error::Error for CommandError {}

fn check_gain(db: f32) -> Result<(), CommandError> {
    // -inf dB is a legitimate "silence"; NaN and large boosts are not.
    if db.is_nan() || db > MAX_GAIN_DB {
        return Err(CommandError::GainOutOfRange(db));
    }
    Ok(())
}

impl Command {
    /// Checks a command against the engine it is meant for, before it is sent.
    pub fn validate(&self, speakers: usize, max_delay: u32) -> Result<(), CommandError> {
        match *self {
            Command::Speaker { index, update } => {
                if index >= speakers {
                    return Err(CommandError::NoSuchSpeaker {
                        index,
                        count: speakers,
                    });
                }
                if let Some(p) = update.pan
                    && !(-1.0..=1.0).contains(&p)
                {
                    return Err(CommandError::PanOutOfRange(p));
                }
                if let Some(d) = update.delay_samples
                    && d > max_delay
                {
                    return Err(CommandError::DelayTooLong {
                        requested: d,
                        max: max_delay,
                    });
                }
                if let Some(g) = update.gain_db {
                    check_gain(g)?;
                }
                Ok(())
            }
            Command::Volume { db } => check_gain(db),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rejects_what_the_engine_cannot_do() {
        let pan = Command::Speaker {
            index: 0,
            update: SpeakerUpdate {
                pan: Some(1.5),
                ..Default::default()
            },
        };
        assert_eq!(pan.validate(3, 100), Err(CommandError::PanOutOfRange(1.5)));
        let delay = Command::Speaker {
            index: 0,
            update: SpeakerUpdate {
                delay_samples: Some(101),
                ..Default::default()
            },
        };
        assert!(matches!(
            delay.validate(3, 100),
            Err(CommandError::DelayTooLong { .. })
        ));
        let speaker = Command::Speaker {
            index: 3,
            update: SpeakerUpdate::default(),
        };
        assert!(matches!(
            speaker.validate(3, 100),
            Err(CommandError::NoSuchSpeaker { .. })
        ));
        assert!(Command::Volume { db: f32::NAN }.validate(3, 100).is_err());
        assert!(
            Command::Volume {
                db: f32::NEG_INFINITY
            }
            .validate(3, 100)
            .is_ok()
        );
    }
}
