//! `poc-pw`: the throwaway proof of concept of native PipeWire I/O for aurasync
//! (d-7c8794-36dde5, `docs/research/12-motor-de-audio-en-rust.md` §4).
//!
//! Live mode (Linux only) creates a virtual sink and an N-channel playback to the existing
//! combine-stream sink, both in one `node.link-group`, as `module-loopback.c` does, and
//! runs the minimal engine of `poc-dsp` inside the graph's real-time cycle. The other
//! modes need no PipeWire: `--dry-run` processes a WAV file, `combine-config` prints the
//! arguments for the output sink that the service would create.

mod config;
mod control;
mod dry_run;
mod report;

#[cfg(target_os = "linux")]
mod pw_io;

use std::path::PathBuf;
use std::process::ExitCode;

const USAGE: &str = "\
poc-pw: native PipeWire I/O proof of concept (aurasync, probes/17-e-s-nativa-rust)

USAGE:
  poc-pw [run] [CONFIG] [--target NAME] [--node-name NAME] [--description TEXT]
               [--log FILE] [--latency FRAMES/RATE]
  poc-pw --dry-run --in IN.wav --out OUT.wav [CONFIG] [--sizes 64,1024,2048,3000]
  poc-pw combine-config [CONFIG] [--name aurasync_salida]

CONFIG (the first that applies):
  --config FILE         this program's JSON (see config.example.json)
  --installation FILE   the service's installation (default ~/.config/aurasync/instalacion.json)

Live mode: one JSON command per line on stdin, e.g.
  {\"speaker\":0,\"pan\":0.7,\"delay_samples\":650,\"gain_db\":0,\"muted\":false}
  {\"volume_db\":-6}
and one JSON line per second to the log (default ./poc-pw-<unix time>.jsonl).
SIGINT or SIGTERM stops it cleanly.
";

#[derive(Debug)]
struct Args {
    command: String,
    config: Option<PathBuf>,
    installation: Option<PathBuf>,
    target: String,
    node_name: String,
    description: String,
    log: Option<PathBuf>,
    latency: Option<String>,
    input: Option<PathBuf>,
    output: Option<PathBuf>,
    sizes: Vec<usize>,
    combine_name: String,
}

fn parse_args(raw: Vec<String>) -> Result<Args, String> {
    let mut args = Args {
        command: "run".into(),
        config: None,
        installation: None,
        target: "aurasync_salida".into(),
        node_name: "aurasync_poc".into(),
        description: "aurasync PoC (Rust)".into(),
        log: None,
        latency: None,
        input: None,
        output: None,
        sizes: dry_run::DEFAULT_SIZES.to_vec(),
        combine_name: "aurasync_salida".into(),
    };
    let mut it = raw.into_iter().peekable();
    if let Some(first) = it.peek()
        && !first.starts_with("--")
    {
        args.command = it.next().unwrap_or_default();
    }
    while let Some(flag) = it.next() {
        let mut value = || it.next().ok_or_else(|| format!("{flag} needs a value"));
        match flag.as_str() {
            "--help" | "-h" => args.command = "help".into(),
            "--dry-run" => args.command = "dry-run".into(),
            "--config" => args.config = Some(value()?.into()),
            "--installation" => args.installation = Some(value()?.into()),
            "--target" => args.target = value()?,
            "--node-name" => args.node_name = value()?,
            "--description" => args.description = value()?,
            "--log" => args.log = Some(value()?.into()),
            "--latency" => args.latency = Some(value()?),
            "--in" => args.input = Some(value()?.into()),
            "--out" => args.output = Some(value()?.into()),
            "--name" => args.combine_name = value()?,
            "--sizes" => {
                args.sizes = value()?
                    .split(',')
                    .map(|s| {
                        s.trim()
                            .parse::<usize>()
                            .map_err(|e| format!("--sizes: {e}"))
                    })
                    .collect::<Result<_, _>>()?;
                if args.sizes.is_empty() || args.sizes.contains(&0) {
                    return Err("--sizes needs positive sizes".into());
                }
            }
            other => return Err(format!("unknown argument {other:?}")),
        }
    }
    Ok(args)
}

fn main() -> ExitCode {
    let args = match parse_args(std::env::args().skip(1).collect()) {
        Ok(a) => a,
        Err(e) => {
            eprintln!("poc-pw: {e}\n\n{USAGE}");
            return ExitCode::from(2);
        }
    };
    if args.command == "help" {
        print!("{USAGE}");
        return ExitCode::SUCCESS;
    }
    let config = match config::load(args.config.as_deref(), args.installation.as_deref()) {
        Ok(c) => c,
        Err(e) => {
            eprintln!("poc-pw: {e}");
            return ExitCode::from(2);
        }
    };
    let result = match args.command.as_str() {
        "dry-run" => match (&args.input, &args.output) {
            (Some(input), Some(output)) => dry_run::run(
                &config,
                &dry_run::DryRunOptions {
                    input: input.clone(),
                    output: output.clone(),
                    sizes: args.sizes.clone(),
                },
            )
            .map(|summary| println!("{summary}")),
            _ => Err("--dry-run needs --in and --out".into()),
        },
        "combine-config" => {
            config::combine_module_args(&config, &args.combine_name).map(|s| println!("{s}"))
        }
        "run" => live(&config, &args),
        other => Err(format!("unknown command {other:?}")),
    };
    match result {
        Ok(()) => ExitCode::SUCCESS,
        Err(e) => {
            eprintln!("poc-pw: {e}");
            ExitCode::FAILURE
        }
    }
}

#[cfg(target_os = "linux")]
fn live(config: &config::PocConfig, args: &Args) -> Result<(), String> {
    let log_path = args
        .log
        .clone()
        .unwrap_or_else(|| PathBuf::from(format!("poc-pw-{}.jsonl", report::unix_time() as u64)));
    pw_io::run(
        config,
        &pw_io::LiveOptions {
            node_name: args.node_name.clone(),
            description: args.description.clone(),
            target: args.target.clone(),
            log_path,
            latency: args.latency.clone(),
        },
    )
    .map_err(|e| e.to_string())
}

#[cfg(not(target_os = "linux"))]
fn live(_config: &config::PocConfig, _args: &Args) -> Result<(), String> {
    Err("live mode needs Linux with PipeWire; on this machine use --dry-run".into())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn parse(s: &str) -> Args {
        parse_args(s.split_whitespace().map(String::from).collect()).unwrap()
    }

    #[test]
    fn defaults_and_modes() {
        let a = parse("");
        assert_eq!(a.command, "run");
        assert_eq!(a.target, "aurasync_salida");
        assert_eq!(a.node_name, "aurasync_poc");
        assert_eq!(parse("--dry-run --in a.wav --out b.wav").command, "dry-run");
        assert_eq!(parse("combine-config --name x").combine_name, "x");
        assert_eq!(parse("--sizes 1,2").sizes, vec![1, 2]);
        assert!(parse_args(vec!["--bogus".into()]).is_err());
        assert!(parse_args(vec!["--target".into()]).is_err());
    }
}
