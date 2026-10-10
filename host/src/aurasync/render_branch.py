"""The render branch: the part of each speaker's path that depends on the render.

Spec `2026-10-08-seamless-transitions-design.md` §4 (the render row) and §5, stage 3. A render
(`classic`, `spatial`, `front`, `direct`) decides, per speaker, where the signal comes from (the
pan of L/R, the spatial upmix, the constant-power pan of `direct`), whether it is decorrelated,
whether the diffuse tail and the bass feed are added, the effective delay (`direct` has no Haas),
the EQ (flat in `direct`) and whether the bass stage filters it. Everything after it (the gain,
the probe, the limiter) and before it (the extractor) is shared.

`RenderBranch` holds the stateful objects of one render. The motor keeps the playing one in its
own attributes (`render`, `espacial`, `_decorreladores`, `_difusion`, `_graves`, `_lineas`,
`_ecualizador`) and, while a render crossfade runs, the one leaving in `_rama_vieja`: both run on
the same input and their per-speaker outputs are mixed before the gain. The delay lines belong to
the branch too: a line has one input, and the two renders feed it different signals, so a shared
line could not give each branch its own history (the stage-3 plan had them shared; corrected when
it was built, 2026-10-10).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from aurasync.dsp import eq, interpolation, spatial


@dataclass
class RenderBranch:
    """One render and the stateful objects that belong to it."""

    render: str
    spatial: Any
    """The `SpatialUpmix` of the `spatial` and `front` renders, None otherwise."""
    decorrelators: dict[str, Any]
    """One `StreamingFIR` per speaker, None without a filter."""
    diffuse: Any
    bass: Any
    lines: dict[str, Any]
    """One `LineaDeRetardo` per speaker, at this render's effective delay."""
    eq: dict[str, Any]
    """One `StreamingFIR` per speaker with this render's taps; empty without the EQ stage."""


def warm_samples(branch: RenderBranch, *, sr: int, delays_ms: list[float], bass_delay: int, filters: list[int]) -> int:
    """How long a fresh branch runs in the shadow before it is mixed in: the memory of its stages
    one after the other (the upmix's latency and fade-in, the decorrelator, the line, the EQ, the
    bass filters), plus the diffuse tail, added beside them. `Transition` caps it at one second.

    `filters`: the decorrelator's filter lengths; `bass_delay`: what the bass feed is held by."""
    upmix = branch.spatial.latency + spatial.FADE_IN if branch.spatial is not None else 0
    decorrelator = max((length - 1 for length in filters), default=0)
    line = round(max(delays_ms, default=0.0) * sr / 1000) + 2 * interpolation.HALF + 1
    equalizer = eq.TAPS - 1 if branch.eq else 0
    return (
        upmix
        + decorrelator
        + line
        + equalizer
        + branch.bass.memory_samples(bass_delay)
        + branch.diffuse.memory_samples()
    )
