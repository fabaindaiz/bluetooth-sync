"""Sound quality, live: loudness in and out, net gain, PSR and true peak (spec 2026-10-02 §6.2).

The digital chain is the reliable place to measure: an exact reference, one clock, no room
(`docs/research/11-…` §1). A `QualityMeter` takes, block by block, what came into the
engine and what each speaker got, and keeps a BS.1770 meter (`dsp/loudness.py`) on the
input (2 channels, G = 1) and one on each output.

**It runs on the engine thread**, where the telemetry is already recorded, because it is
cheap: 0.28 ms per 4096-sample block for the input and 3 outputs (MEDIDO on the Mac,
`probes/18-costo-de-la-cadena/costo.py`; the budget was 0.5 ms). The readings are computed
when `summary()` is called, which the session does at most at `SUMMARY_HZ`.

**What the engine thread holds has a fixed length** (user, 2026-10-03: the stream is always
processed over fixed-length data, the history apart and asynchronous). The meters keep only
their last 3 s; the input's integrated loudness, the one reading that is history, is kept by
`LoudnessHistory` on its own thread, which the engine hands each new step with one queue put.
Until 2026-10-03 `summary()` rebuilt it from every step since the start, on the engine thread,
twice a second: 1.9 ms at 15 min and growing (experimentos/12 §4.1).

Derived numbers:

- `net_gain_lu`: short-term loudness of the **sum of the outputs' powers** (every speaker
  weighted 1: a listener who moves has no azimuth) minus the input's. With the panel at
  0 dB and the chain at its defaults it is about 0 LU (MEDIDO: `tests/test_quality.py`);
  `chain_gain_lu` is the same without the digital volume, which is the listener's choice.
- `psr` (peak to short-term loudness): of each output, and of the input **per channel**, the
  lower of the two, so that a speaker playing one channel compares like with like (a PSR of
  the stereo pair against its summed loudness reads 3 dB low, and the highest peak of two
  channels sits higher than one's: MEDIDO, a centred mix flagged 1.7 dB of "flattening" that
  no stage made). `flattening` is true when an output's PSR is more than `FLATTENING_DB` under
  the input's while both carry music: the chain is squashing it.
- `tp`: the highest 4x-oversampled peak of the last 3 s, dBTP.

Non-finite values (silence: -inf LUFS) are `None` in the summary: JSON has no infinity.
"""

from __future__ import annotations

import math
import queue
import threading
import time
from typing import Any

import numpy as np

from aurasync.dsp.loudness import GatedIntegrator, LoudnessMeter

SUMMARY_HZ = 2.0
FLATTENING_DB = 1.0
QUIET_LUFS = -60.0
"""Below this the PSR of a stream says nothing (silence, a muted speaker)."""


def _value(x: float, digits: int = 1) -> float | None:
    return round(float(x), digits) if x is not None and math.isfinite(x) else None


def _lufs(mean_square: float) -> float:
    return -0.691 + 10 * math.log10(mean_square) if mean_square > 0 else -math.inf


class LoudnessHistory:
    """The integrated loudness of the input, kept on its own thread (`GatedIntegrator`).

    `push` is what the engine thread calls: one `put` of the new steps. The worker sums the
    two channels' steps and publishes `integrated`, a float the engine thread only reads.
    """

    def __init__(self, window: int, step_n: int) -> None:
        self._integrator = GatedIntegrator(window, step_n)
        self.integrated = -math.inf
        self._queue: queue.SimpleQueue[tuple[list[float], list[float]] | None] = queue.SimpleQueue()
        self._pending = 0
        self._lock = threading.Lock()
        self.thread = threading.Thread(target=self._work, name="aurasync-loudness-history", daemon=True)
        self.thread.start()

    def push(self, left: list[float], right: list[float]) -> None:
        if not left:
            return
        with self._lock:
            self._pending += 1
        self._queue.put((left, right))

    def _work(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                return
            for a, b in zip(*item, strict=True):
                self._integrator.push(a + b)
            self.integrated = self._integrator.integrated
            with self._lock:
                self._pending -= 1

    def wait_idle(self, timeout: float = 5.0) -> bool:
        """For tests: until every step pushed has been counted."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            with self._lock:
                if self._pending == 0:
                    return True
            time.sleep(0.005)
        return False

    def close(self) -> None:
        self._queue.put(None)
        self.thread.join(timeout=1.0)


class QualityMeter:
    def __init__(self, sr: int, names: list[str]) -> None:
        self.sr = sr
        self.channels = (LoudnessMeter(sr, 1, history=False), LoudnessMeter(sr, 1, history=False))
        """The input, one meter per channel: its PSR is per channel, and its loudness is the sum
        of the two (G = 1 each), as one stereo meter would give, without a third oversampling."""
        self.outputs: dict[str, LoudnessMeter] = {name: LoudnessMeter(sr, 1, history=False) for name in names}
        ref = self.channels[0]
        self.history = LoudnessHistory(ref._momentary_steps, ref._step_n)  # noqa: SLF001
        self.samples = 0
        """Samples pushed so far: the A/B waits on audio time, not on the wall clock."""
        self.cost_ms = 0.0
        """Time per `push`, smoothed."""

    def push(self, pair: tuple[np.ndarray, np.ndarray] | None, outputs: dict[str, np.ndarray]) -> None:
        """One block: the engine's input (None: nothing came in, counted as silence) and outputs."""
        started = time.perf_counter()
        n = len(next(iter(outputs.values()), np.zeros(0)))
        if n == 0:
            return
        if pair is None:
            pair = (np.zeros(n), np.zeros(n))
        before = self.channels[0].steps_total
        for meter, x in zip(self.channels, pair, strict=True):
            meter.push(x)
        new = self.channels[0].steps_total - before
        self.history.push(*(m.last_steps(new) for m in self.channels))
        for name, x in outputs.items():
            meter = self.outputs.get(name)
            if meter is None:
                meter = self.outputs[name] = LoudnessMeter(self.sr, 1, history=False)
            meter.push(x)
        self.samples += n
        self.cost_ms = 0.9 * self.cost_ms + 0.1 * (time.perf_counter() - started) * 1000

    def _sum(self, steps: int, meters: Any = None) -> float:
        """Loudness of the summed power of `meters` (the outputs) over the last `steps` 100 ms steps."""
        meters = self.outputs.values() if meters is None else meters
        return _lufs(sum(m._mean(steps) for m in meters if m._steps))  # noqa: SLF001

    def _input_integrated(self) -> float:
        """Gated loudness of the stereo input since the start, as `LoudnessHistory` last published it."""
        return self.history.integrated

    def close(self) -> None:
        """Stops the history's thread. Safe to call twice."""
        self.history.close()

    @property
    def outputs_short_term(self) -> float:
        """LUFS of the sum of the outputs over the last 3 s (the A/B's number)."""
        meter = next(iter(self.outputs.values()), None)
        return self._sum(meter._short_steps) if meter is not None else -math.inf  # noqa: SLF001

    @property
    def outputs_momentary(self) -> float:
        meter = next(iter(self.outputs.values()), None)
        return self._sum(meter._momentary_steps) if meter is not None else -math.inf  # noqa: SLF001

    def summary(self, limiter_pct: dict[str, float] | None = None, volume_db: float | None = None) -> dict:
        """The `quality` event of spec §6.3 (and `state.quality`)."""
        limiter_pct = limiter_pct or {}
        ref = self.channels[0]
        in_short = self._sum(ref._short_steps, self.channels)  # noqa: SLF001
        in_peak = max(m.true_peak_short_dbtp for m in self.channels)
        psrs = [m.psr for m in self.channels if math.isfinite(m.psr)]
        in_psr = min(psrs) if psrs else math.nan
        outputs = {}
        flattening = []
        for name, meter in self.outputs.items():
            short = meter.short_term
            psr = meter.psr
            flat = bool(
                math.isfinite(in_short)
                and math.isfinite(short)
                and in_short > QUIET_LUFS
                and short > QUIET_LUFS
                and math.isfinite(psr)
                and math.isfinite(in_psr)
                and psr < in_psr - FLATTENING_DB
            )
            if flat:
                flattening.append(name)
            outputs[name] = {
                "m": _value(meter.momentary),
                "s": _value(short),
                "tp": _value(meter.true_peak_short_dbtp),
                "psr": _value(psr),
                "limiter_pct": limiter_pct.get(name),
                "flattening": flat,
            }
        out_short = self.outputs_short_term
        net = out_short - in_short if math.isfinite(out_short) and math.isfinite(in_short) else math.nan
        peaks = [o["tp"] for o in outputs.values() if o["tp"] is not None]
        pcts = [p for p in limiter_pct.values() if p is not None]
        return {
            "input": {
                "m": _value(self._sum(ref._momentary_steps, self.channels)),  # noqa: SLF001
                "s": _value(in_short),
                "i": _value(self._input_integrated()),
                "tp": _value(in_peak),
                "psr": _value(in_psr),
            },
            "outputs": outputs,
            "sum": {"m": _value(self.outputs_momentary), "s": _value(out_short)},
            "net_gain_lu": _value(net, 2),
            "chain_gain_lu": _value(net - volume_db, 2) if volume_db is not None else None,
            "flattening": bool(flattening),
            "flattening_outputs": flattening,
            "tp_max": max(peaks) if peaks else None,
            "limiter_pct_max": max(pcts) if pcts else None,
            "cost_ms": round(self.cost_ms, 3),
        }
