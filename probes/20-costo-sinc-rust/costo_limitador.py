#!/usr/bin/env python3
"""Costo por bloque del limitador de pico real (`limiter.TruePeakLimiter`): numpy contra Rust (experimentos/20, §9).

Un bloque = 4096 muestras (85,3 ms a 48 kHz); 1, 3 y 8 parlantes, un limitador por parlante con
los valores por defecto (techo -1 dB, anticipación 3 ms, retención 15 ms, liberación 250 ms); el
tiempo es el de todos juntos. Tres señales (ruido gaussiano), porque el limitador toma caminos
distintos según el nivel:

- **fuerte** (desviación 0,5): pasa del techo todo el tiempo; el camino entero (sobremuestreo,
  mínimo móvil, promedio de coseno elevado, liberación).
- **medio** (0,1): sus picos pasan del cuarto del techo, así que se sobremuestrea, pero nada pide
  reducir: el atajo después de la detección.
- **bajo** (0,02): todo bajo el cuarto del techo; ni se sobremuestrea.

Mediana y p95 de 500 bloques tras 50 de calentamiento. Pensada para el Mac (no lee `/sys`).

    nice -n 19 $(hatch env find hatch-test.py3.12)/bin/python ../probes/20-costo-sinc-rust/costo_limitador.py
"""

from __future__ import annotations

import os
import platform
import subprocess
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "host" / "src"))

from costo_fir import BLOCK, BLOCKS, SR, WARM, cronometrar  # noqa: E402

from aurasync.dsp import backend, limiter  # noqa: E402

NIVELES = {"fuerte": 0.5, "medio": 0.1, "bajo": 0.02}


def senal(n: int, escala: float) -> np.ndarray:
    return escala * np.random.default_rng(7).standard_normal((WARM + BLOCKS) * BLOCK * n).reshape(n, -1)


def caso(motor: str, n: int, x: np.ndarray) -> tuple[float, float, float]:
    """Mediana y p95 (ms) y la fracción activa media del último parlante."""
    backend.reset()
    backend.use(backend.RUST if motor == "rust" else backend.NUMPY)
    lims = [limiter.TruePeakLimiter(SR) for _ in range(n)]
    activa = []

    def paso(i: int) -> None:
        for k, lim in enumerate(lims):
            lim.process(x[k, i * BLOCK : (i + 1) * BLOCK])
        activa.append(lims[-1].active_fraction)

    med, p95 = cronometrar(paso)
    assert all((lim._rust is not None) == (motor == "rust") for lim in lims)  # noqa: SLF001
    assert backend.failure() is None
    return med, p95, float(np.mean(activa))


def sh(*cmd: str) -> str:
    return subprocess.run(cmd, capture_output=True, text=True, check=False).stdout.strip()


def main() -> None:
    print("uptime antes:", sh("uptime"))
    print(f"load1 antes: {os.getloadavg()[0]:.2f}")
    print(f"equipo {platform.node()}, {platform.platform()}, {sh('sysctl', '-n', 'machdep.cpu.brand_string')}")
    print(f"python {platform.python_version()}, numpy {np.__version__}, cpus {os.cpu_count()}")
    print("rustc:", sh("rustc", "--version"))
    print(f"bloque {BLOCK} muestras = {BLOCK / 48:.1f} ms; {BLOCKS} bloques tras {WARM}")
    cab = ("senal", "parlantes", "activa", "np med", "np p95", "rust med", "rust p95", "np/rust")
    print(" ".join(f"{c:>9}" for c in cab))
    for nombre, escala in NIVELES.items():
        x = senal(8, escala)
        for n in (1, 3, 8):
            npy, rs = caso("numpy", n, x), caso("rust", n, x)
            print(
                f"{nombre:>9} {n:>9} {npy[2]:>9.2f} {npy[0]:>9.3f} {npy[1]:>9.3f} {rs[0]:>9.3f} {rs[1]:>9.3f} "
                f"{npy[0] / rs[0]:>9.1f}"
            )
    backend.reset()
    print("uptime despues:", sh("uptime"))
    print(f"load1 despues: {os.getloadavg()[0]:.2f}")


if __name__ == "__main__":
    main()
