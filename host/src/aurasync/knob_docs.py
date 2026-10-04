"""What a setting tells the listener: its recommendation, how it changes the sound, and a figure.

d-7c8794-0a4586: every setting carries a recommendation and an explanation, in words and
visual, of how it changes the sound, one by one and together. This is the shared format; the
sync estimator's settings fill it first (`sync_docs`), the chain's knobs later.

A figure is declarative: the panel draws it (no images), from series the server computed with
the same simulator as the tests, and it says whether it is SIMULADO or MEDIDO.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal


@dataclass(frozen=True)
class Figure:
    kind: Literal["timeline", "bars", "histogram", "room"]
    x: str
    y: str
    series: list[dict[str, Any]]
    """Each `{"label", "points": [[x, y], ...], "style": "recommended" | "current" | "other"}`;
    a `y` of None is a gap (nothing suggested then)."""
    caption: str
    evidence: Literal["SIMULADO", "MEDIDO"] = "SIMULADO"
    marks: list[dict[str, Any]] = field(default_factory=list)
    """Vertical lines with a label, e.g. `{"x": 20.0, "label": "salto de 6,5 ms"}`."""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Doc:
    id: str
    title: str
    summary: str
    help: str
    recommended: Any
    why_recommended: str
    sounds_low: str = ""
    """How the sound changes with a low value (numeric settings)."""
    sounds_high: str = ""
    sounds_choices: dict[str, str] = field(default_factory=dict)
    """How the sound changes with each choice (settings with choices)."""
    unit: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
