#!/usr/bin/env python3
"""Costo por bloque de la lectura sinc del retardo: numpy contra Rust (experimentos/20).

Un bloque = una lectura de 4096 muestras por parlante (1, 3 u 8 parlantes), cada uno con su
retardo. «Quieto» es un retardo fijo (pocas fracciones distintas); «en movimiento» es una rampa
de recalibración (muchas fracciones). Mediana y p95 de 500 bloques, tras 50 de calentamiento.

    cd host && hatch run hatch-test.py3.12:python ../probes/20-costo-sinc-rust/costo.py
    (o el python de ese entorno directamente; la extensión tiene que estar compilada)

Desde el 2026-10-09 la lectura de Rust es `aurasync_engine.Reader().read`: la función del módulo,
`aurasync_engine.read`, ya no existe. Las cifras ya registradas en experimentos/20 se midieron con
esa función.
"""

from __future__ import annotations

import os
import platform
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "host" / "src"))

import aurasync_engine  # noqa: E402

from aurasync.dsp import interpolation  # noqa: E402

BLOCK, BLOCKS, WARM = 4096, 500, 50


def positions(n_speakers: int, moving: bool) -> list[np.ndarray]:
    out = []
    for s in range(n_speakers):
        base = np.arange(BLOCK) + 100 + 37.3 * (s + 1) + 0.37
        if moving:
            base = base + np.linspace(0.0, 1.7, BLOCK)
        out.append(base)
    return out


def measure(read, data, pos) -> tuple[float, float]:
    times = []
    for i in range(WARM + BLOCKS):
        t = time.perf_counter()
        for p in pos:
            read(data, p + i)  # el bloque siguiente: otra posición, no la misma memoria
        if i >= WARM:
            times.append((time.perf_counter() - t) * 1e3)
    return float(np.median(times)), float(np.percentile(times, 95))


def main() -> None:
    print("uptime antes:", subprocess.run(["uptime"], capture_output=True, text=True).stdout.strip())
    print(f"load1 antes: {os.getloadavg()[0]:.2f}")
    print(f"python {platform.python_version()}, numpy {np.__version__}, cpus {os.cpu_count()}")
    print(f"bloque {BLOCK} muestras a 48 kHz = {BLOCK / 48:.1f} ms; {BLOCKS} bloques")
    data = np.random.default_rng(1).uniform(-1.0, 1.0, 40_000)
    print(f"{'parlantes':>9} {'retardo':>10} {'numpy med':>10} {'numpy p95':>10} {'rust med':>9} {'rust p95':>9} {'x':>6}")
    for n in (1, 3, 8):
        for moving in (False, True):
            pos = positions(n, moving)
            nm, n95 = measure(interpolation.read_numpy, data, pos)
            rm, r95 = measure(aurasync_engine.Reader().read, data, pos)
            kind = "movimiento" if moving else "quieto"
            print(f"{n:>9} {kind:>10} {nm:>10.3f} {n95:>10.3f} {rm:>9.3f} {r95:>9.3f} {nm / rm:>6.1f}")
    print("uptime despues:", subprocess.run(["uptime"], capture_output=True, text=True).stdout.strip())
    print(f"load1 despues: {os.getloadavg()[0]:.2f}")


if __name__ == "__main__":
    main()
