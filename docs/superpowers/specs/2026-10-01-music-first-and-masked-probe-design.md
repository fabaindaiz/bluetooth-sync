# Music first, and a masked probe for the sync loop — design

Date: 2026-10-01. Status: **part 1 built, parts 2–3 planned**. Roadmap: i-7c8794-10ccb4,
i-7c8794-b0512d, i-7c8794-e3e40d. Decisions: d-7c8794-e8f7e3, d-7c8794-f94c13.

## 1. Why

The listener's verdict after the first EQ (2026-10-01): *"muy apagada y aplanada, sin bajos y
muy uniforme"*. Envelopment is secondary to hearing the music well, loud and with its whole
recorded spectrum. And: calibration is for **latency and sync**, not for the spectrum; it
should run while music plays, unnoticed, and be switchable without restarting.

What the live state showed (MEASURED with `pactl` and `/v1/state`, PC-Ryzen5):

| Stage | Value | Effect |
|---|---|---|
| EQ per speaker (first version) | −18 dB at 125–315 Hz, whole curve between −6 and −18 dB | the Go 4's bass hump removed, everything quieter |
| combined sink `aurasync_salida` | 46 % (−20.2 dB), restored by WirePlumber | 20 dB lost on top of the panel's volume |
| panel volume | −15 dB | |
| speakers (AVRCP) | 80 % (−5.8 dB) | the listener's |
| output format | s16 | at −40 dB total, ~10 bits left for the music |

## 2. Part 1 — music first (built)

- **The EQ only lifts, never cuts** (`dsp/eq.py`). What the speaker does strongly stays; a
  dip inside the band it can reproduce is lifted up to +6 dB, smoothed 1-2-1, with a 1 dB
  dead band. A previous cutting curve is lifted back to 0 by the next `eq_apply`.
- **Optimistic reading.** The cheap microphone is assumed to hear less than the speaker
  gives: the band that may be lifted is the maker's (a **speaker kind** the person chooses,
  `dsp/profiles.py`: Go 4 90 Hz–20 kHz, Charge 6 56 Hz–20 kHz) or, without one, the
  measured band widened by an octave each side (never below 60 Hz).
- **A peak limiter** at the end of each chain (`dsp/limiter.py`, −1 dBFS ceiling, instant
  attack, 0.25 s release) so lifting never clips.
- **Gain staging**: the combined sink is set to 100 % and verified on every session start
  (`sonido.fijar_volumen_completo`); the engine is the only volume. Output in **f32**.
- **Speaker kind** in the contract (`kind`, per speaker), the snapshot (`kind`,
  `kind_guess` from the name) and the panel (a "Tipo" column).

**Still to do in part 1** (roadmap i-7c8794-10ccb4):
1. A "música" preset: ambience 0.1 everywhere, rear delay low; the envelopment presets stay.
2. **A measured reference**: the same passage played directly to one Go 4 (no aurasync)
   and through aurasync, recorded at the listening spot; compare third-octave spectrum and
   loudness (LUFS-like RMS). The pass criterion is "aurasync is not duller than direct":
   within ±2 dB per third from 100 Hz to 10 kHz with EQ off, and lifted dips with EQ on.
3. A blind A/B (the existing tool) of EQ on/off with music, ≥16 trials.

## 3. Part 2 — the loop is part of the protocol (built)

- `recalibrate` is a global setting, **on by default** (d-7c8794-f94c13); `start` uses it
  unless told otherwise, and without a microphone it plays without the loop and says so.
- Switching it is live (`set {recalibrate}` or the `recalibrate` op) and lives in Ajustes as
  an advanced option.
- `calibrate` pauses the loop and resumes it when the calibration ends; `calibration_apply`
  restarts it from the applied values, so its history does not pull them back.

## 4. Part 3 — a masked probe for latency (planned, i-7c8794-e3e40d)

Today the loop correlates the microphone with the music each speaker got. It fails when the
speakers carry correlated content (experimentos/08, 09 §5): the speaker that adds most
envelopment is the worst measured. With the music-first presets the content is *more*
correlated, so this gets worse. A probe that is independent of the music fixes it at the
root. Research: `docs/research/03` §3.2.

### 4.1 Design

- **Probe**: pseudo-random noise per speaker (independent seeds), band-limited to
  ~300 Hz–8 kHz (where the Go 4 respond well; near-ultrasound is ruled out, §3.2).
- **Shaping**: per block, third-octave bands; the probe's level in each band is the music's
  energy in that band minus a **margin** (start at −20 dB, the IFPI rule of thumb). The
  engine has the block before it sends it, so the shaping has one block of look-ahead
  against pre-echo; release 100–200 ms.
- **Floor**: below −50 dBFS of music the probe is off (no measurement in silence; 30 s of
  pause drift only ~0.7 ms at 22 ppm).
- **Time multiplexing**: one speaker carries the probe per window, in turn (3 speakers ×
  3–5 s windows → each measured every 9–15 s). No probe-to-probe interference at all.
- **Estimator**: GCC (SCOT or ML weighting restricted to the probe band, not raw PHAT)
  between the microphone and **the probe that was sent**, parabolic peak interpolation, a
  minimum peak-to-sidelobe ratio. The existing loop filters (stability, dead band, max
  jump, confirmation, gain 0.5) stay.
- **Switching**: a global `probe` field; on/off with a 50 ms ramp (`dsp/ramps.py`), live.
  Off, the loop falls back to the music-correlation it has today.

### 4.2 How it is validated (CLAUDE.md: measured, and surviving a parameter change)

1. **Offline first**: recorded music + simulated room (`simulated.py`) at margins −15, −20,
   −25, −30 dB and windows 2/4/8 s; the error distribution against the known delays, with
   correlated content (the case that fails today).
2. **With speakers**: the injected-delay test (`campana.py` phase E) with the probe on:
   +5 ms on one speaker must be found and corrected, **twice, with two different seeds**.
3. **Inaudibility**: the blind A/B, probe on vs off, at each margin, with critical material
   (solo piano, voice, fades), ≥16 trials per condition, at the listening spot and at 1 m.
   The adopted margin is the loudest that does not reach p < 0.05.
4. Each result goes to `docs/research/experimentos/11-…` with the firmware and versions.

### 4.3 What it touches

`dsp/probe.py` (new: generator, shaper), `motor.py` (adds the probe after the limiter's
input, before the per-speaker gain — so it follows the speaker's level), `sincronia.py`
(references are the probe, not the emitted mix), `session.py` (window rotation),
`control.py` (`probe`, `probe_margin_db`), the panel (Ajustes, Diagnóstico). Nothing of it is
A2DP-specific: the same probe serves an Auracast backend.

### 4.4 Risks

- SBC at low bitpool may drop the probe's quietest bands with loud music (research §3.2,
  INFERIDO): the shaping keeps the probe in the bands where the music already is, which is
  where SBC spends bits.
- The microphone's own noise floor: at −20 dB under quiet music the probe may sit near it.
  The floor rule and the integration time handle it; step 1 measures it.
