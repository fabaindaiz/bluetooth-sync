# aurasync control API (version 1)

The control service (`aurasync service`) is a long-lived program with one audio session
at a time. It is driven by a JSON contract that does not depend on the transport; REST is
the first transport, serial will be the second. Design:
[`docs/superpowers/specs/2026-09-29-control-service-design.md`](../../docs/superpowers/specs/2026-09-29-control-service-design.md).

## Running it

```bash
aurasync service                      # listens on 0.0.0.0:8731, with the panel at /
aurasync service --bind 127.0.0.1     # this machine only
aurasync service --simular            # no speakers: the real motor over a simulated room
```

It prints the panel's link with the token (and a QR for the phone when it listens on the
network). Opening it once leaves an `HttpOnly` cookie; the link can then be dropped.

On its first run it writes `~/.config/aurasync/service.json` (mode `0600`) with a random
token, and prints a link with the token for a phone on the same network. It refuses to
start if that file is readable by others, is not valid JSON, or has unknown keys.

```json
{"bind": "0.0.0.0", "port": 8731, "token": "…", "installation": "~/.config/aurasync/instalacion.json", "microphone": null}
```

`microphone` is the PipeWire node used by `start` with `recalibrate`; when `null`, the
system's default source is used. Ctrl-C, `SIGTERM` or `POST /v1/shutdown` close the
session in order and exit; a second Ctrl-C exits at once.

The program starts with the session **stopped** and `volume_db` at **-20 dB**.

## Authentication

Every request carries the token: `Authorization: Bearer <token>`, or `?token=<token>` in
the URL. Without it, or with a wrong one, the reply is 401 `unauthorized`. The token
travels in clear text over HTTP: fine for a home network, not for a shared one.

## Messages

```json
→ {"v": 1, "id": 7, "op": "set", "speaker": "JBL Go 4 Red", "changes": {"ambience": 0.6, "pan": -0.3}}
← {"v": 1, "id": 7, "ok": true, "result": {"sequence": 42}}
← {"v": 1, "id": 7, "ok": false, "error": {"code": "out_of_range", "message": "ambience goes from 0 to 1; got 1.4"}}
```

- `id` is optional and echoed back.
- A `set` with several fields is **atomic**: one invalid field and none is applied.
- Values are never clamped: out of range is an error. `1` is not `true`; `"0.5"` is not a
  number; NaN and infinities are rejected.
- `sequence` grows with every accepted change, and `state` reports the last one.

| `op` | Fields | Effect |
|---|---|---|
| `state` | — | The snapshot below |
| `start` | `recalibrate` (bool, optional) | Opens the audio session; speakers are checked first |
| `stop` | — | Closes the session; the program stays up |
| `set` | `speaker` (optional), `changes` | Per speaker: `pan` [-1, 1], `ambience` [0, 1], `gain_db` [-40, 6]. Global: `rear_delay_ms` [0, 50], `volume_db` [-60, 0], `extract_ambience`, `decorrelate` (bool). Works with the session stopped |
| `presets` | — | Every saved preset |
| `preset_save` | `name` | Saves the artistic fields (no `delay_ms`, no `volume_db`) |
| `preset_load` | `name` | Applies a preset; while playing, always through an 80 + 80 ms fade |
| `preset_delete` | `name` | Removes it |
| `save` | — | Writes the installation file, including the loop's `delay_ms` |
| `shutdown` | — | Replies, closes the session, exits |

### What a change sounds like

- `pan`, `ambience`, `gain_db` and `volume_db` move with ramps: no clicks.
- `ambience` also changes the speaker's rear delay (`ambience × rear_delay_ms`). When the
  delay would take more than 2 s to ramp (more than 1 ms of change), the change goes
  through the fade instead: a short dip to silence.
- `decorrelate` and `preset_load` always go through the fade.
- `extract_ambience` off keeps the extractor running and mixes it out, so the latency does
  not change.

## State

```json
{
  "service": {"version": "0.0.0", "uptime_s": 812.4},
  "session": {"status": "playing", "since": "2026-10-01T18:02:11-03:00", "reason": null},
  "sequence": 42,
  "global": {"rear_delay_ms": 12.0, "volume_db": -20.0, "extract_ambience": true, "decorrelate": true},
  "speakers": [{"name": "JBL Go 4 Red", "sink": "bluez_output.90_F2_60_75_4A_83.1",
                "pan": -0.7, "ambience": 0.15, "gain_db": 0.0, "delay_ms": 0.0, "delay_now_ms": 0.0}],
  "preset": null,
  "recalibration": {"active": false, "last": null},
  "warnings": ["alignment not measured in this session"]
}
```

`session.status` is `stopped`, `starting`, `playing` or `error` (with `reason`). There is
no push in version 1: a client polls `GET /v1/state`.

## Errors

| Code | HTTP | When |
|---|---|---|
| `bad_request` | 400 | malformed JSON, body over 64 KiB, missing field |
| `version` | 400 | unknown `v` |
| `unknown_op` | 400 | unknown `op` |
| `unknown_field` | 400 | unknown key or field |
| `type` | 400 | wrong JSON type |
| `out_of_range` | 400 | value outside its range |
| `read_only` | 400 | (no longer used: `delay_ms` is settable while the loop is off) |
| `not_found` | 404 | unknown speaker, preset or route; no installation |
| `conflict` | 409 | `start` while playing; another sink named `aurasync` exists |
| `unavailable` | 409 | a speaker is not connected; streams did not reach their speakers |
| `unauthorized` | 401 | missing or wrong token |
| `busy` | 503 | `GET /v1/stream` when 8 streams are already open |
| `internal` | 500 | unexpected exception; logged, the program keeps running |

## REST routes

| Route | Message |
|---|---|
| `GET /v1/state` | `state` |
| `POST /v1/session/start` (body optional: `{"recalibrate": true}`) | `start` |
| `POST /v1/session/stop` | `stop` |
| `PATCH /v1/speakers/{name}` (body = `changes`) | `set` with `speaker` |
| `PATCH /v1/global` (body = `changes`) | `set` |
| `GET /v1/presets` | `presets` |
| `PUT /v1/presets/{name}` · `DELETE /v1/presets/{name}` | `preset_save` · `preset_delete` |
| `POST /v1/presets/{name}/load` | `preset_load` |
| `POST /v1/installation/save` | `save` |
| `POST /v1/shutdown` | `shutdown` |
| `POST /v1/command` | the raw message, exactly as it would travel over serial |

`{name}` is URL-encoded: `JBL%20Go%204%20Red`.

## Live stream (spec §17)

`GET /v1/stream[?since=<log seq>]` answers `text/event-stream` (Server-Sent Events) until the
client leaves or the service shuts down. Authentication is the same as any route (bearer,
`?token=`, or the panel's cookie, which a same-origin `EventSource` sends). At most 8 at once;
the 9th gets `503 busy`. It is read-only: orders still go through the routes above.

| Event | When | `data` |
|---|---|---|
| `state` | when `sequence` changes, and at least once a second | the snapshot, as `GET /v1/state` returns in `result` |
| `meters` | 20 Hz, while a session plays | `{"meters": {name: {"rms_db", "peak_db"}}, "limiter_db": {speaker: dB}, "age_ms", "synced"}` |
| `input` | 10 Hz, while music comes in | `{"bands_db": [27 thirds, 50 Hz-20 kHz], "correlation", "side_db", "age_ms"}` |
| `log` | as lines are written | as the `logs` operation: `{"records", "last", "gap"}` |
| `: ping` (comment) | after 5 s with nothing else | keeps the connection open |

**Meters are timed to the ear.** Each 1024-sample chunk is shown when it is heard: write
time plus the output latency the last calibration measured. `synced: false` means there is
no measured latency yet, and the meters run ahead of the sound by it (~0.5 s here). The
microphone's meter (`mic`) is never delayed: the room is playing what it hears. Values are
raw per chunk (no peak hold); a client applies its own ballistics.

```bash
curl -N -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8731/v1/stream
```

## Examples

```bash
T=$(python3 -c 'import json,os;print(json.load(open(os.path.expanduser("~/.config/aurasync/service.json")))["token"])')
H="Authorization: Bearer $T"; U=http://127.0.0.1:8731/v1

curl -s -H "$H" $U/state
curl -s -H "$H" -X POST $U/session/start
curl -s -H "$H" -X PATCH $U/global -d '{"volume_db": -18}'
curl -s -H "$H" -X PATCH "$U/speakers/JBL%20Go%204%20Blue" -d '{"ambience": 0.8}'
curl -s -H "$H" -X PUT $U/presets/wide
curl -s -H "$H" -X POST $U/presets/wide/load
curl -s -H "$H" -X POST $U/session/stop
```

## Panel operations (spec §15)

Sent as raw messages to `POST /v1/command`. All additive to version 1.

| `op` | Fields | Effect |
|---|---|---|
| `set` (speaker) | also `delay_ms` [0, 100] and `muted` (bool) | `delay_ms` by hand only while the recalibration loop is off (`conflict` otherwise); `muted` is not saved |
| `set` (global) | also `layout` (`quad`, `lcrs`), `block_size` (1024…8192), `player_latency_ms` [50, 500], `sink_description`, `recalibrate_every_s` [5, 300], `recalibrate_measure_s` [10, 30] | the last five apply on the next `start`; `state.config.pending_restart` lists them meanwhile |
| `assign` | `speaker`, `role` | sets the role's `pan` and `ambience`; one speaker per role |
| `source` | `kind` (`system`, `app`, `file`, `tone`), `name` | switches on a worker thread; verified in `pw-dump` |
| `tone` | `speaker`, `seconds` [0.5, 10] | 660 Hz at -26 dBFS on one speaker |
| `recalibrate` | `active` | switches the loop on or off while playing |
| `calibrate` | `seconds` [5, 20], `amplitude` [0.02, 0.2] | stimulus inside the open session; pauses the loop and resumes it after |
| `calibrate_cancel` · `calibration_apply` | — | apply goes through the fade |
| `measurement_save` | `note` | writes `calibracion-<date>.json` to `measurements` in `service.json`; refused when simulated |
| `scan` · `connect` · `disconnect` · `forget` | `address` (`AA:BB:…`, upper case) | `bluetoothctl`, on a worker thread; `forget` removes the pairing and is refused for a speaker of the installation |
| `microphone_set` | `node` (or `null`) | the microphone for calibration and the loop; kept in `service.json`; a running loop restarts with it |
| `speaker_add` (`address`) · `speaker_remove` (`speaker`) | | session stopped; `save` writes it |
| `logs` | `since`, `limit` | the process's log lines after `since`; `gap` says some were lost |
| `service_start` · `service_stop` · `service_restart` | `name` | `session`, `recalibration`, `source`; the rest are only observed |
| `ab_start` (`a`, `b`) · `ab_play` (`which`: `a`, `b`, `x`) · `ab_answer` (`x_is`) · `ab_stop` | | blind A/B; X is drawn again after each answer and never shown |

`health.cuts` lists every interruption of the last 10 minutes (`cuts.py`): `events` (each with
`kind`: `underrun`, `low`, `late`, `xrun`, `input_gap`, `fade`, `lost`, `routing`; `where`, `what`,
`detail`, and the `context` the engine knew then: `loop_measuring`, `bt_discovering`,
`slow_order`, `last_order`), `faults_10min`, `faults_1min`, `by_kind`, `likely` (a one-line
reading) and `now` (the clock the events' `t` is on). `health.pipe_level_ms` is the audio
waiting for the speakers when the engine last wrote. `devices[]` carry `battery_pct` when the
device reports it.

The state also carries `speakers[].role/muted/connected/codec/pid`, `services`, `health`,
`latency`, `meters`, `config`, `calibration`, `source`, `apps`, `devices`, `presets`, `ab`,
`dirty` and `logs_last`.
