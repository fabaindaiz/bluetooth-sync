# Microcuts, a processing chain with every knob, and sound quality — design

Date: 2026-10-02. Status: **approved in conversation; being built on the Mac (simulation),
tested with speakers on PC-Ryzen5**. Research behind it: `docs/research/11-…` (four
fronts: measuring quality, processing, channels/codecs, visualisation/usability).

## 1. Why, and in what order

The listener, 2026-10-02: the sound is good in general; "apagado" improved but not fully;
**the main problem is constant microcuts, sometimes**. Second: **bass and loudness**. Third:
**more controls, visible and understandable — the panel must reach every knob of every
algorithm and choose between algorithms, maybe on a screen of its own**.

The order of the pieces follows that:

1. **Microcuts** (§3): measure the radio, show it, and a protocol to find the cause.
2. **The chain** (§4): the engine as a list of stages with descriptors; a contract to read
   and set any knob; persistence of the choices. **No change in sound** (bit-exact).
3. **Bass, volume and dynamics** (§5): new algorithms on the chain, **off by default**
   until speaker tests decide.
4. **Quality metrics** (§6): loudness in/out, net gain, true peak, PSR, limiter activity;
   offline chain tests.
5. **The panel** (§7): the **Cadena** screen generated from descriptors, a quality strip,
   the radio in Cortes, and the usability fixes the research measured.
6. **Tests prepared for PC-Ryzen5** (§8).

Everything that only speakers can answer stays as a prepared protocol, with its acceptance
criterion written before the measurement.

## 2. What the research changed (the facts this design rests on)

- **Each `reduce bitpool` in PipeWire's bluez5 sink is a dropped packet: a cut of
  ~24–40 ms** (VERIFIED in `spa/plugins/bluez5/media-sink.c`, tag 1.6.9: on `EAGAIN` the
  packet is skipped — "There will be a sound glitch in any case" — and the bitpool drops by
  2, at most once per 0.5 s). `increase bitpool` once per second is the **healthy**
  heartbeat. experimentos/10 §5.1/§5.5 read it the other way: its 62 "bajadas" in 50 s are
  ≥1.2 cuts/s. The Cortes card cannot see any of this today (it only sees what happens
  before PipeWire). This is the leading suspect for the microcuts.
- **There is no property to fix the SBC bitpool** in PipeWire 1.6.9 (VERIFIED). Mono SBC is
  6.7 dB worse than today's duplicated joint stereo (bitpool 29 vs 40, SIMULATED with
  libsbc); SBC-XQ costs 1.8× the air time for +0.9 dB. **Keep joint stereo 40.**
- **The SBC encoder receives S16 without dither** (VERIFIED). 30–40 dB of digital
  attenuation throws away resolution: the listener's volume belongs in **AVRCP**, the
  digital chain near 0 dBFS.
- **Bass:** a high-pass on the Go 4 removes excursion that triggers its protection
  (REPORTED: the Go 4 DSP cuts bass above ~50 % volume). With a bigger speaker, a LR4
  crossover sends the bass there (in-phase outputs, no extra latency). Without one,
  psychoacoustic bass (NLD, Larsen–Aarts; Moliner DAFx-20) — as a knob, since some
  listeners prefer none.
- **Limiter:** today's instant-attack sample-peak limiter modulates bass (INFERRED); a
  3 ms look-ahead true-peak (×4) limiter costs 0.08 ms/speaker (MEASURED on the Mac).
- **Quality:** the digital chain is the reliable place to measure (exact reference, same
  clock, no room). The uncalibrated mic only supports A-vs-B comparisons with the same mic,
  position and material. Perceptual models (PEAQ, ViSQOL) are for digital pairs only.
- **Panel (MEASURED, Mac, simulated):** meters force ~7 layouts per frame (420/s); the
  stream keeps running with the tab hidden; several states are colour-only (WCAG 1.4.1);
  12 px and 24 px touch targets; the A/B does not equalise loudness, so it may measure
  volume, not preference.

## 3. Piece 1 — microcuts

### 3.1 The radio monitor (`host/src/aurasync/radio.py`, new, English)

- Follows the user journal of the process that hosts the bluez5 nodes (WirePlumber 0.5
  loads them; experimentos/10 saw the lines after `wpctl set-log-level 4`):
  `journalctl --user -f -o json -u wireplumber` (and `pipewire`, in case), from "now".
- `parse(entry) -> RadioEvent | None` recognises `reduce bitpool: N` and
  `increase bitpool: N` (and any line carrying the object pointer together with a
  transport path or MAC, to map pointer → speaker). **How the pointer maps to a speaker must
  be read from `media-sink.c`/`media-source.c` 1.6.9** (which debug lines print `%p`
  together with the transport or address). If no mapping is possible, events are kept per
  pointer and shown as "radio · enlace sin identificar"; the PC-Ryzen5 protocol settles it.
- Per speaker: current bitpool, max seen, drops (count and timestamps), drops/min over the
  last 60 s, `bitpool_median_60s`.
- Every `reduce` goes to `CutLog` with kind **`radio`**, `where` = speaker (or the pointer),
  and the context CutLog already keeps.
- **Unavailable is a state, not an error:** if the debug level is not on, or `journalctl`
  is missing (the Mac), `radio.available = false` with a reason the panel shows.

### 3.2 Turning the radio log on (a system change)

- New op `radio_log {active: bool}`. On: raise the log level **only for the bluez5 media
  topics** if PipeWire/WirePlumber accept a topic pattern (to verify on the machine; e.g.
  `wpctl set-log-level` with a pattern string, or `pw-metadata -n settings 0 log.level`).
  Fallback: global level 4, flagged in the state as "heavy" because journald's rate limit
  may drop lines.
- **Before** changing anything, the service appends to `<config>/cambios-de-sistema.txt`
  the change and how to revert it (the repo rule). Off restores the previous level. The
  service restores it on `shutdown`, SIGTERM and session failure (card
  *kill-switch-reaches-every-path*).
- CLI mirror: `aurasync radio-log on|off|status`.

### 3.3 The protocol (PC-Ryzen5, `probes/14-microcortes/`, experimentos/12)

- **C2 baseline:** 2 × 10 min, same song, same positions, battery noted; radio drops per
  minute per speaker + the Cortes card. A cut counts if it shows in both sessions'
  statistics (rate within a factor of 2), not if one session alone has it.
- **C3, one variable at a time** (2 × 10 min each, against the baseline):
  1. only 2 speakers connected (controller load);
  2. swap the positions of two speakers (cuts follow the speaker → its link; stay → the
     position/obstacles);
  3. one speaker 1 m from the PC (distance);
  4. bluetooth scan on purpose during playback (LE scan hypothesis);
  5. a larger buffer on the bluez sinks, if a property exists (to verify; with the
     calibration measuring the added latency).
- Adopt only what lowers drops in **both** repetitions. If it all points to controller
  load, the way out is two controllers (hardware: discussed before buying anything).
- **Goal:** 0 drops in 20 min with 3 Go 4; otherwise the minimum measured and the cause
  written down. Script: `probes/14-microcortes/sesion.py --minutos 10 --nota "…"`, saving to
  `docs/research/experimentos/datos/12/`.

## 4. Piece 2 — the chain

### 4.1 Stages and descriptors (`host/src/aurasync/chain.py`, new, English)

```python
@dataclass(frozen=True)
class Param:
    id: str; title: str; summary: str; help: str
    kind: Literal["float", "int", "bool", "choice"]
    default: Any; low: float | None = None; high: float | None = None
    step: float | None = None; unit: str = ""; choices: tuple[str, ...] = ()
    scope: Literal["global", "speaker"] = "global"
    apply: Literal["live", "cut", "restart"] = "live"

@dataclass(frozen=True)
class Algorithm:
    id: str; title: str; summary: str; help: str
    params: tuple[Param, ...] = ()
    cost: str = ""        # e.g. "0,1 ms por parlante"
    latency_ms: float = 0.0  # identical on every speaker, by construction

@dataclass(frozen=True)
class Stage:
    id: str; title: str; summary: str; help: str
    algorithms: tuple[Algorithm, ...]
    default_algorithm: str
```

`CHAIN: tuple[Stage, ...]` in the real processing order. Algorithm `"off"` is just an
algorithm with no params. Titles/summaries/help are **Spanish** (they are UI text);
identifiers are English.

| # | Stage `id` | Algorithms | Params (default = today's value) |
|---|---|---|---|
| 1 | `ambience` | `avendano_jot` · `off` | `mix` 1.0 (live), `lam` 0.9, `threshold` 0.5, `sigma` 2.0, `min_energy` 0.25 (cut); per speaker `pan`, `ambience` (live) |
| 2 | `decorrelate` | `group_delay` · `off` | `mean_ms` 2.5, `spread_ms` 1.5, `length` 256, `seed` 0 (cut) |
| 3 | `diffuse` ★ | `off` · `noise_tail` | `level_db` −16, `rt60_s` 0.6, `predelay_ms` 15, `damping_hz` 6000 (cut), `seed` |
| 4 | `align` | `sinc` | `rear_delay_ms` 12 (live), `delay_speed_ms_s` 0.5 (live) |
| 5 | `eq` | `boost_only` · `off` | `max_boost_db` 6, `dead_band_db` 1, ★`budget_db` off (0 = no budget), ★`treble_cap_db` off (cut) |
| 6 | `bass` ★ | `off` · `protect` · `crossover` | `protect`: `cutoff_hz` 90, `harmonics_db` −∞ … +6 (0 = none); `crossover`: `cutoff_hz` 100, `to` (a speaker of a bass-capable kind) |
| 7 | `volume` ★ | `digital` · `avrcp` | `volume_db` (live); per speaker `gain_db`, `muted` (live) |
| 8 | `limiter` | `peak` · `true_peak` ★ | `ceiling_db` −1, `release_ms` 250; `true_peak`: `lookahead_ms` 3 (cut) |

★ = new algorithm/stage, built in piece 3. **Defaults reproduce today's sound exactly**:
`diffuse=off`, `bass=off`, `volume=digital`, `limiter=peak`, budget and treble cap off.

- **Latency is equal on every speaker by construction**: a stage that adds latency adds it
  to all (like the EQ FIR today). The chain reports its total latency; the calibration
  measures it anyway.
- An algorithm may be **unavailable** with a reason (e.g. `crossover` without a speaker
  of a bass-capable kind in the installation). Setting it is `unavailable` (409).
- Each stage exposes `metrics() -> dict` (small, cheap), e.g. limiter gain reduction and %
  of time active, bass energy moved, ambience share. Streamed (§6.3).

### 4.2 The engine

`motor.py` keeps its public API (`Motor`, `procesar`, `cortar`, `actualizar*`,
`retardos_*`, `reduccion_limitador_db`) and its language; internally it reads the chain
values to build/route stages. New stage code goes to new English modules. The `Motor`
constructor gains `chain: ChainValues | None` (None = defaults).

**Guarantee: bit-exact.** Before touching `motor.py`, a golden file is recorded from the
current engine (`tests/data/golden_motor.npz`: pink noise + synthetic music, 3 speakers,
fixed seeds, live changes of pan/ambience/volume/mute/EQ/preset cut mid-run, block 4096
and 1024). The refactored engine with default chain values must reproduce it with
`max |diff| ≤ 1e-9`. The test is seen to fail with a stage deliberately broken.

### 4.3 Contract (additive; `control.py`)

- `chain` → `{"stages": [{id, title, summary, help, algorithms: [{id, title, summary, help,
  cost, latency_ms, available, unavailable_reason, params: [Param as dict]}],
  default_algorithm, value: {algorithm, params: {…}, speakers: {name: {…}}},
  chosen: {…sparse…}}], "latency_ms": total}`.
- `chain_set {stage, algorithm?, params?, speaker?}` — validated **completely** against the
  descriptors before anything happens (unknown stage/param → `unknown_field`; type;
  range; choice; `speaker` required iff the params are speaker-scoped; algorithm
  unavailable → `unavailable`). Applies with the param's `apply`: live → ramps; cut → the
  80+80 ms cut; restart → `pending_restart`. Returns the stage's new `value`.
- `chain_reset {stage, param?, speaker?}` → removes the choice (back to the default).
- **Old fields stay** and become aliases of the chain: `extract_ambience` ↔
  `ambience.algorithm`, `decorrelate` ↔ `decorrelate.algorithm`, `eq_active` ↔
  `eq.algorithm`, `rear_delay_ms` ↔ `align.rear_delay_ms`, `volume_db` ↔
  `volume.volume_db`, and the speaker fields `pan`, `ambience`, `gain_db`, `muted`. Both
  ways stay consistent (a test sets one and reads the other).
- `state` gains `chain_summary` (algorithm per stage) and `chain_latency_ms`.

### 4.4 Persistence (card *persist-inputs-derive-verdicts*, *no-simultaneous-deploy*)

- Only **choices** are stored, sparse: `{stage: {"algorithm"?: …, "params"?: {…},
  "speakers"?: {name: {…}}}}`. Defaults are derived at read time, so a changed default
  reaches whoever did not choose.
- Global chain choices: `<config>/chain.json` (new file; `instalacion.json` is read with
  `cls(**data)` and would reject an unknown key, so it is not touched).
- Preset chain choices: `<config>/presets-chain.json`, keyed by preset name (the existing
  `presets.json` reader rejects unknown keys; a rollback must still start). A preset
  without chain choices loads exactly as today. Tests cover old reader × new files and new
  reader × old files.
- Invalid entries in `chain.json` (unknown stage/param, out of range) are dropped with a
  log line; they never stop the service.

## 5. Piece 3 — bass, volume and dynamics (new DSP, off by default)

All numpy, no new package dependency (`scipy` stays out on purpose). New modules, English.

- **`dsp/crossover.py`** — LR4 (two cascaded Butterworth 2nd order) as truncated causal
  FIR via FFT, in-phase complementary outputs. `protect` = the high-pass alone on the
  Go 4 path. `crossover` = high-pass on every non-bass speaker, plus the low-passed sum of
  the direct L+R added to the bass speaker. *Offline acceptance:* |HP+LP| flat ±0.1 dB
  20 Hz–20 kHz; Go 4 output below 60 Hz < −24 dB re input; latency 0.
- **`dsp/virtual_bass.py`** — NLD: band 20 Hz–fc (fc = `cutoff_hz`), full-wave rectifier,
  band-pass fc–4fc, gain `harmonics_db`, added to the high-passed path. *Offline
  acceptance:* two tones 50+70 Hz → inharmonic products ≥30 dB below the harmonics; pink
  noise → energy below 80 Hz does not rise, 100–400 Hz rises by about the knob.
- **`dsp/limiter.py`** gains `TruePeakLimiter` (×4 FFT oversampled detection, 3 ms
  look-ahead, cosine attack, release knob). *Offline acceptance:* no ×4 oversampled sample
  above −1 dBTP on a corpus with +6 dB EQ; a 60 Hz sine 6 dB over the ceiling produces
  harmonics ≥20 dB below today's limiter.
- **`dsp/diffuse.py`** — exponentially decaying noise IR with high-frequency damping, one
  seed per speaker, uniform partitioned convolution (partition = block), fed with the
  extracted ambience plus a fraction of the direct. *Offline acceptance:* pairwise
  coherence 500 Hz–4 kHz with correlated pink noise drops ≥0.15 vs without; octave
  spectrum within ±1 dB.
- **`dsp/eq.py`**: cache the taps' FFT in `StreamingFIR` (−37 % CPU, MEASURED); optional
  total boost budget and treble cap above 8 kHz.
- **Volume `avrcp`** (service, not DSP): the panel volume moves the Bluetooth volume of
  every speaker (`pactl set-sink-volume` on the `bluez_output…` sinks), the digital volume
  stays at 0 dB, and **each change is read back** (the repo rule: what is asked of
  PipeWire is verified). A Go 4's curve is not linear (experimentos/10 §5.4: −6 dB asked
  gave −4.1 dB): the mapping is the plain PipeWire volume until the PC-Ryzen5 protocol
  measures the curve. In simulation the "AVRCP" volume is applied by the simulated room.

CPU budget: everything new together must keep the engine ≥20× real time on PC-Ryzen5
(today 63×); measured by `probes/12-cortes/jitter.py`-style timing in a test.

## 6. Piece 4 — quality metrics

### 6.1 `dsp/loudness.py` (new)

- BS.1770-5 K-weighting **in the frequency domain** (power spectrum × |K(f)|², Parseval)
  or as a short FIR — whichever matches the IIR within 0.1 LU on the EBU Tech 3341 test
  signals (1 kHz sine at −23 dBFS stereo → −23.0 LUFS M/S/I; −33 → −33.0; the gating
  cases); validated in a test.
- Momentary (400 ms), short-term (3 s), integrated (gated) per stream; true peak (×4); PSR
  = TP − short-term loudness.

### 6.2 What is computed live

Per block, outside the critical path where possible: input (stereo, G = 1 per channel)
and each speaker output. Derived: **net gain in LU** (sum of outputs vs input, with the
panel at 0 dB expected 0 ± 1 LU minus the ambience mix), **PSR in vs out** (out ≥ in − 1 dB
or "la cadena aplana"), **true peak max**, **limiter active %**. Cost measured; must stay
under 0.5 ms per block for 3 speakers.

### 6.3 Stream and state

- New SSE event `quality` at 2 Hz: `{input: {m, s, i, tp, psr}, outputs: {name: {m, s,
  tp, psr, limiter_pct}}, net_gain_lu, flattening: bool}`.
- New SSE event `chain` at 5 Hz: `{stage_id: metrics}`.
- New SSE event `radio` at 1 Hz (and on each drop): `{available, reason, speakers: {name:
  {bitpool, drops_total, drops_per_min, last_drop_s}}}`.
- `state` carries `radio` and `quality` summaries for the polling fallback.

### 6.4 Offline tests (permanent, `host/tests/`)

- `test_chain_quality.py`: `probes/11-calidad-de-la-cadena/cadena.py` as a test — with EQ
  off, |Δ| ≤ 0.5 dB per third 50 Hz–16 kHz vs the ideal mix and coherence ≥ 0.99, with
  block 1024 and 4096 equal within 0.05 dB, two seeds.
- Stream integrity: 2 min (sped up) of input with live changes → exactly N samples out,
  no block-edge discontinuity beyond the signal's own.
- Loudness: net gain 0 ± 1 LU with defaults; PSR loss ≤ 1 dB with defaults on music-like
  material.

## 7. Piece 5 — the panel

### 7.1 The **Cadena** screen (new tab, between Escuchar and Parlantes)

- Generated **entirely from `chain`**: a stage card per stage in processing order, with an
  arrow between cards ("Entrada → … → Parlantes"). A stage added in Python appears without
  touching the HTML.
- Each card: title; summary line; algorithm selector (segmented control; each option with
  its summary, cost and latency; unavailable ones disabled with their reason); the params
  of the chosen algorithm (slider + number + unit, a ↺ button when not default, (?) with
  the help); a badge for how it applies ("en vivo" / "con un corte breve" / "al
  reiniciar"); per-speaker params as a small table (columns = speakers on the PC, one row
  per speaker on the phone); the stage's live metric (e.g. limiter reduction bar, bass
  moved, ambience share).
- The quality strip on top: LUFS in / out, net gain (LU), PSR in/out, TP max, limiter %;
  "la cadena aplana" when PSR out < in − 1 dB; "ganancia perdida" when net gain < −3 LU.
- Ajustes → Sonido moves into Cadena (no duplicated control); Ajustes keeps Sincronía and
  Salida. `probes/10-panel-organizacion/medir.py` scenarios are updated and the costs
  remeasured.

### 7.2 Cortes gets the radio

A third lane "radio" per speaker in the timeline, drops/min per speaker, current bitpool,
and a button "Activar registro de radio" (calls `radio_log`, says it is a system change
and how it is reverted). The top bar chip counts radio drops too.

### 7.3 Usability fixes (each with a browser test)

1. Meters without forced layout: `transform: scaleX()`/`translateX`, track width cached
   with `ResizeObserver`; the input spectrum repainted only on a new frame. With CPU ×4
   (CDP): ≤ 65 layouts/s, main thread ≤ 150 ms/s (`probes/10-panel-organizacion/render_cost.py`).
2. Close the stream when the tab is hidden; reopen on visible; the service's stream count
   returns to 0 within 2 s.
3. Battery ≤ 20 %, lost speaker and radio drops shown in **Escuchar → Ahora** and the
   header, with icon and text.
4. Sync for the listener: in Ahora, one number (residual misalignment, ms) on a ruler with
   the zones of research/09 §3, a phrase and a shape per zone, plus the drift trend.
5. A/B with equal loudness: the short-term LUFS of A and B and their difference; a warning
   above 0.5 LU and an option to compensate.
6. "En camino" state on a control for the measured latency after it changes.
7. Accessibility: `aria-valuetext` on meters; "SAT" text on clip; a shape per cut kind;
   Pause for Niveles/Entrada (WCAG 2.2.2); `prefers-reduced-motion` → ≤ 5 updates/s and
   no transitions; touch targets ≥ 44 px (or 24 px with the 2.5.8 spacing exception);
   a visible ↺ instead of relying on double-tap on touch.
8. Numbers with `Intl.NumberFormat("es")`.
9. `medir.py` as a regression test with thresholds (pestañas ≤ 18.4 phone, ≤ 6.5 PC, or
   the new values after Cadena, written down).

## 8. Tests prepared for PC-Ryzen5 (with speakers)

| Probe | What | Accepts if |
|---|---|---|
| `probes/14-microcortes/` | C2 + C3 (§3.3) | drops/min falls in both repetitions |
| `probes/15-graves-y-volumen/curva_avrcp.py` | AVRCP volume → dB per Go 4, mic, 2 sessions | monotonic, repeatable ±0.5 dB |
| `probes/15-…/proteccion.py` | 125 Hz level vs AVRCP volume 40→100 %, `bass=off` vs `protect` | the drop of 125 Hz moves to a higher volume, in 2 sessions |
| `probes/15-…/ab_graves.py` | blind A/B `protect+harmonics` vs off, loudness matched | ≥ 20/30 in each of 2 sessions to claim "se oye"; preference pairs for "mejor" |
| `probes/16-calidad/suma_go4.py` | tone in L, R, L=R, L=−R to one Go 4 | decides whether the Go 4 sums L+R |
| `probes/16-…/directo_vs_motor.py` | same passage direct vs engine, loudness matched | ΔLUFS ≤ 0.5 LU and ≤ 1 dB per 1/6 oct 100 Hz–8 kHz, 3 songs × 2 sessions |
| `probes/16-…/respuesta.py` | per-speaker response with coherence, 3 placements | ±1.5 dB 100 Hz–8 kHz where γ² ≥ 0.9 |

Each probe writes raw data to `docs/research/experimentos/datos/<n>/` and its result to an
experiment file with the environment (kernel, PipeWire, WirePlumber, BlueZ, firmware of the
JBL if readable, battery, positions).

## 9. Out of scope (and why)

ML source separation (SDR ~5 dB, PyTorch, unofficial code); moving DSP to filter-chain or
CamillaDSP (only if Cortes blames the engine); broadband compression/AGC (flattens);
dither on the bluez node; SBC mono and SBC-XQ (§2); a 5.1 sink, a loopback for lip-sync and
a DirAC upmix (in the roadmap, later); a draggable room plan (only when the engine uses
positions).

## 10. Work packages (file ownership while building)

| WP | Owns | Depends on |
|---|---|---|
| A · chain core | `chain.py`, `motor.py`, `control.py`, `presets.py`, `config.py`, `service.py`, `snapshot.py`, `session.py`, their tests, `tests/data/golden_motor.npz` | — |
| B · DSP modules | `dsp/crossover.py`, `dsp/virtual_bass.py`, `dsp/diffuse.py`, `dsp/loudness.py`, `dsp/limiter.py`, `dsp/eq.py`, their tests | — |
| C · radio | `radio.py`, `cuts.py`, `probes/14-microcortes/`, `tests/test_radio.py`, the Mac-portability fix of `test_cli.py` doctor tests | — |
| D · panel fixes | `panel/*`, `tests_browser/*`, `probes/10-panel-organizacion/` | — (front-end only) |
| E · integration | wires B into the chain stages, C into the service/session/stream, metrics into `telemetry.py`/`rest.py`, AVRCP volume | A, B, C |
| F · Cadena screen | `panel/*`, `tests_browser/*` | D, E |
| G · probes 15–16, docs | `probes/15-…`, `probes/16-…`, research 11, experiments 12, roadmap, decisions | E |

## 11. What changed while building (2026-10-02)

- **Engine cost was wrong in §5.** The "63×" was measured before the sinc delay read; with it the
  engine was ~5× real time (Bessel per sample and tap). Fixed in numpy: identical output for a
  still delay, ≤1e-10 in a ramp; 32× with 3 speakers and EQ on the Mac
  (`tests/test_interpolation.py`, experimentos/12 §1.1).
- **Chain storage:** knobs that already live elsewhere stay there (pan, ambience, gain, rear delay
  in `instalacion.json`; volume and mute in memory); `chain.json` holds only the chain's own
  choices. The on/off fields (`extract_ambience`, `decorrelate`, `eq_active`) now persist across
  restarts because they are stage algorithms.
- **Descriptor details:** `harmonics_db` −24 (= none) … +6, where 0 dB means harmonics with the
  energy of the removed band; `treble_cap_db` 6 = no cap; `diffuse.level_db` defaults to −12 dB
  (−16 dB lowered coherence by only 0.07 in the worst case); extra speed knobs and descriptor fields
  (`store`, `implemented`, `choices_from`, `requires`, `algorithm_apply`, `latency_param`).
- **Not met in §5, written down:** the NLD virtual bass cannot keep inharmonic products 30 dB
  below the harmonics with two low tones (a memoryless rectifier cannot; research/11 §5.1); the
  diffuse tail needs −11 dB for a 0.15 coherence drop with identical feeds.
- **Crossover:** bass-capable speakers also pass the crossover all-pass so their own bass and the
  fed bass add in phase (+5.9 dB instead of +3.6 dB).
- **Quality (§6):** net gain is 0 ± 1 LU only for wide stereo; centred mixes sum up to +1.8 LU and
  ambience moves it −2.4 … −0.7 LU. Input PSR is taken per channel (the lower one).
- **Radio (§3):** the monitor lives with the service, not the session, so it sees the lines that
  map each link to its speaker when a speaker starts; levels by topic with `wpctl set-log-level`
  (VERIFIED in WirePlumber 0.5.17's code). A larger socket buffer has no property in 1.6.9, so
  that C3 variable was dropped; `rfkill block wifi` was added as the first C3 variable.
- **Panel stack (§7):** Vite + TypeScript + Preact with npm, built output versioned
  (d-7c8794-6da524); Python generates the contract types and checks the build without Node.
- **Added after this spec:** the panel as a PWA on GitHub Pages over the local network with HTTPS,
  per-client tokens and pairing (d-7c8794-37f9bc, research/13 §5); a Rust native-I/O proof of
  concept (research/12, experimentos/13); 8 speakers as the scale target (d-7c8794-3b7793).
