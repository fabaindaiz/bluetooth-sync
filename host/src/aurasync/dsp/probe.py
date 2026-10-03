"""The masked probe: an independent noise per speaker, shaped under its own music.

Why it exists (spec 2026-10-01 §4, research 03 §3.2): the recalibration loop measured each
speaker by correlating the microphone with the music that speaker got, and that fails when
the speakers carry correlated content — the music-first presets, two speakers with the same
pan (experimentos/08, 09 §5, 11). A noise that is independent of the music, and of every
other speaker's noise, gives the correlation nothing to confuse.

What is built here (step 2 of i-7c8794-e3e40d; step 1 was `probes/13-sonda-enmascarada` and
experimentos/11, the 8-speaker case experimentos/16 §4):

- **band** 300 Hz-8 kHz, where the Go 4 respond (`BAND_HZ`);
- **shaping** per frame (1024 samples, hop 512, sqrt-Hann, as the probe of experimentos/11)
  and per third-octave band: the probe's level in each band is the speaker's own signal in
  that band plus `margin_db` (-20 dB by default, the margin experimentos/11 and 16 validated);
- **release** of `RELEASE_S` (20 dB in 150 ms): the probe follows a decaying note down
  slowly, not frame by frame (spec §4.1: 100-200 ms);
- **floor**: a frame of music below `FLOOR_DBFS` gets no probe (no measurement in silence);
- **independent per speaker and simultaneous**: each speaker has its own seed, and they all
  carry their probe at once. Taking turns does not converge from 6 speakers on
  (experimentos/16 §4.2: the confirmation never agrees when N x window x drift > 0.5 ms),
  and 8 simultaneous probes measure as well as one (§4.1);
- **latency** `LATENCY` samples (21 ms): the probe that goes out at a given instant was
  shaped on the music of 21 ms before. It trails the music, so it never comes before an
  onset (no pre-echo); after an offset it lingers 21 ms plus the release, inside the
  post-masking of the note that just ended (INFERIDO until the blind A/B, step 4);
- **switching** with a 50 ms ramp (`RAMP_MS`). Off and settled, `active` is false and the
  engine does not call this module at all: the output is bit for bit the one without the
  probe (`tests/test_chain_golden.py`, `tests/test_probe.py`).

**The level is calibrated.** With a sqrt-Hann analysis window, a third-octave band of the
music has a mean squared spectrum of (band power) x N/4 per one-sided bin; a unit-magnitude
spectrum of gain G with an independent random phase in every frame, inverse-transformed,
windowed and overlapped at 50 %, has a band power of 2 Σ G² / N². Equal levels need
G² = 2 x mean |X|². SIMULADO on 10 s of pink noise (`tests/test_probe.py`): -20.0 dB per
third without the release, -19.1 to -19.9 dB with it (the release holds the louder frames).
`probes/13` normalised the STFT of one white noise instead, whose overlapping frames add
partly in phase, and measured -20.5 dB for its -20 label: the margins of experimentos/11 and
16 are within 1 dB of the ones this module names.

It is the engine that decides where the probe is added (`motor.py`): at the limiter's input,
after the delay line, the EQ, the gain and the volume, so that it follows the level of what
the speaker plays, goes through the same limiter, and its reference (`last`) is exactly what
was added — the loop correlates the microphone against it (`probe_measure.py`).
"""

from __future__ import annotations

import numpy as np

from aurasync.dsp.ramps import Smoothed

SR = 48000
BAND_HZ = (300.0, 8000.0)
FRAME = 1024
HOP = FRAME // 2
LATENCY = FRAME
"""Samples the probe trails the music it was shaped on. One frame covers any block size: a
block that is a multiple of `HOP` would need only `HOP`."""
MARGIN_DB = -20.0
"""The probe's level under the speaker's music, per third-octave band. experimentos/11: at
-20 dB with 4 s windows none of 240 measurements was off by more than 1 ms; experimentos/16
§4.1: with 8 speakers, -20 dB or 4 s windows (-25 dB with 2 s failed in both seeds).
Those experiments' probe was within 1 dB of this one at the same label (module doc)."""
MARGIN_RANGE_DB = (-40.0, -10.0)
FLOOR_DBFS = -50.0
"""Below this RMS of a frame of the speaker's signal there is no probe (spec §4.1)."""
RELEASE_S = 0.15
"""How long the probe takes to fall 20 dB (one margin) when the music falls faster."""
RAMP_MS = 50.0
"""Switching the probe on or off, as a mute (`volume.mute_fade_ms`)."""
SEED_STRIDE = 7919
"""Speaker k's noise uses seed `seed + k * SEED_STRIDE`, as the diffuse tails do."""
THIRDS = 1000 * 2 ** (np.arange(-13, 14) / 3)


def _window() -> np.ndarray:
    """sqrt of the periodic Hann: its square overlaps to exactly 1 at 50 %."""
    return np.sqrt(0.5 - 0.5 * np.cos(2 * np.pi * np.arange(FRAME) / FRAME))


class ProbeShaper:
    """One speaker's probe: give it the speaker's signal block by block, get its probe back.

    Streaming: any block size; the output has exactly as many samples as the input, and
    trails the music it was shaped on by `LATENCY` samples.
    """

    def __init__(self, seed: int, sr: int = SR, margin_db: float = MARGIN_DB) -> None:
        self.sr = sr
        self.margin_db = margin_db
        self._rng = np.random.default_rng(seed)
        self._window = _window()
        f = np.fft.rfftfreq(FRAME, 1 / sr)
        in_band = (f >= BAND_HZ[0]) & (f <= BAND_HZ[1])
        # Each bin in the probe band belongs to exactly one third-octave band (the edges are
        # contiguous); a band is the mean of its bins.
        band_of = np.full(len(f), -1)
        for k, c in enumerate(THIRDS):
            band_of[in_band & (f >= c / 2 ** (1 / 6)) & (f < c * 2 ** (1 / 6))] = k
        used = sorted({int(b) for b in band_of if b >= 0})
        self._bands = np.zeros((len(f), len(used)))
        for j, k in enumerate(used):
            members = band_of == k
            self._bands[members, j] = 1.0 / members.sum()
        self._spread = (self._bands > 0).astype(float).T
        """bands x bins: a band's gain to each of its bins."""
        self._bins = len(f)
        self._decay = 10 ** (-HOP / (RELEASE_S * sr))
        self._floor = 10 ** (FLOOR_DBFS / 20)
        self.reset()

    def reset(self) -> None:
        self._music = np.zeros(0)
        self._tail = np.zeros(FRAME - HOP)
        self._ready = np.zeros(LATENCY)
        self._gain = np.zeros(self._bands.shape[1])

    def process(self, x: np.ndarray) -> np.ndarray:
        buf = np.concatenate([self._music, np.asarray(x, dtype=float)])
        frames = (len(buf) - FRAME) // HOP + 1 if len(buf) >= FRAME else 0
        if frames > 0:
            idx = np.arange(frames)[:, None] * HOP + np.arange(FRAME)
            raw = buf[idx]
            spectrum = np.fft.rfft(raw * self._window, axis=1)
            band_power = (np.abs(spectrum) ** 2) @ self._bands
            target = np.sqrt(2.0 * band_power) * 10 ** (self.margin_db / 20)
            silent = np.sqrt(np.mean(raw * raw, axis=1)) < self._floor
            target[silent] = 0.0
            gains = np.empty_like(target)
            g = self._gain
            for k in range(frames):
                g = np.maximum(target[k], g * self._decay)
                gains[k] = g
            self._gain = g
            phase = np.exp(2j * np.pi * self._rng.random((frames, self._bins)))
            synth = np.fft.irfft((gains @ self._spread) * phase, FRAME, axis=1) * self._window
            out = np.empty(frames * HOP)
            tail = self._tail
            for k in range(frames):
                out[k * HOP : (k + 1) * HOP] = tail + synth[k, :HOP]
                tail = synth[k, HOP:]
            self._tail = tail
            self._ready = np.concatenate([self._ready, out])
            self._music = buf[frames * HOP :]
        else:
            self._music = buf
        n = len(x)
        emitted, self._ready = self._ready[:n], self._ready[n:]
        return emitted


class MaskedProbe:
    """The probes of every speaker, switched on and off together with a 50 ms ramp.

    The engine calls `begin(n)` once per block and `add(name, x, envelope)` per speaker,
    only while `active`. `last` keeps, per speaker, the probe that was added in the last
    block: the loop's reference.
    """

    def __init__(self, names: list[str], sr: int = SR, margin_db: float = MARGIN_DB, seed: int = 0) -> None:
        if not names:
            msg = "no speakers"
            raise ValueError(msg)
        self.sr = sr
        self._seed = seed
        self._shapers = {n: ProbeShaper(seed + k * SEED_STRIDE, sr, margin_db) for k, n in enumerate(names)}
        self._ramp = Smoothed(0.0, 1000.0 / RAMP_MS, sr)
        self._envelope: float | np.ndarray = 0.0
        self._margin_db = margin_db
        self.last: dict[str, np.ndarray] = {}

    @property
    def margin_db(self) -> float:
        return self._margin_db

    @margin_db.setter
    def margin_db(self, value: float) -> None:
        lo, hi = MARGIN_RANGE_DB
        if not lo <= value <= hi:
            msg = f"the probe's margin must be within {lo} and {hi} dB; got {value}"
            raise ValueError(msg)
        self._margin_db = float(value)
        for shaper in self._shapers.values():
            shaper.margin_db = float(value)

    @property
    def enabled(self) -> bool:
        return self._ramp.target > 0

    @enabled.setter
    def enabled(self, on: bool) -> None:
        if on and not self.active:
            for shaper in self._shapers.values():
                shaper.reset()
        self._ramp.target = 1.0 if on else 0.0

    @property
    def active(self) -> bool:
        """Whether the engine has to call this module: on, or still ramping down."""
        return self._ramp.target > 0 or self._ramp.current > 0

    @property
    def full(self) -> bool:
        """On and at full level (the ramp is over)."""
        return self._ramp.target > 0 and self._ramp.settled

    def begin(self, n: int) -> None:
        self._envelope = self._ramp.block(n)
        self.last = {}

    def add(self, name: str, x: np.ndarray, envelope: float | np.ndarray = 1.0) -> np.ndarray:
        """`x` plus its probe, shaped on `x` itself. `envelope` (the engine's cut) multiplies
        the probe once more, so that the bottom of a cut is exact silence."""
        shaper = self._shapers.get(name)
        if shaper is None:  # a speaker added while it plays: a seed of its own
            shaper = ProbeShaper(self._seed + len(self._shapers) * SEED_STRIDE, self.sr, self._margin_db)
            self._shapers[name] = shaper
        probe = shaper.process(x) * self._envelope * envelope
        self.last[name] = probe
        return x + probe
