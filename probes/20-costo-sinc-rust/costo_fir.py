#!/usr/bin/env python3
"""Costo por bloque de los filtros FIR por convolución FFT: numpy contra Rust (experimentos/20, §5).

Un bloque = 4096 muestras (85,3 ms a 48 kHz). Se mide, con cada motor, a través de la clase numpy
que despacha (`eq.StreamingFIR` / `eq.PartitionedFIR`, con `backend` y la conversión a float64
incluidos), y el objeto de la extensión solo ("solo Rust"):

- **EQ por parlante**: un `StreamingFIR` de `eq.TAPS` (2048) coeficientes por parlante, como el
  motor (`motor.py`, `_ecualizador`), con 1, 3 y 8 parlantes; el tiempo es el de todos juntos.
- **Crossover**: sus dos ramas (`crossover.HighPass` y `LowPass` a 100 Hz, LR4), un bloque cada una.
- **Graves virtuales**: un `PartitionedFIR` como lo usa `VirtualBass` (el filtro de banda de
  `virtual_bass._filters` a 90 Hz, particiones de 4096).
- **El primer bloque de un tamaño nuevo** (el que arma el plan, los búferes y el espectro de los
  coeficientes) y **construir** un filtro, en los dos motores.

Mediana y p95 de 500 bloques tras 50 de calentamiento.

    cd host && hatch test tests/test_eq_rust.py   # deja compilada la extensión
    $(hatch env find hatch-test.py3.12)/bin/python ../probes/20-costo-sinc-rust/costo_fir.py
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

from aurasync.dsp import backend, crossover, eq, virtual_bass  # noqa: E402
from aurasync.dsp.response import THIRDS  # noqa: E402

SR, BLOCK, BLOCKS, WARM = 48000, 4096, 500, 50


def leer(ruta: str) -> str:
    try:
        return Path(ruta).read_text().strip()
    except OSError as exc:
        return f"? ({exc.__class__.__name__})"


def energia() -> str:
    perfil = subprocess.run(["powerprofilesctl", "get"], capture_output=True, text=True, check=False).stdout.strip()
    return (
        f"red {leer('/sys/class/power_supply/ADP1/online')}, "
        f"gobernador {leer('/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor')}, "
        f"perfil {perfil or '?'}, no_turbo {leer('/sys/devices/system/cpu/intel_pstate/no_turbo')}"
    )


def senal(n: int, semilla: int) -> np.ndarray:
    return 0.1 * np.random.default_rng(semilla).standard_normal((WARM + BLOCKS) * BLOCK * n).reshape(n, -1)


def cronometrar(paso, bloques: int = WARM + BLOCKS) -> tuple[float, float]:
    """`paso(i)` por bloque; mediana y p95 en ms de los medidos."""
    tiempos = []
    for i in range(bloques):
        t = time.perf_counter()
        paso(i)
        if i >= WARM:
            tiempos.append((time.perf_counter() - t) * 1e3)
    return float(np.median(tiempos)), float(np.percentile(tiempos, 95))


def con_motor(motor: str) -> None:
    backend.reset()
    backend.use(motor)


def curvas(n: int) -> list[np.ndarray]:
    rng = np.random.default_rng(3)
    return [eq.fir(np.round(rng.uniform(0.0, 6.0, len(THIRDS)), 2)) for _ in range(n)]


def eq_por_parlante(motor: str | None, n: int, x: np.ndarray) -> tuple[float, float]:
    """`motor` None: los objetos de la extensión, sin la clase numpy."""
    if motor is None:
        filtros = [aurasync_engine.StreamingFIR(h) for h in curvas(n)]
    else:
        con_motor(motor)
        filtros = [eq.StreamingFIR(h) for h in curvas(n)]

    def paso(i: int) -> None:
        for k, f in enumerate(filtros):
            f.process(x[k, i * BLOCK : (i + 1) * BLOCK])

    medido = cronometrar(paso)
    if motor is not None:
        assert all((f._rust is not None) == (motor == backend.RUST) for f in filtros)  # noqa: SLF001
        assert backend.failure() is None
    return medido


def crossover_dos_ramas(motor: str, x: np.ndarray) -> tuple[float, float]:
    con_motor(motor)
    alto, bajo = crossover.HighPass(SR, 100.0), crossover.LowPass(SR, 100.0)

    def paso(i: int) -> None:
        b = x[0, i * BLOCK : (i + 1) * BLOCK]
        alto.process(b)
        bajo.process(b)

    medido = cronometrar(paso)
    assert (alto._fir._rust is not None) == (motor == backend.RUST)  # noqa: SLF001
    return medido


def graves_virtuales(motor: str | None, x: np.ndarray) -> tuple[float, float]:
    banda, _, _ = virtual_bass._filters(SR, 90.0)  # noqa: SLF001
    if motor is None:
        f = aurasync_engine.PartitionedFIR(banda, BLOCK)
    else:
        con_motor(motor)
        f = eq.PartitionedFIR(banda, BLOCK)
    medido = cronometrar(lambda i: f.process(x[0, i * BLOCK : (i + 1) * BLOCK]))
    if motor is not None:
        assert (f._rust is not None) == (motor == backend.RUST)  # noqa: SLF001
    return medido


def primer_bloque_y_construccion(motor: str) -> dict[str, float]:
    """ms de: construir un StreamingFIR de 2048 (y su primer bloque de 4096, que en Rust arma el
    objeto); un bloque de 1000 muestras por primera vez (tamaño nuevo) y la segunda; construir el
    PartitionedFIR de banda de `VirtualBass`."""
    con_motor(motor)
    h = curvas(1)[0]
    banda, _, _ = virtual_bass._filters(SR, 90.0)  # noqa: SLF001
    x = 0.1 * np.random.default_rng(9).standard_normal(BLOCK)
    out: dict[str, list[float]] = {k: [] for k in ("construir", "primer 4096", "primer 1000", "segundo 1000", "particionado")}
    for _ in range(30):
        t = time.perf_counter()
        f = eq.StreamingFIR(h)
        out["construir"].append(time.perf_counter() - t)
        t = time.perf_counter()
        f.process(x)
        out["primer 4096"].append(time.perf_counter() - t)
        t = time.perf_counter()
        f.process(x[:1000])
        out["primer 1000"].append(time.perf_counter() - t)
        t = time.perf_counter()
        f.process(x[:1000])
        out["segundo 1000"].append(time.perf_counter() - t)
        t = time.perf_counter()
        p = eq.PartitionedFIR(banda, BLOCK)
        p.process(x)
        out["particionado"].append(time.perf_counter() - t)
    return {k: float(np.median(v)) * 1e3 for k, v in out.items()}


def main() -> None:
    print("uptime antes:", subprocess.run(["uptime"], capture_output=True, text=True, check=False).stdout.strip())
    print(f"load1 antes: {os.getloadavg()[0]:.2f}")
    print("energía antes:", energia())
    print(f"equipo {platform.node()}, python {platform.python_version()}, numpy {np.__version__}, cpus {os.cpu_count()}")
    print(f"bloque {BLOCK} muestras a 48 kHz = {BLOCK / 48:.1f} ms; {BLOCKS} bloques")
    x = senal(8, 1)
    lp, _ = crossover.impulses(SR, 100.0, 4)
    banda, _, _ = virtual_bass._filters(SR, 90.0)  # noqa: SLF001
    print(f"coeficientes: EQ {eq.TAPS}, crossover {len(lp)}, banda de graves virtuales {len(banda)}")
    cab = ("caso", "numpy med", "numpy p95", "rust med", "rust p95", "x", "solo rust")
    print(f"{cab[0]:>22} {cab[1]:>10} {cab[2]:>10} {cab[3]:>9} {cab[4]:>9} {cab[5]:>6} {cab[6]:>10}")

    def fila(caso: str, numpy_: tuple[float, float], rust: tuple[float, float], solo: float | None) -> None:
        extra = f"{solo:>10.3f}" if solo is not None else f"{'-':>10}"
        print(f"{caso:>22} {numpy_[0]:>10.3f} {numpy_[1]:>10.3f} {rust[0]:>9.3f} {rust[1]:>9.3f} {numpy_[0] / rust[0]:>6.1f} {extra}")

    for n in (1, 3, 8):
        fila(
            f"EQ, {n} parlante(s)",
            eq_por_parlante(backend.NUMPY, n, x),
            eq_por_parlante(backend.RUST, n, x),
            eq_por_parlante(None, n, x)[0],
        )
    fila("crossover, 2 ramas", crossover_dos_ramas(backend.NUMPY, x), crossover_dos_ramas(backend.RUST, x), None)
    fila(
        "graves virtuales, 1",
        graves_virtuales(backend.NUMPY, x),
        graves_virtuales(backend.RUST, x),
        graves_virtuales(None, x)[0],
    )
    for motor in (backend.NUMPY, backend.RUST):
        partes = ", ".join(f"{k} {v:.3f}" for k, v in primer_bloque_y_construccion(motor).items())
        print(f"{motor}: ms (mediana de 30): {partes}")
    backend.reset()
    print("uptime despues:", subprocess.run(["uptime"], capture_output=True, text=True, check=False).stdout.strip())
    print(f"load1 despues: {os.getloadavg()[0]:.2f}")
    print("energía después:", energia())


if __name__ == "__main__":
    main()
