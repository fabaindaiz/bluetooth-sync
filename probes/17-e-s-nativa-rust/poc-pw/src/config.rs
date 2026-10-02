//! The initial configuration: speakers, pan, delays and gains.
//!
//! Two sources, the first that applies:
//! - `--config FILE`: this proof of concept's own JSON (see `config.example.json`);
//! - the installation the service uses, `~/.config/aurasync/instalacion.json` (respecting
//!   `XDG_CONFIG_HOME`), converting `retardo_ms` to whole samples the same way the
//!   service computes the delay it applies (`Motor.retardos_efectivos_ms` in `motor.py`):
//!   `retardo_ms + ambiente × retardo_traseros_ms`.

use std::path::{Path, PathBuf};

use poc_dsp::{EngineConfig, SpeakerParams};
use serde::Deserialize;

pub const DEFAULT_RATE: u32 = 48_000;

#[derive(Clone, Debug, PartialEq)]
pub struct SpeakerConfig {
    pub name: String,
    /// The PipeWire node of the speaker (`bluez_output.…`), when known. Only used to print
    /// the combine-stream configuration.
    pub sink: Option<String>,
    pub params: SpeakerParams,
}

#[derive(Clone, Debug, PartialEq)]
pub struct PocConfig {
    pub rate: u32,
    pub max_delay_samples: u32,
    pub volume_db: f32,
    pub speakers: Vec<SpeakerConfig>,
    /// Where it came from, for the log.
    pub source: String,
}

impl PocConfig {
    pub fn engine_config(&self) -> EngineConfig {
        EngineConfig {
            sample_rate: self.rate,
            max_delay_samples: self.max_delay_samples,
            speakers: self.speakers.iter().map(|s| s.params).collect(),
            volume_db: self.volume_db,
            command_capacity: 256,
        }
    }

    pub fn names(&self) -> Vec<String> {
        self.speakers.iter().map(|s| s.name.clone()).collect()
    }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct ConfigFile {
    #[serde(default)]
    rate: Option<u32>,
    #[serde(default)]
    max_delay_samples: Option<u32>,
    #[serde(default)]
    volume_db: f32,
    speakers: Vec<ConfigSpeaker>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct ConfigSpeaker {
    name: String,
    #[serde(default)]
    sink: Option<String>,
    #[serde(default)]
    pan: f32,
    #[serde(default)]
    delay_samples: u32,
    #[serde(default)]
    gain_db: f32,
    #[serde(default)]
    muted: bool,
}

/// The fields of `aurasync.config.Instalacion` this program needs; the rest are ignored.
#[derive(Deserialize)]
struct Installation {
    parlantes: Vec<Parlante>,
    #[serde(default = "default_rear_delay")]
    retardo_traseros_ms: f64,
}

fn default_rear_delay() -> f64 {
    12.0
}

#[derive(Deserialize)]
struct Parlante {
    nombre: String,
    sink: String,
    #[serde(default)]
    retardo_ms: f64,
    #[serde(default)]
    ganancia_db: f64,
    #[serde(default)]
    pan: f64,
    #[serde(default)]
    ambiente: f64,
}

fn max_delay_for(speakers: &[SpeakerConfig], rate: u32) -> u32 {
    // At least one second, and room for the largest configured delay plus one second of
    // live changes.
    let largest = speakers
        .iter()
        .map(|s| s.params.delay_samples)
        .max()
        .unwrap_or(0);
    rate.max(largest.saturating_add(rate))
}

fn check(config: PocConfig) -> Result<PocConfig, String> {
    if config.speakers.is_empty() {
        return Err("the configuration has no speakers".into());
    }
    for s in &config.speakers {
        if !(-1.0..=1.0).contains(&s.params.pan) {
            return Err(format!(
                "speaker {:?}: pan {} is outside -1..1",
                s.name, s.params.pan
            ));
        }
        if !s.params.gain_db.is_finite() {
            return Err(format!("speaker {:?}: gain is not finite", s.name));
        }
    }
    Ok(config)
}

/// From this program's own JSON.
pub fn from_config_json(text: &str, source: &str) -> Result<PocConfig, String> {
    let file: ConfigFile = serde_json::from_str(text).map_err(|e| format!("{source}: {e}"))?;
    let rate = file.rate.unwrap_or(DEFAULT_RATE);
    let speakers: Vec<SpeakerConfig> = file
        .speakers
        .into_iter()
        .map(|s| SpeakerConfig {
            name: s.name,
            sink: s.sink,
            params: SpeakerParams {
                pan: s.pan,
                delay_samples: s.delay_samples,
                gain_db: s.gain_db,
                muted: s.muted,
            },
        })
        .collect();
    let max_delay_samples = file
        .max_delay_samples
        .unwrap_or_else(|| max_delay_for(&speakers, rate));
    if let Some(s) = speakers
        .iter()
        .find(|s| s.params.delay_samples > max_delay_samples)
    {
        return Err(format!(
            "speaker {:?}: delay {} exceeds max_delay_samples {max_delay_samples}",
            s.name, s.params.delay_samples
        ));
    }
    check(PocConfig {
        rate,
        max_delay_samples,
        volume_db: file.volume_db,
        speakers,
        source: source.to_string(),
    })
}

/// Milliseconds to whole samples, to the nearest (half away from zero). Negative delays
/// do not exist in the installation; they become 0.
pub fn ms_to_samples(ms: f64, rate: u32) -> u32 {
    (ms.max(0.0) * rate as f64 / 1000.0).round() as u32
}

/// From the service's `instalacion.json`.
pub fn from_installation_json(text: &str, source: &str, rate: u32) -> Result<PocConfig, String> {
    let inst: Installation = serde_json::from_str(text).map_err(|e| format!("{source}: {e}"))?;
    let speakers: Vec<SpeakerConfig> = inst
        .parlantes
        .into_iter()
        .map(|p| SpeakerConfig {
            params: SpeakerParams {
                pan: p.pan as f32,
                delay_samples: ms_to_samples(
                    p.retardo_ms + p.ambiente * inst.retardo_traseros_ms,
                    rate,
                ),
                gain_db: p.ganancia_db as f32,
                muted: false,
            },
            name: p.nombre,
            sink: Some(p.sink),
        })
        .collect();
    let max_delay_samples = max_delay_for(&speakers, rate);
    check(PocConfig {
        rate,
        max_delay_samples,
        volume_db: 0.0,
        speakers,
        source: source.to_string(),
    })
}

/// `$XDG_CONFIG_HOME/aurasync/instalacion.json`, or `~/.config/…`, like
/// `aurasync.config.ruta_por_defecto`.
pub fn default_installation_path() -> Option<PathBuf> {
    let base = match std::env::var_os("XDG_CONFIG_HOME") {
        Some(v) if !v.is_empty() => PathBuf::from(v),
        _ => PathBuf::from(std::env::var_os("HOME")?).join(".config"),
    };
    Some(base.join("aurasync").join("instalacion.json"))
}

/// Resolves `--config` / `--installation` into a configuration.
pub fn load(config: Option<&Path>, installation: Option<&Path>) -> Result<PocConfig, String> {
    if let Some(path) = config {
        let text = std::fs::read_to_string(path).map_err(|e| format!("{}: {e}", path.display()))?;
        return from_config_json(&text, &path.display().to_string());
    }
    let path = match installation {
        Some(p) => p.to_path_buf(),
        None => default_installation_path().ok_or("cannot find the home directory")?,
    };
    match std::fs::read_to_string(&path) {
        Ok(text) => from_installation_json(&text, &path.display().to_string(), DEFAULT_RATE),
        Err(e) => Err(format!(
            "no --config given and {} could not be read ({e})",
            path.display()
        )),
    }
}

/// The arguments for `pw-cli -m load-module libpipewire-module-combine-stream`, character
/// for character what `ReproductorCombinado.configuracion()` in `sonido.py` builds, so that
/// session B can run the same output sink without the service.
pub fn combine_module_args(config: &PocConfig, name: &str) -> Result<String, String> {
    let mut rules = Vec::new();
    let mut channels = Vec::new();
    for (i, s) in config.speakers.iter().enumerate() {
        let sink = s
            .sink
            .as_deref()
            .ok_or_else(|| format!("speaker {:?} has no `sink`", s.name))?;
        let channel = format!("AUX{i}");
        rules.push(format!(
            "{{ matches = [ {{ node.name = \"{sink}\" }} ] actions = {{ create-stream = {{ \
             combine.audio.position = [ {channel} ] audio.position = [ MONO ] \
             node.dont-reconnect = true node.dont-move = true }} }} }}"
        ));
        channels.push(channel);
    }
    Ok(format!(
        "{{ combine.mode = sink node.name = \"{name}\" node.description = \"aurasync (salida a los parlantes)\" \
         combine.latency-compensate = false \
         combine.props = {{ audio.position = [ {} ] node.dont-move = true }} \
         stream.rules = [ {} ] }}",
        channels.join(" "),
        rules.join(" ")
    ))
}

#[cfg(test)]
mod tests {
    use super::*;

    const INSTALLATION: &str = r#"{
      "parlantes": [
        {"nombre": "Red", "sink": "bluez_output.AA.1", "x": null, "y": null,
         "retardo_ms": 13.52, "ganancia_db": -1.5, "pan": -1.0, "ambiente": 0.0,
         "ecualizacion_db": null, "tipo": "go4"},
        {"nombre": "Blue", "sink": "bluez_output.BB.1", "retardo_ms": 0.0,
         "ganancia_db": 0.0, "pan": 1.0, "ambiente": 0.5},
        {"nombre": "Black", "sink": "bluez_output.CC.1", "retardo_ms": 0.01}
      ],
      "oyente_x": 0.0, "oyente_y": 0.0, "retardo_traseros_ms": 12.0
    }"#;

    #[test]
    fn installation_delays_become_whole_samples_with_the_haas_part() {
        let c = from_installation_json(INSTALLATION, "test", 48_000).unwrap();
        let d: Vec<u32> = c.speakers.iter().map(|s| s.params.delay_samples).collect();
        // 13.52 ms = 648.96 → 649; 0 + 0.5 × 12 ms = 288; 0.01 ms = 0.48 → 0.
        assert_eq!(d, vec![649, 288, 0]);
        assert_eq!(c.speakers[0].params.gain_db, -1.5);
        assert_eq!(c.speakers[1].params.pan, 1.0);
        assert_eq!(c.names(), vec!["Red", "Blue", "Black"]);
        assert!(c.max_delay_samples >= 649 + 48_000);
    }

    #[test]
    fn own_config_rejects_unknown_fields_and_bad_pan() {
        let ok = r#"{"speakers": [{"name": "A", "pan": 0.7, "delay_samples": 650}]}"#;
        let c = from_config_json(ok, "t").unwrap();
        assert_eq!(c.speakers[0].params.delay_samples, 650);
        assert!(from_config_json(r#"{"speakers": [{"name": "A", "pann": 1}]}"#, "t").is_err());
        assert!(from_config_json(r#"{"speakers": [{"name": "A", "pan": 2}]}"#, "t").is_err());
        assert!(from_config_json(r#"{"speakers": []}"#, "t").is_err());
    }

    /// Generated with `ReproductorCombinado(["bluez_output.AA.1", "bluez_output.BB.1"],
    /// nombre="aurasync_salida").configuracion()` from `host/src/aurasync/sonido.py`
    /// (2026-10-02).
    const FROM_PYTHON: &str = include_str!("../tests/combine-config-from-python.txt");

    #[test]
    fn combine_config_matches_the_service() {
        let c = from_config_json(
            r#"{"speakers": [{"name": "A", "sink": "bluez_output.AA.1"},
                             {"name": "B", "sink": "bluez_output.BB.1"}]}"#,
            "t",
        )
        .unwrap();
        assert_eq!(
            combine_module_args(&c, "aurasync_salida").unwrap(),
            FROM_PYTHON.trim_end()
        );
    }
}
