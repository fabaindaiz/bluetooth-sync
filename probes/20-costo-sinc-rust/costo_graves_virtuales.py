#!/usr/bin/env python3
"""Costo por bloque del generador de armónicos (`virtual_bass.VirtualBass`): numpy contra Rust (experimentos/20, §7).

Un bloque = 4096 muestras (85,3 ms a 48 kHz). Armónicos encendidos a 0 dB, 90 Hz, ruido de
amplitud 0,1; 1, 3 y 8 parlantes (un `VirtualBass` por parlante, como `chain_stages.BassStage`);
el tiempo es el de todos juntos. Tres columnas:

- **numpy**: `engine=numpy`, los dos `PartitionedFIR` en numpy.
- **FIR en Rust, desde Python** (el estado tras la tarea 7): `engine=rust`, pero el generador
  llama a sus dos `PartitionedFIR` (cada uno con su objeto Rust) desde Python, con el valor
  absoluto, la calibración y la rampa en numpy.
- **Rust**: `engine=rust`, el generador entero en un objeto Rust (una llamada por bloque).

Y la parte fija: armar el objeto Rust (`build`) y mover el estado al cambiar de motor.
Mediana y p95 de 500 bloques tras 50 de calentamiento.

    cd host && hatch test tests/test_virtual_bass_rust.py   # deja compilada la extensión
    $(hatch env find hatch-test.py3.12)/bin/python ../probes/20-costo-sinc-rust/costo_graves_virtuales.py
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

from aurasync.dsp import backend  # noqa: E402
from aurasync.dsp.virtual_bass import VirtualBass, _gain  # noqa: E402


def caso(modo: str, n: int, x: np.ndarray) -> tuple[float, float]:
    backend.reset()
    backend.use(backend.NUMPY if modo == "numpy" else backend.RUST)
    generadores = [VirtualBass(SR, 90.0, 0.0, BLOCK) for _ in range(n)]
    objetivo = _gain(0.0)

    def paso(i: int) -> None:
        for k, g in enumerate(generadores):
            b = x[k, i * BLOCK : (i + 1) * BLOCK]
            if modo == "fir-rust-desde-python":
                g._dirty = True  # noqa: SLF001
                g._process_numpy(b, objetivo)  # noqa: SLF001 - el camino numpy con los FIR de la tarea 7
            else:
                g.process(b)

    medido = cronometrar(paso)
    if modo == "rust":
        assert all(g._rust is not None for g in generadores)  # noqa: SLF001
    else:
        assert all(g._rust is None for g in generadores)  # noqa: SLF001
    if modo == "fir-rust-desde-python":
        assert all(g._fir_band._rust is not None and g._fir_out._rust is not None for g in generadores)  # noqa: SLF001
    assert backend.failure() is None
    return medido


def fijo() -> dict[str, float]:
    """Armar el objeto Rust y mover el estado en los dos sentidos (mediana en ms de 200 veces)."""
    backend.reset()
    backend.use(backend.RUST)
    g = VirtualBass(SR, 90.0, 0.0, BLOCK)
    g.process(np.zeros(BLOCK))
    salida = {}
    for nombre, f in {
        "armar el objeto Rust (con su estado)": lambda: g._build_rust(),  # noqa: SLF001
        "leer su estado (a numpy)": lambda: g._rust.state(),  # noqa: SLF001
        "cargar el estado en numpy": lambda: g._load_state(g._rust.state()),  # noqa: SLF001
    }.items():
        tiempos = []
        for _ in range(200):
            t = time.perf_counter()
            f()
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
    cab = ("parlantes", "numpy med", "numpy p95", "FIR-R med", "FIR-R p95", "rust med", "rust p95", "x num", "x FIR-R")
    print(" ".join(f"{c:>10}" for c in cab))
    for n in (1, 3, 8):
        a, f, r = caso("numpy", n, x), caso("fir-rust-desde-python", n, x), caso("rust", n, x)
        print(
            f"{n:>10} {a[0]:>10.3f} {a[1]:>10.3f} {f[0]:>10.3f} {f[1]:>10.3f} {r[0]:>10.3f} {r[1]:>10.3f} "
            f"{a[0] / r[0]:>10.1f} {f[0] / r[0]:>10.1f}"
        )
    print("-- la parte fija (mediana, ms)")
    for nombre, ms in fijo().items():
        print(f"{nombre:>40}: {ms:.4f}")
    backend.reset()
    print("uptime despues:", subprocess.run(["uptime"], capture_output=True, text=True, check=False).stdout.strip())
    print(f"load1 despues: {os.getloadavg()[0]:.2f}")
    print("energía después:", energia())


if __name__ == "__main__":
    main()
