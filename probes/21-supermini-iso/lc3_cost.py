"""CPU cost of LC3 for the emitter: 4 channels, 48 kHz, 10 ms frames, 120 B per channel (48_4).

Measures encode and decode time per 10 ms frame with lc3py (the version pinned in host/pyproject.toml),
single thread. Usage: python lc3_cost.py [seconds_of_audio]
"""

from __future__ import annotations

import json
import math
import statistics
import sys
import time

import lc3
import numpy as np

RATE, FRAME_US, CHANNELS, FRAME_BYTES = 48000, 10000, 4, 120
SAMPLES = RATE * FRAME_US // 1_000_000


def main(seconds: float) -> None:
    frames = int(seconds * 1e6 / FRAME_US)
    t = np.arange(frames * SAMPLES) / RATE
    tones = [500.0, 1000.0, 1500.0, 2000.0]
    pcm = np.stack([0.3 * np.sin(2 * math.pi * f * t) for f in tones], axis=1).astype(np.float32)
    encoder = lc3.Encoder(FRAME_US, RATE, num_channels=CHANNELS)
    decoder = lc3.Decoder(FRAME_US, RATE, num_channels=CHANNELS)
    enc, dec = [], []
    for i in range(frames):
        block = pcm[i * SAMPLES : (i + 1) * SAMPLES].reshape(-1)
        start = time.perf_counter()
        data = encoder.encode(block.tolist(), num_bytes=CHANNELS * FRAME_BYTES)
        enc.append(time.perf_counter() - start)
        start = time.perf_counter()
        decoder.decode(data)
        dec.append(time.perf_counter() - start)
    def summary(values: list[float]) -> dict:
        ms = sorted(v * 1000 for v in values)
        return {
            'median_ms': round(statistics.median(ms), 3),
            'p99_ms': round(ms[int(0.99 * len(ms))], 3),
            'max_ms': round(ms[-1], 3),
            'share_of_10ms_median': round(statistics.median(ms) / 10, 4),
        }
    print(json.dumps({'frames': frames, 'encode_4ch': summary(enc), 'decode_4ch': summary(dec)}))


if __name__ == '__main__':
    main(float(sys.argv[1]) if len(sys.argv) > 1 else 30.0)
