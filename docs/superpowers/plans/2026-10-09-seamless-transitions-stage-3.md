# Seamless transitions, stage 3 (the render switch) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Switching the render (`classic` / `spatial` / `front` / `direct`) in `crossfade` mode passes without
the cut: the per-speaker path of the old render and of the new one run side by side during the transition and
are mixed per speaker with the block weights; the render makeup glides; at the end only the new path remains.

**Architecture:** The per-speaker part of `Motor.procesar` that depends on the render (source selection,
spatial renderer, decorrelator, diffuse, bass feed/before_delay/process, EQ taps — flat in `direct` —, and the
effective delay) becomes a small "branch": the render plus the stateful objects that belong to it. Today
`_switch_render` swaps them at a cut's bottom (and rebuilds decorrelators/diffuse/bass/EQ taps when entering
or leaving `direct`). In crossfade mode the new branch is built outside the block (as `aplicar_cadena` already
prebuilds `espacial` and `fresh`), warms in the shadow (WARM: the longest memory of its stages, ≤ 1 s), then
both branches run and their per-speaker outputs are mixed before the gain/limiter; at the end the old branch is
dropped. Shared, untouched: the extractor (its output feeds both), the delay lines (two reads when the
effective delays differ — `fundir_a`), the gains, the probe and the limiter (once, after the mix).

**Spec:** `docs/superpowers/specs/2026-10-08-seamless-transitions-design.md` §4 render row, §5 stage 3, §7.

## Global Constraints

- motor.py stays Spanish; new helpers that can live outside it go to English modules. No commits; `nice -n 19`.
- Cut mode byte-identical (`golden_motor` must match). `pide_corte` stops returning true for a render change.
- The mix of the two branches uses the chain's `shape`: two renders of the same music are partly correlated;
  use `equal_power` if the no-hole test shows `equal_gain` dips, and record which with the measured dip (the
  same rule as delays — controller ruling expected, ask in the report if the numbers are borderline).
- The render makeup (`on_render_switch` / render_match): the new render's makeup target is set at the start
  and glides with the fade; render_match holds while `en_corte` (as today).
- CPU: during a render transition the per-speaker path runs twice for WARM + FADE (spec §4: about 4 blocks).
- Everything a render switch rebuilt at the cut's bottom (decorrelators, diffuse/bass `fresh`, EQ taps,
  `espacial`) belongs to its branch; nothing is rebuilt inside the block.

## Review Focus

1. Entering and leaving `direct` (EQ flat, no decorrelator/diffuse/bass feed, no Haas): both directions, no hole.
2. A render switch while a stage-2 crossfade of the same stage runs (e.g. diffuse) — one transition at a time
   via the collapse rule; never two branches nested.
3. The spatial renderer's state (`SpatialUpmix`, Rust-backed) in both branches, and engine switches mid-fade.
4. Metrics/latency during the fade report the new render; `motor.latencia` does not jump.

---

### Task 1: the branch refactor (no behaviour change)

- [ ] Extract the render-dependent per-speaker work of `procesar` into a branch object/function, with the cut
  path and every existing test unchanged (golden ≤ 1e-9 untouched). Full suite green before Task 2.

### Task 2: crossfade the render

- [ ] In crossfade mode a render change builds the new branch, warms it, mixes per speaker, glides the makeup and
  delays, resolves at the end and at a cut's bottom. Tests: for each pair among the four renders (at least
  classic↔front, front↔spatial, classic↔direct, direct↔spatial): no hole (two steady reference runs, as stage 2),
  lands like a cut after warm + fade + memory (≤ 1e-9), cut mode still cuts, an engine switch mid-fade changes
  nothing, a cut mid-fade resolves to the new branch, dragging the render knob collapses.

### Task 3: service, presets, A/B, docs

- [ ] `preset_load`/`ab_play` with a render difference crossfade (the A/B pair rule still decides once for the
  pair); control-api (`apply: "crossfade"` for a render change), roadmap stage 3 → A medias (built, to listen),
  experimentos/23 pending listening item for render changes.
