"""Measurements with a known truth, for testing and explaining the sync estimator. SIMULADO.

A room is a set of positions (where a microphone is), each with its acoustic path to every
speaker and the speakers it hears; the speakers have a latency that drifts and jumps. Each
position records every `every_s`, and each recording has its own common offset (a phone's
latency and clock), so only the differences between speakers of one recording carry anything
(spec 2026-10-03 §3). The figures of the panel's explanations come from here too
(`sync_docs`), so what the panel shows is what the tests check.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from aurasync.sync_measurement import Measurement

SPEED_OF_SOUND_MS_PER_M = 1000 / 343.0
"""2.92 ms per metre."""


@dataclass
class Scenario:
    speakers: list[str]
    latency_ms: dict[str, float]
    drift_ppm: dict[str, float]
    jumps: list[tuple[float, str, float]]
    """(t in s, speaker, ms)."""
    positions: dict[str, dict[str, float]]
    """Position -> speaker -> acoustic path in ms."""
    hears: dict[str, set[str]]
    noise_ms: float = 0.01
    outlier_rate: float = 0.0
    outlier_ms: float = 5.0
    every_s: float = 20.0
    duration_s: float = 3600.0
    anchor_position: str | None = None
    """Its first recording is a point `target`; the rest are continuous (or none: `anchor_every_s`)."""
    anchor_every_s: float | None = 20.0
    """None: the anchor records only once (a phone that measured and left)."""
    bias_ms: dict[str, dict[str, float]] = field(default_factory=dict)
    """Position -> speaker -> a constant error of that microphone."""
    moves: list[tuple[float, str, dict[str, float]]] = field(default_factory=list)
    """(t in s, position, new acoustic paths): a microphone that was moved."""
    votes: dict[str, float] = field(default_factory=dict)
    """Position -> weight: positions whose recordings are point `vote`s, not continuous."""


def true_latency(sc: Scenario, speaker: str, t: float) -> float:
    """The speaker's electronic latency at `t` (s), in ms."""
    jumped = sum(ms for at, s, ms in sc.jumps if s == speaker and t >= at)
    return sc.latency_ms[speaker] + sc.drift_ppm.get(speaker, 0.0) * 1e-6 * t * 1000 + jumped


def acoustic(sc: Scenario, position: str, speaker: str, t: float) -> float:
    paths = sc.positions[position]
    for at, where, new in sc.moves:
        if where == position and t >= at:
            paths = new
    return paths[speaker]


def alignment_error(sc: Scenario, delays: dict[str, float], t: float, position: str) -> float:
    """The misalignment heard at `position` at `t` with `delays`: max - min arrival, in ms."""
    arrivals = [true_latency(sc, s, t) + acoustic(sc, position, s, t) + d for s, d in delays.items()]
    return float(max(arrivals) - min(arrivals)) if len(arrivals) > 1 else 0.0


def measurements(sc: Scenario, seed: int) -> list[Measurement]:
    rng = np.random.default_rng(seed)
    out = []
    times = np.arange(0.0, sc.duration_s, sc.every_s)
    for position in sc.positions:
        anchor = position == sc.anchor_position
        for i, t in enumerate(times):
            if anchor and i > 0 and (sc.anchor_every_s is None or i % max(1, round(sc.anchor_every_s / sc.every_s))):
                continue
            common = rng.uniform(-200.0, 200.0) + 500.0
            arrivals = {}
            for s in sorted(sc.hears[position]):
                y = true_latency(sc, s, t) + acoustic(sc, position, s, t) + common
                y += sc.bias_ms.get(position, {}).get(s, 0.0)
                y += rng.normal(0.0, sc.noise_ms)
                if sc.outlier_rate and rng.random() < sc.outlier_rate:
                    y += rng.choice([-1, 1]) * sc.outlier_ms
                arrivals[s] = float(y)
            if anchor and i == 0:
                kind, role, weight = "point", "target", 1.0
            elif position in sc.votes:
                kind, role, weight = "point", "vote", sc.votes[position]
            else:
                kind, role, weight = "continuous", None, 1.0
            halves = {s: (a, a) for s, a in arrivals.items()}
            out.append(Measurement(position, position, kind, role, weight, float(t), arrivals, halves))
    out.sort(key=lambda m: m.t)
    return out


def _room(names: list[str], rng: np.random.Generator) -> dict[str, float]:
    return {s: float(rng.uniform(1.0, 12.0)) for s in names}


def standard(name: str, n: int = 3) -> Scenario:
    """The cases of spec §7. Speakers `s0..`; `s0` drifts 22 ppm, `s2` -15 ppm when there is one."""
    names = [f"s{i}" for i in range(n)]
    rng = np.random.default_rng(1000 + n)
    latency = {s: float(250.0 + rng.uniform(-30.0, 30.0)) for s in names}
    drift = {"s0": 22.0}
    if n > 2:  # noqa: PLR2004
        drift["s2"] = -15.0
    everyone = set(names)
    base = {"server": _room(names, rng)}
    sc = Scenario(names, latency, drift, [], base, {"server": everyone}, anchor_position="server")
    if name == "drift":
        return sc
    if name == "jump":
        sc.jumps = [(1200.0, "s1", 6.52)]
        return sc
    if name == "partial":
        half = n // 2 + 1
        sc.positions |= {"phone_a": _room(names, rng), "phone_b": _room(names, rng)}
        sc.hears |= {"phone_a": set(names[:half]), "phone_b": set(names[half - 1 :])}
        sc.anchor_every_s = None
        return sc
    if name == "two_positions":
        sc.positions["phone_a"] = _room(names, rng)
        sc.hears["phone_a"] = everyone
        return sc
    if name == "moved":
        sc.positions["phone_a"] = _room(names, rng)
        sc.hears["phone_a"] = everyone
        moved = dict(sc.positions["phone_a"])
        moved["s0"] += SPEED_OF_SOUND_MS_PER_M
        moved[names[-1]] -= SPEED_OF_SOUND_MS_PER_M
        sc.moves = [(1800.0, "phone_a", moved)]
        return sc
    if name == "biased":
        sc.positions["phone_a"] = _room(names, rng)
        sc.hears["phone_a"] = everyone
        sc.bias_ms = {"phone_a": {"s0": 2.0}}
        return sc
    msg = f"no standard scenario {name!r}"
    raise ValueError(msg)
