#!/usr/bin/env python3
"""Qué gasta la cola difusa (`chain_stages.DiffuseStage`) fuera de su convolución (experimentos/20, §10).

La tarea 12 del port era la cola difusa. Su trabajo por bloque es un `eq.PartitionedFIR` (la
respuesta al impulso de cada parlante), que ya corre en Rust (§5). Esta sonda mide, como §6.1 para
el crossover, cuánto cuesta lo demás: la ganancia (constante o en rampa), las dos energías
(`np.dot`), el logaritmo de la proporción y la suma `x + wet`.

Tres columnas por número de parlantes (1, 3 y 8; el tiempo es el de todos juntos), con el motor
Rust y los valores por defecto de la cadena con `diffuse` = `noise_tail`:

- **etapa**: `DiffuseStage.process` entero, como lo llama el motor (`x`, `feed`).
- **convolución**: solo `PartitionedFIR.process(feed)` de cada cola (Rust).
- **fuera de la convolución**: la etapa con el filtro reemplazado por uno que devuelve una copia de
  una salida ya calculada (el resto del trabajo, sin convolución).

Y lo mismo con `engine=numpy`, como referencia. Mediana y p95 de 500 bloques tras 50 de
calentamiento. Pensada para el Mac (no lee `/sys`).

    nice -n 19 $(hatch env find hatch-test.py3.12)/bin/python ../probes/20-costo-sinc-rust/costo_difusion.py
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

from aurasync import chain_stages  # noqa: E402
from aurasync.chain import ChainValues  # noqa: E402
from aurasync.dsp import backend  # noqa: E402


class _Fijo:
    """Un filtro que no convoluciona: devuelve una copia de una salida ya calculada."""

    def __init__(self, salida: np.ndarray) -> None:
        self._salida = salida

    def process(self, x: np.ndarray) -> np.ndarray:
        return self._salida[: len(x)].copy()


def etapa(n: int) -> chain_stages.DiffuseStage:
    valores = ChainValues().with_algorithm("diffuse", "noise_tail")
    return chain_stages.DiffuseStage(valores, [f"P{k}" for k in range(n)], SR, BLOCK)


def caso(motor: str, n: int, x: np.ndarray, feed: np.ndarray) -> dict[str, tuple[float, float]]:
    backend.reset()
    backend.use(backend.RUST if motor == "rust" else backend.NUMPY)
    nombres = [f"P{k}" for k in range(n)]
    medido = {}
    st = etapa(n)
    assert st.active

    def paso_etapa(i: int) -> None:
        s = slice(i * BLOCK, (i + 1) * BLOCK)
        for k, nombre in enumerate(nombres):
            st.process(nombre, x[k, s], feed[k, s])

    medido["etapa"] = cronometrar(paso_etapa)
    colas = [st._tails[nombre]._fir for nombre in nombres]  # noqa: SLF001
    assert all((f._rust is not None) == (motor == "rust") for f in colas)  # noqa: SLF001
    otra = etapa(n)

    def paso_fir(i: int) -> None:
        s = slice(i * BLOCK, (i + 1) * BLOCK)
        for k, nombre in enumerate(nombres):
            otra._tails[nombre]._fir.process(feed[k, s])  # noqa: SLF001

    medido["convolucion"] = cronometrar(paso_fir)
    fija = etapa(n)
    for k, nombre in enumerate(nombres):
        cola = fija._tails[nombre]  # noqa: SLF001
        cola._fir = _Fijo(0.01 * feed[k, :BLOCK])  # noqa: SLF001

    def paso_fuera(i: int) -> None:
        s = slice(i * BLOCK, (i + 1) * BLOCK)
        for k, nombre in enumerate(nombres):
            fija.process(nombre, x[k, s], feed[k, s])

    medido["fuera"] = cronometrar(paso_fuera)
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
    largo = len(etapa(1)._tails["P0"].ir)  # noqa: SLF001
    print(f"respuesta al impulso: {largo} muestras; bloque {BLOCK} muestras = {BLOCK / 48:.1f} ms; {BLOCKS} bloques")
    x, feed = senal(8, 1), senal(8, 2)
    cab = ("motor", "parlantes", "etapa med", "etapa p95", "conv med", "conv p95", "fuera med", "fuera p95")
    print(" ".join(f"{c:>10}" for c in cab))
    for motor in ("rust", "numpy"):
        for n in (1, 3, 8):
            m = caso(motor, n, x, feed)
            fila = [f"{v:>10.4f}" for clave in ("etapa", "convolucion", "fuera") for v in m[clave]]
            print(f"{motor:>10} {n:>10} " + " ".join(fila))
    backend.reset()
    print("uptime despues:", sh("uptime"))
    print(f"load1 despues: {os.getloadavg()[0]:.2f}")


if __name__ == "__main__":
    main()
