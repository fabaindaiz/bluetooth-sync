# A base sync estimator fed by continuous and point measurements from many microphones — design

Date: 2026-10-03. Status: **approved in conversation (four sections, each approved); the user
then said "apruebo el plan, continúa autónomamente"**. Built and tested on `PC-Ryzen5` in
simulation first; the speaker tests are prepared protocols. Roadmap i-7c8794-737d4e (extends
i-7c8794-4745b4 and i-7c8794-e3e40d). Research behind it: research/13 §5.3, experimentos/16
§4.1.1, research/03 §3.1.

## 1. Why

The listener (2026-10-03): calibrate with the microphones of the phones connected to the
panel, with the invisible pattern under the music, and send the calibration to the server, so
that whoever is connected can help recalibrate when needed. The pattern must **tell the
speakers apart**, so a microphone that does not hear every speaker still calibrates the ones
it hears. Then: continuous calibration from **several** microphones, and a **point**
calibration, invisible, one microphone at a time, **computed on the phone**. And: "there
should be a base estimator on the server that is fed back by the global or point measurements
as they are taken".

What already exists (VERIFIED, code): the masked probe (`dsp/probe.py`, an independent noise
per speaker, -20 dB under its own music, 300 Hz-8 kHz); its estimator (`probe_measure.py`,
band-limited PHAT, two halves, consensus — the consensus among the speakers whose halves
agree since 2026-10-03, experimentos/16 §4.1.1); `ArrivalLoop` (`arrival_loop.py`, one track
per speaker, Theil-Sen drift, a jump believed when repeated), fed by one microphone; and the
noise calibration (`session.Calibration`), which sets delays and gains.

## 2. Decisions taken in the conversation

| Id | Decision |
|---|---|
| d-7c8794-589dec | The engine thread always processes fixed-length data; history lives apart, asynchronously (the user, while fixing the microcuts). The estimator runs on its own thread; the engine and the HTTP threads hand it measurements through a queue |
| d-7c8794-ee5d47 | A point measurement either **sets the target** (`target`: aligned at that spot) or **votes** (`vote`, with a configurable weight). Chosen when it is taken; the default is a knob |
| d-7c8794-e61118 | The estimator corrects **delays only**. Measurements carry each speaker's level, which the estimator keeps as **reference statistics** (per microphone and position) and never feeds back to gains or EQ: the microphones are not accurate |
| d-7c8794-2c6f91 | The estimator **suggests absolute target delays** (never corrections on top of corrections: experimentos/09 §5) and **the listener applies them from the panel**, through the usual cut. It coexists with `ArrivalLoop` and the calibration, which keep working as today |
| d-7c8794-0a4586 | Every setting carries a **recommendation** and an explanation, **in words and visual**, of how it changes the sound, **one by one and together** (the whole current configuration) |

## 3. The model

For microphone `m` (one physical microphone at one position), speaker `s`, time `t`:

    arrival(m, s, t) = electronic(s, t) + base(m, s) + common(m, t)

- `common(m, t)`: the phone's input latency and clock offset for that recording. It is the
  same for every speaker of one recording, so only differences within a recording are used.
- `base(m, s)`: the acoustic path from that position to each speaker (~2.9 ms per metre),
  fixed while the microphone does not move.
- `electronic(s, t)`: what is corrected; it drifts (~22 ppm measured, experimentos/10) and
  jumps when a stream resynchronises (6.52 ms on 2026-10-02).

What each kind of measurement contributes:

- **point, `target`**: an anchor. Its `base(m, ·)` is defined as 0: the alignment is for that
  spot. A new target replaces the previous anchor (the old one stays in the history).
- **point, `vote`**: `base(m, ·) ≈ 0` with weight `point_vote_weight`.
- **continuous**: its `base(m, ·)` is learnt (its first measurements), so it contributes only
  *changes* of `electronic`. A microphone that moves is detected (§4.3) and gets a new base.

**Until a point `target` exists, the server's own microphone position is the anchor** (what
the loop does today: it aligns at the fixed microphone). The noise calibration's delays enter
the same way, as a point `target` from the server's microphone.

**Only anchors and votes place a speaker.** A continuous position's `base` is free, so it says
nothing about a speaker's offset, only about its drift and jumps (shared by every position). A
speaker that no anchor or vote has heard gets no suggestion and keeps its delay.

A recording needs at least `min_speakers` speakers heard (default 2: one arrival has nothing
to be compared with). Speakers that were not heard are simply absent.

## 4. The estimator

### 4.1 Pieces

- `sync_measurement.py` — `Measurement`, one shape for every source:
  `source_id`, `position_id`, `kind` (`continuous` | `point`), `role` (`target` | `vote` |
  `None`), `weight`, `t` (server clock, the middle of the window), `arrivals_ms` and
  `halves_ms` per heard speaker, `levels_db` per heard speaker, `quality` (what the browser
  applied: `echo_cancellation`, `noise_suppression`, `auto_gain_control`, sample rate), and
  `origin` (`server` | `browser`). Validation (types, ranges, known speakers, `t` within
  `MAX_AGE_S` of now) lives here.
- `sync_estimator.py` — `SyncEstimator`: a thread, a queue (`submit` is O(1)), the settings,
  and the method. After each accepted measurement it publishes a `Suggestion`: absolute
  delays per speaker, the confidence of each (ms), the spread the suggestion would leave, which
  measurements it rests on, the anchor, and the reason when it suggests nothing. It never
  writes the motor or the installation.
- `sync_methods.py` — the three methods behind one interface
  (`fit(measurements, settings, now) -> Fit`): `robust_ls` (recommended), `tracks`, `kalman`.
- `sync_levels.py` — the level statistics: per (source, position, speaker) the median, the
  spread and the trend over the window; per speaker the spread across microphones.
- `knob_docs.py` — the extended descriptor of a setting (§5), shared format.

### 4.2 Methods

All three use §3 and the same inputs; they differ in how they estimate.

- **`robust_ls` (recommended).** On each new measurement, over the last `window_min`:
  unknowns `e_s` (offset) and `d_s` (drift, ms/s) per speaker, with segments split at
  confirmed jumps, and `b_{m,s}` per (position, speaker) for continuous positions. Residuals
  are within-recording differences (each recording's `common` removed by subtracting its
  weighted mean). Anchors: the current target's `b = 0`; votes: a prior `b = 0` with their
  weight. Huber loss with `huber_ms`, iteratively reweighted least squares (a few iterations;
  tens of unknowns). A jump is a segment break only after `jump_repeats` agreeing
  measurements. The gauge (a common constant) is fixed by the smallest delay at 0, as the loop
  does.
- **`tracks`.** `ArrivalLoop`'s logic per (position, speaker): Theil-Sen drift, a jump held
  until repeated; each position's track gives changes; the suggestion is the anchor plus the
  median of the changes across positions.
- **`kalman`.** State `[e_s, d_s]` per speaker plus `b_{m,s}` per continuous position; each
  measurement updates only the speakers heard; Mahalanobis gating at `kalman_gate_sigma`
  replaces the repetition rule; process noise from `kalman_drift_ppm`.

The three are compared in simulation (§7) and the recommendation follows the table, not this
document: if `tracks` or `kalman` wins, the recommendation changes.

### 4.3 A microphone that moved

If a continuous position's within-recording differences all shift by more than
`moved_threshold_ms` against its base in the same measurement, and the next measurement
confirms it, the position gets a new `position_id` (a new base) instead of moving any speaker.

### 4.4 Settings (all with §5's descriptor)

| Setting | Default (recommended) | Range |
|---|---|---|
| `method` | `robust_ls` | `robust_ls`, `tracks`, `kalman` |
| `window_min` | 10 | 2-60 |
| `huber_ms` | 0.3 | 0.05-2.0 |
| `jump_repeats` | 2 | 1-4 |
| `point_role_default` | `target` | `target`, `vote` |
| `point_vote_weight` | 3 | 1-20 |
| `moved_threshold_ms` | 1.0 | 0.3-5.0 |
| `min_speakers` | 2 | 2-8 |
| `accept_processed_audio` | false | a measurement whose browser kept voice processing on is rejected, or kept as doubtful |
| `continuous_every_s` | 20 | 4-120; how often a phone in "micrófono continuo" measures (the loop's `every_s`) |
| `kalman_gate_sigma`, `kalman_drift_ppm` | 3, 50 | only for `kalman` |

Stored like the chain's choices (a JSON next to `chain.json`), with the same contract shape.

## 5. Explaining every setting: words and a visual, one by one and together

The setting descriptor (`knob_docs.Doc`) adds to today's `title`, `summary` and `help`:

- `recommended`: the value, and `why_recommended` (one sentence);
- `sounds`: how the sound changes, in words, for low and high values (or per choice). For the
  estimator it is said honestly: these settings change **what is suggested**; the sound changes
  when the suggestion is applied;
- `visual`: a declarative figure the panel draws (no images): `{kind, x, y, series[], marks[],
  caption, evidence}`, where `kind` is `timeline` | `bars` | `histogram` | `room`, and
  `evidence` is `SIMULADO` or `MEDIDO`. The series come from the same simulator as the tests
  (`sync_sim.py`), computed for the recommended value and for the listener's.

**The figures and the "together" text are computed on the estimator's thread**, cached by
settings, never on the engine thread (ops run there): `sync_explain` returns the cached result
or `pending` and asks the thread for it.

**Together:** `GET /v1/sync/explain` (op `sync_explain`) returns a short text of what the
whole current configuration does (two or three sentences assembled from the settings) and one
`timeline` figure: one simulated hour (drift, a jump, a microphone that hears two of three, a
phone that moves) with the current configuration against the recommended one.

The format lives in the shared descriptor, so the chain's knobs can use it later (a separate
iteration, i-7c8794-737d4e step 6); today it is filled for the estimator's settings only.

## 6. The point measurement in the browser, and the contract

### 6.1 Ops (contract, `control.py`) and REST

| Op | Scope | REST |
|---|---|---|
| `sync_time` | read | `GET /v1/time` — the server clock, for the phone's offset estimate |
| `probe_reference` (`from`, `seconds`) | control | `GET /v1/probe/reference` — the probe each speaker sent in that span, int16 little-endian, base64 per speaker, with its scale |
| `sync_measure` (a `Measurement`) | control | `POST /v1/measurements` |
| `sync_state` | read | `GET /v1/sync` — settings, the suggestion, the level statistics, the sources |
| `sync_set` (`changes`) | control | `PATCH /v1/sync` |
| `sync_apply` | control | `POST /v1/sync/apply` — applies the suggestion through the cut |
| `sync_explain` | read | `GET /v1/sync/explain` |

Limits: one measurement per second and one reference in flight per client; references older
than `REFERENCE_KEEP_S` (30 s) are gone (a fixed ring, d-7c8794-589dec).

### 6.2 The server keeps the probe it sent

`ProbeRing`: a fixed-size ring per speaker of what `MaskedProbe.last` added, with the
server-clock time of each block's first sample as it left the engine (the playback latency is
in `common`). The engine thread writes one block into it (fixed length).

### 6.3 On the phone (panel / PWA)

1. "Calibrar desde aquí": choose the role (default `point_role_default`); `getUserMedia`
   with `echoCancellation`, `noiseSuppression`, `autoGainControl` false; read back
   `getSettings()` and send what was applied.
2. Clock: 8 round trips to `GET /v1/time`; keep the one with the shortest round trip.
3. Record 8 s with an `AudioWorklet`, noting the server time of its first sample.
4. `GET /v1/probe/reference` for that span plus `MAX_LAG_MS`.
5. Compute locally: `probe_measure.measure` ported to JS (`panel/sync_measure.js`), same
   constants; a Python-vs-JS test on the same recordings (§7).
6. `POST /v1/measurements` with the result. **The audio never leaves the phone.**

Continuous from a phone: the same every `continuous_every_s` while the phone stays in
"micrófono continuo". The server's own microphone (the loop's) also submits to the estimator.

Errors shown as they are: no speaker heard, one only, the browser kept voice processing, the
probe was off, the reference is gone, the clock estimate is too loose.

HTTPS is needed for `getUserMedia` (research/13 §5): `"tls": true` in `service.json`,
reverted with `"tls": false`. The service already ships it.

## 7. Testing

Simulation (SIMULADO, automatic):

- **Estimator against known truth** (`sync_sim.py`): rooms with 3 and 8 speakers; microphones
  hearing subsets; 2-4 positions; 22 ppm drift; a 6.5 ms jump; a phone that moves 1 m; a
  microphone biased on purpose. Pass: the suggestion within the dead band at the anchor; no
  false jump; a moved phone re-based. **Each in two seeds, and surviving a change of a
  parameter that should not matter (the window length)** (CLAUDE.md).
- **The three methods on the same cases**: the comparison table backs the recommendation.
- **JS against Python**: the same recordings give the same arrivals within 0.01 ms.
- **Contract**: shapes, scopes, limits, out-of-window measurements, unknown speakers.
- **Fixed length**: `submit` and the `ProbeRing` write are O(1); the fit runs off the engine
  thread.

With speakers (MEDIDO, `PC-Ryzen5`, a new experiment in `docs/research/experimentos/`):

1. Phone vs fifine at the same spot: the same relative arrivals; Chrome Android and Safari
   iPhone; whether the browser honoured the processing switches.
2. Two phones at two spots plus the fifine, 20 min: the suggestion stays steady and agrees
   with a point `target` measurement.
3. The blind A/B that the probe is inaudible (i-7c8794-e3e40d step 4): everything here rests
   on it.

## 8. Order (each step usable on its own)

1. `Measurement`, `SyncEstimator` with `robust_ls`, fed by the server's microphone, suggesting
   only; `sync_state`, `sync_apply`; the panel card with "Aplicar" and the "together" text.
2. `knob_docs` and the estimator's settings with recommendation, words and simulated visual.
3. The point measurement in the browser: `ProbeRing`, `sync_time`, `probe_reference`, the JS
   estimator, `sync_measure`.
4. Continuous from phones, and the moved-microphone rule.
5. `tracks` and `kalman` as options, with the comparison table.
6. Later and apart: the chain's knobs in the new format.

## 9. Out of scope

Separating `electronic` from `base` everywhere in the room (it needs the speakers' plan or
many positions); the regenerable probe (seed plus band gains, for less bandwidth); WASM;
levels for equalisation (d-7c8794-e61118).
