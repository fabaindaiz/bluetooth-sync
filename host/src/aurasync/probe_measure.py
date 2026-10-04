"""Each speaker's arrival, measured against the masked probe it carried (`dsp/probe.py`).

The estimator of step 2 of i-7c8794-e3e40d (spec 2026-10-01 §4.1): a generalised
cross-correlation between the microphone and **the probe that was sent**, PHAT-weighted only
inside the probe's band (300 Hz-8 kHz), with the peak interpolated — the estimator that
`probes/13` validated in experimentos/11 (and with 8 simultaneous probes, experimentos/16
§4.1).

**What makes a speaker's measurement valid** (CLAUDE.md: a true delay survives a change of a
parameter that should not matter, and shows up twice in independent measurements):

- **repetition**: the window is split in two halves, each with its own stretch of probe noise
  and of music — two independent measurements. They must agree within `AGREEMENT_MS`, and
  the whole window with them (a change of the window length, the parameter that should not
  matter);
- **consensus**: the arrival must be within `CONSENSUS_MS` of the median of the arrivals of
  the speakers whose halves agreed (not of every speaker: see `measure`), as in `medicion.alineacion_gruesa`: all the speakers share the playback latency
  to within a PipeWire quantum, and a peak hundreds of ms away is not that speaker;
- **a probe to measure**: each half must carry probe (`dsp/probe.py` switches it off in
  silence).

Each speaker is judged on its own: one speaker that fails does not throw away the others
(experimentos/16 §4.2: with 8 speakers, demanding all at once keeps p⁸ of the measurements).

The peak-to-sidelobe ratio that the spec mentions is not used: `medicion.CONFIANZA_MINIMA`
explains why a peak's sharpness was dropped as a criterion (it overlapped between noise and
weak real signal).

**The arrivals are absolute, not residuals.** The reference is the probe as it left the
engine, after the delay line, so the arrival measured is the speaker's own playback latency
(pw-play, A2DP, the speaker, the air): it does not change when the loop moves that speaker's
delay. `arrival_loop.ArrivalLoop` is built for that (and `sincronia.Controlador.proponer`,
which treats the measurement as a residual, is not: see `arrival_loop.py`).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from aurasync.dsp import probe

SR = 48000
MAX_LAG_MS = 1500.0
"""How far into the recording a probe can arrive: `sincronia.VentanaDeEmision` leaves the
reference 0.5 s plus the playback latency into the microphone's slice."""
AGREEMENT_MS = 0.25
"""How much the two halves (and the whole window) may disagree. The probe's error is ~0.01 ms
at p95 (experimentos/11, 16 §4.1); a wrong peak is off by more than 1 ms. Half the loop's dead
band, so that an accepted measurement cannot move a speaker by more than the dead band out of
its own inconsistency."""
CONSENSUS_MS = 100.0
"""As `medicion.alineacion_gruesa`: one PipeWire quantum (42.7 ms) of spread is real."""
SILENT_RMS = 1e-7
"""Below this RMS a half of a reference carried no probe."""


@dataclass(frozen=True)
class ProbeMeasurement:
    """One window: each speaker's arrival in the recording, and whether to believe it."""

    arrivals_ms: dict[str, float]
    """Where each speaker's probe starts in the recording (the whole window), in ms. Absolute
    within this measurement: a common offset between measurements is meaningless."""
    halves_ms: dict[str, tuple[float, float]]
    valid: frozenset[str]
    reasons: dict[str, str] = field(default_factory=dict)
    """Why each invalid speaker was not believed."""

    def spread_ms(self) -> float | None:
        """The misalignment the valid speakers show in this window (max - min arrival)."""
        values = [self.arrivals_ms[n] for n in self.valid]
        return float(max(values) - min(values)) if len(values) >= 2 else None  # noqa: PLR2004


def _peak(correlation: np.ndarray, limit: int) -> float:
    window = correlation[: limit + 1]
    i = int(np.argmax(window))
    if 0 < i < len(window) - 1:
        a, b, c = window[i - 1], window[i], window[i + 1]
        d = a - 2 * b + c
        if d != 0:
            return i + 0.5 * (a - c) / d
    return float(i)


def measure(
    mic: np.ndarray,
    probes: dict[str, np.ndarray],
    sr: int = SR,
    *,
    max_lag_ms: float = MAX_LAG_MS,
    agreement_ms: float = AGREEMENT_MS,
    band_hz: tuple[float, float] = probe.BAND_HZ,
) -> ProbeMeasurement:
    """Measure every speaker in `probes` from one recording.

    `mic` must start before the probes do (the window arithmetic of `VentanaDeEmision`).
    """
    if not probes:
        return ProbeMeasurement({}, {}, frozenset())
    length = max(len(r) for r in probes.values())
    n = 1 << int(np.ceil(np.log2(len(mic) + length)))
    f = np.fft.rfftfreq(n, 1 / sr)
    band = (f >= band_hz[0]) & (f <= band_hz[1])
    spectrum = np.fft.rfft(mic, n)
    limit = min(int(sr * max_lag_ms / 1000), n - 1)

    def arrival(ref_spectrum: np.ndarray) -> float:
        cross = np.where(band, spectrum * np.conj(ref_spectrum), 0)
        magnitude = np.abs(cross)
        cross = np.divide(cross, magnitude, out=np.zeros_like(cross), where=magnitude > 1e-20)  # noqa: PLR2004
        return _peak(np.fft.irfft(cross, n), limit) / sr * 1000

    arrivals, halves, reasons = {}, {}, {}
    for name, ref in probes.items():
        half = len(ref) // 2
        first, second = np.zeros(len(ref)), np.zeros(len(ref))
        first[:half], second[half:] = ref[:half], ref[half:]
        if min(float(np.sqrt(np.mean(first[:half] ** 2))), float(np.sqrt(np.mean(second[half:] ** 2)))) < SILENT_RMS:
            reasons[name] = "no probe in the window (silence)"
            continue
        r1, r2 = np.fft.rfft(first, n), np.fft.rfft(second, n)
        arrivals[name] = arrival(r1 + r2)
        halves[name] = (arrival(r1), arrival(r2))
        a1, a2 = halves[name]
        if abs(a1 - a2) > agreement_ms or abs(arrivals[name] - 0.5 * (a1 + a2)) > agreement_ms:
            reasons[name] = f"the two halves disagree: {a1:.2f} and {a2:.2f} ms"
    # The consensus is among the speakers whose two halves agreed: one the microphone does not
    # hear gives a peak anywhere in the lags searched, and with most speakers unheard (a phone
    # near two of eight) those peaks set the median and threw out the speakers heard
    # (`tests/test_probe_measure.py`, 2026-10-03).
    agreed = [a for name, a in arrivals.items() if name not in reasons]
    if agreed:
        median = float(np.median(agreed))
        for name, a in arrivals.items():
            if name not in reasons and abs(a - median) > CONSENSUS_MS:
                reasons[name] = f"{a - median:+.0f} ms away from the others"
    valid = frozenset(n for n in arrivals if n not in reasons)
    return ProbeMeasurement(arrivals, halves, valid, reasons)
