# Control service: live adjustment over a transport-independent contract

- **Date:** 2026-09-29
- **Status:** approved by the user on 2026-10-01, with §13 and §14 added in that review.
- **Roadmap:** i-7c8794-bdb678 (live diffusion mode). First of two deliveries; the
  second is i-7c8794-f3ddf8 (blind A/B and a user interface).
- **Decisions:** d-7c8794-74b639 (the contract and the service), d-7c8794-7b3093 (new
  code in English).
- **Language:** this document, the API, its routes and all new code are in English
  (d-7c8794-7b3093). Existing modules keep their Spanish names until
  i-7c8794-f30928 migrates them.

## 1. Why

The first listening test with three Go 4
(`docs/research/experimentos/09-primera-escucha-con-3-go-4.md`) found the effect
*"noticeable but weaker than expected"*. Today every parameter is read from
`instalacion.json` when `aurasync run` starts, so each change costs a restart, and the
comparison lives in the listener's memory, which is useless for subtle differences.

What the user asked for:

- adjust the parameters **while audio plays**, from a phone, walking around the room
  (which is where the effect has to be judged), and show it to someone else;
- a **persistent program** that is started and stopped explicitly, with a **REST API**;
  which interface drives it is decided later;
- a control surface that can later travel over a **serial line**, because Phase 3 puts
  part of the system on a Raspberry Pi (§9);
- **named presets** to compare A/B for real, including blind (second delivery).

## 2. Decisions taken in the design conversation

| Question | Answer |
|---|---|
| Network exposure | Listens on the LAN, every request carries a random token |
| What survives the program | Everything lives in memory; `save` writes the installation and `preset_save` writes `presets.json`. Nothing else touches disk |
| A/B | Plain toggle **and** a blind mode (blind mode is the second delivery) |
| Architecture | A persistent program with a REST adapter over a transport-independent contract (the user's variant of option C) |
| What "persistent" means | **Not** a system service. `aurasync service` is started by hand and stays up until explicitly shut down. A failing audio session does not stop it |
| Program vs audio lifecycle | The program is always up; the virtual sink and the speaker streams exist only between `start` and `stop` |
| Language | New code, routes, contract fields and API docs in English; existing code migrated separately |

## 3. Scope

**In this delivery:** the contract (`control.py`), the program (`service.py`), the REST
adapter (`rest.py`), the audio session extracted from `cmd_run` (`session.py`), presets
(`presets.py`), the live-parameter mechanics (`dsp/ramps.py` and the wiring in
`motor.py`), the microphone setting, tests, and the API reference.

**Not in this delivery:** any user interface; blind A/B; the serial transport
(i-7c8794-a9f161, roadmap only); renaming existing code (i-7c8794-f30928); installing
the program as a system unit.

## 4. Architecture

The rule from `docs/research/08-integracion-y-plan.md` §6.1 holds: **whatever does no
I/O is tested with data**, without network, radio or speakers.

| Module | I/O | Responsibility |
|---|---|---|
| `control.py` (new) | none | The contract: parse and validate a message, dispatch it against a `Controllable` interface, build the reply. Owns the name map between contract fields and `config.py` fields. Knows nothing about HTTP or serial |
| `presets.py` (new) | file | Named presets with the **artistic fields only**. Atomic writes |
| `session.py` (new) | audio | The audio loop now inside `cmd_run`: open the streams, the silence, the virtual sink, the routing check, the block loop and the recalibration loop. A class with `open()`, `step()` and `close()` |
| `service.py` (new) | process | Loads `service.json`, starts the REST adapter, owns the session, applies queued commands between blocks, publishes state snapshots, handles shutdown |
| `rest.py` (new) | network | `http.server.ThreadingHTTPServer` from the standard library. Maps routes to contract messages, checks the token |
| `dsp/ramps.py` (new) | none | Smoothed parameters, gain ramps and the fade gate |
| `motor.py` (changed) | none | Uses `dsp/ramps.py`; keeps its Spanish identifiers until i-7c8794-f30928 |
| `cli.py` (changed) | — | New `service` subcommand; `run` delegates to `session.py`; microphone resolution (§4.3) |

No new dependencies: `http.server`, `json`, `secrets`, `hmac`, `threading` and `queue`
are in the standard library, and the project avoids native dependencies on purpose
(`host/pyproject.toml`).

### 4.1 One writer

The motor and the installation have **one writer: the audio thread**, as the
recalibration loop already does today.

1. A request arrives on an HTTP thread. `control.py` validates it against the current
   snapshot and, if it is valid, puts a command on a `queue.Queue` and replies.
2. The audio thread drains the queue **between blocks** and applies each command.
3. Every few blocks the audio thread publishes an immutable **snapshot** of the state;
   `state` reads the latest snapshot, never the live objects.

The service has **one engine thread** for its whole life. With a session playing it
drains the queue between audio blocks; with no session it drains it on a 50 ms tick.
Either way it is the only writer, so `set` with the session stopped, `presets` and
`save` follow the same path as everything else. HTTP threads never touch the motor or
the installation.

### 4.2 Configuration: `service.json`

`~/.config/aurasync/service.json` (respects `XDG_CONFIG_HOME`), mode `0600`:

```json
{
  "bind": "0.0.0.0",
  "port": 8731,
  "token": "<43 url-safe characters from secrets.token_urlsafe(32)>",
  "installation": "~/.config/aurasync/instalacion.json",
  "microphone": null
}
```

- Missing file → created with defaults and a new token **before** the port is opened.
- Invalid JSON, unknown keys, or permissions wider than `0600` → the program refuses to
  start and says how to fix it (fail closed, as `ssh` does with an exposed key).
- `--bind` and `--port` on the command line override the file for one run.
- The token is stored so the phone link survives a restart of the program.

### 4.3 Microphone resolution

The inventory of `HP-O16` (`docs/research/experimentos/00-inventario-hp-o16.md`) found
the microphone hard-coded to the USB microphone of `PC-Ryzen5`
(`MICROFONO_POR_DEFECTO` in `cli.py`). Resolution order, for `calibrate`,
`run --recalibrar` and the service: `--microfono` → `microphone` in `service.json` →
PipeWire's default source. The hard-coded constant goes away.

## 5. The contract

### 5.1 Envelope

```json
→ {"v": 1, "id": 7, "op": "set", "speaker": "Go 4 Red", "changes": {"ambience": 0.6, "pan": -0.3}}
← {"v": 1, "id": 7, "ok": true, "result": {"sequence": 42}}
← {"v": 1, "id": 7, "ok": false, "error": {"code": "out_of_range", "message": "ambience goes from 0 to 1; got 1.4"}}
```

- `v` is the contract version; an unknown version is rejected with `version`.
- `id` is optional and echoed back. HTTP does not need it; serial does, to pair replies.
- **A `set` with several fields is atomic:** all are validated first; if one fails, none
  is applied. Accepted changes are applied in the same audio block.
- `sequence` grows with every accepted change; `state` reports the last one applied, so
  a client knows when its change is already sounding.
- Unknown top-level keys are rejected (`unknown_field`), never ignored.

### 5.2 Error codes

Stable strings, so firmware in C can branch on them without parsing text:

| Code | HTTP | When |
|---|---|---|
| `bad_request` | 400 | malformed JSON, body over 64 KiB, missing `op` |
| `version` | 400 | unknown `v` |
| `unknown_op` | 400 | unknown `op` |
| `unknown_field` | 400 | unknown key, speaker field or global field |
| `type` | 400 | wrong JSON type (`1` is not a boolean, `"0.5"` is not a number, NaN and infinities rejected) |
| `out_of_range` | 400 | value outside its range (§5.3); never clamped silently |
| `read_only` | 400 | `delay_ms`, which belongs to the recalibration loop |
| `not_found` | 404 | unknown speaker or preset; no installation file |
| `conflict` | 409 | `start` while playing; `extract_ambience` on a motor built without extractor |
| `unavailable` | 409 | `start` with a speaker not connected (the message names it) |
| `unauthorized` | 401 | missing or wrong token |
| `internal` | 500 | unexpected exception; logged, the program keeps running |

### 5.3 Operations (version 1)

| `op` | Fields | Effect |
|---|---|---|
| `state` | — | Full snapshot (§5.4) |
| `start` | `recalibrate` (bool, optional) | Opens the audio session. Speakers are checked before anything is created |
| `stop` | — | Closes the session; the program stays up |
| `set` | `speaker` (optional), `changes` | With `speaker`: `pan` [-1, 1], `ambience` [0, 1], `gain_db` [-40, +6]. Without: `rear_delay_ms` [0, 50], `volume_db` [-60, 0], `extract_ambience` (bool), `decorrelate` (bool). Works with the session stopped: it changes the installation in memory, which is what plays on `start` |
| `presets` | — | Names and contents of saved presets |
| `preset_save` | `name` | Saves the current artistic fields; writes `presets.json` |
| `preset_load` | `name` | Applies a preset, through the fade gate if a session is playing (§6.5) |
| `preset_delete` | `name` | Removes it; writes `presets.json` |
| `save` | — | Writes the installation file, including the `delay_ms` the loop corrected |
| `shutdown` | — | Replies, closes the session in order, exits 0 |

Ranges: the research recommends 5–20 ms of rear delay
(`docs/research/09-efecto-ambiental-y-diseno-de-la-experiencia.md` §11.2); the contract
allows 0–50 so that the range can be explored, not only confirmed.

### 5.4 State snapshot

```json
{
  "service": {"version": "0.0.0", "uptime_s": 812.4},
  "session": {"status": "playing", "since": "2026-09-29T18:02:11-03:00", "reason": null},
  "sequence": 42,
  "global": {"rear_delay_ms": 12.0, "volume_db": -12.0,
             "extract_ambience": true, "decorrelate": true},
  "speakers": [
    {"name": "Go 4 Red", "sink": "bluez_output.90_F2_60_75_4A_83.1",
     "pan": -0.3, "ambience": 0.6, "gain_db": 0.0,
     "delay_ms": 3.41, "delay_now_ms": 3.38}
  ],
  "preset": "wide",
  "recalibration": {"active": true, "last": {"t": 780.2, "kind": "ajuste", "reason": "…"}},
  "warnings": ["the calibration was measured with decorrelation on"]
}
```

`session.status` is one of `stopped`, `starting`, `playing`, `error`; with `error`,
`reason` says why. The `recalibration.last` entry passes through what the loop already
logs (its kinds are Spanish today and stay so until i-7c8794-f30928).

### 5.5 Name map

`control.py` owns the only translation between the contract and `config.py`:

| Contract | `config.py` / motor |
|---|---|
| `speaker` / `name` | `Parlante.nombre` |
| `pan` | `Parlante.pan` |
| `ambience` | `Parlante.ambiente` |
| `gain_db` | `Parlante.ganancia_db` |
| `delay_ms` (read-only) | `Parlante.retardo_ms` |
| `rear_delay_ms` | `Instalacion.retardo_traseros_ms` |
| `extract_ambience` | extractor mix factor (§6.2) |
| `decorrelate` | decorrelator mix (§6.3) |
| `volume_db` | new motor output gain (§6.4) |

### 5.6 Presets file

`~/.config/aurasync/presets.json`, written atomically (temporary file, then
`os.replace`):

```json
{"v": 1, "presets": {"wide": {
  "global": {"rear_delay_ms": 15.0, "extract_ambience": true, "decorrelate": true},
  "speakers": {"Go 4 Red": {"pan": -0.3, "ambience": 0.6, "gain_db": 0.0}}}}}
```

`delay_ms` and `volume_db` are never in a preset: the first belongs to the loop, the
second is the listener's volume and would bias an A/B comparison. Loading a preset that
names a speaker the installation lacks → `not_found`, nothing applied.

## 6. Live-parameter mechanics

The new mechanisms live in `dsp/ramps.py`; `motor.py` only wires them in. The stage
order does not change: ambience → decorrelation → delay → gain.

### 6.1 `pan` and `ambience` are smoothed

Today `procesar()` reads them raw every block, so a change is a step inside the signal.
They become smoothed parameters: a target and a current value that moves sample by
sample at a limited speed of **2 units/s** (0 → 1 in 0.5 s). While moving, the block's
weights are arrays; once settled, scalars, so the cost at rest is unchanged.

### 6.2 `extract_ambience` without changing latency

Turning the extractor off would remove its 43 ms latency from the direct path and make
the audio jump in time. Instead the extractor **keeps running** and a global mix factor,
smoothed, multiplies every speaker's `ambience`. Off means direct only, with the same
latency. Cost: the extractor's CPU even when unused. A motor built with
`extraer_ambiente=False` (today's `play --sin-ambiente`) has no extractor, and turning
it on answers `conflict`.

### 6.3 `decorrelate` always through the fade gate

The decorrelator is a 128-tap random-phase FIR: its energy is spread over ~2.7 ms with
no defined bulk delay, so switching it off **shifts the speaker in time by up to
~1–2 ms**. That is the scale the calibration corrects, and the calibration was measured
with decorrelation on. A crossfade would also comb-filter during the transition. So the
switch goes through the fade gate (§6.5); `state` adds the warning; if the
recalibration loop is active it will see the shift and correct it, which doubles as a
check. The filter keeps convolving while bypassed, so its tail is ready when it comes
back.

### 6.4 `volume_db` and `gain_db`

`gain_db` keeps the existing ramp (6 dB/s, tuned for the loop). `volume_db` moves from
a multiplication in `cmd_run` into the motor, with its own faster ramp of **30 dB/s**,
because it is a hand control. Both ramps are linear per sample: no steps.

### 6.5 The fade gate

Moved from a control, `rear_delay_ms` follows the existing delay ramp (0.5 ms/s): slow,
inaudible. But these go through the **fade gate**:

- `preset_load`;
- `decorrelate` on or off;
- any delay change the ramp would need more than 2 s to reach.

Sequence: fade out over 80 ms (raised cosine) → with the output at exactly zero, every
parameter jumps to its target (delay lines through their existing `saltar_a`) → fade in
over 80 ms.

- **`preset_load` fades even when nothing changes.** In blind A/B the presence or
  absence of the dip would otherwise give the answer away.
- The emission window of the recalibration loop records what was really emitted, fade
  included. **The loop proposes no adjustment while a fade is in progress**, because it
  would be measuring a cut signal.

## 7. Errors and lifecycle

| Situation | Result |
|---|---|
| Port in use, `service.json` invalid or wider than `0600` | Does not start; exit 1 with the fix |
| `service.json` missing | Created with a new token before listening |
| Installation file missing | Starts anyway; `state` says so; `start` → `not_found` |
| `start` with a speaker disconnected | `unavailable`, naming it; nothing created |
| **During a session:** all speakers disconnect, routing ends up wrong, a `pw-play` dies, the motor raises | **Only the session closes**, in order (the virtual sink disappears). `session.status = error` with reason and time. The program waits for another `start` |
| **During a session:** one speaker is turned off, or its `pw-play` dies (changed on 2026-10-01) | Its stream is **closed** and the others keep playing, as `run` always did; `state` names it in `warnings` and marks it `"playing": false`. Closed, not moved back: an orphan stream can be moved by WirePlumber to another output, including the virtual sink (the feedback loop of experiment 09). Only when **all** are gone does the session close |
| **During a session:** a stream ends up on the wrong sink | Checked every 2 s (`pw-dump`, ~16 ms) and moved back; `state` counts the repairs |
| Exception inside a request | 500 `internal`, logged; the server keeps running |
| Ctrl-C, `SIGTERM` or `shutdown` | Closes the session in order, replies to `shutdown` first, exits 0. A second Ctrl-C exits immediately: P1 measured that the virtual sink disappears even after `kill -9` (`docs/research/experimentos/07-p1-la-captura-no-deja-huella.md`) |

- **Atomic writes** for `presets.json`, `service.json` and `save`: temporary file, then
  `os.replace`. `Instalacion.guardar` writes directly today; the service wraps it
  without touching `config.py`.
- **One instance per port:** the second cannot bind and does not start. `run` and the
  service would create two sinks with the same name: before creating its sink, a session
  (in `run` or in the service) looks for a PipeWire node with that name and refuses with
  `conflict` if it exists.
- **The WirePlumber trap stays guarded.** `session.py` moves `cmd_run`'s order
  **unchanged**: streams first, half a second of silence, then the virtual sink, then the
  routing check and repair (`docs/research/experimentos/09-primera-escucha-con-3-go-4.md`
  §2). A service that restarts sessions repeats that sequence every time.

## 8. Testing

All in `scripts/check.sh`, no hardware:

- **`test_control.py`:** every operation and every error code; atomicity (one invalid
  field applies nothing); `1` is not `true`; NaN rejected; `delay_ms` read-only;
  unknown top-level keys rejected; the name map against `config.py`.
- **`test_ramps.py`:** with the criterion `test_retardo.py` already uses, the largest
  sample-to-sample jump never exceeds the signal's own; every ramp reaches its target
  in the expected time; the fade reaches **exactly zero** at the jump sample; the fade
  happens with an identical preset.
- **`test_motor.py`**, new cases: moving `pan` and `ambience` mid-stream has no
  discontinuity; turning the extractor off keeps latency (an impulse comes out at the
  same sample); `decorrelate` changes only through the fade; `volume_db` ramps.
- **`test_presets.py`:** round trip without `delay_ms` or `volume_db`; unknown fields in
  the file rejected; atomic write (a failure mid-write leaves the old file).
- **`test_rest.py`:** a real `ThreadingHTTPServer` on `127.0.0.1` and a free port.
  401 without or with a wrong token; **parity**: every REST shortcut returns exactly the
  same reply as the raw message on `/v1/command`; every HTTP status of §5.2.
- **`test_service.py`:** a failing session leaves `status = error` and the program alive;
  shutdown closes the session; the token is generated before listening with mode
  `0600`; refuses to start with wider permissions; single writer (commands applied only
  between blocks).
- **Every new test is seen failing at least once** against a deliberately broken
  version of the code (card *a-check-must-be-seen-to-fail*).

**The honest limit.** The real audio session is not exercised by `check.sh`: the service
tests use a fake session with the same interface as `session.py`. The fake is only
trustworthy if the interface is small and the real class is thin; that is why the audio
order moves unchanged. A validation protocol goes to `docs/research/experimentos/`, to be
run **on `PC-Ryzen5` with speakers, not on `HP-O16`**: `run` sounds as before the
refactor; the service starts, adjusts, loads a preset, fails a session (turn a speaker
off) and stays up, and stops.

## 9. Where this runs, and why the contract is transport-independent

From `docs/research/08-integracion-y-plan.md` §3.1 and §7.3:

| Stage | Today (A2DP) | Phase 3, Pico 2 W | Phase 3, Pi Zero 2 W |
|---|---|---|---|
| Capture | PC | PC | PC (as a USB source) |
| Motor (ambience, decorrelation, delays, gains) | PC | **PC** | **Pi** |
| LC3 and the BIG | — | Pico + SuperMini | Pi + SuperMini |
| This service and its API | PC | **PC** (the Pico gets a small control of its own) | **Pi** |

- The Pico will not run the Python core: its firmware is C (TinyUSB, BTstack, liblc3),
  and the motor uses numpy and FFTs.
- **With the Pico,** the service stays on the PC and sends four processed channels over
  USB audio; serial is the link between the service and the firmware.
- **With the Pi Zero,** the whole service moves to the Pi (same code on Linux); serial is
  another way for an interface to reach it, over a USB gadget serial port.

In both cases the same JSON, one message per line, with `id` to pair replies, is the
serial transport (i-7c8794-a9f161). The `/v1/command` route exists so that this parity is
tested today.

## 10. REST routes

Every route is a shortcut to one contract message. All require the token
(`Authorization: Bearer <token>` or `?token=<token>` for a link opened on a phone),
compared with `hmac.compare_digest`.

| Route | Message |
|---|---|
| `GET /v1/state` | `state` |
| `POST /v1/session/start` · `POST /v1/session/stop` | `start` · `stop` |
| `PATCH /v1/speakers/{name}` | `set` with `speaker` (body = `changes`) |
| `PATCH /v1/global` | `set` without `speaker` |
| `GET /v1/presets` | `presets` |
| `PUT /v1/presets/{name}` · `DELETE /v1/presets/{name}` | `preset_save` · `preset_delete` |
| `POST /v1/presets/{name}/load` | `preset_load` |
| `POST /v1/installation/save` | `save` |
| `POST /v1/shutdown` | `shutdown` |
| **`POST /v1/command`** | **the raw message, exactly as it would travel over serial** |

`{name}` is URL-decoded; speaker names contain spaces ("Go 4 Red").

## 11. Documentation and roadmap changes

- `host/docs/control-api.md`: the API reference, in English.
- `host/README.md`: the new modules and the `service` subcommand.
- `docs/decisions.md`: d-7c8794-74b639 and d-7c8794-7b3093.
- `docs/roadmap.md`: i-7c8794-bdb678 updated; new entries i-7c8794-a9f161 (serial
  transport), i-7c8794-f30928 (migrate existing code to English), i-7c8794-f3ddf8
  (blind A/B and interface).
- `CLAUDE.md`: the English rule under *Forma de trabajar*.
- The validation protocol (§8) in `docs/research/experimentos/`.

## 12. Risks

- **The refactor of `cmd_run` into `session.py` touches the one part no test sees.**
  Mitigation: move, don't rewrite; validate on `PC-Ryzen5` before trusting it.
- **A session restarted many times inside one long-lived process** is new. The first
  listening test measured 15 ms of variation between three `calibrate` runs: each new
  session may start with a different offset. With `recalibrate` off, `state` carries the
  warning *"alignment not measured in this session"*, so nobody reads the installation's
  `delay_ms` as a measurement.
- **The token travels in clear text over HTTP on the LAN.** Acceptable for a home or
  studio network; not for a shared one. TLS is out of scope; `bind` can be set to
  `127.0.0.1`.
- **CPU:** the extractor and the decorrelators keep running while "off". The motor's
  real-time cost has not been measured (only the calibration's, in
  `docs/research/experimentos/08-lazo-de-recalibracion-en-simulacion.md`). The first
  implementation step measures blocks per second against real time; it matters again on
  the Pi Zero in Phase 3.

## 13. The panel on `panel-demo` becomes a client of this contract

On 2026-10-01 a separate session built a web panel over a simulated engine, on the branch
`panel-demo`, without seeing this design. It has its own state and command contract
(`state.py`, `engine/base.py`) over a WebSocket. Two contracts for one engine would drift
apart, so:

- **`control.py` is the only contract.** When the panel is integrated (i-7c8794-f3ddf8),
  its commands and its snapshot are rewritten as messages of §5, and its WebSocket becomes
  one more transport over the same dispatcher, like REST and serial.
- **Its simulated engine is kept as a demonstration backend** of the service, and is a
  candidate for the fake session the service tests use (§8).
- Nothing on `panel-demo` is merged in this delivery; the branch stays as it is.

## 14. Change notification is out of version 1

REST only answers when asked. The panel refreshes its state about ten times a second and
streams logs live; over this contract it could only poll `GET /v1/state`. Polling is
enough for this delivery (a phone polling twice a second costs nothing here), so version 1
has **no subscription**. It is recorded as pending, undesigned: when the panel or the
serial transport needs it, it is an addition to the contract (a `subscribe` operation or a
streaming route), and `sequence` already lets a polling client tell whether anything
changed.

## 15. The panel (added on 2026-10-01, d-7c8794-09d10f)

The user asked for every feature of the `panel-demo` panel, controlling the real engine.
The panel is served by the service itself (`/`, `/static/*`, `/pairing.svg`) and speaks
**only** this contract: it polls `GET /v1/state` every 500 ms and sends each order as the
raw message to `POST /v1/command`. No WebSocket and no aiohttp: with three speakers,
polling costs nothing and keeps one transport path.

### 15.1 What each panel feature is on the A2DP engine

| Panel (`panel-demo`) | Real engine |
|---|---|
| channel per speaker, quad / LCRS | a **role** (`FL FR RL RR` or `FL FC FR RC`) is a shortcut for a `(pan, ambience)` pair; a speaker matching none is "custom"; one speaker per role |
| volume, mute, delay, gain, tone | `gain_db`; `muted` (50 ms ramp, listener state, not saved); `delay_ms` by hand only while the loop is off; a 660 Hz tone at -26 dBFS on one speaker |
| scan speakers | `bluetoothctl` devices with the A2DP sink UUID; connect (pair and trust first if needed), disconnect, add to / remove from the installation (session stopped) |
| services with PID and logs | session, virtual sink, one `pw-play` per speaker, recalibration loop (its microphone), source; `bluetoothd`, `pipewire`, `wireplumber` only observed. Logs are the process's `logging`, polled with a cursor (`logs`) |
| link health | input receiving audio, motor time per block, routing repairs and lost speakers, the loop's last decision, a chart of the delays it applied, latency by stage (A2DP unmeasured) |
| levels | input L/R and each speaker's output, RMS and held peak |
| engine config | live: extraction, decorrelation, rear delay; on next start: block size, `pw-play` buffer, sink name, loop period and window. Auracast keys are absent until E4 |
| calibration | the stimulus is played **inside the open session** (no second set of streams), recorded, and measured on a worker thread; apply goes through the fade; `measurement_save` writes a JSON record with the environment |
| source | system (the "aurasync" output), one application (its streams moved with `pactl`), a file, or the test signal — each verified in `pw-dump` after starting |

Plus the second delivery of §3: **presets and the blind A/B** (`ab_start`, `ab_play`,
`ab_answer`, `ab_stop`). X is drawn again after every answer, and the state never says
which preset X is.

### 15.2 New operations

`assign`, `source`, `tone`, `recalibrate`, `calibrate`, `calibrate_cancel`,
`calibration_apply`, `measurement_save`, `scan`, `connect`, `disconnect`, `speaker_add`,
`speaker_remove`, `logs`, `service_start`, `service_stop`, `service_restart`, `ab_start`,
`ab_play`, `ab_answer`, `ab_stop`. They are additive: version 1 stays version 1. The one
change to an existing field: `delay_ms` is settable (it was `read_only`), and the service
answers `conflict` while the loop runs.

### 15.3 Security for a browser

The token travels once in the URL (`/?t=…`) and becomes an `HttpOnly; SameSite=Strict`
cookie, with a redirect that drops it from the address bar. Every request's `Host` must
be one of this machine's names (DNS rebinding); a request authenticated only by the cookie
must carry an `Origin` equal to the server (CSRF). `curl` with the bearer token is
unaffected.

### 15.4 Nothing slow on the engine thread

Bluetooth actions, switching the source and measuring a calibration run on worker
threads; the system view (`system.Observer`, ~100 ms per read on `PC-Ryzen5`) refreshes on
its own thread. The engine thread only reads their last result.

### 15.5 Simulated mode

`aurasync service --simular` runs the real motor, contract, loop and calibration over a
simulated room (each speaker reaches the microphone 3, 7.5 and 12 ms late, with gains 1,
0.8 and 0.6, plus noise), on a copy of the installation and presets. The panel shows
SIMULADO and `measurement_save` refuses. It is what the browser tests run on.

### 15.6 What building it found in the measurement code

The simulated calibration returned the right delays and wrong gains. `medicion.niveles`
had two errors, both now fixed with tests: it divided by the reference's energy (wrong for
pink noise, whose autocorrelation varies a lot between realizations: up to 11.4 dB of
error) and it cut the circular correlation at index 0, losing the speaker that arrives
before the median. See `docs/research/experimentos/10-…` §3.

## 16. The output path after measuring it (2026-10-01, d-7c8794-a41ec9)

Measured with the three Go 4 and the microphone (`docs/research/experimentos/10-…` §5):

- **One stream, one clock.** Each Bluetooth sink is its own PipeWire driver. One `pw-play`
  per speaker let the clock difference pile up in each pipe and come out as jumps of exactly
  one quantum (2048 samples, 42.67 ms) in one speaker. The session now writes a single
  N-channel stream to a combine-stream sink (`ReproductorCombinado`), which spreads it with
  adaptive resampling: 0 jumps in 8 calibrations, and a smooth ~22 ppm drift left for the
  loop. `output_mode = separado` keeps the old path for comparison.
- **Streams nobody can move.** `node.dont-move`, `node.dont-reconnect` and
  `node.dont-fallback` on every stream the session creates: WirePlumber was moving the
  stream of the speaker that was the default output into the `aurasync` sink on every start.
- **Short pipes.** Two blocks of pipe to `pw-play` (the kernel's 64 KB was 0.68 s of audio):
  measured latency from written to heard went from ~1.03 s to ~0.50 s.
- **Calibration measures the residual** through the corrections in place (closure: 0.35 ms),
  reports the measured latency, and the motor runs on silence so that pending fades finish
  with nothing playing.
- **Protocol-only calibration is not possible with these speakers:** no Delay Reporting,
  PipeWire's reported latency is the same constant for all three. What the protocol does
  offer: the quantum (the size of the jumps) and AVRCP volume (gain without touching the
  delay, with a speaker curve still to measure).

## 17. Live metrics: a stream from the service (added on 2026-10-01, i-7c8794-530882, d-7c8794-316465)

**Status: built (2026-10-01).** The state goes every 1 s and not every 2 s: the snapshot also
carries what changes without `sequence` (health, the observer's view), and the panel used to
see it every 0.5 s.

The user asked for metrics "more live, maybe with a stream". §14 left change notification
out of version 1 as an *addition* for when the panel needed it; this is that addition. The
contract and its polling stay as they are: a client that never opens the stream loses
nothing.

### 17.1 Why polling is not enough

- Meters are computed once per engine block (4096 samples, 85 ms) and the panel reads them
  every 500 ms: a meter that moves twice a second is not a meter.
- The whole snapshot travels every time (tens of KB) even when nothing changed; going to
  20 Hz with it would cost 20× for the same information.
- What the meters show runs **~0.5 s ahead of what is heard**: the engine processes a block
  long before the speakers play it (pipe, `pw-play`, Bluetooth; `latency.measured_ms`).
  Seeing a peak before hearing it is what makes a meter feel disconnected.

### 17.2 Design

- **Transport: Server-Sent Events**, `GET /v1/stream`, `text/event-stream`. Same-origin
  `EventSource` sends the panel's cookie, so authentication is the existing one (cookie or
  `?token=`). It is one-way, which is all that is needed: orders keep going through
  `POST /v1/command`. A WebSocket would need its own framing in the standard library (or a
  dependency) for a back channel nobody uses. Over serial (i-7c8794-a9f161) the same events
  become a `subscribe` operation.
- **Events** (JSON in `data:`, the event name in `event:`):

  | Event | Rate | Content |
  |---|---|---|
  | `state` | when `sequence` changes, and at least every 1 s | the full snapshot, as `GET /v1/state` |
  | `meters` | 20 Hz | per meter: RMS and peak (dBFS), limiter reduction per speaker; `t` |
  | `input` | 10 Hz | input third-octave spectrum (50 Hz-20 kHz), L/R correlation, side/mid |
  | `log` | as they happen | the log lines, as `logs` returns them |
  | (comment) | every 5 s | keep-alive, so proxies and phones do not drop the connection |

- **Telemetry, timed to the ear.** The engine thread records, for every 1024-sample chunk of
  each block it writes, the meters and its **play time** = write time + the output latency
  (measured by the last calibration; without one, 0 and the stream says `"synced": false`).
  The stream sends the newest chunk whose play time has arrived: the meter moves when the
  sound does. One clock (`time.monotonic`) decides it (card `derive-state-from-one-clock`):
  a frame is chosen by comparing its play time with now, never by counting ticks.
- **The stream can never slow the engine** (card `best-effort-side-channels`). The engine
  appends to a bounded ring under a short lock and never waits for a reader; each stream
  runs in its own HTTP thread and reads the ring. A client that stops reading fills only
  its own socket; a failed write ends only that stream, with one log line.
- **Bounded:** at most 8 streams at once; the 9th gets `503` with a stable error code
  (`busy`). Each stream ends when the service shuts down.
- **The panel** opens the stream and stops polling while it is alive; meters draw on
  `requestAnimationFrame` from the last frame. After 3 failed reconnections it falls back
  to polling every 500 ms and says so ("consultando" instead of "en vivo"). A `state` event
  replaces the whole snapshot (the source wins; nothing is merged, card
  `derived-copy-goes-stale-silently`).

### 17.3 Also in this delivery: controls

- Sliders send every 80 ms while dragging (was 200 ms): with the stream, the result comes
  back within the next frame.
- An input spectrum (31 bars) and a correlation meter (−1 to +1) in the Entrada card.
- Limiter reduction per speaker next to its meter: when the EQ's lift reaches full scale,
  it shows.

### 17.4 How it is verified

- `telemetry`: chunks per block, the frame chosen at a given instant (and the same frame
  reached from before and after), ring bounded, a reader that raises does not stop a writer.
- REST: the stream sends `state`, `meters` and `input` within a second, rejects without
  auth, refuses the 9th client, and a client that closes mid-stream leaves the service and
  the session untouched.
- Browser: meters move with the stream; with the stream blocked, the panel falls back to
  polling and says "consultando".
- With speakers: the meter peak against the microphone's, to check the play-time offset
  (the measured latency) puts them within one frame (50 ms).
