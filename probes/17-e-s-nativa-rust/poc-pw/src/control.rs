//! Live control: one JSON object per line on stdin, one JSON answer per line on stdout.
//!
//! ```text
//! {"speaker":0,"pan":0.7,"delay_samples":650,"gain_db":0,"muted":false}
//! {"speaker":"Red","muted":true}
//! {"volume_db":-6}
//! ```
//!
//! Fields that are missing are left as they are. A speaker is addressed by its index or by
//! its name. Lines are parsed and validated here, on a normal thread; only validated
//! commands are pushed into the ring that the real-time thread drains.

use std::io::BufRead;
use std::thread::JoinHandle;

use poc_dsp::rtrb;
use poc_dsp::{Command, SpeakerUpdate};
use serde::Deserialize;

#[derive(Deserialize)]
#[serde(untagged)]
enum SpeakerRef {
    Index(usize),
    Name(String),
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Line {
    speaker: Option<SpeakerRef>,
    pan: Option<f32>,
    delay_samples: Option<u32>,
    gain_db: Option<f32>,
    muted: Option<bool>,
    volume_db: Option<f32>,
}

/// Parses and validates one line into the commands it asks for (at most two: a speaker
/// update and a volume).
pub fn parse_line(line: &str, names: &[String], max_delay: u32) -> Result<Vec<Command>, String> {
    let l: Line = serde_json::from_str(line).map_err(|e| e.to_string())?;
    let mut commands = Vec::new();
    let update = SpeakerUpdate {
        pan: l.pan,
        delay_samples: l.delay_samples,
        gain_db: l.gain_db,
        muted: l.muted,
    };
    match l.speaker {
        Some(r) => {
            let index = match r {
                SpeakerRef::Index(i) => i,
                SpeakerRef::Name(n) => names
                    .iter()
                    .position(|x| *x == n)
                    .ok_or_else(|| format!("no speaker named {n:?}; there are {names:?}"))?,
            };
            commands.push(Command::Speaker { index, update });
        }
        None if update != SpeakerUpdate::default() => {
            return Err("pan, delay_samples, gain_db and muted need a \"speaker\"".into());
        }
        None => {}
    }
    if let Some(db) = l.volume_db {
        commands.push(Command::Volume { db });
    }
    if commands.is_empty() {
        return Err("nothing to do".into());
    }
    for c in &commands {
        c.validate(names.len(), max_delay)
            .map_err(|e| e.to_string())?;
    }
    Ok(commands)
}

/// Reads stdin until it closes, pushing commands into the ring. The answers go to stdout,
/// so a script can wait for them.
#[cfg_attr(not(target_os = "linux"), allow(dead_code))] // used by the live mode only
pub fn spawn_stdin(
    mut ring: rtrb::Producer<Command>,
    names: Vec<String>,
    max_delay: u32,
) -> std::io::Result<JoinHandle<()>> {
    std::thread::Builder::new()
        .name("poc-control".into())
        .spawn(move || {
            for line in std::io::stdin().lock().lines() {
                let Ok(line) = line else { break };
                if line.trim().is_empty() {
                    continue;
                }
                let answer = match parse_line(&line, &names, max_delay) {
                    Ok(commands) => {
                        let mut sent = 0;
                        for c in &commands {
                            if ring.push(*c).is_ok() {
                                sent += 1;
                            }
                        }
                        if sent == commands.len() {
                            serde_json::json!({"ok": true, "commands": sent})
                        } else {
                            serde_json::json!({"ok": false, "error": "command ring full", "commands": sent})
                        }
                    }
                    Err(e) => serde_json::json!({"ok": false, "error": e}),
                };
                println!("{answer}");
            }
        })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn names() -> Vec<String> {
        vec!["Red".into(), "Blue".into(), "Black".into()]
    }

    #[test]
    fn the_example_line_is_one_speaker_command() {
        let c = parse_line(
            r#"{"speaker":0,"pan":0.7,"delay_samples":650,"gain_db":0,"muted":false}"#,
            &names(),
            48_000,
        )
        .unwrap();
        assert_eq!(
            c,
            vec![Command::Speaker {
                index: 0,
                update: SpeakerUpdate {
                    pan: Some(0.7),
                    delay_samples: Some(650),
                    gain_db: Some(0.0),
                    muted: Some(false),
                },
            }]
        );
    }

    #[test]
    fn names_volume_and_errors() {
        let c = parse_line(
            r#"{"speaker":"Blue","muted":true,"volume_db":-6}"#,
            &names(),
            10,
        )
        .unwrap();
        assert_eq!(c.len(), 2);
        assert!(matches!(c[0], Command::Speaker { index: 1, .. }));
        assert_eq!(c[1], Command::Volume { db: -6.0 });
        assert!(parse_line(r#"{"speaker":"Green","muted":true}"#, &names(), 10).is_err());
        assert!(parse_line(r#"{"pan":0.5}"#, &names(), 10).is_err());
        assert!(parse_line(r#"{"speaker":0,"delay_samples":11}"#, &names(), 10).is_err());
        assert!(parse_line(r#"{"speaker":0,"gain":1}"#, &names(), 10).is_err());
        assert!(parse_line(r#"{}"#, &names(), 10).is_err());
    }
}
