"""How the sync estimator turns measurements into each speaker's latency (spec 2026-10-03 §4).

The model, for position `p` (a microphone where it is), speaker `s`, recording `k`:

    arrival = e_s + d_s * (t_k - now) + steps_s(t_k) + b_{p,s} + c_k

- `c_k`, the recording's common offset (a phone's latency and clock), is an unknown per
  recording: only differences within one recording carry information;
- `b_{p,s}`, the acoustic path from that position to each speaker, is an unknown for every
  position except the **anchor**, where it is 0 by definition (the alignment is for that
  spot). The anchor is the latest point `target`, or the server's microphone while there is
  none. A point `vote` keeps its `b` but with a prior `b = 0` of weight `point_vote_weight`.
  A continuous position's `b` is free: it tells drift and jumps, never where a speaker sits;
- `d_s`, the drift, ms per second (1 ms/s = 1000 ppm), shared by every position;
- `steps_s`, the confirmed jumps of that speaker (a stream that resynchronised).

`robust_ls` solves it as weighted least squares (`numpy.linalg.lstsq`, whose minimum-norm
solution takes care of the gauge: a constant can move between `e` and `c`), reweighted with a
Huber loss so that a wrong peak does not move the fit. A jump is believed only when the last
`jump_repeats` residuals of one speaker agree on it, one speaker per pass: the common offset
absorbs part of a jump, so the others' residuals move too, but less.

Only anchors and votes place a speaker. One no anchor nor vote has heard gets no latency, and
the suggestion leaves its delay alone (spec §3).

`tracks` and `kalman` are chosen in the settings but built in step 5 of the spec: until then
they suggest nothing and say so.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field, fields, replace
from typing import TYPE_CHECKING, Any

import numpy as np

from aurasync.arrival_loop import MIN_SPAN_S

if TYPE_CHECKING:
    from collections.abc import Callable

    from aurasync.sync_measurement import Measurement

METHODS = ("robust_ls", "tracks", "kalman")
BUILT = ("robust_ls",)
IRLS_ITERATIONS = 5
JUMP_PASSES = 3
PRIOR_SCALE = 1e-3
"""The priors `b = 0` weigh this much against one measurement row: they only split `e` from `b`,
so the data must rule. At the scale of the data, 16 recordings of a vote pulled harder than one
of the anchor, and a vote of 1 landed at 0.65-0.72 of the way instead of 1/2 (review 2026-10-03).
Scaled down, the alignment lands at w/(1+w) of the way to a vote of weight w, however many
recordings each spot has."""
SAME_TIME_S = 1e-6
"""Two jumps of one speaker this close in time are the same jump."""
TUKEY_FACTOR = 6.0
"""Tukey's cut, in `huber_ms`: residuals past it have no weight in the last pass."""
JUMP_FACTOR = 3.0
"""A residual this many `huber_ms` away is a candidate for a jump."""

RANGES: dict[str, tuple[float, float]] = {
    "window_min": (2.0, 60.0),
    "huber_ms": (0.05, 2.0),
    "jump_repeats": (1, 4),
    "point_vote_weight": (1.0, 20.0),
    "moved_threshold_ms": (0.3, 5.0),
    "min_speakers": (2, 8),
    "continuous_every_s": (4.0, 120.0),
    "kalman_gate_sigma": (1.0, 10.0),
    "kalman_drift_ppm": (1.0, 500.0),
}
CHOICES: dict[str, tuple[str, ...]] = {"method": METHODS, "point_role_default": ("target", "vote")}
INTEGERS = frozenset({"jump_repeats", "min_speakers"})
"""Counts: a fraction would slice a list with a float and kill the estimator's thread (review 2026-10-03)."""


@dataclass(frozen=True)
class SyncSettings:
    method: str = "robust_ls"
    window_min: float = 10.0
    huber_ms: float = 0.3
    jump_repeats: int = 2
    point_role_default: str = "target"
    point_vote_weight: float = 3.0
    moved_threshold_ms: float = 1.0
    min_speakers: int = 2
    accept_processed_audio: bool = False
    continuous_every_s: float = 20.0
    kalman_gate_sigma: float = 3.0
    kalman_drift_ppm: float = 50.0

    def __post_init__(self) -> None:
        for name, (low, high) in RANGES.items():
            value = getattr(self, name)
            kinds = int if name in INTEGERS else int | float
            if isinstance(value, bool) or not isinstance(value, kinds) or not low <= value <= high:
                what = "a whole number" if name in INTEGERS else "a number"
                msg = f"{name} must be {what} between {low} and {high}, not {value!r}"
                raise ValueError(msg)
        for name, choices in CHOICES.items():
            if getattr(self, name) not in choices:
                msg = f"{name} must be one of {list(choices)}, not {getattr(self, name)!r}"
                raise ValueError(msg)
        if not isinstance(self.accept_processed_audio, bool):
            msg = "accept_processed_audio must be true or false"
            raise ValueError(msg)  # noqa: TRY004 - the contract maps ValueError to out_of_range

    def replace(self, **changes: Any) -> SyncSettings:
        unknown = set(changes) - {f.name for f in fields(self)}
        if unknown:
            msg = f"unknown settings: {sorted(unknown)}"
            raise ValueError(msg)
        return replace(self, **changes)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Any, warn: Callable[[str], None]) -> SyncSettings:
        """Lenient: what is wrong is reported and left at its default."""
        out = cls()
        if not isinstance(data, dict):
            warn("sync settings: expected an object; using the defaults")
            return out
        for key, value in data.items():
            try:
                out = out.replace(**{key: value})
            except ValueError as exc:
                warn(f"sync settings: {exc}; kept the default")
        return out


@dataclass(frozen=True)
class Fit:
    latency_ms: dict[str, float] = field(default_factory=dict)
    """Each placed speaker's latency at `now` (anchor frame), ms. Comparable between speakers only."""
    sigma_ms: dict[str, float] = field(default_factory=dict)
    drift_ppm: dict[str, float] = field(default_factory=dict)
    jumps: list[tuple[float, str, float]] = field(default_factory=list)
    used: int = 0
    rejected: dict[int, str] = field(default_factory=dict)
    """Index in the list given -> why it was left out."""
    anchor: str | None = None
    reason: str | None = None
    """Why nothing (or not every speaker heard) is placed."""
    last_t: float | None = None
    """The newest measurement the fit used: with `used`, tells whether anything new was kept."""


def suggested_delays(f: Fit) -> dict[str, float]:
    """The delay that aligns each placed speaker at the anchor, the smallest at 0."""
    if not f.latency_ms:
        return {}
    top = max(f.latency_ms.values())
    return {s: top - v for s, v in f.latency_ms.items()}


def fit(
    measurements: list[Measurement],
    settings: SyncSettings,
    now: float,
    server_position: str,
    known_jumps: tuple[tuple[float, str, float], ...] | list = (),
) -> Fit:
    """`known_jumps`: the jumps a previous fit confirmed (`Fit.jumps`), carried by the caller. A
    jump is believed when `jump_repeats` residuals agree on it; once more points come after it,
    the drift and the robust weights would absorb it again, and it would be lost (review
    2026-10-03: the suggestion was up to 2 ms off for ~6 min). So it stays a step of the model
    while measurements from before it remain in the window."""
    if settings.method not in BUILT:
        return Fit(reason=f"the method {settings.method!r} is not built yet (step 5 of the spec): no suggestion")
    kept, rejected = _select(measurements, settings, now)
    if not kept:
        reason = (
            f"no measurement heard at least {settings.min_speakers} speakers in the last {settings.window_min:g} min"
        )
        return Fit(rejected=rejected, reason=reason)
    # The latest target among everything given, not only what the window kept: newer recordings
    # from the same spot may have superseded the target's own row (`_select`).
    targets = [m for m in measurements if m.kind == "point" and m.role == "target" and m.t <= now]
    anchor = max(targets, key=lambda m: m.t).position_id if targets else server_position
    return _robust_ls(kept, settings, now, anchor, rejected, known_jumps)


def rejection(m: Measurement, settings: SyncSettings) -> str | None:
    """Why a measurement can never be used under `settings` (the window aside), or None."""
    if len(m.heard) < settings.min_speakers:
        return f"heard {len(m.heard)} speaker(s); a measurement needs {settings.min_speakers}"
    if m.processed and not settings.accept_processed_audio:
        return "the browser kept voice processing on"
    return None


def _select(measurements: list[Measurement], settings: SyncSettings, now: float):
    start = now - settings.window_min * 60
    targets = [i for i, m in enumerate(measurements) if m.kind == "point" and m.role == "target"]
    latest_target = max(targets, key=lambda i: measurements[i].t) if targets else None
    if latest_target is not None:
        where = measurements[latest_target].position_id
        # Newer recordings from the same spot supersede an old target: far back in time it
        # has a long lever on the drift, and one wrong peak in it would bend the slope.
        if any(m.position_id == where and start <= m.t <= now for m in measurements):
            latest_target = None
    kept, rejected = [], {}
    for i, m in enumerate(measurements):
        why = rejection(m, settings)
        if why is not None:
            rejected[i] = why
        elif m.t > now + 1.0:
            rejected[i] = "from the future"
        elif m.t < start and i != latest_target:
            # Out of the window; the latest target is kept anyway: it defines the alignment.
            continue
        else:
            kept.append(m)
    return kept, rejected


class _Columns:
    def __init__(self) -> None:
        self.index: dict[tuple, int] = {}

    def __call__(self, key: tuple) -> int:
        if key not in self.index:
            self.index[key] = len(self.index)
        return self.index[key]


def _robust_ls(kept, settings: SyncSettings, now: float, anchor: str, rejected, known_jumps=()) -> Fit:
    speakers = sorted({s for m in kept for s in m.heard})
    times = {s: [m.t for m in kept if s in m.heard] for s in speakers}
    drifting = {s for s in speakers if max(times[s]) - min(times[s]) >= MIN_SPAN_S}
    placed = {s for m in kept if m.position_id == anchor or m.role == "vote" for s in m.heard}
    # A known jump is a step only while some recording of that speaker is from before it: with
    # all of them after, the step and the offset are the same column.
    jumps: list[tuple[float, str]] = [(t, sp) for t, sp, *_ in known_jumps if sp in times and min(times[sp]) < t <= now]
    solution, rows, columns, weights, residual = _solve(kept, settings, now, anchor, speakers, drifting, jumps)
    for _ in range(JUMP_PASSES):
        found = _jump(kept, rows, residual, settings, jumps)
        if found is None:
            break
        jumps.append(found)
        # Solved again right away: a jump appended and never solved had no column (review 2026-10-03).
        solution, rows, columns, weights, residual = _solve(kept, settings, now, anchor, speakers, drifting, jumps)
    latency, sigma = {}, {}
    cov = _covariance(rows, weights, residual)
    for s in speakers:
        if s not in placed:
            continue
        value = solution[columns.index[("e", s)]]
        value += sum(solution[columns.index[("u", t, j)]] for t, j in jumps if j == s and t <= now)
        latency[s] = float(value)
    for s in latency:
        contrast = np.zeros(len(columns.index))
        contrast[columns.index[("e", s)]] = 1.0
        for other in latency:
            contrast[columns.index[("e", other)]] -= 1.0 / len(latency)
        sigma[s] = float(math.sqrt(max(contrast @ cov @ contrast, 0.0)))
    # Relative to the drifting speakers' mean: a drift common to all is absorbed by the
    # recordings' offsets (only differences within a recording are seen), so it is unknown.
    raw = {s: float(solution[columns.index[("d", s)]] * 1000.0) for s in drifting}
    mean = float(np.mean(list(raw.values()))) if raw else 0.0
    drift = {s: v - mean for s, v in raw.items()}
    unplaced = sorted(set(speakers) - placed)
    reason = f"not heard by the anchor or a vote: {', '.join(unplaced)}" if unplaced else None
    if len(latency) < 2:  # noqa: PLR2004
        reason = reason or "fewer than two speakers placed"
    steps = [(t, s, float(solution[columns.index[("u", t, s)]])) for t, s in jumps]
    return Fit(latency, sigma, drift, steps, len(kept), rejected, anchor, reason, max(m.t for m in kept))


def _solve(kept, settings, now, anchor, speakers, drifting, jumps):
    columns = _Columns()
    for s in speakers:
        columns(("e", s))
    for s in sorted(drifting):
        columns(("d", s))
    rows: list[tuple[dict[int, float], float, float, int, str]] = []
    """(coefficients, value, weight, measurement index, speaker)."""
    for k, m in enumerate(kept):
        c = columns(("c", k))
        for s, y in m.arrivals_ms.items():
            coef = {columns.index[("e", s)]: 1.0, c: 1.0}
            if s in drifting:
                coef[columns.index[("d", s)]] = m.t - now
            for t, j in jumps:
                if j == s and m.t >= t:
                    coef[columns(("u", t, j))] = 1.0
            coef[columns(("b", m.position_id, s))] = 1.0
            rows.append((coef, y, m.weight, k, s))
    # One prior `b = 0` per (position, speaker): weight 1 at the anchor, `point_vote_weight` at
    # a vote. With enough data the alignment lands on the weighted mean of those positions;
    # without a vote, on the anchor exactly (only its prior splits `e` from `b`).
    priors: dict[tuple[str, str], tuple[float, int]] = {}
    for k, m in enumerate(kept):
        if m.position_id == anchor:
            weight = 1.0
        elif m.role == "vote":
            weight = settings.point_vote_weight
        else:
            continue
        for s in m.heard:
            priors[(m.position_id, s)] = (weight, k)
    for (position, s), (weight, k) in sorted(priors.items()):
        rows.append(({columns(("b", position, s)): 1.0}, 0.0, PRIOR_SCALE * weight, k, ""))
    for t, j in jumps:
        columns(("u", t, j))
    a = np.zeros((len(rows), len(columns.index)))
    y = np.array([r[1] for r in rows])
    base = np.array([r[2] for r in rows])
    for i, (coef, *_rest) in enumerate(rows):
        for col, v in coef.items():
            a[i, col] = v
    huber = np.ones(len(rows))
    prior_rows = [i for i, r in enumerate(rows) if not r[4]]
    solution = np.zeros(len(columns.index))
    for _ in range(IRLS_ITERATIONS):
        w = np.sqrt(base * huber)
        solution = np.linalg.lstsq(a * w[:, None], y * w, rcond=None)[0]
        residual = y - a @ solution
        r = np.abs(residual)
        huber = np.where(r <= settings.huber_ms, 1.0, settings.huber_ms / np.maximum(r, 1e-12))
        # The priors keep their weight: they are what a vote weighs, not a measurement that can be
        # wrong. Reweighted, a vote of 3 landed at 0.9 of the way instead of 3/4 (review 2026-10-03).
        huber[prior_rows] = 1.0
    # A last redescending pass (Tukey): a wrong peak far out gets no weight at all, where
    # Huber still leaves it `huber_ms` of pull, enough with three speakers per recording.
    c = TUKEY_FACTOR * settings.huber_ms
    residual = y - a @ solution
    tukey = np.where(np.abs(residual) < c, (1 - (residual / c) ** 2) ** 2, 0.0)
    tukey[prior_rows] = 1.0  # the priors keep their weight
    w = np.sqrt(base * tukey)
    solution = np.linalg.lstsq(a * w[:, None], y * w, rcond=None)[0]
    residual = y - a @ solution
    return solution, (a, rows), columns, base * tukey, residual


def _jump(kept, rows, residual, settings, jumps) -> tuple[float, str] | None:
    """The speaker whose last `jump_repeats` residuals agree on a shift, if any (the largest)."""
    _, meta = rows
    by_speaker: dict[str, list[tuple[float, float]]] = {}
    for (_, _, _, k, s), r in zip(meta, residual, strict=True):
        if s:
            by_speaker.setdefault(s, []).append((kept[k].t, float(r)))
    threshold = JUMP_FACTOR * settings.huber_ms
    best = None
    for s, pts in by_speaker.items():
        pts.sort()
        last = pts[-settings.jump_repeats :]
        if len(last) < settings.jump_repeats:
            continue
        values = [r for _, r in last]
        same_sign = all(v > threshold for v in values) or all(v < -threshold for v in values)
        agree = max(values) - min(values) <= max(settings.huber_ms, 0.1 * abs(np.mean(values)))
        if same_sign and agree:
            # The step starts where the run of shifted residuals starts, not at the first of the
            # last `jump_repeats`: earlier shifted points belong to the same jump.
            sign = 1.0 if values[0] > 0 else -1.0
            start = len(pts) - len(last)
            while start > 0 and sign * pts[start - 1][1] > threshold:
                start -= 1
            at = pts[start][0]
            if any(j == s and abs(t - at) < SAME_TIME_S for t, j in jumps):
                continue
            size = abs(float(np.mean(values)))
            if best is None or size > best[0]:
                best = (size, at, s)
    return None if best is None else (best[1], best[2])


def _covariance(rows, weights, residual) -> np.ndarray:
    a, _ = rows
    w = weights
    dof = max(len(residual) - np.linalg.matrix_rank(a), 1)
    sigma2 = float(np.sum(w * residual**2) / dof)
    return sigma2 * np.linalg.pinv(a.T @ (a * w[:, None]))
