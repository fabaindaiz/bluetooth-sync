#!/usr/bin/env python3
"""Costo por bloque de la convolución del decorrelador: np.convolve, FFT en numpy y Rust (experimentos/20, §8).

Un bloque = 4096 muestras (85,3 ms a 48 kHz), ruido de amplitud 0,1; 1, 3 y 8 parlantes con los
filtros del banco real del motor (`Motor._filtros`); el tiempo es el de todos juntos. Tres columnas:

- **np.convolve (antes)**: el solapar y sumar directo que tenía `Motor._convolucionar` hasta la
  tarea 8 (copiado acá tal cual).
- **FFT numpy (después)**: `eq.StreamingFIR` con `engine=numpy`, lo que hace hoy el motor.
- **Rust**: `eq.StreamingFIR` con `engine=rust`.

Mediana y p95 de 500 bloques tras 50 de calentamiento. Pensada para el Mac (no lee `/sys`).

    nice -n 19 $(hatch env find hatch-test.py3.12)/bin/python ../probes/20-costo-sinc-rust/costo_decorrelador.py
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

from costo_fir import BLOCK, BLOCKS, SR, WARM, cronometrar, senal  # noqa: E402

from aurasync import motor  # noqa: E402
from aurasync.config import Instalacion, Parlante  # noqa: E402
from aurasync.dsp import backend, eq  # noqa: E402


def instalacion(n: int) -> Instalacion:
    return Instalacion(parlantes=[Parlante(f"P{k}", f"s{k}", pan=-1 + 2 * k / max(n - 1, 1)) for k in range(n)])


def convolucionar_directo(h: np.ndarray, cola: np.ndarray, x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """`Motor._convolucionar` de antes de la tarea 8, con la cola como argumento."""
    completa = np.convolve(x, h)
    salida = completa[: len(x)].copy()
    solape = min(len(cola), len(salida))
    salida[:solape] += cola[:solape]
    nueva_cola = completa[len(x) :]
    if len(cola) > solape:
        resto = cola[solape:]
        largo = max(len(nueva_cola), len(resto))
        acumulada = np.zeros(largo)
        acumulada[: len(nueva_cola)] += nueva_cola
        acumulada[: len(resto)] += resto
        nueva_cola = acumulada
    return salida, nueva_cola


def caso(modo: str, n: int, x: np.ndarray) -> tuple[float, float]:
    backend.reset()
    backend.use(backend.RUST if modo == "rust" else backend.NUMPY)
    filtros = list(motor.Motor(instalacion(n), SR)._filtros.values())  # noqa: SLF001
    if modo == "directo":
        colas = [np.zeros(len(h) - 1) for h in filtros]

        def paso(i: int) -> None:
            for k, h in enumerate(filtros):
                _, colas[k] = convolucionar_directo(h, colas[k], x[k, i * BLOCK : (i + 1) * BLOCK])

        return cronometrar(paso)
    firs = [eq.StreamingFIR(h) for h in filtros]

    def paso(i: int) -> None:
        for k, f in enumerate(firs):
            f.process(x[k, i * BLOCK : (i + 1) * BLOCK])

    medido = cronometrar(paso)
    assert all((f._rust is not None) == (modo == "rust") for f in firs)  # noqa: SLF001
    assert backend.failure() is None
    return medido


def sh(*cmd: str) -> str:
    return subprocess.run(cmd, capture_output=True, text=True, check=False).stdout.strip()


def main() -> None:
    print("uptime antes:", sh("uptime"))
    print(f"load1 antes: {os.getloadavg()[0]:.2f}")
    print(f"equipo {platform.node()}, {platform.platform()}, {sh('sysctl', '-n', 'machdep.cpu.brand_string')}")
    print(f"python {platform.python_version()}, numpy {np.__version__}, cpus {os.cpu_count()}")
    print("rustc:", sh("rustc", "--version"))
    largo = len(next(iter(motor.Motor(instalacion(3), SR)._filtros.values())))  # noqa: SLF001
    print(f"filtro del banco: {largo} coeficientes; bloque {BLOCK} muestras = {BLOCK / 48:.1f} ms; {BLOCKS} bloques")
    x = senal(8, 1)
    cab = ("parlantes", "conv med", "conv p95", "fft med", "fft p95", "rust med", "rust p95", "conv/rust", "fft/rust")
    print(" ".join(f"{c:>10}" for c in cab))
    for n in (1, 3, 8):
        d, f, r = caso("directo", n, x), caso("fft", n, x), caso("rust", n, x)
        print(
            f"{n:>10} {d[0]:>10.3f} {d[1]:>10.3f} {f[0]:>10.3f} {f[1]:>10.3f} {r[0]:>10.3f} {r[1]:>10.3f} "
            f"{d[0] / r[0]:>10.1f} {f[0] / r[0]:>10.1f}"
        )
    backend.reset()
    print("uptime despues:", sh("uptime"))
    print(f"load1 despues: {os.getloadavg()[0]:.2f}")


if __name__ == "__main__":
    main()
