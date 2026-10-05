# A spatial mode, an "auto" layout for N speakers, and principal or ambient speakers — design

Date: 2026-10-04. Status: **approved in conversation (two sections, each approved); the user
then said to go on autonomously**. Roadmap i-7c8794-eb1f78. Research behind it: research/09
(envelopment), experimentos/16 §5 (layouts), research/07 (what open software already does,
extended on 2026-10-04).

## 1. Why

The listener (2026-10-03/04): there is no layout for 3 speakers, and they want **one optimised
for N, always available**. They want to choose, in a side list, whether every speaker is a
**principal** (direct sound, with a direction) or some are **ambient** (full-spectrum ambience,
no specific direction) — "muy configurable y espacial". They barely hear the decorrelation, want
a mode where **spatiality is very noticeable**, and feel that **plain stereo sometimes beats the
algorithm with 3 speakers**, which should not happen.

A reading of the engine (VERIFIED, `motor.procesar`): in today's mode every speaker gets
`(1-a)·direct(pan) + a·ambience`, and the per-speaker decorrelator convolves **all** of it,
direct included. A broadband pan sends each instrument to every speaker at once, and the
decorrelation smears the direct sound's localisation: a likely reason (INFERIDO) why stereo can
win.

## 2. Decisions

| Id | Decision |
|---|---|
| d-7c8794-d67a23 | **Layout `auto`, always available**: the principal speakers on equal angles around the listener, symmetric about the front; with 3, a pair at ±60° and one behind at 180° (option A, chosen over a centre front and two at ±120°). Roles `P1`…`PN`, clockwise from front left. The default when no fixed layout fits the count |
| d-7c8794-48ae2c | **Each speaker is `principal` or `ambient`**, chosen in the panel's side list (and "all principal"); stored in the installation; **default all principal** (nothing changes until chosen). The ring places only the principals |
| d-7c8794-be2477 | **A `spatial` render mode beside `classic`**: a time-frequency upmix by per-bin panning index (direct to the nearest principals) and ambience index (decorrelated, Haas-delayed, level-set ambience to the ambients). **`classic` stays the default until `spatial` wins the blind A/B 8 of 10** against plain stereo and against classic, loudness matched |
| (d-7c8794-589dec) | Fixed-length work per block on the engine thread |
| (d-7c8794-0a4586) | Every setting with its recommendation, how it sounds, and a figure |

## 3. The spatial renderer (`dsp/spatial.py`)

A streaming STFT like `ambience.Extractor` (2048 points, hop 512, root-Hann analysis and
synthesis, latency 2048 samples: **no new latency**, the direct path is already delayed by it).
Per frame and bin `k`:

- smoothed powers and cross-power (`lam` as the extractor): `P11`, `P22`, `P12`;
- **ambience index** `A_k`: `1 - |P12|/sqrt(P11·P22)`, zero where the energies are not
  comparable (the extractor's criterion), mapped by `ambience.mapeo` with the `ambience` knob as
  its strength → mask `m_k` in [0, 1];
- **panning index** `Ψ_k = (P22 - P11) / (P11 + P22)` in [-1, 1] (−1 left, +1 right; a
  level-difference index, monotonic in the amplitude pan; INFERIDO equivalent in use to
  Avendaño–Jot's similarity-based index, simpler and stable on smoothed powers);
- **direct** `D_k = (1 - m_k) · |X| · e^{jφ}`, with `|X| = sqrt(|L|²+|R|²)` (energy kept) and the
  phase of `L+R` (of the louder channel where `L+R` cancels);
- the direct bin goes to angle `θ_k = Ψ_k · arc`, and with **constant-power** gains to the two
  principals around `θ_k` on the ring (wrapping at ±180°); one principal: all to it;
- **ambient** bins: ambient speaker `j` gets `m_k · L_k` (even `j`) or `m_k · R_k` (odd), so two
  ambients start from different signals; with **no ambient speakers**, the ambience is spread
  over the principals instead (all-principal still envelops).

Outputs per speaker: a direct block and an ambient block. In the motor (spatial mode only):
`x = direct + g_amb · Haas(decorrelate(ambient))`, and the rest of the chain (diffuse, bass, delay
line, EQ, gain, probe, limiter) is unchanged. **Classic mode is bit-exact** (the golden test).
Ambient speakers get no direct part.

Cost target (fixed length per block): < 4 ms per 4096-sample block with 3 speakers (MEASURED 3.1 ms; the first target of 3 ms was a guess), < 8 ms with 8,
on PC-Ryzen5.

## 4. The knobs (the chain's `spatial` stage)

| Knob | Default (recommended) | Range | What it moves |
|---|---|---|---|
| `render` | `classic` | `classic`, `spatial` | the stage on or off |
| `character` | 0.5 | 0 (localisation) – 1 (envelopment) | a macro over the four below |
| `arc_deg` | from character: 150 → 60 | 30 – 180 | how far the stereo stage opens on the ring |
| `ambience` | from character: 0.2 → 0.8 | 0 – 1 | how much ambience is extracted |
| `ambient_level_db` | from character: 0 → +6 | −6 – +10 | level of the ambient feeds |
| `haas_ms` | from character: 8 → 20 | 0 – 30 | delay of the ambient feeds (research/09 §3) |

Setting an advanced knob overrides the macro for that knob (the panel says so). Each knob uses
`knob_docs.Doc` and a SIMULATED figure; the layout gets a **top view of the room**: the listener in
the middle, each speaker at its angle with its role and what it gets.

## 5. Contract and panel

- Installation: `role_kind` per speaker, `principal` | `ambient` (default principal).
- `control.LAYOUT_ANGLES` gains `auto`, whose roles are computed from the number of principals;
  `assign` accepts `P1`…`P8`.
- The chain gets the `spatial` stage (§4), live-changeable except `render` (through the cut).
- Panel: the side list of speakers gets a principal/ambient switch and "todos principales"; the
  layout selector offers `auto`; the room view draws the ring.

## 6. Testing

SIMULATED (two seeds each):

- **localisation**: an instrument panned left, centre or right comes out mostly of the principal
  at its angle, ≥ 10 dB over the farthest principals; widening `arc_deg` moves it as expected;
- **envelopment**: ambient feeds' mutual correlation < 0.3, and ambient energy rises with
  `character`;
- **edges**: mono, silence, one channel only, all principal with no ambients, one principal;
- **loudness**: spatial vs classic total within 1 LU (`QualityMeter`), so the A/B compares space,
  not volume;
- **fixed length**: cost per block bounded and flat over 15 min;
- **classic unchanged**: the golden test passes bit-exact.

With speakers (MEASURED, a new experiment): the blind A/B of spatial vs plain stereo and vs classic,
loudness matched, the listener's music, 3 Go 4; **criterion written before: spatial wins ≥ 8 of 10
(p ≈ 0.055)**, or the result is written down and the knobs revised before it becomes the default.

## 7. Out of scope

DBAP with x, y positions (research/09 §4; later); per-bin decorrelation of the direct part; more
than one ring (`rings` stays a fixed layout).
