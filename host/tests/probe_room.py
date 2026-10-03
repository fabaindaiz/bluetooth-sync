"""Synthetic music and a room for the masked probe's tests (SIMULATED, as `probes/13`).

- `music`: stereo chords with 8 harmonics, bass, kick and hi-hat at 120 bpm and a short
  decorrelated reverb per channel: non-stationary, with music's spectrum, and **correlated
  between the speakers** once panned (the case the loop could not measure, experimentos/11);
- `room`: each speaker's feed rolled off like a Go 4 at the microphone (-10 dB near 10 kHz),
  delayed by a fractional delay, scaled, with a 0.4 s reverberant tail of its own, plus
  microphone noise at -60 dB.
"""

from __future__ import annotations

import numpy as np

SR = 48000


def fftconv(x: np.ndarray, h: np.ndarray) -> np.ndarray:
    k = 1 << int(np.ceil(np.log2(len(x) + len(h))))
    return np.fft.irfft(np.fft.rfft(x, k) * np.fft.rfft(h, k), k)[: len(x)]


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
        f = 440 * 2 ** ((root - 24 - 69) / 12)
        bass = np.sin(2 * np.pi * f * seg) * np.exp(-seg * 3)
        kick = np.sin(2 * np.pi * (50 + 80 * np.exp(-seg * 30)) * seg) * np.exp(-seg * 12)
        left[a:b] += 0.15 * bass + 0.25 * kick
        right[a:b] += 0.15 * bass + 0.25 * kick
        hat_len = min(b - a, int(0.04 * SR))
        hat = np.diff(rng.standard_normal(hat_len) * np.exp(-np.arange(hat_len) / (0.008 * SR)), prepend=0)
        pos = a + int(beat / 2 * SR)
        if pos + hat_len < n:
            left[pos : pos + hat_len] += 0.08 * hat
            right[pos : pos + hat_len] += 0.06 * hat
    tails = [rng.standard_normal(int(0.6 * SR)) * np.exp(-np.arange(int(0.6 * SR)) / (0.12 * SR)) * 0.02 for _ in "LR"]
    left = left + fftconv(left, tails[0])
    right = right + fftconv(right, tails[1])
    peak = max(np.abs(left).max(), np.abs(right).max())
    return 0.5 * left / peak, 0.5 * right / peak


def room(feeds: list[np.ndarray], delays_ms: list[float], gains: list[float], rng: np.random.Generator) -> np.ndarray:
    n = len(feeds[0])
    k = 1 << int(np.ceil(np.log2(n + SR)))
    f = np.fft.rfftfreq(k, 1 / SR)
    rolloff = 1 / np.sqrt(1 + (f / 9000) ** 4)
    mic = np.zeros(n)
    for x, d, g in zip(feeds, delays_ms, gains, strict=True):
        y = np.fft.irfft(np.fft.rfft(x, k) * rolloff * np.exp(-2j * np.pi * f * d / 1000), k)[:n] * g
        ir = rng.standard_normal(int(0.4 * SR)) * np.exp(-np.arange(int(0.4 * SR)) / (0.06 * SR)) * 0.03
        ir[0] = 1.0
        mic += fftconv(y, ir)
    return mic + rng.standard_normal(n) * 10 ** (-60 / 20)
