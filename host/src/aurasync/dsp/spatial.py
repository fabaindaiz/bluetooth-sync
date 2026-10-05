"""The spatial mode: each bin's direct part to the principal speakers at its angle, its ambience
to the ambient ones (spec 2026-10-04 §3, d-7c8794-be2477).

Why: in the classic mode every speaker gets a broadband mix of L and R, so each instrument comes
out of every speaker at once, and the per-speaker decorrelator smears the direct sound as well.
Plain stereo can then sound as good or better (the listener, 2026-10-03). Here an instrument
comes out of the speakers at its own angle, and only the ambience is spread and decorrelated.

Per frame of a streaming STFT (as `ambience.Extractor`: 2048 points, hop 512, root-Hann, latency
2048 samples, so no new latency) and per bin:

- smoothed powers `P11`, `P22` and cross-power `P12` (forgetting factor `lam`);
- **ambience index**: `1 - |P12| / sqrt(P11 P22)`, zero where the two channels' energies are not
  comparable (a source panned to one side is not ambience), shaped by `ambience.mapeo` and scaled
  by the `ambience` knob → mask `m`;
- **panning index** `Ψ = (P22 - P11) / (P11 + P22)` in [-1, 1] (-1 left, +1 right): a
  level-difference index, monotonic in an amplitude pan, on the smoothed powers so it does not
  flicker;
- **direct** `(1 - m) |X| e^{jφ}`, with `|X|² = |L|² + |R|²` and the phase of `L + R` (of the
  louder channel where `L + R` nearly cancels), to the angle `Ψ · arc_deg`, with constant-power
  gains between the two principals around that angle on the ring;
- **ambience** `m L` and `m R` to the ambient speakers, alternating, so two of them start from
  different signals (the motor's decorrelator makes them more so); without ambient speakers, to
  the principals; without principals, the direct part goes to the ambients too;
- each bin is scaled so that the output keeps the input's energy: `ambient_level_db` is then the
  balance between ambience and direct sound, not a volume knob, and the A/B compares space, not
  loudness.

The ambience is then delayed by `haas_ms` (research/09 §3: at 10-25 ms a speaker can be up to 10
dB louder without taking the localisation). Fixed-length work per block (d-7c8794-589dec).
"""

from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np

from aurasync.dsp import ambience

N_FFT = 2048
HOP = N_FFT // 4
MAX_HAAS_MS = 30.0
FADE_IN = 4096
"""Samples of a new renderer's entry ramp (85 ms, about the cut's own fade)."""
_FLOOR = 1e-8
_SILENT = 1e-30
"""A bin with less energy than this is silence: it gets no scale (and no division by zero)."""


@dataclass(frozen=True)
class SpatialParams:
    arc_deg: float = 105.0
    """How far the stereo stage opens on the ring: a hard-left source goes to -arc_deg."""
    ambience: float = 0.5
    """How much of what looks like ambience is taken out of the direct sound (0 to 1)."""
    ambient_level_db: float = 3.0
    """The ambience against the direct sound."""
    haas_ms: float = 14.0
    """Delay of the ambience."""
    threshold: float = 0.5
    """Where the ambience curve (`ambience.mapeo`) turns: a bin counts as ambience above this
    ambience index. The extractor's 0.5 lets little of typical music through (coherence ~0.75 is
    an index of 0.25), so `character` lowers it for envelopment (review of the figures, 2026-10-04)."""
    lam: float = 0.9
    front_intact: bool = False
    """"Frente intacto" (research/14 §4, Logic7's principle): the front pair plays the stereo
    untouched and every other speaker only the ambience, boosted. Never worse than the stereo
    alone in front, and the room around it."""


HALF_TURN = 180.0
SPATIAL_RENDERS = frozenset({"spatial", "front"})
"""The `spatial` stage's algorithms that this renderer plays (the other is `classic`)."""

FRONT_AMBIENCE_BOOST_DB = 6.0
"""In "frente intacto" the ambience is added on top of the full stereo, not taken out of it, and a
studio mix holds 10-15 dB less ambience than direct sound (research/14 §0): this lifts it so the
rear is heard. INFERIDO, to be set by the A/B of experimentos/17."""


def front_pair(angles: dict[str, float]) -> tuple[str, str] | None:
    """The principal nearest the front on each side (angle in degrees, positive to the right), or
    None when a side has none."""
    left = [n for n, a in angles.items() if -HALF_TURN < a < 0.0]
    right = [n for n, a in angles.items() if 0.0 < a < HALF_TURN]
    if not left or not right:
        return None
    return max(left, key=lambda n: angles[n]), min(right, key=lambda n: angles[n])


def from_character(c: float) -> SpatialParams:
    """The `character` macro: 0 is localisation (a wide arc, little ambience), 1 envelopment."""
    c = min(max(c, 0.0), 1.0)
    return SpatialParams(
        arc_deg=round(150.0 - 90.0 * c, 3),
        ambience=round(0.2 + 0.6 * c, 3),
        ambient_level_db=round(6.0 * c, 3),
        haas_ms=round(8.0 + 12.0 * c, 3),
        threshold=round(0.6 - 0.45 * c, 3),
    )


def _window(n: int) -> np.ndarray:
    return np.sqrt(np.hanning(n + 1)[:n])


class SpatialUpmix:
    """Streaming: `process` takes a stereo block and gives, per speaker, (direct, ambience)."""

    def __init__(
        self,
        names: list[str],
        angles: dict[str, float],
        ambient: set[str],
        sr: int = 48000,
        params: SpatialParams | None = None,
        n_fft: int = N_FFT,
        hop: int = HOP,
        classic: dict[str, tuple[float, float]] | None = None,
    ) -> None:
        self.names = list(names)
        self.sr = sr
        self.n_fft, self.hop = n_fft, hop
        self.latency = n_fft
        self.set_params(params or SpatialParams())
        self._w = _window(n_fft)
        bins = n_fft // 2 + 1
        self._acc12 = np.zeros(bins, dtype=complex)
        self._acc11 = np.zeros(bins)
        self._acc22 = np.zeros(bins)
        self._pending_l = np.zeros(0)
        self._pending_r = np.zeros(0)
        self._haas_len = round(MAX_HAAS_MS * sr / 1000)
        self.set_layout(angles, ambient, classic)
        self._reset_state()

    # -- configuration -------------------------------------------------------------------

    def set_params(self, params: SpatialParams) -> None:
        self.params = replace(params, haas_ms=min(max(params.haas_ms, 0.0), MAX_HAAS_MS))
        self._curve = ambience.Parametros(umbral=self.params.threshold)
        """The ambience curve, built once per change, not per frame."""

    def set_layout(
        self,
        angles: dict[str, float],
        ambient: set[str],
        classic: dict[str, tuple[float, float]] | None = None,
    ) -> None:
        """Which speakers are principal (with their angle) and which ambient. **Live**: only the ring
        changes; the overlap-add crossfades the new gains frame by frame. Resetting the buffers here
        clicked and added up to 2047 samples of latency (review 2026-10-04).

        `classic`: each speaker's (pan, ambience). The output is then scaled, bin by bin, to the
        energy the classic mix of these speakers would have, so switching the render does not change
        the loudness (spec §6); without it, to the input's energy."""
        self.ambient = [n for n in self.names if n in ambient]
        principal = [n for n in self.names if n not in ambient and n in angles]
        principal.sort(key=lambda n: angles[n])
        self.principal = principal
        self.front = front_pair({n: angles[n] for n in principal})
        self._ring = np.array([angles[n] for n in principal], dtype=float)
        self._back = self._back_gap()
        self._classic = None
        if classic:
            cl = np.array([(1 - pan) / 2 * (1 - a) for pan, a in classic.values()])
            cr = np.array([(1 + pan) / 2 * (1 - a) for pan, a in classic.values()])
            amb = np.array([a for _, a in classic.values()])
            self._classic = (float(cl @ cl), float(cr @ cr), float(cl @ cr), float(amb @ amb))

    def _reset_state(self) -> None:
        n = self.n_fft
        self._ola_direct = {nm: np.zeros(n) for nm in self.names}
        self._ola_amb = {nm: np.zeros(n) for nm in self.names}
        self._norm = np.zeros(n)
        self._ready_direct = {nm: np.zeros(self.latency) for nm in self.names}
        self._ready_amb = {nm: np.zeros(self.latency) for nm in self.names}
        self._haas = {nm: np.zeros(self._haas_len) for nm in self.names}
        self._haas_read = dict.fromkeys(self.names, round(self.params.haas_ms * self.sr / 1000))
        """Each line's current read point: a change crossfades from it."""
        self._emitted = 0

    # -- the work ---------------------------------------------------------------------------

    def _gains(self, theta: np.ndarray) -> np.ndarray:
        """Constant-power gains (principals x bins) for the angles `theta` (degrees) on the ring."""
        k = len(self._ring)
        g = np.zeros((k, len(theta)))
        if k == 1:
            g[0] = 1.0
            return g
        ext = np.append(self._ring, self._ring[0] + 360.0)
        theta = self._close_the_back(theta)
        t = np.where(theta < self._ring[0], theta + 360.0, theta)
        i = np.clip(np.searchsorted(ext, t, side="right") - 1, 0, k - 1)
        lo, hi = ext[i], ext[i + 1]
        frac = np.clip((t - lo) / np.maximum(hi - lo, 1e-9), 0.0, 1.0)
        cols = np.arange(len(theta))
        g[i % k, cols] += np.cos(frac * np.pi / 2)
        g[(i + 1) % k, cols] += np.sin(frac * np.pi / 2)
        return g

    def _back_gap(self) -> tuple[float, float] | None:
        """The gap between principals that holds 180°, if it is 180° or more (nobody behind, as two
        principals at ±90°). Computed when the layout changes, not per frame."""
        k = len(self._ring)
        if k < 2:  # noqa: PLR2004
            return None
        ext = np.append(self._ring, self._ring[0] + 360.0)
        for i in range(k):
            lo, hi = float(ext[i]), float(ext[i + 1])
            if lo <= 180.0 < hi and hi - lo >= 180.0 - 1e-9:  # noqa: PLR2004
                return lo, hi
        return None

    def _close_the_back(self, theta: np.ndarray) -> np.ndarray:
        """An angle in the back gap goes to the nearer edge principal instead of being panned
        "around the back" into the speaker on the other side."""
        if self._back is None:
            return theta
        lo, hi = self._back
        t = np.where(theta < lo, theta + 360.0, theta)
        inside = (t > lo) & (t < hi)
        snapped = np.where((t - lo) <= (hi - t), lo, hi)
        snapped = np.where(snapped > 180.0, snapped - 360.0, snapped)  # noqa: PLR2004
        return np.where(inside, snapped, theta)

    def _frame(self, fl: np.ndarray, fr: np.ndarray) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray]]:
        p = self.params
        lam = p.lam
        # Powers as re² + im²: `abs` would take a square root only to square it again.
        el = fl.real * fl.real + fl.imag * fl.imag
        er = fr.real * fr.real + fr.imag * fr.imag
        self._acc12 = lam * self._acc12 + (1 - lam) * fl * np.conj(fr)
        self._acc11 = lam * self._acc11 + (1 - lam) * el
        self._acc22 = lam * self._acc22 + (1 - lam) * er
        p11, p22 = self._acc11, self._acc22

        coherence = np.abs(self._acc12) / np.sqrt(p11 * p22 + 1e-20)
        index = np.clip(1.0 - coherence, 0.0, 1.0)
        weak, strong = np.minimum(p11, p22), np.maximum(p11, p22) + 1e-20
        index[weak / strong < ambience.Parametros().energia_minima] = 0.0
        mask = p.ambience * ambience.mapeo(index, self._curve)
        if p.front_intact and self.front is not None:
            return self._front_frame(fl, fr, mask)

        psi = (p22 - p11) / (p11 + p22 + 1e-20)
        energy = el + er
        total = fl + fr
        et = total.real * total.real + total.imag * total.imag
        # The phase of L + R, or of the louder channel where L + R nearly cancels (|L+R|² < 1 % of |X|²).
        phase_src = np.where(et > 0.01 * energy, total, np.where(el >= er, fl, fr))
        ep = phase_src.real * phase_src.real + phase_src.imag * phase_src.imag
        direct = (1.0 - mask) * np.sqrt(energy / np.maximum(ep, 1e-40)) * phase_src

        level = 10 ** (p.ambient_level_db / 20)
        amb_l, amb_r = level * mask * fl, level * mask * fr
        # Keep each bin's energy: the level is a balance, not a volume (module doc).
        m2 = (level * mask) ** 2
        out_energy = (1.0 - mask) ** 2 * energy + m2 * energy
        target = energy
        if self._classic is not None:
            # What the classic mix of these speakers would put out in this bin: their direct parts
            # (pan and 1 - ambience) plus the ambience, so the render switch keeps the loudness.
            a, b, c, d = self._classic
            cross = (fl * np.conj(fr)).real
            target = a * el + b * er + 2 * c * cross + d * (mask * mask) * energy / 2
            target = np.maximum(target, 0.0)
        scale = np.sqrt(target / np.maximum(out_energy, 1e-30))
        scale[energy < _SILENT] = 0.0
        direct, amb_l, amb_r = direct * scale, amb_l * scale, amb_r * scale

        directs: dict[str, np.ndarray] = {}
        ambs: dict[str, np.ndarray] = {}
        targets_direct = self.principal or self.ambient
        if self.principal:
            gains = self._gains(psi * p.arc_deg)
            for row, name in enumerate(self.principal):
                directs[name] = gains[row] * direct
        elif targets_direct:
            share = direct / np.sqrt(len(targets_direct))
            for name in targets_direct:
                directs[name] = share
        receivers = self.ambient or self.principal
        if receivers:
            if len(receivers) == 1:
                ambs[receivers[0]] = (amb_l + amb_r) / np.sqrt(2)
            else:
                lefts, rights = receivers[0::2], receivers[1::2]
                for name in lefts:
                    ambs[name] = amb_l / np.sqrt(len(lefts))
                for name in rights:
                    ambs[name] = amb_r / np.sqrt(len(rights))
        return directs, ambs

    def _front_frame(
        self, fl: np.ndarray, fr: np.ndarray, mask: np.ndarray
    ) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray]]:
        """ "Frente intacto": L and R as they come to the front pair (the overlap-add gives them back
        exactly), and to everyone else only the ambience, on top: nothing is taken from the front."""
        front_left, front_right = self.front or ("", "")
        level = 10 ** ((self.params.ambient_level_db + FRONT_AMBIENCE_BOOST_DB) / 20)
        amb_l, amb_r = level * mask * fl, level * mask * fr
        ambs: dict[str, np.ndarray] = {}
        receivers = [n for n in self.names if n not in {front_left, front_right}]
        if len(receivers) == 1:
            ambs[receivers[0]] = (amb_l + amb_r) / np.sqrt(2)
        elif receivers:
            lefts, rights = receivers[0::2], receivers[1::2]
            for name in lefts:
                ambs[name] = amb_l / np.sqrt(len(lefts))
            for name in rights:
                ambs[name] = amb_r / np.sqrt(len(rights))
        return {front_left: fl, front_right: fr}, ambs

    def process(self, left: np.ndarray, right: np.ndarray) -> dict[str, tuple[np.ndarray, np.ndarray]]:
        if len(left) != len(right):
            msg = f"the channels have different lengths: {len(left)} and {len(right)}"
            raise ValueError(msg)
        n = len(left)
        self._pending_l = np.concatenate([self._pending_l, left])
        self._pending_r = np.concatenate([self._pending_r, right])
        w, size, hop = self._w, self.n_fft, self.hop
        new_direct = {nm: [] for nm in self.names}
        new_amb = {nm: [] for nm in self.names}
        while len(self._pending_l) >= size:
            fl = np.fft.rfft(self._pending_l[:size] * w)
            fr = np.fft.rfft(self._pending_r[:size] * w)
            directs, ambs = self._frame(fl, fr)
            self._norm += w**2
            norm = self._norm[:hop]
            inverse = np.where(norm > _FLOOR, 1.0 / np.maximum(norm, _FLOOR), 0.0)
            for name in self.names:
                for ola, spectra, out in ((self._ola_direct, directs, new_direct), (self._ola_amb, ambs, new_amb)):
                    buf = ola[name]
                    if name in spectra:
                        buf += np.fft.irfft(spectra[name], n=size) * w
                    out[name].append(buf[:hop] * inverse)
                    # Shifted in place: no new array per hop and per speaker (fixed-length work).
                    buf[:-hop] = buf[hop:]
                    buf[-hop:] = 0.0
            self._norm[:-hop] = self._norm[hop:]
            self._norm[-hop:] = 0.0
            self._pending_l = self._pending_l[hop:]
            self._pending_r = self._pending_r[hop:]

        result = {}
        fade = self._fade_in(n)
        for name in self.names:
            ready_d = np.concatenate([self._ready_direct[name], *new_direct[name]])
            ready_a = np.concatenate([self._ready_amb[name], *new_amb[name]])
            if len(ready_d) < n:
                ready_d = np.concatenate([ready_d, np.zeros(n - len(ready_d))])
                ready_a = np.concatenate([ready_a, np.zeros(n - len(ready_a))])
            direct, self._ready_direct[name] = ready_d[:n], ready_d[n:]
            amb, self._ready_amb[name] = ready_a[:n], ready_a[n:]
            result[name] = (direct * fade, self._delay(name, amb * fade))
        self._emitted += n
        return result

    def _fade_in(self, n: int) -> np.ndarray | float:
        """A raised-cosine entry over `FADE_IN` samples once the first real output arrives (after
        `latency`): a new renderer, switched in at the bottom of a cut, started abruptly while the
        cut was already fading back up (review 2026-10-04: 490x the steady second difference)."""
        start = self._emitted - self.latency
        if start >= FADE_IN:
            return 1.0
        k = np.arange(start, start + n, dtype=float)
        return np.where(k < 0, 0.0, 0.5 - 0.5 * np.cos(np.pi * np.clip(k / FADE_IN, 0.0, 1.0)))

    def _delay(self, name: str, amb: np.ndarray) -> np.ndarray:
        """The Haas delay: a fixed line of `MAX_HAAS_MS`. A change of delay crossfades, over the
        block, between the old and the new read point (a jump of the read point clicked)."""
        n = len(amb)
        line = np.concatenate([self._haas[name], amb])
        new = round(self.params.haas_ms * self.sr / 1000)
        old = self._haas_read[name]
        start = self._haas_len - new
        delayed = line[start : start + n]
        if old != new and n:
            before = line[self._haas_len - old : self._haas_len - old + n]
            ramp = np.linspace(0.0, 1.0, n)
            delayed = (1 - ramp) * before + ramp * delayed
        self._haas[name] = line[-self._haas_len :]
        self._haas_read[name] = new
        return delayed
