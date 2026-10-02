//! Native PipeWire I/O (Linux only), in the pattern of `module-loopback.c` (PipeWire
//! 1.6.9, l. 341-443 and 720-770):
//!
//! - a **capture** stream with `media.class=Audio/Sink`: applications see it as an output
//!   device and play into it;
//! - a **playback** stream of N channels (`AUX0…`), one per speaker, with `target.object`
//!   set to the combine-stream sink that already exists (`aurasync_salida`);
//! - both with `RT_PROCESS` and the same `node.group` and `node.link-group`, so that the
//!   graph puts them under the same driver as the combine-stream and the Bluetooth sinks
//!   (`context.c` l. 1049-1063, `module-combine-stream.c` l. 1606-1610);
//! - the playback with `TRIGGER`; the capture with `ASYNC`, and its `process` only calls
//!   `trigger_process` on the playback. The playback's `process` takes the newest capture
//!   buffer, runs the engine, and queues both buffers.
//!
//! Nothing here sets a default sink. The nodes live in this process: when it exits, even
//! by `kill -9`, the server destroys them with the client.

use std::cell::RefCell;
use std::error::Error;
use std::ffi::{CStr, c_void};
use std::path::PathBuf;
use std::ptr;
use std::rc::Rc;
use std::sync::Arc;
use std::sync::atomic::{AtomicPtr, Ordering};
use std::time::{Duration, Instant};

use assert_no_alloc::{AllocDisabler, assert_no_alloc, violation_count};
use pipewire as pw;
use pw::buffer::Buffer;
use pw::properties::properties;
use pw::spa;
use pw::spa::buffer::Data;
use pw::spa::pod::Pod;
use pw::stream::{Stream, StreamFlags, StreamRc};
use serde_json::{Value, json};

use poc_dsp::{Engine, Metrics, MetricsSnapshot};

use crate::config::PocConfig;
use crate::control;
use crate::report::{self, Logger};

/// Counts (never aborts on) allocations inside the guarded real-time callbacks, in debug
/// and release builds; the count goes to the log as `rt_alloc_violations`.
#[global_allocator]
static ALLOCATOR: AllocDisabler = AllocDisabler;

/// Speakers the playback stream can carry (one planar buffer per channel).
const MAX_CHANNELS: usize = 16;
/// `SPA_IO_CLOCK_FLAG_XRUN_RECOVER`, `spa/node/io.h` l. 136 (1.6.9): set on the cycle after
/// a node of this graph missed its deadline. A `#define`, so it is not relied upon from
/// the generated bindings.
const CLOCK_FLAG_XRUN_RECOVER: u32 = 1 << 1;

type PositionPtr = Arc<AtomicPtr<spa::sys::spa_io_position>>;

pub struct LiveOptions {
    pub node_name: String,
    pub description: String,
    pub target: String,
    pub log_path: PathBuf,
    /// `node.latency` for both streams (e.g. `1024/48000`); `None` lets the graph decide.
    pub latency: Option<String>,
}

/// What the capture callback needs.
struct CaptureRt {
    playback: StreamRc,
    metrics: Arc<Metrics>,
}

/// What the playback callback needs. The engine lives here and is only touched from the
/// real-time thread.
struct PlaybackRt {
    engine: Engine,
    capture: StreamRc,
    metrics: Arc<Metrics>,
    position: PositionPtr,
    channels: usize,
    last_start: u64,
}

/// CLOCK_MONOTONIC in nanoseconds: the clock of `spa_io_clock.nsec` and `pw_time.now`.
fn monotonic_ns() -> u64 {
    let mut ts = libc::timespec {
        tv_sec: 0,
        tv_nsec: 0,
    };
    // SAFETY: clock_gettime only writes `ts`, which is valid and local. CLOCK_MONOTONIC
    // always exists on Linux. It is a vDSO call: no allocation, no lock, real-time safe.
    unsafe { libc::clock_gettime(libc::CLOCK_MONOTONIC, &mut ts) };
    ts.tv_sec as u64 * 1_000_000_000 + ts.tv_nsec as u64
}

/// The fields of the driver's clock this program reads.
#[derive(Clone, Copy)]
struct Clock {
    id: u32,
    nsec: u64,
    duration: u64,
    rate_denom: u32,
    flags: u32,
}

/// Mirrors `io_changed(SPA_IO_Position)` into an atomic the callbacks can read.
fn store_position(position: &PositionPtr, id: u32, area: *mut c_void) {
    if id == spa::sys::SPA_IO_Position {
        position.store(area.cast(), Ordering::Release);
    }
}

fn read_clock(position: &PositionPtr) -> Option<Clock> {
    let p = position.load(Ordering::Acquire);
    if p.is_null() {
        return None;
    }
    // SAFETY: `p` came from `io_changed(SPA_IO_Position)` and PipeWire keeps the area
    // mapped until it sends another `io_changed` for it (mirrored in the atomic); this is
    // the same assumption `module-loopback.c` makes with its plain pointer. The area is
    // written by the driver, maybe from another process: volatile reads, and a torn read
    // only skews one sample of a metric.
    unsafe {
        let c = ptr::addr_of!((*p).clock);
        Some(Clock {
            id: ptr::read_volatile(ptr::addr_of!((*c).id)),
            nsec: ptr::read_volatile(ptr::addr_of!((*c).nsec)),
            duration: ptr::read_volatile(ptr::addr_of!((*c).duration)),
            rate_denom: ptr::read_volatile(ptr::addr_of!((*c).rate.denom)),
            flags: ptr::read_volatile(ptr::addr_of!((*c).flags)),
        })
    }
}

/// The driver clock's name (`spa_io_clock.name`), for the log. Not for the RT thread.
fn clock_name(position: &PositionPtr) -> Option<String> {
    let p = position.load(Ordering::Acquire);
    if p.is_null() {
        return None;
    }
    // SAFETY: as in `read_clock`; the 64 bytes are copied out before being interpreted,
    // and the conversion does not assume a terminating NUL.
    let raw: [std::os::raw::c_char; 64] =
        unsafe { ptr::read_volatile(ptr::addr_of!((*p).clock.name)) };
    let bytes: Vec<u8> = raw.iter().map(|&c| c as u8).collect();
    let end = bytes.iter().position(|&b| b == 0).unwrap_or(bytes.len());
    Some(String::from_utf8_lossy(&bytes[..end]).into_owned())
}

/// The valid samples of one input plane (planar F32P: one plane per channel).
fn input_plane(d: &mut Data) -> Option<&[f32]> {
    let maxsize = d.as_raw().maxsize as usize;
    let chunk = d.chunk();
    let offset = (chunk.offset() as usize).min(maxsize);
    let size = (chunk.size() as usize).min(maxsize - offset);
    let bytes: &[u8] = d.data()?;
    bytemuck::try_cast_slice(&bytes[offset..offset + size]).ok()
}

/// The whole writable area of one output plane.
fn output_plane(d: &mut Data) -> Option<&mut [f32]> {
    bytemuck::try_cast_slice_mut(d.data()?).ok()
}

fn set_chunk(d: &mut Data, frames: usize) {
    let chunk = d.chunk_mut();
    *chunk.offset_mut() = 0;
    *chunk.size_mut() = (frames * size_of::<f32>()) as u32;
    *chunk.stride_mut() = size_of::<f32>() as i32;
}

/// Writes silence to every plane; returns the frames written.
fn silence(output: &mut Buffer<'_>, frames: usize) -> usize {
    let mut written = frames;
    for d in output.datas_mut() {
        let n = match output_plane(d) {
            Some(p) => {
                let n = frames.min(p.len());
                p[..n].fill(0.0);
                n
            }
            None => 0,
        };
        set_chunk(d, n);
        written = written.min(n);
    }
    written
}

/// Runs the engine from the input buffer into the output buffer. `None` if a buffer does
/// not have the layout that was negotiated.
fn render(
    engine: &mut Engine,
    input: &mut Buffer<'_>,
    output: &mut Buffer<'_>,
    channels: usize,
) -> Option<usize> {
    let [left, right, ..] = input.datas_mut() else {
        return None;
    };
    let (left, right) = (input_plane(left)?, input_plane(right)?);
    let planes = output.datas_mut();
    if planes.len() < channels {
        return None;
    }
    let mut outs: [&mut [f32]; MAX_CHANNELS] = Default::default();
    for (slot, d) in outs.iter_mut().zip(planes.iter_mut()).take(channels) {
        *slot = output_plane(d)?;
    }
    let frames = engine.process(left, right, &mut outs[..channels]);
    for (i, d) in planes.iter_mut().enumerate() {
        if i >= channels
            && let Some(p) = output_plane(d)
        {
            let n = frames.min(p.len());
            p[..n].fill(0.0);
        }
        set_chunk(d, frames);
    }
    Some(frames)
}

/// Frames to write when there is nothing to render: what the graph asked for.
fn fallback_frames(output: &Buffer<'_>, clock: Option<Clock>) -> usize {
    match output.requested() {
        0 => clock.map_or(0, |c| c.duration as usize),
        n => n as usize,
    }
}

/// The capture side only starts the playback, like `capture_process` in module-loopback.
fn capture_process(stream: &Stream, rt: &mut CaptureRt) {
    assert_no_alloc(|| {
        if rt.playback.trigger_process().is_err() {
            Metrics::bump(&rt.metrics.trigger_failed);
            // Nobody will read this cycle's input: give the buffers back.
            while let Some(buffer) = stream.dequeue_buffer() {
                drop(buffer);
            }
        }
    });
}

fn playback_cycle(stream: &Stream, rt: &mut PlaybackRt, clock: Option<Clock>) {
    // Keep only the newest capture buffer. Replacing `input` drops the older one, and
    // dropping a `Buffer` queues it back (module-loopback.c l. 365-374 does it by hand).
    let mut input = None;
    while let Some(b) = rt.capture.dequeue_buffer() {
        input = Some(b);
    }
    let Some(mut output) = stream.dequeue_buffer() else {
        Metrics::bump(&rt.metrics.no_output);
        return;
    };
    let wanted = fallback_frames(&output, clock);
    let frames = match input.as_mut() {
        Some(inbuf) => match render(&mut rt.engine, inbuf, &mut output, rt.channels) {
            Some(n) => n,
            None => {
                Metrics::bump(&rt.metrics.bad_buffer);
                silence(&mut output, wanted)
            }
        },
        None => {
            Metrics::bump(&rt.metrics.no_input);
            silence(&mut output, wanted)
        }
    };
    rt.metrics.record_quantum(frames as u64);
    // Capture first, then playback, as module-loopback queues them.
    drop(input);
    drop(output);
}

fn playback_process(stream: &Stream, rt: &mut PlaybackRt) {
    let start = monotonic_ns();
    let clock = read_clock(&rt.position);
    assert_no_alloc(|| playback_cycle(stream, rt, clock));
    let end = monotonic_ns();
    let m = &rt.metrics;
    m.callback_ns.record(end.saturating_sub(start));
    if rt.last_start != 0 {
        m.period_ns.record(start.saturating_sub(rt.last_start));
    }
    rt.last_start = start;
    if let Some(c) = clock {
        if c.nsec != 0 && start >= c.nsec {
            m.wakeup_ns.record(start - c.nsec);
        }
        if c.flags & CLOCK_FLAG_XRUN_RECOVER != 0 {
            Metrics::bump(&m.graph_xrun_flags);
        }
    }
    Metrics::bump(&m.callbacks);
    m.commands_applied
        .store(rt.engine.commands_applied(), Ordering::Relaxed);
    m.rt_alloc_violations
        .store(u64::from(violation_count()), Ordering::Relaxed);
}

/// An `EnumFormat` pod: planar f32 (the graph's own DSP format, so the adapter does not
/// convert), fixed rate and channel positions.
fn format_pod(rate: u32, positions: &[u32]) -> Result<Vec<u8>, Box<dyn Error>> {
    let mut info = spa::param::audio::AudioInfoRaw::new();
    info.set_format(spa::param::audio::AudioFormat::F32P);
    info.set_rate(rate);
    info.set_channels(positions.len() as u32);
    let mut position = [0u32; spa::param::audio::MAX_CHANNELS];
    position[..positions.len()].copy_from_slice(positions);
    info.set_position(position);
    let (cursor, _) = spa::pod::serialize::PodSerializer::serialize(
        std::io::Cursor::new(Vec::new()),
        &spa::pod::Value::Object(spa::pod::Object {
            type_: spa::sys::SPA_TYPE_OBJECT_Format,
            id: spa::sys::SPA_PARAM_EnumFormat,
            properties: info.into(),
        }),
    )
    .map_err(|e| format!("cannot build the format: {e:?}"))?;
    Ok(cursor.into_inner())
}

/// The negotiated format, for the console.
fn describe_format(id: u32, param: Option<&Pod>) -> Option<String> {
    let param = param?;
    if id != spa::sys::SPA_PARAM_Format {
        return None;
    }
    let mut info = spa::param::audio::AudioInfoRaw::new();
    info.parse(param).ok()?;
    Some(format!(
        "{:?}, {} Hz, {} channels",
        info.format(),
        info.rate(),
        info.channels()
    ))
}

fn stream_view(stream: &Stream, position: &PositionPtr) -> Value {
    let time = stream.time().ok().map(|t| {
        let rate = t.rate();
        let delay_ms = if rate.denom > 0 {
            Some((t.delay() as f64 * rate.num as f64 / rate.denom as f64 * 1e5).round() / 100.0)
        } else {
            None
        };
        json!({
            "now_ns": t.now(),
            "ticks": t.ticks(),
            "delay": t.delay(),
            "delay_ms": delay_ms,
            "queued": t.queued(),
            "buffered": t.buffered(),
            "rate": format!("{}/{}", rate.num, rate.denom),
        })
    });
    let driver = read_clock(position).map(|c| {
        json!({
            "clock_id": c.id,
            "clock_name": clock_name(position),
            "duration": c.duration,
            "rate": c.rate_denom,
        })
    });
    json!({
        "node_id": stream.node_id(),
        "state": format!("{:?}", stream.state()),
        "time": time,
        "driver": driver,
    })
}

/// Whether both streams follow the same driver clock: the in-process view of what
/// `pw-top` shows. `None` until both have a position.
fn same_driver(a: &PositionPtr, b: &PositionPtr) -> Option<bool> {
    Some(read_clock(a)?.id == read_clock(b)?.id)
}

/// Blocks SIGINT and SIGTERM in this thread before any other thread exists, so that every
/// thread inherits the block and the signals only arrive through the main loop's signalfd.
/// PipeWire's `impl_signalfd_create` blocks the signal only in the calling thread
/// (`spa/plugins/support/system.c` l. 222-236, 1.6.9): a data-loop thread started before
/// it would otherwise take the default action and kill the process uncleanly.
fn block_termination_signals() -> Result<(), Box<dyn Error>> {
    // SAFETY: sigemptyset and sigaddset only write the local, zero-initialised set;
    // pthread_sigmask only reads it and changes this thread's mask.
    let rc = unsafe {
        let mut set: libc::sigset_t = std::mem::zeroed();
        libc::sigemptyset(&mut set);
        libc::sigaddset(&mut set, libc::SIGINT);
        libc::sigaddset(&mut set, libc::SIGTERM);
        libc::pthread_sigmask(libc::SIG_BLOCK, &set, ptr::null_mut())
    };
    if rc != 0 {
        return Err(format!("pthread_sigmask failed ({rc})").into());
    }
    Ok(())
}

fn library_version() -> String {
    // SAFETY: pw_get_library_version returns a pointer to a static NUL-terminated string.
    unsafe { CStr::from_ptr(pw::sys::pw_get_library_version()) }
        .to_string_lossy()
        .into_owned()
}

fn start_line(config: &PocConfig, opts: &LiveOptions, group: &str) -> Value {
    json!({
        "kind": "start",
        "t": report::unix_time(),
        "pid": std::process::id(),
        "libpipewire": library_version(),
        "node_name": opts.node_name,
        "target": opts.target,
        "group": group,
        "latency": opts.latency,
        "config": {
            "source": config.source,
            "rate": config.rate,
            "volume_db": config.volume_db,
            "max_delay_samples": config.max_delay_samples,
            "speakers": config.speakers.iter().map(|s| json!({
                "name": s.name,
                "sink": s.sink,
                "pan": s.params.pan,
                "delay_samples": s.params.delay_samples,
                "gain_db": s.params.gain_db,
                "muted": s.params.muted,
            })).collect::<Vec<_>>(),
        },
    })
}

pub fn run(config: &PocConfig, opts: &LiveOptions) -> Result<(), Box<dyn Error>> {
    let channels = config.speakers.len();
    if channels == 0 || channels > MAX_CHANNELS {
        return Err(
            format!("{channels} speakers; this program handles 1 to {MAX_CHANNELS}").into(),
        );
    }
    block_termination_signals()?;
    pw::init();
    let mainloop = pw::main_loop::MainLoopRc::new(None)?;
    let context = pw::context::ContextRc::new(&mainloop, None)?;
    let core = context.connect_rc(None)?;

    let (engine, commands) = Engine::new(&config.engine_config());
    let max_delay = engine.max_delay_samples();
    let metrics = Arc::new(Metrics::new());
    let group = format!("aurasync-poc-{}", std::process::id());
    let aux_names: Vec<String> = (0..channels).map(|i| format!("AUX{i}")).collect();

    // The sink applications play into. Same name and class as `SinkVirtual` in sonido.py,
    // plus what `module-loopback` adds (group, link-group, prefill), plus what keeps
    // WirePlumber away from it: no moving, no reconnecting, no fallback, and no restoring
    // a remembered volume or target (the 46 % volume of experimentos/10).
    // `priority.session=1` (INFERRED): last in line if WirePlumber ever has to pick a
    // default sink on its own; it does not stop a user from choosing it.
    let mut capture_props = properties! {
        "media.type" => "Audio",
        "media.class" => "Audio/Sink",
        "node.name" => opts.node_name.as_str(),
        "node.description" => opts.description.as_str(),
        "audio.position" => "[ FL FR ]",
        "node.group" => group.as_str(),
        "node.link-group" => group.as_str(),
        "node.dont-move" => "true",
        "node.dont-reconnect" => "true",
        "node.dont-fallback" => "true",
        "state.restore-props" => "false",
        "state.restore-target" => "false",
        "priority.session" => "1",
        "resample.prefill" => "true",
    };
    // The N-channel stream to the combine-stream sink, like the service's `pw-play`
    // (`ReproductorCombinado`): AUX positions, the same `dont-*` properties.
    let mut playback_props = properties! {
        "media.type" => "Audio",
        "media.category" => "Playback",
        "node.name" => format!("{}_output", opts.node_name),
        "node.description" => format!("{} → {}", opts.description, opts.target),
        "target.object" => opts.target.as_str(),
        "audio.position" => format!("[ {} ]", aux_names.join(" ")),
        "node.group" => group.as_str(),
        "node.link-group" => group.as_str(),
        "node.dont-move" => "true",
        "node.dont-reconnect" => "true",
        "node.dont-fallback" => "true",
        "state.restore-props" => "false",
        "state.restore-target" => "false",
        "resample.prefill" => "true",
    };
    if let Some(latency) = &opts.latency {
        capture_props.insert("node.latency", latency.as_str());
        playback_props.insert("node.latency", latency.as_str());
    }
    let capture = StreamRc::new(core.clone(), "aurasync-poc-capture", capture_props)?;
    let playback = StreamRc::new(core.clone(), "aurasync-poc-playback", playback_props)?;

    let capture_position: PositionPtr = Arc::default();
    let playback_position: PositionPtr = Arc::default();

    let _capture_listener = capture
        .add_local_listener_with_user_data(CaptureRt {
            playback: playback.clone(),
            metrics: Arc::clone(&metrics),
        })
        .process(capture_process)
        .io_changed({
            let p = Arc::clone(&capture_position);
            move |_, _, id, area, _| store_position(&p, id, area)
        })
        .param_changed(|_, _, id, param| {
            if let Some(f) = describe_format(id, param) {
                eprintln!("poc-pw: capture format: {f}");
            }
        })
        .state_changed(|_, _, old, new| eprintln!("poc-pw: capture {old:?} -> {new:?}"))
        .register()?;

    let _playback_listener = playback
        .add_local_listener_with_user_data(PlaybackRt {
            engine,
            capture: capture.clone(),
            metrics: Arc::clone(&metrics),
            position: Arc::clone(&playback_position),
            channels,
            last_start: 0,
        })
        .process(playback_process)
        .io_changed({
            let p = Arc::clone(&playback_position);
            move |_, _, id, area, _| store_position(&p, id, area)
        })
        .param_changed(|_, _, id, param| {
            if let Some(f) = describe_format(id, param) {
                eprintln!("poc-pw: playback format: {f}");
            }
        })
        .state_changed(|_, _, old, new| eprintln!("poc-pw: playback {old:?} -> {new:?}"))
        .register()?;

    // Playback first, so it is active before the capture triggers it (module-loopback.c
    // l. 743).
    let aux: Vec<u32> = (0..channels as u32)
        .map(|i| spa::sys::SPA_AUDIO_CHANNEL_AUX0 + i)
        .collect();
    let playback_format = format_pod(config.rate, &aux)?;
    let mut playback_params = [Pod::from_bytes(&playback_format).ok_or("invalid format pod")?];
    playback.connect(
        spa::utils::Direction::Output,
        None,
        StreamFlags::AUTOCONNECT
            | StreamFlags::MAP_BUFFERS
            | StreamFlags::RT_PROCESS
            | StreamFlags::TRIGGER,
        &mut playback_params,
    )?;
    let capture_format = format_pod(
        config.rate,
        &[
            spa::sys::SPA_AUDIO_CHANNEL_FL,
            spa::sys::SPA_AUDIO_CHANNEL_FR,
        ],
    )?;
    let mut capture_params = [Pod::from_bytes(&capture_format).ok_or("invalid format pod")?];
    // `PW_STREAM_FLAG_ASYNC` (stream.h l. 489, since 0.3.73) has no constant in
    // pipewire-rs 0.10.1 (`stream/mod.rs` l. 944-959); it is taken from the bindings,
    // which bindgen generates from this machine's headers.
    let async_flag = StreamFlags::from_bits_retain(pw::sys::pw_stream_flags_PW_STREAM_FLAG_ASYNC);
    capture.connect(
        spa::utils::Direction::Input,
        None,
        StreamFlags::AUTOCONNECT | StreamFlags::MAP_BUFFERS | StreamFlags::RT_PROCESS | async_flag,
        &mut capture_params,
    )?;

    // Only now the control thread: it inherits the blocked signals.
    let _control = control::spawn_stdin(commands, config.names(), max_delay)?;

    let log = Rc::new(RefCell::new(Logger::open(&opts.log_path)?));
    log.borrow_mut().write(&start_line(config, opts, &group));
    let total = Rc::new(RefCell::new(MetricsSnapshot::default()));
    let started = Instant::now();
    let rate = config.rate;

    let timer = mainloop.loop_().add_timer({
        let (log, total, metrics) = (Rc::clone(&log), Rc::clone(&total), Arc::clone(&metrics));
        let (capture, playback) = (capture.clone(), playback.clone());
        let (cp, pp) = (
            Arc::clone(&capture_position),
            Arc::clone(&playback_position),
        );
        move |_expirations| {
            let interval = metrics.take();
            total.borrow_mut().merge(&interval);
            log.borrow_mut().write(&json!({
                "kind": "interval",
                "t": report::unix_time(),
                "uptime_s": (started.elapsed().as_secs_f64() * 10.0).round() / 10.0,
                "metrics": report::metrics(&interval, rate),
                "capture": stream_view(&capture, &cp),
                "playback": stream_view(&playback, &pp),
                "same_driver": same_driver(&cp, &pp),
            }));
        }
    });
    timer
        .update_timer(Some(Duration::from_secs(1)), Some(Duration::from_secs(1)))
        .into_result()?;

    let _sigint = mainloop.loop_().add_signal_local(pw::loop_::Signal::INT, {
        let mainloop = mainloop.clone();
        move || mainloop.quit()
    });
    let _sigterm = mainloop.loop_().add_signal_local(pw::loop_::Signal::TERM, {
        let mainloop = mainloop.clone();
        move || mainloop.quit()
    });

    eprintln!(
        "poc-pw: sink {:?} ({} speakers) -> {:?}; libpipewire {}; log {}",
        opts.node_name,
        channels,
        opts.target,
        library_version(),
        opts.log_path.display()
    );
    mainloop.run();
    eprintln!("poc-pw: stopping");

    // Capture first, so nothing triggers the playback any more.
    if let Err(e) = capture.disconnect() {
        eprintln!("poc-pw: capture disconnect: {e}");
    }
    if let Err(e) = playback.disconnect() {
        eprintln!("poc-pw: playback disconnect: {e}");
    }
    let last = metrics.take();
    let mut total = total.borrow_mut();
    total.merge(&last);
    log.borrow_mut().write(&json!({
        "kind": "summary",
        "t": report::unix_time(),
        "uptime_s": (started.elapsed().as_secs_f64() * 10.0).round() / 10.0,
        "metrics": report::metrics(&total, rate),
    }));
    Ok(())
}
