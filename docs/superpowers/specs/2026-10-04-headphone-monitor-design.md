# Headphone monitor — design

**Date:** 2026-10-04 · **Status:** approved by the user as part of the plan of
`docs/research/14-el-proyecto-en-su-contexto.md` ("No hay problema con monitor no sincronizado por
ahora, quiero que esta feature sí esté disponible").

## 1. What it is

A second output of the engine, to any PipeWire sink that is not a speaker of the installation
(wired or Bluetooth headphones, the PC's own output, a virtual sink). It plays **at the same time as
the speakers** and is **not synchronised** with them: it is outside the measured loop, and its
latency is whatever PipeWire and the device add.

Modes:

| Mode | What reaches the headphones |
|---|---|
| `off` | nothing (default) |
| `stereo` | the input pair, before the chain: the music as the application sends it |
| `mix` | the speaker channels after the chain, folded to L/R by each speaker's angle (constant power) |
| `binaural` | the speaker channels through PipeWire's SOFA spatializer, each one a virtual speaker at its angle (MIT KEMAR HRTF): the room heard on headphones |

`gain_db` (−40…0, default −12) scales what goes out. The monitor never changes what the speakers get.

## 2. Pieces

- `host/src/aurasync/monitor.py` (new, English):
  - `fold(blocks, angles) -> (left, right)`: constant-power fold, `angle` in degrees, 0 front,
    positive right (the repository's convention, `control.angle_of`).
  - `sofa_azimuth(angle) -> float`: SOFA azimuth is counter-clockwise, so `(-angle) mod 360`.
  - `binaural_args(names, angles, sink, target, sofa) -> str`: the `filter-chain` module arguments:
    one `sofa`/`spatializer` node per speaker, two builtin `mixer`s (≤ 8 inputs, the speaker
    maximum), the capture side a sink with `AUX0..AUXn-1`, the playback side to `target` with
    `dont-move`, `dont-reconnect`, `dont-fallback`.
  - `MonitorOutput(settings, names, angles, rate, forbidden)`: opens the module (binaural only)
    and one `pw-play --raw` with 2 or N channels; **a writer thread with a bounded queue**:
    `push(pair, blocks)` never blocks the engine, it drops the oldest block and counts it.
    `routing()` reads `pw-dump` and says where the stream (and in binaural the filter's output)
    really goes.
- Session: `set_monitor(settings)` opens/replaces/closes it; `step()` pushes after writing to the
  speakers; `close()` closes it. A target equal to a speaker's sink or to one of aurasync's own
  nodes is refused (`invalid`: it would loop back, experiment 09).
- Service: the settings live in the service (they outlive sessions, reapplied when one opens);
  op `monitor_set {mode, target, gain_db}` (scope `control`); the snapshot carries `monitor`
  `{mode, target, gain_db, state, routed_to, drops, candidates}`. `candidates` are the sinks that
  are not speakers of the installation nor aurasync's own.
- Panel: a "Monitor (audífonos)" card in Escuchar: mode, destination, level, and what PipeWire
  really did ("llega a X" / "no llegó"), with the warning that it is not synchronised.

## 3. Verification

- Unit: fold (a speaker at −90° only left; equal power), azimuth convention, module arguments
  (one node per speaker, links to both mixers, target and the three `dont-*`), refusal of a
  speaker or aurasync target, `push` never blocks with a stalled writer and counts drops.
- Session with the simulated room: the monitor gets each block and the speakers' output is
  bit-identical with and without it.
- Browser: the card lists candidates, sets the mode, shows the routing state.
- On `PC-Ryzen5` (MEDIDO): with the 3 Go 4 playing, binaural to the PC's output, `pw-dump`
  shows the links; microcuts counted as in experiment 12 with and without the monitor (a second
  Bluetooth link on the same radio may add cuts: INFERIDO, to measure).
