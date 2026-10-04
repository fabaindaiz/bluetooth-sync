"""One measurement for the sync estimator, from any source (spec 2026-10-03 §4.1).

The server's microphone (the loop), a phone measuring now and then (`point`), or one left
measuring (`continuous`) all give the same thing: where each speaker it heard arrived in one
recording, the two halves the arrival was confirmed with, each speaker's level, and what the
microphone was. Speakers it did not hear are absent.

Arrivals are absolute within one recording only: a recording's common offset (the phone's
latency, its clock against the server's) is meaningless, so the estimator uses only the
differences between speakers of one recording (spec §3).

Levels are reference statistics, never fed back to gains or EQ (d-7c8794-e61118).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Literal

BAD_REQUEST, OUT_OF_RANGE, UNKNOWN_FIELD = "bad_request", "out_of_range", "unknown_field"
"""The contract's error codes this module raises (`control.HTTP_STATUS`)."""
MAX_AGE_S = 600.0
"""A measurement older than this (server clock) is refused: the estimator's window is 10 min."""
MAX_ARRIVAL_MS = 10_000.0
FIELDS = {
    "source_id",
    "position_id",
    "kind",
    "role",
    "weight",
    "t",
    "arrivals_ms",
    "halves_ms",
    "levels_db",
    "quality",
    "origin",
}
KINDS = ("continuous", "point")
ROLES = ("target", "vote")
ORIGINS = ("server", "browser")


class MeasurementError(ValueError):
    """A measurement that cannot be taken. `code` is a contract error code (`control.py`)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Measurement:
    source_id: str
    position_id: str
    kind: Literal["continuous", "point"]
    role: Literal["target", "vote"] | None
    weight: float
    t: float
    """Server clock (`time.monotonic` of the service), the middle of the recorded window."""
    arrivals_ms: dict[str, float]
    halves_ms: dict[str, tuple[float, float]] = field(default_factory=dict)
    levels_db: dict[str, float] = field(default_factory=dict)
    quality: dict[str, Any] = field(default_factory=dict)
    """What the browser applied (`echo_cancellation`, `noise_suppression`, `auto_gain_control`,
    `sample_rate`); empty for the server's microphone."""
    origin: Literal["server", "browser"] = "server"

    @property
    def heard(self) -> frozenset[str]:
        return frozenset(self.arrivals_ms)

    @property
    def processed(self) -> bool:
        """Whether the browser kept any voice processing on (a noise suppressor eats a probe that is noise)."""
        return any(bool(self.quality.get(k)) for k in ("echo_cancellation", "noise_suppression", "auto_gain_control"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "position_id": self.position_id,
            "kind": self.kind,
            "role": self.role,
            "weight": self.weight,
            "t": self.t,
            "arrivals_ms": dict(self.arrivals_ms),
            "halves_ms": {n: list(v) for n, v in self.halves_ms.items()},
            "levels_db": dict(self.levels_db),
            "quality": dict(self.quality),
            "origin": self.origin,
        }

    @classmethod
    def from_dict(cls, data: Any, speakers: set[str], now: float) -> Measurement:
        """Validated completely: an unknown field, speaker or value refuses the whole measurement."""
        if not isinstance(data, dict):
            msg = "a measurement is an object"
            raise MeasurementError(BAD_REQUEST, msg)
        unknown = set(data) - FIELDS
        if unknown:
            msg = f"unknown fields: {sorted(unknown)}"
            raise MeasurementError(UNKNOWN_FIELD, msg)
        kind = _choice(data, "kind", KINDS)
        origin = _choice(data, "origin", ORIGINS, default="server")
        role = data.get("role")
        if kind == "point" and role not in ROLES:
            msg = f"a point measurement needs a role: one of {list(ROLES)}"
            raise MeasurementError(BAD_REQUEST, msg)
        if kind == "continuous" and role is not None:
            msg = "a continuous measurement has no role"
            raise MeasurementError(BAD_REQUEST, msg)
        source_id, position_id = _text(data, "source_id"), _text(data, "position_id")
        weight = _number(data, "weight", 0.0, 100.0, default=1.0)
        t = _number(data, "t", -math.inf, math.inf)
        if t < now - MAX_AGE_S or t > now + 5.0:
            msg = f"t is {now - t:.0f} s from now; at most {MAX_AGE_S:.0f} s old"
            raise MeasurementError(OUT_OF_RANGE, msg)
        arrivals = _per_speaker(data.get("arrivals_ms", {}), "arrivals_ms", speakers, 0.0, MAX_ARRIVAL_MS)
        if not arrivals:
            msg = "a measurement needs at least one arrival"
            raise MeasurementError(BAD_REQUEST, msg)
        halves_raw = data.get("halves_ms") or {}
        if not isinstance(halves_raw, dict):
            msg = "halves_ms is an object"
            raise MeasurementError(BAD_REQUEST, msg)
        halves = {}
        for name, pair in halves_raw.items():
            if name not in arrivals:
                msg = f"halves_ms names {name!r}, which has no arrival"
                raise MeasurementError(OUT_OF_RANGE, msg)
            if not isinstance(pair, list | tuple) or len(pair) != 2:  # noqa: PLR2004
                msg = f"halves_ms[{name!r}] is two numbers"
                raise MeasurementError(BAD_REQUEST, msg)
            halves[name] = tuple(_finite(v, f"halves_ms[{name!r}]", 0.0, MAX_ARRIVAL_MS) for v in pair)
        levels = _per_speaker(data.get("levels_db") or {}, "levels_db", set(arrivals), -200.0, 60.0)
        quality = data.get("quality") or {}
        if not isinstance(quality, dict):
            msg = "quality is an object"
            raise MeasurementError(BAD_REQUEST, msg)
        return cls(source_id, position_id, kind, role, weight, t, arrivals, halves, levels, dict(quality), origin)


def _choice(data: dict, key: str, choices: tuple[str, ...], default: str | None = None) -> Any:
    value = data.get(key, default)
    if value not in choices:
        msg = f"{key} must be one of {list(choices)}"
        raise MeasurementError(BAD_REQUEST, msg)
    return value


def _text(data: dict, key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not 1 <= len(value) <= 128:  # noqa: PLR2004
        msg = f"{key} is a string of 1 to 128 characters"
        raise MeasurementError(BAD_REQUEST, msg)
    return value


def _finite(value: Any, what: str, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        msg = f"{what} must be a finite number"
        raise MeasurementError(OUT_OF_RANGE, msg)
    if not low <= value <= high:
        msg = f"{what} must be between {low} and {high}"
        raise MeasurementError(OUT_OF_RANGE, msg)
    return float(value)


def _number(data: dict, key: str, low: float, high: float, default: float | None = None) -> float:
    if key not in data and default is not None:
        return default
    return _finite(data.get(key), key, low, high)


def _per_speaker(raw: Any, key: str, allowed: set[str], low: float, high: float) -> dict[str, float]:
    if not isinstance(raw, dict):
        msg = f"{key} is an object"
        raise MeasurementError(BAD_REQUEST, msg)
    out = {}
    for name, value in raw.items():
        if name not in allowed:
            msg = f"{key} names {name!r}, not a speaker here"
            raise MeasurementError(OUT_OF_RANGE, msg)
        out[name] = _finite(value, f"{key}[{name!r}]", low, high)
    return out
