"""The stages of the chain that came after the engine was written: diffusion, bass, the limiters.

`motor.py` keeps its Spanish and its structure; what the 2026-10-02 design added
(`docs/superpowers/specs/2026-10-02-microcuts-chain-and-quality-design.md` §5) lives here, in
English, and the engine only plugs it in. Each stage is built from the chain's values
(`chain.ChainValues`) and is replaced as a whole at the bottom of a cut when a `cut` knob
changes; `live` knobs are set on the running object.

**Latency is the same on every speaker by construction.** The diffuse tail (partitioned
convolution, first partition in the same block), the crossover and the high-pass (in-phase
LR filters applied as causal FIRs) and the psychoacoustic bass add none; the true-peak
limiter adds its look-ahead, and every speaker gets one.

**Where each stage sits** (the order of `chain.CHAIN`):

- `diffuse`: after the decorrelator. Fed with the speaker's own mix before the decorrelator:
  its share of the extracted ambience plus the rest of its direct (research 11 R6: "the
  extracted ambience plus a fraction of the direct"), so a dry mix still gets a tail and
  `diffuse.level_db` is the tail's energy relative to what the speaker plays, the reference
  `dsp/diffuse.py` was measured against. (Feeding the whole ambience plus the whole direct,
  the first wiring, made the tail of a mostly-ambience speaker 8 dB louder than the knob
  said: MEDIDO in the simulated service, -4 dB of tail at -12 dB.)
- `bass`: `protect` high-passes every speaker of a kind that is not bass-capable after the
  EQ, and adds the harmonics of what it took (`dsp/virtual_bass.py`). `crossover` high-passes
  the same speakers, passes the bass-capable ones through the crossover's all-pass (so their
  own bass stays in phase with what they are fed), and adds the low-passed mid, 0.5 (L + R),
  to the chosen bass speaker **before its delay line**: the feed must reach the room through that speaker's own calibration
  delay and EQ, or it would arrive tens of ms off the high-passed rest (the calibration
  delays differ by 5-40 ms here, experimentos/10 §5.3; at 100 Hz 1 ms is 36 degrees). The
  feed is also delayed by the decorrelator's mean group delay while it is on, as the
  speaker's own signal is.
- `limiter`: the last stage, `PeakLimiter` or `TruePeakLimiter`.
"""

from __future__ import annotations

import functools
from typing import TYPE_CHECKING

import numpy as np

from aurasync import chain as chain_model
from aurasync.dsp import crossover, limiter
from aurasync.dsp.diffuse import Diffuse
from aurasync.dsp.eq import StreamingFIR
from aurasync.dsp.virtual_bass import VirtualBass

if TYPE_CHECKING:
    from aurasync.chain import ChainValues

HARMONICS_OFF_DB = -24.0
"""`bass.harmonics_db` at its minimum means no harmonics at all (`VirtualBass` with None)."""
SEED_STRIDE = 7919
"""Speaker k's tail uses seed `seed + k * SEED_STRIDE`: a different, uncorrelated tail each."""


def _db(energy_ratio: float) -> float | None:
    return round(float(10 * np.log10(energy_ratio)), 2) if energy_ratio > 0 and np.isfinite(energy_ratio) else None


# -- diffuse ------------------------------------------------------------------------------


class DiffuseStage:
    """One `Diffuse` tail per speaker, or nothing when the stage is `off`."""

    def __init__(self, values: ChainValues, names: list[str], sr: int, block: int) -> None:
        self.active = values.algorithm("diffuse") == "noise_tail"
        self._tails: dict[str, Diffuse] = {}
        self._share_db: dict[str, float | None] = {}
        if not self.active:
            return
        p = values.params("diffuse")
        for k, name in enumerate(names):
            self._tails[name] = Diffuse(
                sr,
                seed=int(p["seed"]) + k * SEED_STRIDE,
                level_db=p["level_db"],
                rt60_s=p["rt60_s"],
                predelay_ms=p["predelay_ms"],
                damping_hz=p["damping_hz"],
                block=block,
            )

    def set_level(self, level_db: float) -> None:
        """`diffuse.level_db` is live: each tail ramps to it across the next block."""
        for tail in self._tails.values():
            tail.level_db = level_db

    def process(self, name: str, x: np.ndarray, feed: np.ndarray) -> np.ndarray:
        tail = self._tails.get(name)
        if tail is None:
            return x
        wet = tail.process(feed)
        e_x, e_wet = float(np.dot(x, x)), float(np.dot(wet, wet))
        self._share_db[name] = _db(e_wet / e_x) if e_x > 0 else None
        return x + wet

    def metrics(self) -> dict:
        return {"active": self.active, "tail_db": dict(self._share_db)}

    def memory_samples(self) -> int:
        """How long a fresh stage needs to run before its output is a running one's: the longest
        tail (predelay + rt60). 0 when `off`."""
        return max((len(tail.ir) for tail in self._tails.values()), default=0)


# -- bass ---------------------------------------------------------------------------------


@functools.lru_cache(maxsize=16)
def _lr_taps(sr: int, cutoff_hz: float, order: int) -> tuple[np.ndarray, np.ndarray]:
    """(low-pass, high-pass) FIRs, computed once per cutoff: building one takes ~10 ms."""
    lp, hp = crossover.impulses(sr, cutoff_hz, order)
    lp.flags.writeable = False
    hp.flags.writeable = False
    return lp, hp


class _Branch:
    """One crossover output by FFT overlap-add, with the energy it moved in the last block."""

    def __init__(self, taps: np.ndarray) -> None:
        self._fir = StreamingFIR(taps)
        self.moved_db: float | None = None

    @property
    def memory_samples(self) -> int:
        return len(self._fir.taps) - 1

    def process(self, x: np.ndarray) -> np.ndarray:
        y = self._fir.process(x)
        e_in, e_out = float(np.dot(x, x)), float(np.dot(y, y))
        self.moved_db = _db(e_in / e_out) if e_out > 0 else None
        return y


class BassStage:
    """`off`, `protect` or `crossover` (`chain.py` stage `bass`)."""

    def __init__(self, values: ChainValues, speakers: list[tuple[str, str | None]], sr: int, block: int) -> None:
        self.algorithm = values.algorithm("bass")
        self.to: str | None = None
        self.reason: str | None = None
        self._high: dict[str, _Branch] = {}
        self._allpass: dict[str, StreamingFIR] = {}
        self._harmonics: dict[str, VirtualBass] = {}
        self._feed: _Branch | None = None
        self._delay = 0
        self._history = np.zeros(0)
        self._feed_db: float | None = None
        self._fed: np.ndarray | None = None
        """What the last `feed` returned: `before_delay` adds it by default, so two instances of
        the stage (a crossfade, `dsp/transition.py`) each add their own."""
        if self.algorithm == "off":
            return
        bass = [name for name, kind in speakers if chain_model.bass_capable(kind)]
        small = [name for name, kind in speakers if not chain_model.bass_capable(kind)]
        cutoff = float(values.param("bass", "cutoff_hz"))
        if self.algorithm == "protect":
            order = int(values.param("bass", "order"))
            _, hp = _lr_taps(sr, cutoff, order)
            harmonics = values.param("bass", "harmonics_db")
            for name in small:
                self._high[name] = _Branch(hp)
                self._harmonics[name] = VirtualBass(sr, cutoff, _harmonics(harmonics), block)
            return
        wanted = values.param("bass", "to")
        self.to = (bass[0] if bass else None) if wanted == chain_model.AUTO else (wanted if wanted in bass else None)
        if self.to is None:
            self.reason = "no hay un parlante apto para graves en la instalación"
            return
        lp, hp = _lr_taps(sr, cutoff, 4)
        for name in small:
            self._high[name] = _Branch(hp)
        # The bass speakers keep their whole signal, through the crossover's all-pass (LP + HP):
        # the fed low comes out of the LP, whose phase is the all-pass's, so the speaker's own
        # bass and the fed bass add in phase. Without it they were ~87 degrees apart at 50 Hz
        # and the sum came out 3.6 dB instead of 6 (MEDIDO, tests/test_motor_stages.py).
        for name in bass:
            self._allpass[name] = StreamingFIR(lp + hp)
        self._feed = _Branch(lp)

    @property
    def active(self) -> bool:
        return bool(self._high) or self._feed is not None

    def set_harmonics(self, harmonics_db: float) -> None:
        """`bass.harmonics_db` is live: `VirtualBass` ramps its gain across the next block."""
        for vb in self._harmonics.values():
            vb.harmonics_db = _harmonics(harmonics_db)

    def feed(self, left: np.ndarray, right: np.ndarray, delay: int) -> np.ndarray | None:
        """`crossover`: the low-passed mid for the bass speaker, delayed by `delay` samples
        (the decorrelator's mean group delay while it is on)."""
        if self._feed is None:
            return None
        self._fed = self._delayed(self._feed.process(0.5 * (left + right)), delay)
        return self._fed

    def _delayed(self, low: np.ndarray, delay: int) -> np.ndarray:
        """Meter `low`, then hold it `delay` samples."""
        self._feed_db = None
        e = float(np.dot(low, low))
        if e > 0:
            self._feed_db = round(float(10 * np.log10(e / len(low))), 1)
        if delay != self._delay:
            # Changes only at the bottom of a cut, with the output silent.
            self._delay, self._history = delay, np.zeros(delay)
        if delay == 0:
            return low
        joined = np.concatenate([self._history, low])
        self._history = joined[len(low) :]
        return joined[: len(low)]

    def before_delay(self, name: str, x: np.ndarray, feed: np.ndarray | None = None) -> np.ndarray:
        """`crossover`, on a bass speaker, before its delay line: its own signal through the
        all-pass, plus the fed low if it is the one chosen. (The all-pass goes here and not
        after the EQ: there it would turn the fed low, already through the LP, once more.)
        `feed` None: the low this stage's last `feed` made (what the motor uses)."""
        allpass = self._allpass.get(name)
        if allpass is None:
            return x
        if feed is None:
            feed = self._fed
        y = allpass.process(x)
        return y + feed if feed is not None and name == self.to else y

    def process(self, name: str, x: np.ndarray) -> np.ndarray:
        high = self._high.get(name)
        if high is None:
            return x
        y = high.process(x)
        vb = self._harmonics.get(name)
        if vb is not None:
            y = y + vb.process(x)
        return y

    def memory_samples(self, delay: int = 0) -> int:
        """How long a fresh stage needs to run before its output is a running one's: its longest
        filter (the feed's adds `delay`, the decorrelator's group delay it is held by). 0 when
        `off`."""
        lengths = [b.memory_samples for b in self._high.values()]
        lengths += [vb.memory_samples for vb in self._harmonics.values()]
        lengths += [len(fir.taps) - 1 for fir in self._allpass.values()]
        if self._feed is not None:
            lengths.append(self._feed.memory_samples + delay)
        return max(lengths, default=0)

    def metrics(self) -> dict:
        return {
            "active": self.active,
            "to": self.to,
            "reason": self.reason,
            "removed_db": {n: b.moved_db for n, b in self._high.items()},
            "harmonics_db": {
                n: (round(vb.added_db, 2) if vb.added_db is not None else None) for n, vb in self._harmonics.items()
            },
            "feed_dbfs": self._feed_db,
        }


def _harmonics(value: float) -> float | None:
    return None if value <= HARMONICS_OFF_DB else float(value)


# -- limiter ------------------------------------------------------------------------------


def new_limiter(values: ChainValues, sr: int) -> limiter.PeakLimiter | limiter.TruePeakLimiter:
    """The limiter the chain asks for (`limiter.peak` is the engine's of always)."""
    if values.algorithm("limiter") == "true_peak":
        return limiter.TruePeakLimiter(
            sr,
            ceiling_db=values.param("limiter", "ceiling_db"),
            lookahead_ms=values.param("limiter", "lookahead_ms"),
            release_ms=values.param("limiter", "release_ms"),
        )
    return limiter.PeakLimiter(
        sr,
        ceiling=10 ** (values.param("limiter", "ceiling_db") / 20),
        release_s=values.param("limiter", "release_ms") / 1000,
    )


def limiter_latency(values: ChainValues, sr: int) -> int:
    """Samples the limiter delays every speaker by."""
    if values.algorithm("limiter") == "true_peak":
        return round(values.param("limiter", "lookahead_ms") / 1000 * sr)
    return 0
