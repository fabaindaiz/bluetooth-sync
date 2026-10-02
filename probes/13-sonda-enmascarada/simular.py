"""Step 1 of the masked probe (roadmap i-7c8794-e3e40d, spec 2026-10-01 §4): does a probe
shaped under the music measure each speaker's delay, at what margin and window length?

SIMULATED. Synthetic stereo music (chords, bass, drums, reverb), three speakers fed with
pan mixes of it (so their content is strongly correlated: the case that fails today), a
room (known delays, gains, treble roll-off like the Go 4, reverberation, noise) and one
microphone. For each speaker in turn, an independent noise probe is added to its feed,
shaped per third-octave band at `margin` dB under that speaker's music, 300 Hz-8 kHz.

Compared:
- **probe**: GCC between the microphone and the probe that was sent, PHAT-weighted only
  inside the probe band;
- **music** (what the loop does today): `medicion.calibrar` against the speakers' feeds.

Output: per margin and window, the median and p95 absolute error of the delay and the
share of measurements off by more than 1 ms, over independent trials. Two seeds per
condition must agree (CLAUDE.md: a true delay shows up twice).

    cd host && hatch run python ../probes/13-sonda-enmascarada/simular.py [trials]
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np

from aurasync import medicion, motor
from aurasync.config import Instalacion, Parlante

SR = 48000
THIRDS = 1000 * 2 ** (np.arange(-13, 14) / 3)
PROBE_BAND = (300.0, 8000.0)
N_FFT, HOP = 1024, 512
DELAYS_MS = {"Red": 3.13, "Black": 7.61, "Blue": 12.27}
GAINS = {"Red": 1.0, "Black": 0.8, "Blue": 0.6}
PANS = {"Red": -0.7, "Black": 0.7, "Blue": 0.7}
AMBIENCE = {"Red": 0.15, "Black": 0.15, "Blue": 0.55}
"""The quad roles FL, FR and RR: Black and Blue share a pan and differ only by ambience."""


def engine_feeds(left: np.ndarray, right: np.ndarray, *, decorrelate: bool) -> dict[str, np.ndarray]:
    """What the real engine sends each speaker (extractor on; decorrelator as asked)."""
    inst = Instalacion(parlantes=[Parlante(n, f"s{i}", pan=PANS[n], ambiente=AMBIENCE[n]) for i, n in enumerate(PANS)])
    inst.retardo_traseros_ms = 0.0  # the room's delays are the only ones in this simulation
    m = motor.Motor(inst, SR, extraer_ambiente=True, decorrelar=decorrelate, ecualizar=False)
    return motor.procesar_completo(m, left, right, 4096)


def fftconv(x: np.ndarray, h: np.ndarray) -> np.ndarray:
    """`np.convolve(x, h)[:len(x)]` through the FFT (direct convolution took minutes)."""
    k = 1 << int(np.ceil(np.log2(len(x) + len(h))))
    return np.fft.irfft(np.fft.rfft(x, k) * np.fft.rfft(h, k), k)[: len(x)]


# -- synthetic music -------------------------------------------------------------------

def music(seconds: float, rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
    n = int(seconds * SR)
    t = np.arange(n) / SR
    left, right = np.zeros(n), np.zeros(n)
    beat = 0.5
    roots = [57, 60, 64, 62, 55, 59, 62, 65]
    for k in range(int(seconds / beat)):
        a, b = int(k * beat * SR), min(n, int((k + 2) * beat * SR))
        seg = t[a:b] - t[a]
        env = np.exp(-seg * 1.5) * (1 - np.exp(-seg * 60))
        root = roots[(k // 2) % len(roots)]
        for interval, side in ((0, 0.0), (4, -0.5), (7, 0.5), (12, 0.2)):
            f = 440 * 2 ** ((root + interval - 69) / 12)
            tone = sum(np.sin(2 * np.pi * f * h * seg + rng.uniform(0, 6.28)) / h for h in range(1, 9))
            left[a:b] += 0.05 * env * tone * (1 - side)
            right[a:b] += 0.05 * env * tone * (1 + side)
        # bass
        f = 440 * 2 ** ((root - 24 - 69) / 12)
        bass = np.sin(2 * np.pi * f * seg) * np.exp(-seg * 3)
        left[a:b] += 0.15 * bass
        right[a:b] += 0.15 * bass
        # kick and hi-hat
        kick = np.sin(2 * np.pi * (50 + 80 * np.exp(-seg * 30)) * seg) * np.exp(-seg * 12)
        left[a:b] += 0.25 * kick
        right[a:b] += 0.25 * kick
        hat_len = min(b - a, int(0.04 * SR))
        hat = rng.standard_normal(hat_len) * np.exp(-np.arange(hat_len) / (0.008 * SR))
        hat = np.diff(hat, prepend=0)
        pos = a + int(beat / 2 * SR)
        if pos + hat_len < n:
            left[pos : pos + hat_len] += 0.08 * hat
            right[pos : pos + hat_len] += 0.06 * hat
    # a little stereo reverb (decorrelated tails)
    for x in (left, right):
        ir = rng.standard_normal(int(0.6 * SR)) * np.exp(-np.arange(int(0.6 * SR)) / (0.12 * SR)) * 0.02
        x += fftconv(x, ir)
    peak = max(np.abs(left).max(), np.abs(right).max())
    return 0.5 * left / peak, 0.5 * right / peak


# -- the probe -------------------------------------------------------------------------

def stft(x: np.ndarray) -> np.ndarray:
    w = np.sqrt(np.hanning(N_FFT + 1)[:-1])
    frames = [np.fft.rfft(x[i : i + N_FFT] * w) for i in range(0, len(x) - N_FFT, HOP)]
    return np.array(frames)


def istft(spec: np.ndarray, n: int) -> np.ndarray:
    w = np.sqrt(np.hanning(N_FFT + 1)[:-1])
    out = np.zeros(n)
    for k, frame in enumerate(spec):
        i = k * HOP
        out[i : i + N_FFT] += np.fft.irfft(frame, N_FFT) * w
    return out


def shaped_probe(feed: np.ndarray, margin_db: float, rng: np.random.Generator) -> np.ndarray:
    """Noise whose third-octave levels follow `feed` at `margin_db` under it, in the probe band."""
    f = np.fft.rfftfreq(N_FFT, 1 / SR)
    music_spec = stft(feed)
    noise_spec = stft(rng.standard_normal(len(feed)))
    noise_spec /= np.abs(noise_spec) + 1e-12
    gain = np.zeros_like(music_spec, dtype=float)
    for c in THIRDS:
        band = (f >= c / 2 ** (1 / 6)) & (f < c * 2 ** (1 / 6)) & (f >= PROBE_BAND[0]) & (f <= PROBE_BAND[1])
        if not band.any():
            continue
        rms = np.sqrt(np.mean(np.abs(music_spec[:, band]) ** 2, axis=1, keepdims=True))
        gain[:, band] = rms * 10 ** (-margin_db / 20)
    silent = np.sqrt(np.mean(np.abs(music_spec) ** 2, axis=1)) < 10 ** (-50 / 20) * np.sqrt(N_FFT / 2)
    gain[silent] = 0
    return istft(noise_spec * gain, len(feed))


# -- the room --------------------------------------------------------------------------

def frac_delay(x: np.ndarray, ms: float) -> np.ndarray:
    n = len(x)
    k = 1 << int(np.ceil(np.log2(n + SR)))
    f = np.fft.rfftfreq(k, 1 / SR)
    return np.fft.irfft(np.fft.rfft(x, k) * np.exp(-2j * np.pi * f * ms / 1000), k)[:n]


def room(feeds: dict[str, np.ndarray], rng: np.random.Generator) -> np.ndarray:
    n = len(next(iter(feeds.values())))
    mic = np.zeros(n)
    k = 1 << int(np.ceil(np.log2(n)))
    f = np.fft.rfftfreq(k, 1 / SR)
    rolloff = 1 / np.sqrt(1 + (f / 9000) ** 4)  # like the Go 4 at the mic: -10 dB near 10 kHz
    for name, x in feeds.items():
        y = np.fft.irfft(np.fft.rfft(x, k) * rolloff, k)[:n]
        y = frac_delay(y, DELAYS_MS[name]) * GAINS[name]
        ir = rng.standard_normal(int(0.4 * SR)) * np.exp(-np.arange(int(0.4 * SR)) / (0.06 * SR)) * 0.03
        ir[0] = 1.0
        mic += fftconv(y, ir)
    return mic + rng.standard_normal(n) * 10 ** (-60 / 20)


# -- estimators ------------------------------------------------------------------------

def gcc_band(mic: np.ndarray, ref: np.ndarray, max_ms: float = 40.0) -> float:
    n = len(mic)
    k = 1 << int(np.ceil(np.log2(2 * n)))
    f = np.fft.rfftfreq(k, 1 / SR)
    g = np.fft.rfft(mic, k) * np.conj(np.fft.rfft(ref, k))
    band = (f >= PROBE_BAND[0]) & (f <= PROBE_BAND[1])
    g = np.where(band, g / (np.abs(g) + 1e-20), 0)
    c = np.fft.irfft(g, k)
    lim = int(max_ms / 1000 * SR)
    window = c[:lim]
    i = int(np.argmax(window))
    if 0 < i < lim - 1:
        a, b, d = window[i - 1], window[i], window[i + 1]
        i = i + 0.5 * (a - d) / (a - 2 * b + d)
    return i / SR * 1000


def run(trials: int) -> dict:
    results = {}
    names = list(DELAYS_MS)
    conditions = [(m, d) for d in (True, False) for m in (15.0, 20.0, 25.0, 30.0, None)]
    for seed_set in (0, 1):
        for window_s in (2.0, 4.0, 8.0):
            for margin, decorrelate in conditions:  # margin None: today's music correlation
                errors = []
                for trial in range(trials):
                    rng = np.random.default_rng(1000 * seed_set + trial)
                    left, right = music(window_s + 0.5, rng)
                    feeds = engine_feeds(left, right, decorrelate=decorrelate)
                    target = names[trial % len(names)]
                    if margin is None:
                        mic = room(feeds, rng)
                        cal = medicion.calibrar(mic, feeds, SR)
                        if cal is None or target not in getattr(cal, "retardos_ms", {}):
                            errors.append(np.inf)
                            continue
                        # calibrar returns the delay to *add*: the last to arrive gets 0
                        got = cal.retardos_ms
                        truth = {n: max(DELAYS_MS.values()) - DELAYS_MS[n] for n in names}
                        errors.append(abs(got[target] - truth[target]))
                    else:
                        probe = shaped_probe(feeds[target], margin, rng)
                        sent = dict(feeds)
                        sent[target] = feeds[target] + probe
                        mic = room(sent, rng)
                        errors.append(abs(gcc_band(mic, probe) - DELAYS_MS[target]))
                e = np.array(errors)
                method = "music" if margin is None else f"-{margin:.0f}dB"
                key = f"seed{seed_set}|{window_s:.0f}s|{'decorr' if decorrelate else 'plain'}|{method}"
                finite = e[np.isfinite(e)]
                results[key] = {
                    "median_ms": round(float(np.median(finite)), 3) if finite.size else None,
                    "p95_ms": round(float(np.percentile(finite, 95)), 3) if finite.size else None,
                    "over_1ms": round(float(np.mean(e > 1.0)), 3),
                    "failed": int(np.sum(~np.isfinite(e))),
                    "n": len(e),
                }
                print(key, results[key], flush=True)
    return results


if __name__ == "__main__":
    trials = int(sys.argv[1]) if len(sys.argv) > 1 else 12
    t0 = time.time()
    out = run(trials)
    dest = Path(__file__).resolve().parents[2] / "docs/research/experimentos/datos/11/sonda-simulada.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps({"trials": trials, "delays_ms": DELAYS_MS, "results": out}, indent=1) + "\n")
    print(f"{time.time() - t0:.0f} s; {dest}")
