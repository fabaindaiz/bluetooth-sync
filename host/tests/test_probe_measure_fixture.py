"""The fixture the browser's port of `probe_measure.measure` is checked against (spec 2026-10-03 §7:
"the same recordings give the same arrivals within 0.01 ms"). This test writes the recordings and
Python's answers to `web/test/fixtures/probe_measure.json` when they change; `web/test/measure.test.ts`
runs the TypeScript port on the same recordings and compares."""

import base64
import json
from pathlib import Path

import numpy as np

from aurasync import probe_measure

FIXTURE = Path(__file__).resolve().parents[1] / "web" / "test" / "fixtures" / "probe_measure.json"
SR = 16000
"""Lower than the engine's so the file stays small; the probe's band (300 Hz-8 kHz) still fits."""


def _int16(x: np.ndarray, scale: float) -> str:
    return base64.b64encode(np.round(x / scale).astype("<i2").tobytes()).decode()


def _case(seed: int, delays_ms: dict[str, float], heard: set[str]) -> dict:
    rng = np.random.default_rng(seed)
    ref_n, mic_n = int(0.5 * SR), int(0.9 * SR)
    probes = {name: rng.standard_normal(ref_n) * 0.05 for name in delays_ms}
    mic = rng.standard_normal(mic_n) * 0.002
    for name, d in delays_ms.items():
        if name in heard:
            k = round(d * SR / 1000)
            mic[k : k + ref_n] += probes[name] * 0.7
    scale = max(float(np.max(np.abs(a))) for a in [mic, *probes.values()]) / 32767
    m = probe_measure.measure(mic, probes, SR)
    return {
        "sr": SR,
        "scale": scale,
        "mic": _int16(mic, scale),
        "probes": {n: _int16(p, scale) for n, p in probes.items()},
        "python": {
            "arrivals_ms": m.arrivals_ms,
            "halves_ms": {n: list(v) for n, v in m.halves_ms.items()},
            "valid": sorted(m.valid),
        },
    }


def _decode(case: dict) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    def back(b64: str) -> np.ndarray:
        return np.frombuffer(base64.b64decode(b64), dtype="<i2").astype(float) * case["scale"]

    return back(case["mic"]), {n: back(v) for n, v in case["probes"].items()}


def _cases() -> list[dict]:
    return [
        _case(1, {"a": 12.0, "b": 19.5, "c": 15.25}, {"a", "b", "c"}),
        _case(2, {"a": 30.0, "b": 41.0, "c": 35.0}, {"a", "b"}),  # one speaker not heard
    ]


def test_the_fixture_is_current():
    """Python's answer on the int16 recordings as stored (what the browser reads), not the floats."""
    cases = _cases()
    for case in cases:
        mic, probes = _decode(case)
        m = probe_measure.measure(mic, probes, SR)
        case["python"] = {
            "arrivals_ms": m.arrivals_ms,
            "halves_ms": {n: list(v) for n, v in m.halves_ms.items()},
            "valid": sorted(m.valid),
        }
    text = json.dumps({"cases": cases}, indent=1, sort_keys=True) + "\n"
    if not FIXTURE.exists() or FIXTURE.read_text() != text:
        FIXTURE.parent.mkdir(parents=True, exist_ok=True)
        FIXTURE.write_text(text)
    assert cases[0]["python"]["valid"] == ["a", "b", "c"]
    assert cases[1]["python"]["valid"] == ["a", "b"]
    assert abs(cases[0]["python"]["arrivals_ms"]["b"] - cases[0]["python"]["arrivals_ms"]["a"] - 7.5) < 0.05
