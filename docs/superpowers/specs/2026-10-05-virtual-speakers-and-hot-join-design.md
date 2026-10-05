# Virtual speakers, sessions without speakers, and joining a session — design

**Date:** 2026-10-05 · **Status:** designed with the user section by section (2026-10-05); the
user asked for a manual review of every decision once the implementation plan is written.
**Decisions:** d-7c8794-0e5063 (virtual = `sink: null`), d-7c8794-05bdd6 (a session opens with
real speakers absent), d-7c8794-618666 (join only on request; only a `lost` speaker returns by
itself).

## 1. What it is and why

Today a session needs every speaker of the installation connected (`missing_speakers` makes
`open()` fail), and every speaker is a PipeWire sink with its own `pw-play` (or a channel of the
combined stream). So nothing can be heard without the speakers in the room.

The user wants, in the user's words: *the service should be able to start without speakers
connected, and simulate n speakers that are only computed, never instantiated with `pw-play`,
to send the result to the monitor.* The first use is `HP-O16` with only the WH-CH520 headphones:
hear the chain and its effects through the headphone monitor (`mix` or `binaural`; `stereo` is
before the chain, so it carries no effects). Later the same mechanism lets a real speaker join a
running session, and the model must not close the door to **wired outputs alongside Bluetooth**.

What is real and what is not, unlike `--simular`: the input is the real `aurasync` sink, the
engine and the chain are real, the monitor really plays on the headphones. Only the virtual
speakers have no device.

## 2. Phases

- **Phase 1 (what `HP-O16` needs now):** virtual speakers in the installation; a session opens with
  real speakers absent and computes them without output; a clock of its own when no real output
  exists; the monitor gets every channel.
- **Phase 2:** a speaker joins a running session at the user's request; a speaker that was
  playing and dropped returns by itself.
- **Not in this spec:** wired outputs as a feature. Only the seam (§3, `output_kind`).

## 3. The model (phase 1)

`Parlante.sink` becomes `str | None`. **A speaker without a sink is virtual.** No separate
`virtual` flag: two fields saying the same thing can disagree. `Parlante.virtual` is a derived
property. Existing `instalacion.json` files are unchanged (all have a `sink`); `sink` stays a
required key, and its value may be `null` (absence and `null` are not the same thing: a missing
key is still an error). The duplicate-sink rule ignores `null`.

**Output kind**, derived from the sink: `virtual` (no sink), `bluetooth` (`bluez_output.*`),
`wired` (any other node). Everything Bluetooth-specific — address, battery, RSSI, codec,
`volume.avrcp`, the radio monitor — applies only to `bluetooth`. Today the snapshot derives an
"address" from any sink; that becomes conditional. A wired output later needs no model change.

**Adding one:** op `speaker_add_virtual {name?}` (scope `admin`, like `speaker_add`; through
`POST /v1/command`, as `speaker_add` is). Without a name: "Virtual 1", "Virtual 2"… It takes the
next free role of the layout, as `speaker_add` does. Session stopped, like `speaker_add`.
Removing uses `speaker_remove`.

**Each speaker's output state during a session**, new snapshot field `output`:

| `output` | Meaning | Computed | Heard in the room | Phase |
|---|---|---|---|---|
| `virtual` | no sink, by design | yes | no (reaches the monitor) | 1 |
| `absent` | real, not connected when the session opened, or taken out with `leave` | yes | no | 1 |
| `playing` | its stream is alive | yes | yes | today |
| `lost` | was playing in this session and its stream died | yes | no | today; returns by itself in phase 2 |

`null` without a session. `muted` stays orthogonal. The existing `playing` field stays and equals
`output == "playing"`.

**Measurement only sees what sounds.** Calibration and the recalibration loop use only the
`playing` speakers: the others never reach the microphone, and measuring them would produce false
numbers. Calibration requires every participant to be `playing` and says which are left out; the
loop still needs at least two.

**The monitor gets every channel**, virtual and absent included. A virtual speaker has no sink,
so it never enters the monitor's forbidden targets.

## 4. The session: `OutputSet` and the clock (phase 1)

New module `host/src/aurasync/outputs.py` (English). `OutputSet` is the only thing `AudioSession`
talks to for output:

- Inside: the **real part** — today's `Reproductor` or `ReproductorCombinado`, opened **only with
  the sinks of the speakers that will play** (in `--simular`, the `SimulatedPlayer`, so the
  simulated room never hears a virtual speaker either) — and the set of speakers that are only
  computed.
- `write(blocks)` takes one block per speaker **name** (not node), sends the playing ones to the
  real part and drops the rest.
- Keeps the interface the session already uses: `vivos`, `pids`, `mal_ruteados`, `reparar_ruteo`,
  `soltar`. `step()` changes little; meters, quality, telemetry and the monitor keep receiving
  every channel.
- Owns each speaker's `output` state and hands it to the snapshot.

**Opening changes:**

- `missing_speakers` no longer fails `open()`: missing real speakers are `absent`.
- **With no real speaker connected, no `pw-play` and no combine module are opened**: only the
  `aurasync` input sink.
- The routing check and repair run as today, on the real part only; with no real part there is
  nothing to check.

**Losing every real speaker no longer ends the session.** Today `every speaker disconnected`
closes it. Now they are `lost`, a cut is recorded, and the session goes on (heard on the
monitor). Ending it is the user's call.

**The clock** (`Pacer`, inside `OutputSet`):

| Situation | What paces the engine |
|---|---|
| at least one `pw-play` alive | the speaker's pipe, as today (the write blocks while it is full) |
| all virtual/absent, audio coming in | reading the `aurasync` sink, which waits for a whole block |
| all virtual/absent, nothing playing | the `Pacer`: sleeps until the next block's deadline on `time.monotonic` |

The deadline is a pure function of one clock: `origin + k · block / rate`, with `k` the blocks
since the origin — never a sum of the sleeps (card `derive-state-from-one-clock`). When the input
brings data the origin moves to now, so returning to silence does not burst to catch up. If the
`Pacer` finds itself more than two blocks late it resynchronises instead of bursting. The same
logic as `SimulatedInput.leer`.

**INFERIDO, to check on `HP-O16`:** with no device driving it, PipeWire assigns the `aurasync`
sink to its Dummy-Driver, which runs in real time. Checked when implementing (blocks arrive in
real time with an application playing) and written in the experiment.

The monitor hangs from `step()` after the write, as today. It is not synchronised and drops
blocks when behind, so the drift between the input clock and the headphones' shows as an
occasional dropped block — what its design already accepts.

## 5. Joining and leaving (phase 2)

Two ops, only with a session running (scope `control`):

- `speaker_join {speaker}` (`POST /v1/speakers/{name}/join`): a real `absent` or `lost` speaker
  whose sink exists becomes `playing`.
- `speaker_leave {speaker}` (`POST /v1/speakers/{name}/leave`): a `playing` speaker leaves the
  session without stopping it; it becomes `absent` and is still computed (heard on the monitor).

**How it joins without cutting the others more than needed:**

1. **Prepare in the background** while everything keeps playing: build the new real part with the
   new set of sinks, prime it with silence, check its routing. In `combinado`, a new combine
   module with another node name, briefly alongside the old one; in `separado`, only the new
   `pw-play`.
2. **Swap at the bottom of a cut**: `motor.cortar(action)` (the 80 + 80 ms fade presets use). The
   action, on the engine thread, replaces the real part and closes the old one.
3. If preparation fails (the stream did not reach its sink), nothing changes: the speaker stays
   as it was, and the error goes to the log and to `errors`.

`leave` uses the same path with one sink fewer (in `separado` it is only `soltar`).

**Sync after a join:** rebuilding the stream can change each speaker's offset (experiment 05: a
waking A2DP stream comes back with a different offset). If the recalibration loop is on, it
measures again by itself; if not, the panel warns "la alineación puede haber cambiado:
recalibrá". Nothing is blocked.

**Returning by itself, only for passing failures:**

- **Only a `lost` speaker returns by itself**: one that was playing in this session and whose
  stream died without anyone taking it out. When the system observer (every 3 s) sees its sink
  again, the service runs the same `join` and logs it ("volvió JBL Go 4 Red").
- **Never by itself:** an `absent` speaker (not connected at the start, or taken out with
  `leave`), or a virtual one.
- **Brake:** at most one attempt every 10 s per speaker; if it drops again 3 times within 5
  minutes, it stops trying and stays `lost`, with **Reintentar** in the panel (which is a
  `speaker_join`).
- **It does not reconnect Bluetooth.** It reacts when the sink reappears, that is, once the
  speaker or BlueZ reconnected. `bluetoothctl connect` stays the user's `connect`, so as not to
  fight the radio.

## 6. Contract

`docs/control-api.md`, version 1, additions only:

- Ops `speaker_add_virtual {name?}`, `speaker_join {speaker}`, `speaker_leave {speaker}`; REST
  routes for join and leave.
- Snapshot, per speaker: `output` (`virtual` | `absent` | `playing` | `lost` | `null`),
  `output_kind` (`virtual` | `bluetooth` | `wired`); `sink` may be `null`; for a non-Bluetooth
  speaker `address`, `battery_pct`, `codec`, `rssi_dbm`, `modalias` are `null`.
- **Both version mixes**, because the PWA (GitHub Pages) and the service update separately (card
  `no-simultaneous-deploy`): an old PWA against a new service must not break on `sink: null` or
  `address: null` (audit every read of `sink` and `address` in `panel/app.js` and `host/web/src/`);
  a new PWA against an old service derives `output` from `playing` when it is missing.

| Case | Answer |
|---|---|
| `speaker_add_virtual` with a name already taken | `conflict` |
| `speaker_join`/`speaker_leave` without a session | `conflict` |
| `speaker_join` of a virtual or `playing` speaker | `conflict` |
| `speaker_join` whose sink does not exist | `unavailable` ("connect it first") |
| `speaker_leave` of a speaker that is not `playing` | `conflict` |
| background preparation fails | the speaker stays as it was; log and `errors` |

## 7. Panel

- Each speaker shows its state — *Virtual*, *Sin conectar*, *Sonando*, *Perdido · reintentando* /
  *Perdido* — and the button that applies: **Hacer entrar**, **Sacar**, **Reintentar**.
- **Agregar parlante virtual** next to the devices.
- After a `join` with the loop off, the "recalibrá" warning.
- Calibrating says which speakers are not playing and leaves them out.

## 8. Verification

Unit and integration (`hatch test`):

- `Pacer`: all virtual and no input, N blocks take N · block / rate (± one block); the same
  instant reached two ways gives the same deadline; a large delay resynchronises without a burst.
- `OutputSet`: only playing speakers reach the real part; all virtual launches no process (a fake
  `Popen` that fails if called); `lost` when a stream dies; the session goes on when all die.
- Session: opens with speakers absent; the monitor gets every channel; calibration and the loop
  see only `playing`.
- Join/leave: the swap happens at the bottom of the cut; a failed preparation leaves the state
  intact; the automatic return respects 10 s and 3-in-5-minutes and never brings in an `absent`
  or a left speaker.
- Contract: validation of the new ops; `sink: null` survives save and load; both version mixes.
- Test doubles (card `test-double-fidelity`): the fake real part refuses what the real one
  refuses (a sink that does not exist, a write after close).
- Browser (`tests_browser`): add a virtual speaker, see its state, join one, against `--simular`.

On real machines, written in `docs/research/experimentos/`:

- **`HP-O16`, phase 1, once the user allows the headphones:** blocks arrive in real time with all
  virtual (confirms or corrects the Dummy-Driver INFERIDO), monitor drops per minute, and no
  `pw-play` towards a speaker (`pw-dump`).
- **`PC-Ryzen5`, phase 2, with the Go 4:** switch one off and on (automatic return), join and
  leave, and the offset before and after, measured with the microphone.

Until that measurement exists, phase 2 is **not validated with speakers**, said so in the README
(card `unrunnable-system-moves-the-gate`: `HP-O16` cannot see Bluetooth composition).
