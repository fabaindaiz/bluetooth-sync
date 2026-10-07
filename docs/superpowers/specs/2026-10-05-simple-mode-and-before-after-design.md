# Simple mode, perceptual controls, and before/after — design

**Date:** 2026-10-05 · **Status:** written from the user's answers of 2026-10-05, for review. **Roadmap:**
i-7c8794-0ad844. **Depends on:** phase 2 Task 12 (the speakers' `direct` render and their loudness match,
spec 2026-10-05-virtual-speakers-and-hot-join §9) and `loudness_match.py` (the monitor's, 2026-10-05).

## 1. What and why

The user, after reading `research/11` §4.2 and `research/14`: *"no quiero ocultar funcionalidad o
complejidad, quizás sí añadir un modo simple, usar controles en términos de lo que se oye y mostrar antes
y después."* Professional tools show before/after (Dirac Live, Audyssey); IRCAM Spat and B&O offer
perceptual controls (VERIFICADO in research/11 §4.2); Sonos' 2024 redesign warns against simplifying by
removing functions (REPORTADO). So:

- **Nothing is hidden or removed.** The expert chain view stays exactly as it is, one tap away.
- **A simple mode is added** with four perceptual controls the user chose: **Envolvimiento, Graves, Brillo,
  Intensidad del efecto**.
- **Before/after** in three forms the user chose: a **direct/processed button** (loudness-matched),
  an **overlaid spectrum** (input vs output), and **numbers** (LUFS, PSR, limiter).

## 2. The perceptual controls

Each control is a 0–100 % position that **writes ordinary chain knobs** through the existing `chain_set`
path (so every change is the same change an expert would make, validated by `chain.py`, applied through the
cut where the knob requires it, undoable, and saved in presets). The expert view marks the knobs a
simple control is driving ("lo mueve *Envolvimiento*").

| Control | Knobs it moves (each along its own range, monotonically) | At 0 % | At 100 % |
|---|---|---|---|
| **Envolvimiento** | `ambience.mix`; `spatial.ambient_level_db` (and `character` when `manual` is off); `decorrelate.spread_ms`; `diffuse.level_db`; `align.rear_delay_ms` | ambience off, no diffuse tail | the most envelopment the chain offers within each knob's recommended range |
| **Graves** | `bass` protection `cutoff_hz` (lower = more bass to the small speakers); `bass` `harmonics_db` (virtual bass); the EQ's low-shelf lift within `eq.budget_db` | protective, no virtual bass | full virtual bass, deepest safe cutoff |
| **Brillo** | `eq.treble_cap_db` (0 → +3 dB, research/11 §2.1); a gentle high-shelf lift within the budget | no treble lift | +3 dB cap |
| **Intensidad del efecto** | a master: scales the distance of the other three from neutral; at 0 % the output equals the `direct` render | = `direct` | = the three controls as set |

- The EQ only lifts (d-7c8794-e8f7e3): *Brillo* and *Graves* never cut, they choose how much lift.
- The exact curves (which knob moves how much at each %) are a table in code with its source per row
  (research reference or "recommended range" from `chain.py`'s descriptors), and a test that every knob
  stays inside its validated range at every %.
- **Touching an expert knob** that a simple control drives puts that control in "personalizado" (shown,
  not reset). Moving the simple control again takes the knob back (with undo).
- New stage-independent ops: `simple_set {control, value}` (scope `control`), and the snapshot gains
  `simple: {envelopment, bass, brightness, intensity, custom: [controls in "personalizado"]}`.

## 3. Before and after

**Direct/processed button.** Hold (or toggle) to hear the `direct` render on the speakers (phase 2 Task 12)
and the `stereo` path on the monitor, both at matched loudness (`loudness_match.py`, the remembered
makeup, so the switch never jumps). The switch goes through the cut (80 + 80 ms). Op `compare {target:
"direct"|"processed"}`; the snapshot says which is heard.

**Overlaid spectrum.** The input spectrum already exists (`InputAnalyzer`, the panel's "Entrada" card).
Add the output spectrum — the sum of the speakers, and per speaker on demand — on the same third-octave
grid (`dsp/response.THIRDS`), computed off the engine thread from the blocks `quality.py` already receives,
streamed at the same rate as today's spectrum. The panel draws input and output on one graph (two curves,
distinguished by line style and label, not color only — WCAG 1.4.1, research/11 §4.3).

**Numbers.** Before vs after side by side: short-term loudness (LUFS), PSR, true peak and limiter activity —
all already in `quality.summary()`; only the panel's layout is new.

## 4. Where it lives in the panel

A "Simple" view in the Cadena screen (a segmented switch Simple | Experta, remembered per device), the
four controls as large sliders with a one-line description of what they do to the sound (Spanish), the
before/after button next to them, and the spectrum + numbers below. Nothing in the expert view moves or
disappears.

## 5. Verification

- Unit: the mapping table — monotonic per knob, inside validated ranges at 0/25/50/75/100 %; intensity 0 %
  equals the `direct` render (golden ≤1e-9 on the speakers' output); "personalizado" when an expert knob
  diverges; undo restores both.
- Loudness: switching direct↔processed keeps the speakers' summed short-term loudness within 1 LU after the
  first visit (Task 12's test applies).
- Browser: the Simple view drives the knobs (the expert view shows the new values and the "lo mueve" marks),
  the before/after button switches and shows which is heard, the spectrum shows two labelled curves.
- On speakers (PC-Ryzen5, with the EQ A/B campaign i-7c8794-d9c64a): whether the controls' ranges sound
  right — MEDIDO by ear with the blind A/B, not assumed.

## 6. Not in scope

The chain suggestions (i-7c8794-d99df9) will express themselves in these controls, later. "Sonando ahora"
(i-7c8794-99f87e) is separate.

## 7. Additions from the panel audit (user, 2026-10-07)

The user decided that two findings of the audit (research/10 §10, items 10 and 11) enter this spec. The spec
is still **for review** with these additions; the audit's group 1 (measured accessibility defects) is done
**before** this mode.

- **Quick non-blind bypass, per stage and global ("cadena apagada"), loudness-matched** (`loudness_match.py`,
  as §3's direct/processed button): one switch on each chain card and one global, so the user hears what each
  stage does without saving two presets. It complements the blind A/B; it is the "compare with how it is now"
  of research/10 §8. Sources in research/10 §9: Roon's DSP toggle, Peace's on/off, Dirac's measured/corrected,
  X AIR's RTA pre/post.
- **Chain status light in the header**, after Roon's *Signal Path*: one indicator summarising
  direct / processed / limiting / clipped, with **shape and text as well as colour** (WCAG 1.4.1), **held red
  for a few seconds after a clip without blinking** (2.3.1); a click opens the chain's flow row.
- Both get browser tests (and axe, d-7c8794-a5f3ba), and the loudness test of §5 applies to the bypass.

