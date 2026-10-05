"""A render made elsewhere (experimentos/17 §1.1: FFmpeg, the stems) through the motor: each speaker's
channel goes in as it is, past the upmix, and gets only what belongs to the speaker (its delay, EQ,
gain, the volume and the limiter). SIMULADO."""

import numpy as np

from aurasync.config import Instalacion, Parlante
from aurasync.motor import Motor

SR = 48000
BLOCK = 4096


def _inst(delays=(0.0, 0.0)):
    return Instalacion(
        parlantes=[
            Parlante(f"s{i}", f"sink{i}", pan=p, ambiente=0.2, retardo_ms=d)
            for i, (p, d) in enumerate(zip((-0.7, 0.7), delays, strict=False))
        ]
    )


def _run(m, channels):
    out = {p.nombre: [] for p in m.instalacion.parlantes}
    silence = np.zeros(BLOCK)
    for i in range(0, len(next(iter(channels.values()))), BLOCK):
        block = {n: x[i : i + BLOCK] for n, x in channels.items()}
        for n, y in m.procesar(silence, silence, canales=block).items():
            out[n].append(y)
    return {n: np.concatenate(v) for n, v in out.items()}


def test_each_channel_reaches_only_its_speaker():
    rng = np.random.default_rng(1)
    a, b = rng.standard_normal((2, 6 * BLOCK)) * 0.05
    out = _run(Motor(_inst(), SR, semilla=1), {"s0": a, "s1": np.zeros_like(b)})
    tail = slice(2 * BLOCK, None)
    assert np.sum(out["s0"][tail] ** 2) > 0
    assert np.max(np.abs(out["s1"][tail])) < 1e-6  # nothing of s0 leaks: no upmix, no decorrelator


def test_the_speaker_s_delay_still_applies():
    impulse = np.zeros(6 * BLOCK)
    impulse[BLOCK] = 0.5
    plain = _run(Motor(_inst((0.0, 0.0)), SR, semilla=1), {"s0": impulse, "s1": np.zeros_like(impulse)})["s0"]
    later = _run(Motor(_inst((10.0, 0.0)), SR, semilla=1), {"s0": impulse, "s1": np.zeros_like(impulse)})["s0"]
    shift = int(np.argmax(np.abs(later))) - int(np.argmax(np.abs(plain)))
    assert abs(shift - round(10.0 * SR / 1000)) <= 1


def test_a_missing_channel_is_silence():
    out = _run(Motor(_inst(), SR, semilla=1), {"s0": np.full(3 * BLOCK, 0.1)})
    assert len(out["s1"]) == 3 * BLOCK
    assert np.max(np.abs(out["s1"])) < 1e-6
