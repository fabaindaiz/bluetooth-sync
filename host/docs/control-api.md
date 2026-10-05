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
{"bind": "0.0.0.0", "port": 8731, "token": "…", "installation": "~/.config/aurasync/instalacion.json", "microphone": null,
 "tls": true, "https_port": 8443, "panel_origins": ["https://fabaindaiz.github.io", "http://localhost:5173"],
 "pair_window_s": 600, "mdns": false}
```

The last five keys are for clients on other devices (see *Remote clients* below). A file written
before they existed has no `tls` and keeps HTTPS off; a new file gets `"tls": true`.

`microphone` is the PipeWire node used by `start` with `recalibrate`; when `null`, the
system's default source is used. Ctrl-C, `SIGTERM` or `POST /v1/shutdown` close the
session in order and exit; a second Ctrl-C exits at once.

The program starts with the session **stopped** and `volume_db` at **-20 dB**.

## Authentication

Every request carries a token: the **master token** of `service.json` (scope `admin`) or a
**client token** (`asc_<id>_<secret>`, see *Remote clients*), as `Authorization: Bearer
<token>` on any route, `?token=<token>` in the URL, or the panel's `HttpOnly` cookie. Without
one, or with a wrong one, the reply is 401 `unauthorized`. Over plain HTTP the token travels in
clear text: fine for a home network, not for a shared one; HTTPS is on port 8443.

Each operation needs a **scope**: `read` (`state`, `logs`, `presets`, `chain`, the stream),
`control` (everything about listening: sessions, `set`, presets, calibration, A/B, the chain,
connecting speakers) or `admin` (`shutdown`, `forget`, `speaker_add`/`speaker_add_virtual`/`speaker_remove`,
`microphone_set`, `service_*`, `radio_log`, `calibration_dump`, pairing and clients). An
operation not in the table needs `admin`. Too little scope: 403 `forbidden`.

**Failed attempts.** A wrong credential (bearer, `?token=`, cookie, stream ticket, pairing code
or pairing request id) counts against the client's address; a missing one does not. After 5,
each further failure blocks the address for 1, 2, 4 … up to 300 s: 429 `rate_limited` with
`Retry-After`, even with a right token.

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
  "recalibration": {"active": false, "last": null, "mic_check": false},
  "warnings": ["alignment not measured in this session"]
}
```

`monitor` (the headphone monitor) reports `mode`, `target`, `gain_db`, `volume_control`, `state`, `error`,
`routed_to`, `reached`, `drops` (blocks its writer dropped) and its cushion, the audio kept ahead in
the `pw-play` pipe so the Bluetooth driver never finds it empty:

| Field | Meaning |
|---|---|
| `volume_control` | `device` (default): the level is the target sink's volume (for Bluetooth, AVRCP absolute volume) and the software gain stays at 0 dB; `software`: `gain_db`, the sink untouched |
| `device_volume_pct` | the sink's real volume, read back (also what the headphone buttons set); `null` in `software` mode, when the monitor is off or when it cannot be read |
| `device_volume_reason` | `null`, or why the sink volume could not be set or read. A failed set keeps its reason until the sink is read back at the value asked or the next set. When it starts with "no se pudo verificar el volumen del audífono: usando volumen por software", the open output plays at `gain_db` by software although `volume_control` is `device` (see below) |
| `device_volume_set_pct` | the last value set from the panel (the ceiling the sink is lowered to when the monitor opens, never raised; `null` = 30 % the first time). A switch from `software` to `device` without a level caps it at 30 % |

In `device` mode the monitor opens at 0 dB of software gain **only when the sink was read back at
or below the ceiling**. When it was not (no sink volume backend, `pactl` missing, a sink that
refuses, lies or cannot be read, a check that fails or takes more than 10 s), or when PipeWire sends
the audio to another sink than the one checked (`routed_to` ≠ `target`), the output plays by
software instead, at `gain_db` but never above -12 dB, until it is opened again (a change of mode, target or
`volume_control`, or a new session). It fails closed: never at full level by surprise.
| `cushion_ms` | target cushion: one engine block plus one driver quantum (2048 frames), capped at 400 ms; `null` without a monitor output |
| `level_ms` | the pipe level read before the last block, or `null` if it could not be read |
| `refills` | times the pipe was about to starve and was refilled with silence up to the target |
| `pipe_bytes` | the pipe's real size after asking for room for the cushion, or `null` if it could not be set (a warning is logged) |
| `trims` | blocks dropped because the pipe held more than the target plus two blocks |

Every mode is heard at the same loudness. The reference is the input at the chosen volume (the
service's `volume_db`, also with `volume.avrcp`), which is what `stereo` sends; `mix` and `binaural`
get a makeup gain per mode that follows the reference slowly (10 % of the error per second, capped
at ±12 dB), frozen in a pause (the input, before the volume, under -50 LUFS momentary), during a
cut or a calibration, and
remembered per mode so a switch does not jump. All loudness is K-weighted short-term (3 s), before
the monitor's own `gain_db`. These fields are `null` while the monitor is not playing (and absent
from an older service):

| Field | Meaning |
|---|---|
| `makeup_db` | the makeup gain the current mode gets now, dB |
| `loudness_reference` | the reference's loudness, LUFS, or `null` in silence |
| `loudness_monitor` | the monitor's loudness after the makeup, LUFS (for `binaural`, the channels sent plus the HRTF's measured gain), or `null` |
| `match` | `measuring` (moving toward the reference), `locked` (within 0.5 LU of it), `frozen` (pause, cut, calibration or every speaker silent) or `unmeasured` (`binaural` with an HRTF whose gain was never measured: no makeup) |
| `match_reason` | why the match cannot work, or `null`; today only "binaural compensation not measured for this HRTF" |

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
| `forbidden` | 403 | the token's scope does not cover the operation |
| `rate_limited` | 429 | too many failed attempts from this address; see `Retry-After` |
| `busy` | 503 | `GET /v1/stream` when 8 streams are already open |
| `internal` | 500 | unexpected exception; logged, the program keeps running |

## REST routes

| Route | Message |
|---|---|
| `GET /v1/state` | `state` |
| `POST /v1/session/start` (body optional: `{"recalibrate": true}`) | `start` |
| `POST /v1/session/stop` | `stop` |
| `PATCH /v1/speakers/{name}` (body = `changes`) | `set` with `speaker` |
| `POST /v1/speakers/{name}/join` · `POST /v1/speakers/{name}/leave` | `speaker_join` · `speaker_leave` |
| `PATCH /v1/global` (body = `changes`) | `set` |
| `GET /v1/presets` | `presets` |
| `PUT /v1/presets/{name}` · `DELETE /v1/presets/{name}` | `preset_save` · `preset_delete` |
| `POST /v1/presets/{name}/load` | `preset_load` |
| `POST /v1/installation/save` | `save` |
| `POST /v1/shutdown` | `shutdown` |
| `GET /v1/clients` · `DELETE /v1/clients/{id}` · `PATCH /v1/clients/{id}` (`{"name"}`) | `clients` · `client_revoke` · `client_rename` |
| `GET /v1/pair` · `POST /v1/pair/code` (body optional: `{"seconds"}`) | `pair_status` · `pair_start` |
| `POST /v1/pair/{request}/approve` (body optional: `{"scope"}`) · `POST /v1/pair/{request}/deny` | `pair_approve` · `pair_deny` |
| `GET /v1/sync` · `PATCH /v1/sync` (body = `changes`) · `POST /v1/sync/apply` (body optional: `{"suggestion_id"}`) · `GET /v1/sync/explain` | `sync_state` · `sync_set` · `sync_apply` · `sync_explain` |
| `POST /v1/command` | the raw message, exactly as it would travel over serial |

Without credentials (see *Remote clients*): `GET /v1/hello`, `GET /v1/tls/root.pem|crt|mobileconfig`,
`POST /v1/pair/request`, `GET /v1/pair/{request}`. `POST /v1/stream/ticket` needs `read`.

`{name}` is URL-encoded: `JBL%20Go%204%20Red`.

## Live stream (spec §17)

`GET /v1/stream[?since=<log seq>]` answers `text/event-stream` (Server-Sent Events) until the
client leaves or the service shuts down. Authentication is the same as any route (bearer,
`?token=`, or the panel's cookie, which a same-origin `EventSource` sends), plus a one-use
`?ticket=` for an `EventSource` on another origin (see *Remote clients*). A revoked client's
stream ends within 50 ms. At most 8 at once;
the 9th gets `503 busy`. It is read-only: orders still go through the routes above.

| Event | When | `data` |
|---|---|---|
| `state` | when `sequence` changes, and at least once a second | the snapshot, as `GET /v1/state` returns in `result` |
| `quality` | 2 Hz, while a session plays | loudness in and out (see *Quality* below) |
| `chain` | 5 Hz, while a session plays | `{stage_id: metrics}`, each stage's live numbers (see *The chain*) |
| `radio` | 1 Hz, and at once on each dropped packet | as `state.radio` (see *The radio*) |
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
| `set` (global) | also `layout` (`quad`, `lcrs`, `5.0`, `hex`, `7.0`, `octagon`, `rings`; see *Layouts and roles*), `block_size` (1024…8192), `player_latency_ms` [50, 500], `sink_description`, `recalibrate_every_s` [5, 300], `recalibrate_measure_s` [10, 30] | the last five apply on the next `start`; `state.config.pending_restart` lists them meanwhile |
| `assign` | `speaker`, `role` | sets the role's `pan` and `ambience`; one speaker per role; a role of another layout is `out_of_range` |
| `source` | `kind` (`system`, `app`, `file`, `tone`), `name` | switches on a worker thread; verified in `pw-dump` |
| `tone` | `speaker`, `seconds` [0.5, 10] | 660 Hz at -26 dBFS on one speaker |
| `recalibrate` | `active` | switches the loop on or off while playing; the `recalibrate` global setting (what `start` uses when it is not given) is **off** by default |
| `monitor_set` | `mode` (`off`, `stereo`, `mix`, `binaural`), `target`, `gain_db` [-40, 0], `volume_control` (`device`, `software`; default `device`), `device_volume_pct` [0, 100] | the headphone monitor. `device_volume_pct` **present** is a level asked for now: in `device` mode it is applied to the target sink and read back even when it equals the stored one (`monitor.device_volume_reason` says why if it did not take), and it becomes the stored ceiling; a change of only that value does not reopen the monitor. Absent, the sink is never raised: a client sends it only when the listener moves the level, never with a change of mode, target or `volume_control` |
| `mic_check` | — | opens the microphone for 8 s from the moment it opens (a call while it is open does not extend it; a new one opens only after it closed) only to show its level (`meters.mic`, `recalibration.mic_check`), for the check before calibrating while the loop is off; never continuously. `{"opened": false}` when the loop or a calibration already has it; `conflict` without a session |
| `calibrate` | `seconds` [5, 20], `amplitude` [0.02, 0.2] | stimulus inside the open session; pauses the loop and resumes it after |
| `calibrate_cancel` · `calibration_apply` | — | apply goes through the fade |
| `measurement_save` | `note` | writes `calibracion-<date>.json` to `measurements` in `service.json`; refused when simulated |
| `scan` · `connect` · `disconnect` · `forget` | `address` (`AA:BB:…`, upper case) | `bluetoothctl`, on a worker thread; `forget` removes the pairing and is refused for a speaker of the installation |
| `microphone_set` | `node` (or `null`) | the microphone for calibration and the loop; kept in `service.json`; a running loop restarts with it |
| `speaker_add` (`address`) · `speaker_remove` (`speaker`) | | session stopped; `save` writes it |
| `speaker_add_virtual` (`name`?) | `name` 1–64 characters | scope `admin`, session stopped; adds a speaker with no sink (`sink: null`) that is only computed, never played in the room. Without a name it is "Virtual 1", "Virtual 2"… (the first free); it takes the next free role of the layout, as `speaker_add` does. A name already taken is `conflict`. Remove it with `speaker_remove`; `save` writes it |
| `speaker_join` (`speaker`) | scope `control`, session running | a real `absent` or `lost` speaker starts playing. The new real part is prepared in the background and swapped in at the bottom of a cut (80 + 80 ms), in both output modes. Errors: `conflict` without a session, for a virtual or already `playing` speaker, or while another change is in progress; `unavailable` ("connect it first") when its sink is not among the outputs; `not_found`. The reply comes when the change is *requested*; a failed preparation leaves the speaker as it was and goes to the log and `state.errors.output`, a success clears it |
| `speaker_leave` (`speaker`) | scope `control`, session running | a `playing` speaker leaves without stopping the session; it becomes `absent` and is still computed (heard on the monitor). The last playing speaker may leave. `conflict` without a session or when the speaker is not `playing`. A speaker that left never returns by itself |
| `logs` | `since`, `limit` | the process's log lines after `since`; `gap` says some were lost |
| `service_start` · `service_stop` · `service_restart` | `name` | `session`, `recalibration`, `source`; the rest are only observed |
| `ab_start` (`a`, `b`, `match_loudness`?) · `ab_play` (`which`: `a`, `b`, `x`) · `ab_answer` (`x_is`) · `ab_stop` | | blind A/B; X is drawn again after each answer and never shown. Its loudness is measured (see *The A/B and loudness* below) |
| `radio_log` | `active` (bool), `mode` (`light`, `heavy`; default `light`) | raises or restores WirePlumber's log level for the bluez5 topics, so the radio monitor sees dropped packets. A **system change**: see *The radio* below |

`health.cuts` lists every interruption of the last 10 minutes (`cuts.py`): `events` (each with
`kind`: `underrun`, `low`, `late`, `xrun`, `input_gap`, `fade`, `lost`, `routing`; `where`, `what`,
`detail`, and the `context` the engine knew then: `loop_measuring`, `bt_discovering`,
`slow_order`, `last_order`), `faults_10min`, `faults_1min`, `by_kind`, `likely` (a one-line
reading) and `now` (the clock the events' `t` is on). `health.pipe_level_ms` is the audio
waiting for the speakers when the engine last wrote (the lowest pipe in `separado`).
`health.output_cushion` is the speakers' cushion (`cushion.py`), `null` without a session: one value
for the whole real part. `target_ms` is one engine block plus one driver quantum (2048 frames),
capped at 400 ms, as the monitor's; `refills` counts the times a pipe read under a quantum for three
blocks in a row and the same silence, up to the target, was written to every speaker stream at the
bottom of a cut (a `fade` in `health.cuts` with that detail); `pending` is true while such a refill
waits for its cut. A refill is only asked when the silence and the next block fit in the fullest pipe,
so the write never waits; otherwise `reason` says why not (`"separado: relojes distintos"` when the
speakers' own pipes drifted apart), and is `null` the rest of the time. At least 30 s pass between two
of these cuts, and after 3 refills that did not bring the lowest pipe back within a quantum of the
target in 10 s, `gave_up` turns true and no more are asked in this session (a warning is logged
once). There is no trim: the pipe's size bounds what waits in it. `devices[]` carry
`battery_pct` when the device reports it.

The state also carries `speakers[].role/muted/connected/codec/pid`, `services`, `health`,
`latency`, `meters`, `config`, `calibration`, `source`, `apps`, `devices`, `presets`, `ab`,
`dirty` and `logs_last`.

- `speakers[].sink` is `null` for a virtual speaker. `speakers[].output_kind` (`virtual` | `bluetooth` |
  `wired`) is derived from the sink: no sink, a `bluez_output.*` node, or any other node.
  `speakers[].output` says what the session does with the speaker: `virtual` (no sink, by design),
  `absent` (real, not connected when the session opened), `playing` (its stream is alive) or `lost`
  (it was playing in this session and its stream died); `null` without a session. `playing` stays
  and equals `output == "playing"`. `address`, `battery_pct`, `codec`, `rssi_dbm` and `modalias`
  apply only to Bluetooth: they are `null` unless `output_kind == "bluetooth"`, and `connected` is
  `null` for a non-Bluetooth speaker. The AVRCP volume (see below) reaches only the `bluetooth` speakers.
  Calibration and the recalibration loop use only the `playing` speakers: `calibrate` with none
  playing is `conflict` ("no speaker is playing"). The headphone monitor still receives every channel.
  Version mix (contract version 1, additions only): a panel older than the service reads a virtual
  speaker as "sin observar" while stopped and "perdido" while playing and does not break on `sink: null`; a panel newer than the
  service derives `output` from `playing` when the field is missing.
- `speakers[].battery_pct`: the speaker's battery in percent, as BlueZ reports it (`org.bluez.Battery1`
  `Percentage`, read by the observer), or `null` when BlueZ has none for it.
- `sync`: the residual misalignment the recalibration loop measured last, through the
  corrections in place (spec 2026-10-02 §7.3.4):
  `{"residual_ms": 0.42, "measured_at": "2026-10-02T18:02:11-03:00", "age_s": 12.5, "speakers": ["…"]}`.
  All `null` (and `speakers` empty) until the loop has measured in this session. `age_s` says
  how old it is, so a client does not show a stale number as the present one.

### The automatic return of a lost speaker

Only a speaker that was `playing` in this session and whose stream died (`output: "lost"`) returns
by itself, when the system observer (every 3 s) sees its sink again: the service runs the same
`speaker_join` and logs `volvió <name>`. It never calls `connect`: reconnecting Bluetooth stays
the user's. Brake (d-7c8794-618666): at most one attempt every 10 s per speaker; after 3 drops
within 5 minutes it stops and the speaker stays `lost` until a `speaker_join` (the panel's
**Reintentar**). An `absent` speaker (not connected at the start, or taken out with
`speaker_leave`) and a virtual one never return by themselves.

### Layouts and roles

A role is a shortcut for a `(pan, ambience)` pair (with A2DP a speaker gets a mix, not a
channel). Every layout's roles come from one formula by angle around the listener (0° is the
front, positive to the right), rounded to 0.01 (`control.role_from_angle`):

> pan = 0.7 · sin θ / sin 45° · ambience = 0.35 − 0.2 · cos θ / cos 45°, kept in [0.1, 0.55]

It gives **exactly** the roles of before (`quad`, `lcrs`), so nothing that was saved changes
(`tests/test_many_speakers.py`). The new layouts (additive, 2026-10-02; none heard yet):

| `layout` | Roles: angle → (pan, ambience) |
|---|---|
| `quad` | FL −45° (−0.7, 0.15) · FR 45° · RL −135° (−0.7, 0.55) · RR 135° |
| `lcrs` | FL −45° · FC 0° (0, 0.1) · FR 45° · RC 180° (0, 0.55) |
| `5.0` | FL −30° (−0.49, 0.11) · FC 0° · FR 30° · SL −110° (−0.93, 0.45) · SR 110° |
| `hex` | FL −30° · FR 30° · SL −90° (−0.99, 0.35) · SR 90° · RL −150° (−0.49, 0.55) · RR 150° |
| `7.0` | FL −30° · FC 0° · FR 30° · SL −90° · SR 90° · RL −150° · RR 150° |
| `octagon` | FL −22.5° (−0.38, 0.1) · FR · WL −67.5° (−0.91, 0.24) · WR · SL −112.5° (−0.91, 0.46) · SR · RL −157.5° (−0.38, 0.55) · RR |
| `rings` | the `quad` inside, and outside a diamond turned 45° with 0.25 more ambience: OF 0° (0, 0.35) · OL −90° (−0.99, 0.6) · OR 90° · OB 180° (0, 0.8) |

`state.roles` lists each layout's role names; `control.LAYOUT_ANGLES` has their angles (for a
map that draws them). `assign` takes any role of any layout (`FL`, `FR`, `RL`, `RR`, `FC`, `RC`,
`SL`, `SR`, `WL`, `WR`, `OF`, `OL`, `OR`, `OB`) and the service checks it against the current
layout. A speaker whose values match no role of the layout reads `role: null` (custom).

## The chain (spec 2026-10-02 §4)

The engine is a list of stages, each with algorithms and knobs, described in
`src/aurasync/chain.py`. Sent as raw messages to `POST /v1/command`; all additive to
version 1.

| `op` | Fields | Effect |
|---|---|---|
| `chain` | — | Every stage in processing order, with its algorithms, their knobs and the current values |
| `chain_set` | `stage`, `algorithm` (optional), `params` (optional object), `speaker` (only with per-speaker params) | Checked completely before anything changes. Returns `{"sequence", "stage", "value", "apply"}`; `apply` says how it was applied now: `live`, `cut` (the 80 + 80 ms fade), or `none` (no session, or a stage not run yet) |
| `chain_reset` | `stage`, `param` (optional), `speaker` (optional, with a per-speaker `param`) | Back to the default. A whole stage clears the chain's own choices; the knobs kept in the installation (`pan`, `ambience`, `gain_db`, `rear_delay_ms`) and the session (`volume_db`, `muted`) are reset one by one, to their default value |

```json
{"stages": [{"id": "limiter", "title": "Limitador", "summary": "…", "help": "…",
             "algorithm_apply": "cut",
             "algorithms": [{"id": "peak", "title": "De pico", "summary": "…", "help": "…",
                             "cost": "", "latency_ms": 0.0, "implemented": true,
                             "available": true, "unavailable_reason": null, "notice": null,
                             "params": [{"id": "ceiling_db", "title": "Techo", "summary": "…", "help": "…",
                                         "kind": "float", "default": -1.0, "low": -6.0, "high": 0.0,
                                         "step": 0.5, "unit": "dBFS", "choices": [], "scope": "global",
                                         "apply": "live", "store": "chain", "implemented": true}, …]}, …],
             "default_algorithm": "peak",
             "value": {"algorithm": "peak", "params": {"ceiling_db": -1.0, "release_ms": 250.0}, "speakers": {}},
             "chosen": {}, "pending": false}, …],
 "latency_ms": 66.812}
```

- **Stages**, in order: `ambience`, `decorrelate`, `diffuse`, `align`, `eq`, `bass`, `volume`,
  `limiter`. Titles, summaries and help are Spanish (they are UI text); identifiers are English.
- **Defaults are the sound the engine had before the chain** (bit-exact, `tests/test_chain_golden.py`).
- `kind` is `float`, `int`, `bool` or `choice`; `scope` is `global` or `speaker`; `apply` is
  `live` (ramps), `cut` (the fade) or `restart`; `store` says where the knob lives: `chain`
  (`<config>/chain.json`), `installation` (`instalacion.json`) or `session` (memory only).
- `implemented: false` would mark what is declared but not run by the engine yet (the stage then
  says `pending: true` and plays as its default). Since 2026-10-02 every stage and knob runs, and
  `chain_pending` is empty.
- **Everything added on 2026-10-02 is off by default**, and with the defaults the engine plays
  bit for bit as before (`tests/test_chain_golden.py`):
  - `diffuse.noise_tail`: a decaying-noise tail, a different seed per speaker, fed with the
    speaker's own mix; `level_db` (-30 to -6, default -12) is live, the rest go through the cut.
  - `bass.protect`: a Linkwitz-Riley high-pass (`cutoff_hz` 60-150, `order` 4 or 8) on every
    speaker whose kind cannot play bass (the Go 4), plus psychoacoustic harmonics
    (`harmonics_db`, live; -24 is none, 0 dB adds as much energy as the bass removed).
  - `bass.crossover`: the same high-pass on the small speakers; the bass-capable ones keep their
    signal through the crossover's all-pass, and the one in `to` also gets the low-passed mid,
    0.5 (L + R), before its own delay line, so it reaches the room aligned with the rest.
  - `limiter.true_peak`: 4x-oversampled detection with `lookahead_ms` of look-ahead (latency,
    on every speaker alike: `chain_latency_ms` grows by it).
  - `volume.avrcp`: the volume in the speakers (see below).
  - `eq.budget_db` and `eq.treble_cap_db` act on the curve that plays (through the cut) and keep
    the stored one; `eq.dead_band_db` is used when `eq_apply` computes a new curve.
  - `decorrelate.mean_ms` (it is latency: `chain_latency_ms` follows it) and `spread_ms`; the
    filter grows past `length` when the delay asked would not fit.
  - `decorrelate.assignment` (`order`, the default, or `mix`): which filter of the bank goes to
    which speaker. `mix` gives the least alike filters to the speakers with the most alike
    (pan, ambience); it lowers the correlation a model predicts, but in simulation what the
    speakers played did not follow (worse above 500 Hz in 20 of 32 cases,
    `docs/research/experimentos/16-…` §9), so it is only for the blind A/B. Choosing it goes through the cut; a `pan` or `ambience` that changes the best
    assignment waits for the next cut (the metric `reassign_pending` says so) instead of cutting.
- Each stage's live metrics (the stream's `chain` event): `ambience` `{mix_now, share}`,
  `decorrelate` `{active, length, mean_ms, worst_above_500, worst_feeds, assignment: {speaker:
  filter index}, assignment_mode, reassign_pending, notice}`, `diffuse` `{active, tail_db: {speaker: dB of tail
  against what the speaker plays}}`, `align` `{delay_now_ms, moving}`, `eq` `{active,
  max_boost_db, boost_energy_db}`, `bass` `{active, to, reason, removed_db: {speaker: dB the
  high-pass took}, harmonics_db: {speaker: dB of harmonics against the bass they came from},
  feed_dbfs}`, `volume` `{volume_db_now, mode}`, `limiter` `{kind, latency_ms, reduction_db,
  active_pct}`; every stage also has `pending`.
- An algorithm may be unavailable in this installation, with a reason: `bass.crossover`
  without a speaker of a bass-capable kind (`JBL Charge 6`; set `kind` on the speaker).
  Choosing it is `unavailable` (409).
- An available algorithm may carry a `notice` (null otherwise; additive, 2026-10-02):
  `decorrelate.group_delay` with more than 6 speakers says how far apart its filters are, e.g.
  "con 8 parlantes la separación es menor: el peor par sobre 500 Hz es 0,52 (con 3, 0,46); …".
  Until 2026-10-02 it was unavailable there, and the session could not start with 7 or 8
  speakers. The engine's `decorrelate` metrics carry the same text in `notice`.
- `chosen` is what the listener chose, sparse; `value` is what is in effect (choices over
  defaults). Choosing the default is still a choice; `chain_reset` removes it.
- Errors: unknown stage or param → `unknown_field`; unknown algorithm, out of range or not
  one of the choices → `out_of_range`; wrong JSON type → `type`; per-speaker params without
  `speaker`, or global ones with it → `bad_request`; unknown speaker → `not_found`.

**The old fields are the chain.** `extract_ambience`, `decorrelate` and `eq_active` are the
algorithm of `ambience`, `decorrelate` and `eq` (`true` is the stage's algorithm, `false` is
`off`); `rear_delay_ms` is `align.rear_delay_ms`; `volume_db` is `volume.volume_db`; the
speaker fields `pan` and `ambience` are `ambience`'s, `gain_db` and `muted` are `volume`'s.
Setting one reads back in the other. Since the chain is stored, `extract_ambience`,
`decorrelate` and `eq_active` now survive a restart (`volume_db` still starts at -20 dB and
`muted` is still not kept).

`state` carries `chain_summary` (`{stage: algorithm}`), `chain_latency_ms` (what the stages
add, equal on every speaker) and `chain_pending` (stages whose choice is not run yet).

**Files.** `<config>/chain.json` holds only choices: `{"v": 1, "chain": {stage: {"algorithm"?,
"params"?, "speakers"?}}}`. An invalid entry is dropped with a log line; an unreadable file is
kept as `chain.json.bad`; neither stops the service. A preset's chain goes to
`<config>/presets-chain.json` (`{"v": 1, "presets": {name: {stage: …}}}`, every stage but
`volume`); `presets.json` keeps its shape, so an older version still starts with the new
files. A preset with no entry there (saved before the chain) leaves the chain as it is when
loaded.

```bash
curl -s -H "$H" $U/command -d '{"v": 1, "op": "chain"}'
curl -s -H "$H" $U/command -d '{"v": 1, "op": "chain_set", "stage": "limiter", "params": {"release_ms": 400}}'
curl -s -H "$H" $U/command -d '{"v": 1, "op": "chain_set", "stage": "ambience", "speaker": "JBL Go 4 Red", "params": {"pan": -0.5}}'
curl -s -H "$H" $U/command -d '{"v": 1, "op": "chain_reset", "stage": "limiter"}'
```

## The sync estimator (spec 2026-10-03)

A base estimator on the server takes every sync measurement (today the loop's, from the
server's microphone; later point and continuous ones from phones) and **suggests** absolute
delays; nothing changes until `sync_apply` (d-7c8794-2c6f91). It runs on its own thread.

| Op | Args | Effect |
|---|---|---|
| `sync_state` (read) | — | `{"settings", "suggestion", "applied_id", "levels", "sources", "dropped"}`. `suggestion`: `{"id", "delays_ms", "current_ms", "sigma_ms", "spread_now_ms", "spread_after_ms", "anchor", "based_on", "at", "reason", "drift_ppm", "jumps"}`, or null. `levels` are statistics only (d-7c8794-e61118) |
| `sync_set` (control) | `changes` (object of settings) | validated (`out_of_range` otherwise), written to `sync.json` next to `chain.json`, and the estimator refits |
| `sync_apply` (control) | `suggestion_id` (optional) | applies the current suggestion's delays through the cut; speakers without a suggestion keep theirs. `conflict` if there is none, if `suggestion_id` is not the current one, or if it was applied already |
| `sync_explain` (read) | — | every setting's recommendation, how it changes the sound, and a SIMULADO figure, plus `together` (the whole configuration). Built on the estimator's thread: `{"pending": true}` until it is ready |

The snapshot carries `sync_suggestion` (the suggestion plus `applied` and `method`, never the figures).

## Quality, the radio, the speakers' volume (spec 2026-10-02 §3, §5, §6)

All additive to version 1.

### Quality

While a session plays, a BS.1770 meter runs on the input (per channel) and on each speaker's
output, on the engine thread (~0.3-0.5 ms per 4096-sample block for 3 speakers, measured on the
Mac). The stream's `quality` event and `state.quality` (null without a session):

```json
{"input": {"m": -23.1, "s": -23.4, "i": -23.0, "tp": -4.2, "psr": 15.1},
 "outputs": {"JBL Go 4 Red": {"m": -26.0, "s": -26.3, "tp": -6.0, "psr": 14.6,
                              "limiter_pct": 0.0, "flattening": false}, "...": {}},
 "sum": {"m": -22.8, "s": -23.0},
 "net_gain_lu": 0.4, "chain_gain_lu": 0.4,
 "flattening": false, "flattening_outputs": [],
 "tp_max": -6.0, "limiter_pct_max": 0.0, "cost_ms": 0.32}
```

- `m`, `s`, `i`: momentary (400 ms), short-term (3 s) and integrated (gated) loudness, LUFS;
  `tp`: the highest true peak (4x) of the last 3 s, dBTP; `psr` = `tp` - `s`. The input's PSR
  is the lower of its two channels', so that one speaker playing one channel compares like with
  like. Silence is `null`, never an infinity.
- `sum`: the outputs' powers added (every speaker weighted 1). `net_gain_lu` = `sum.s` -
  `input.s`; `chain_gain_lu` is the same without the digital volume (the listener's choice).
  With the panel at 0 dB, the defaults and a wide stereo mix it is 0 ± 1 LU; a centred mix
  reads up to +1.76 LU (three speakers, each about the mono sum), and the ambience mix moves it.
- `flattening`: an output's PSR is more than 1 dB under the input's while both carry music.

### The radio

The service follows WirePlumber's journal for the bluez5 sink's `reduce bitpool` lines: each
is a dropped packet, an audible cut of ~24-40 ms (`radio.py`). Each drop goes to the session's
cut log (`health.cuts`, kind `radio`). `state.radio` and the stream's `radio` event:

```json
{"available": true, "reason": null, "since_s": 812.4, "lines": 2410, "drops_seen": 3,
 "speakers": {"JBL Go 4 Blue": {"identified": true, "address": "90:F2:60:E3:07:39",
   "bitpool": 38, "bitpool_max": 40, "bitpool_median_60s": 40, "drops_total": 3,
   "drops_60s": 1, "drops_per_min": 1.0, "last_drop_s": 12.5, "write_mtu": 895,
   "sinks": ["0x5a1000"]}}}
```

`available` is false (with a `reason` in Spanish for the panel) without `journalctl` (the Mac),
while the log level is not raised, or with nothing playing. A link the journal did not tie to a
speaker is keyed by its sink pointer, with `identified: false`; the tie is only printed when a
speaker starts playing, so the log must be raised before that. Under `--simular` a simulated
monitor writes journal-like lines (with a drop now and then, only while the log is on) and
`simulated: true` says so.

`radio_log` is a **system change**. Before running `wpctl set-log-level`, the service appends
the change and how to revert it to `<config>/cambios-de-sistema.txt`. `light` raises only
`spa.bluez5.sink.media` and `spa.bluez5` to debug; `heavy` is global debug (a warning says
journald may drop lines). It runs on a worker thread: the reply is the status with
`pending: true`, and `state.radio_log` follows it:

```json
{"available": true, "active": true, "mode": "light", "heavy": false, "previous": null,
 "verified": true, "error": null, "pending": false,
 "changes_file": "/home/u/.config/aurasync/cambios-de-sistema.txt"}
```

The level goes back on `shutdown`, SIGTERM, Ctrl-C and when a session fails (a normal `stop`
keeps it); at start the service reverts whatever a killed run left behind. The CLI mirror is
`aurasync radio-log on|off|status [--modo light|heavy]`: through the running service, or by
itself (written down the same way) when none runs.

### The speakers' volume (`volume.avrcp`)

With `chain_set {"stage": "volume", "algorithm": "avrcp"}`, `volume_db` becomes the PipeWire
volume of the loudest speaker's `bluez_output…` sink (cubic curve: 80 % is -5.81 dB), every
speaker keeping its difference with it, and the digital volume stays at 0 dB. Each change runs
`pactl set-sink-volume` and is **read back** with `pactl get-sink-volume`; a speaker that did not
take it is reported in `state.volume_avrcp` and `warnings`, and nothing pretends:

```json
{"state": "on", "pending": false, "volume_db": -20.0, "top_db": -5.81,
 "offsets_db": {"JBL Go 4 Red": 0.0, "JBL Go 4 Blue": -1.2},
 "speakers": {"JBL Go 4 Red": {"asked_pct": 46.4, "read_pct": 46.0, "read_db": -20.2, "ok": true}},
 "error": null}
```

`state` is `off` (digital), `entering`, `on`, `leaving` or `failed` (chosen but not in effect:
the volume stays digital, and the next volume change tries again). **Changing the mode keeps the
level heard**, as digital plus speaker: entering, the speakers go down first and the digital
volume rises to 0 dB through the cut only once every speaker confirmed; leaving, the digital
volume goes down through the cut first and the speakers go back to where they were ~0.6 s later
(when that quieter audio is the one heard). The transient is a short dip, never a burst. The
Go 4's real curve is not PipeWire's (-6 dB asked gave -4.1 dB at the microphone,
experimentos/10 §5.4): the mapping stays PipeWire's until it is measured. Under `--simular` the
simulated room applies these volumes.

### The A/B and loudness

During a blind A/B the service measures the short-term loudness of the sum of the outputs while
A or B plays (never X: it would tell which one X is), from 3.3 s of audio after each switch. With
`ab_start {"match_loudness": true}` the louder preset is played quieter by the difference, with
a gain on the output (the presets are not touched); `ab_stop` takes it away. `state.ab` (and the
reply of `ab_stop`) carry:

```json
{"match_loudness": true, "loudness_lu": {"a": -23.1, "b": -23.0, "diff": 0.1},
 "compensation_db": {"a": 0.0, "b": -2.95}}
```

`loudness_lu` is what is heard (compensation included); `diff` is `b - a`.

### Open streams

`state.health.streams_open` is how many `GET /v1/stream` connections are open now.

## Remote clients: the PWA, HTTPS, pairing (d-7c8794-37f9bc)

The panel can run as a PWA served from GitHub Pages and talk to the service of each machine
over the local network. The service side is `access.py`, `clients.py`, `pairing.py`, `tls.py`,
`lan.py`, `remote.py` and `mdns.py`.

### HTTPS

With `"tls": true` the service also listens on `https_port` (8443); plain HTTP stays on `port`
and still serves the local panel. On the first start it makes, in `~/.config/aurasync/tls/`
(directory 0700, keys 0600):

- `root.pem` / `root.key`: a self-signed root, ECDSA P-256, valid 10 years, made once. Its
  critical `NameConstraints` permit only `.local`, `localhost`, the host name, and private,
  loopback, link-local and CGNAT addresses: a phone that trusts it does not trust it for any
  public name.
- `server.pem` / `server.key`: signed by the root, valid 397 days (Apple's limit is 398), with
  SANs `aurasync.local`, `localhost`, the host name (and `<host>.local`), `127.0.0.1` and the
  current LAN addresses. Names the root may not certify are left out. A thread checks every
  60 s and makes a new one (and loads it into the running server) when an address changes or
  30 days remain; the root never changes.

Why `cryptography` and not the `openssl` program: it is already installed (Bumble requires it,
with wheels for Linux aarch64 and macOS arm64), it builds name constraints and SANs as objects,
and the Mac's `openssl` is LibreSSL, whose flags differ.

Installing the root on a phone (the root, not the server certificate; the steps are in
`host/README.md`):

| Route (no credentials) | Content type | For |
|---|---|---|
| `GET /v1/tls/root.mobileconfig` | `application/x-apple-aspen-config` | iPhone: Safari → install the profile → *Settings → General → About → Certificate Trust Settings* → enable it |
| `GET /v1/tls/root.crt` | `application/x-x509-ca-cert` (DER) | Android: *Settings → Security → Encryption & credentials → Install a certificate → CA certificate* |
| `GET /v1/tls/root.pem` | `application/x-pem-file` | anything else (`curl --cacert`) |

The profile is unsigned (iOS calls it "Not Verified"); its UUIDs derive from the root, so
installing it twice replaces it. Compare the SHA-256 the phone shows with `aurasync tls info` or
`hello`. **Not yet tried on a phone.**

Revert: `"tls": false`; delete `tls/` for a new root (and reinstall it on every phone); remove the
profile (iPhone: *VPN & Device Management*) or the user CA (Android: *User credentials*).

### `GET /v1/hello` (no credentials)

```json
{"v": 1, "ok": true, "result": {
  "service": "aurasync", "contract": 1, "version": "0.0.0", "id": "62468c52", "name": "pc-ryzen5",
  "tls": {"enabled": true, "port": 8443, "root_sha256": "37:47:…", "cert_sha256": "E8:01:…"},
  "pairing": {"accepting": true, "first_window_s": 579.3}}}
```

`id` is a random id of this device, kept in `clients.json`. `tls` is `{"enabled": false}` without
HTTPS. `pairing.accepting` says whether a request can be approved without an admin at hand (the
first-client window is open, or a code is active); requests are always accepted for an admin to
decide.

### Clients and scopes

A client token is `asc_<id>_<secret>`: `id` is 8 hex characters (shown, logged, used to revoke),
`secret` is 256 random bits. `~/.config/aurasync/clients.json` (0600, written atomically) keeps
`sha256(salt ‖ secret)` with a salt per client, the name, scope, creation, last use and last
address; the token itself is never stored. A client unused for 180 days stops working. The master
token stays valid as `admin`; rotating it does not affect any client, and no client token derives
from it.

| `op` | Fields | Result |
|---|---|---|
| `clients` | — | `{"clients": [{"id", "name", "scope", "created", "last_used", "last_ip", "expires"}], "master": {"scope": "admin"}}` |
| `client_revoke` | `client` (8 hex) | `{"revoked": "<id>"}`; its open stream ends and its tickets stop working |
| `client_rename` | `client`, `name` | `{"client": {…}}` |
| `pair_status` | — | `{"requests": [{"id", "name", "ip", "scope", "check", "status", "age_s"}], "window": {"open", "remaining_s"}, "code": {"active", "code", "expires_in_s"}}` |
| `pair_start` | `seconds` (30–600, default 120) | `{"code": "123456", "expires_in_s": 120}` and the code is printed on the service's terminal (never in the log buffer, which `read` clients see) |
| `pair_approve` | `request`, `scope` (optional) | `{"client": {"id", "name", "scope"}}` |
| `pair_deny` | `request` | `{"denied": "<request>"}` |

All of them need `admin`. The fields are `client` and `request` because `id` is the message's own
id in the envelope.

### Pairing

```text
→ POST /v1/pair/request   {"name": "Fabián's iPhone", "scope": "control", "code": "123456"}   (scope, code optional)
← {"v": 1, "ok": true, "result": {"id": "l1FgKngi-Ng8XOTaOLrzBydd", "check": "8199", "status": "pending", "expires_in_s": 300}}
→ GET /v1/pair/l1FgKngi-Ng8XOTaOLrzBydd        (poll every 1–2 s)
← {"v": 1, "ok": true, "result": {"status": "pending"}}
← {"v": 1, "ok": true, "result": {"status": "approved", "token": "asc_942e018c_…", "client": {"id": "942e018c", "name": "…", "scope": "control"}}}
← {"v": 1, "ok": true, "result": {"status": "delivered"}}    (every poll after the one that carried the token)
```

`status` is `pending`, `approved` (only in the reply that carries the token), `delivered`,
`denied`; an unknown or expired id is 404 `not_found` and counts as a failed attempt. The PWA
shows `check` (4 digits) so the owner can match it with the request an admin sees. A request is
approved:

1. by an admin (`pair_approve`, which may change the scope);
2. alone, as **`admin`**, if it is the first request within `pair_window_s` (600 s) of the
   service starting **and no client is paired yet** (the master token does not count). Once a
   client exists the window is closed until a restart with no clients;
3. alone, with the **scope it asked for**, if it carries the active 6-digit code. A code works
   once; 5 wrong codes burn it. At start, on a terminal and with clients already paired, the
   service prints a code valid for 10 min; `aurasync clients code` makes a new one.

A pending request lives 5 min; an approved token nobody collects within 5 min is revoked. One
pending request per address (a new one replaces it), 8 in all (then 503 `busy`). The body is
checked like a contract message (`name` 1–64 printable characters, unknown fields refused).

Hook for later: a code played through the speakers would call `PairingDesk.start_code` and give
the digits to the `tone` source instead of the terminal.

### The stream from another origin

`EventSource` cannot send headers. Either read the stream with `fetch()` and a `ReadableStream`,
which can send `Authorization: Bearer` (and, in Chrome, `targetAddressSpace: "local"`), or:

```text
→ POST /v1/stream/ticket            Authorization: Bearer asc_…        (scope read)
← {"v": 1, "ok": true, "result": {"ticket": "…", "expires_in_s": 30}}
→ GET /v1/stream?ticket=…           (new EventSource(url))
```

A ticket opens one stream, once, within 30 s, and only `/v1/stream` (anywhere else it is not a
credential). It belongs to the client that asked: a revoked client's ticket is refused. The
server logs no request line, so the ticket never reaches a log. At most 64 unused tickets.

### CORS and Chrome's Local Network Access

Only the origins in `panel_origins` (default: `https://fabaindaiz.github.io` and Vite's
`http://localhost:5173`; origins only, without a path) get `Access-Control-Allow-Origin` (the
origin itself), `Vary: Origin` and `Access-Control-Expose-Headers: Retry-After`, on every reply
including errors and the stream. No `Access-Control-Allow-Credentials`: no cookie crosses sites,
the PWA sends its bearer. `OPTIONS` on `/v1/*` answers their preflight with `204`,
`Access-Control-Allow-Methods: GET, POST, PUT, PATCH, DELETE`,
`Access-Control-Allow-Headers: Authorization, Content-Type` and `Access-Control-Max-Age: 600`.

A request with any other `Origin` is refused with 403, whatever its credential; a request with
no `Origin` (curl, a same-origin GET) is not affected. The `Host` check stays: `aurasync.local`,
the host name, `<host>.local`, `localhost` and the machine's addresses, recomputed every 30 s and
at once (at most every 2 s) when an unknown name arrives, so a new DHCP address works without a
restart.

Chrome's Private Network Access preflight (`Access-Control-Request-Private-Network`) is paused;
Local Network Access replaced it with a permission prompt (Chrome 142), which needs no header from
the device. The service still answers `Access-Control-Allow-Private-Network: true` when a preflight
asks, for older Chromium builds; it costs nothing. With HTTPS on the device there is no mixed
content, so this works in Safari too once the root is installed (research H §1).

### mDNS (optional, off)

`"mdns": true` runs `avahi-publish-service "aurasync on <host>" _aurasync._tcp <https port>
v=1 id=… http=… tls=1 https=… fp=<root sha256, 16 hex>` and `avahi-publish-address -R
aurasync.local <LAN ip>` as child processes (package `avahi-utils`). They register through the
running avahi-daemon, leave nothing in `/etc`, are stopped on every exit of the service, and on
Linux die with it even after a SIGKILL (`PR_SET_PDEATHSIG`). Without `avahi-publish-service` (the
Mac) the service logs it and goes on. A second device on the network collides on `aurasync.local`:
that publisher exits and the device stays reachable by its host name. `python-zeroconf` was not
used: it would be a new dependency and a second mDNS responder next to avahi-daemon.

### The panel's pairing QR and the PWA

`GET /pairing.svg` (the "Conectar teléfono" dialog of the panel this service serves) is a QR of
the **PWA's pairing link**, never of a token:

```text
https://fabaindaiz.github.io/bluetooth-sync/#d=<LAN address>:<https_port>&fp=<root SHA-256, hex>
```

The fragment never reaches GitHub Pages. The PWA reads it, asks `GET /v1/hello` at that address,
compares `tls.root_sha256` with `fp` (and refuses to pair on a mismatch), and sends
`POST /v1/pair/request`. Without HTTPS or without a LAN address the route answers 404 with the
reason. (`rest.pairing_link`; the terminal's QR at start still carries the master token for the
panel served over HTTP, since the terminal is the owner's.)

What the PWA does with this API (`host/web/src/transport.ts`, the one transport of the panel):
every request carries `Authorization: Bearer <client token>` and no cookie; the stream is a
ticket (`POST /v1/stream/ticket`) and `new EventSource(…/v1/stream?ticket=…&since=…)`; a 401 means
the client was revoked (the PWA forgets the token and asks to pair again), a 403 is a scope or an
origin refused, a 429 waits `Retry-After`, and a request that throws is the device out of reach
(the PWA still opens, from its service worker, and says so). The service worker never caches a
reply of this API: it answers only the PWA's own files.

### CLI

```bash
aurasync clients list                      # clients and pending requests (from clients.json if the service is down)
aurasync clients approve <request> [--alcance read|control|admin]
aurasync clients deny <request>
aurasync clients revoke <client>           # through the service, or on clients.json when it is down
aurasync clients code [--segundos 120]     # a pairing code
aurasync tls info                          # root and server certificate: paths, SHA-256, names, expiry
aurasync tls root                          # the root's path, SHA-256 and the links to install it
```
