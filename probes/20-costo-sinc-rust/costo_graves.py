#!/usr/bin/env python3
"""Costo por bloque de la etapa de graves (`chain_stages.BassStage`): numpy contra Rust (experimentos/20, §6).

Un bloque = 4096 muestras (85,3 ms a 48 kHz). La etapa se mide entera, como la llama el motor
(`motor.py`): `feed` una vez por bloque, y por parlante `before_delay` (los aptos para graves) y
`process` (los chicos). Casos, con 1, 3 y 8 parlantes:

- **protect**: cada parlante chico pasa por un pasa-altos (un `StreamingFIR` de 2048 coeficientes), sin
  armónicos (el `VirtualBass` aparte se mide en el §5).
- **crossover**: un Charge 6 más los chicos; los chicos con pasa-altos, el Charge 6 con el all-pass
  (`lp + hp`) y la alimentación (el pasa-bajos de la media).

Además, **dónde se va el tiempo**: lo que la etapa hace por bloque fuera de la convolución (suma de
canales, las dos energías por rama, el logaritmo, el retardo) medido solo, y la parte de convolución
(el tiempo dentro de `StreamingFIR.process`) medida con un contador sobre la clase. Mediana y p95 de
500 bloques tras 50 de calentamiento.

    cd host && hatch test tests/test_crossover_rust.py   # deja compilada la extensión
    $(hatch env find hatch-test.py3.12)/bin/python ../probes/20-costo-sinc-rust/costo_graves.py
"""

from __future__ import annotations

import os
import platform
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "host" / "src"))

from costo_fir import BLOCK, BLOCKS, SR, WARM, cronometrar, energia, senal  # noqa: E402

from aurasync import chain_stages  # noqa: E402
from aurasync.chain import ChainValues  # noqa: E402
from aurasync.dsp import backend, eq  # noqa: E402


def etapa(algoritmo: str, n: int):
    """(etapa, nombres de los parlantes): `protect` con `n` Go 4; `crossover` con un Charge 6 y n-1 Go 4."""
    nombres = [f"p{k}" for k in range(n)]
    tipos = ["charge6" if algoritmo == "crossover" and k == n - 1 else "go4" for k in range(n)]
    valores = ChainValues().with_algorithm("bass", algoritmo)
    return chain_stages.BassStage(valores, list(zip(nombres, tipos, strict=True)), SR, BLOCK), nombres


def un_bloque(e, nombres, izq, der) -> None:
    alim = e.feed(izq, der, 0)
    for nombre in nombres:
        x = e.before_delay(nombre, izq, alim)
        e.process(nombre, x)


def caso(algoritmo: str, n: int, motor: str, x: np.ndarray) -> tuple[float, float]:
    backend.reset()
    backend.use(motor)
    e, nombres = etapa(algoritmo, n)

    def paso(i: int) -> None:
        s = slice(i * BLOCK, (i + 1) * BLOCK)
        un_bloque(e, nombres, x[0, s], x[1, s])

    medido = cronometrar(paso)
    filtros = [*e._high.values(), *e._allpass.values()]  # noqa: SLF001
    filtros = [b._fir if hasattr(b, "_fir") else b for b in filtros]  # noqa: SLF001
    if e._feed is not None:  # noqa: SLF001
        filtros.append(e._feed._fir)  # noqa: SLF001
    assert filtros
    assert all((f._rust is not None) == (motor == backend.RUST) for f in filtros)  # noqa: SLF001
    assert backend.failure() is None
    return medido


def reparto(algoritmo: str, n: int, motor: str, x: np.ndarray) -> tuple[float, float]:
    """(ms por bloque dentro de `StreamingFIR.process`, ms por bloque de la etapa entera), medianas."""
    backend.reset()
    backend.use(motor)
    e, nombres = etapa(algoritmo, n)
    acumulado = [0.0]
    original = eq.StreamingFIR.process

    def medido(self, block):
        t = time.perf_counter()
        y = original(self, block)
        acumulado[0] += time.perf_counter() - t
        return y

    eq.StreamingFIR.process = medido
    try:
        dentro, total = [], []
        for i in range(WARM + BLOCKS):
            s = slice(i * BLOCK, (i + 1) * BLOCK)
            acumulado[0] = 0.0
            t = time.perf_counter()
            un_bloque(e, nombres, x[0, s], x[1, s])
            if i >= WARM:
                total.append((time.perf_counter() - t) * 1e3)
                dentro.append(acumulado[0] * 1e3)
    finally:
        eq.StreamingFIR.process = original
    return float(np.median(dentro)), float(np.median(total))


def resto_numpy(x: np.ndarray) -> dict[str, float]:
    """Lo que la etapa hace por bloque en numpy y no es convolución, solo (mediana en ms)."""
    b = x[0, :BLOCK]
    c = x[1, :BLOCK]
    y = 0.5 * b
    pasos = {
        "0,5 * (L + R)": lambda: 0.5 * (b + c),
        "dos energías (np.dot)": lambda: (float(np.dot(b, b)), float(np.dot(y, y))),
        "10 * log10 (escalar)": lambda: 10 * np.log10(1.2345),
        "retardo (concatenar)": lambda: np.concatenate([b[:64], c])[: len(c)],
        "y + alimentación": lambda: b + c,
    }
    salida = {}
    for nombre, f in pasos.items():
        tiempos = []
        for k in range(2000):
            t = time.perf_counter()
            f()
            if k >= 200:
                tiempos.append((time.perf_counter() - t) * 1e3)
        salida[nombre] = float(np.median(tiempos))
    return salida


def main() -> None:
    print("uptime antes:", subprocess.run(["uptime"], capture_output=True, text=True, check=False).stdout.strip())
    print(f"load1 antes: {os.getloadavg()[0]:.2f}")
    print("energía antes:", energia())
    print(f"equipo {platform.node()}, python {platform.python_version()}, numpy {np.__version__}, cpus {os.cpu_count()}")
    print(f"bloque {BLOCK} muestras a 48 kHz = {BLOCK / 48:.1f} ms; {BLOCKS} bloques")
    x = senal(8, 1)
    cab = ("caso", "numpy med", "numpy p95", "rust med", "rust p95", "x")
    print(f"{cab[0]:>22} {cab[1]:>10} {cab[2]:>10} {cab[3]:>9} {cab[4]:>9} {cab[5]:>6}")
    for algoritmo in ("protect", "crossover"):
        for n in (1, 3, 8):
            if algoritmo == "crossover" and n == 1:
                continue  # un Charge 6 solo: sin parlantes chicos, solo el all-pass y la alimentación
            a, r = caso(algoritmo, n, backend.NUMPY, x), caso(algoritmo, n, backend.RUST, x)
            print(f"{algoritmo + ', ' + str(n):>22} {a[0]:>10.3f} {a[1]:>10.3f} {r[0]:>9.3f} {r[1]:>9.3f} {a[0] / r[0]:>6.1f}")
    print("-- reparto (mediana, ms por bloque): dentro de StreamingFIR.process / etapa entera")
    for algoritmo, n in (("protect", 3), ("crossover", 3), ("crossover", 8)):
        for motor in (backend.NUMPY, backend.RUST):
            dentro, total = reparto(algoritmo, n, motor, x)
            print(
                f"{algoritmo + ', ' + str(n):>16} {motor:>6}: convolución {dentro:.3f}, etapa {total:.3f}, "
                f"resto {total - dentro:.3f} ({100 * (total - dentro) / total:.0f} %)"
            )
    print("-- los pasos de numpy que no son convolución (mediana, ms)")
    for nombre, ms in resto_numpy(x).items():
        print(f"{nombre:>26}: {ms:.4f}")
    backend.reset()
    print("uptime despues:", subprocess.run(["uptime"], capture_output=True, text=True, check=False).stdout.strip())
    print(f"load1 despues: {os.getloadavg()[0]:.2f}")
    print("energía después:", energia())


if __name__ == "__main__":
    main()
