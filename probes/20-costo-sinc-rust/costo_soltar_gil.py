#!/usr/bin/env python3
"""Lo que cuesta soltar el GIL en las llamadas por bloque de la extensión (research/15 §B.14).

Desde el 2026-10-09 cada llamada por bloque del puente (`Reader.read`, los `process` de los FIR, el
upmix, el extractor, los graves virtuales y el limitador, y `LoudnessMeter.push`) copia su entrada a
un búfer de Rust y trabaja con `Python::detach`, para que los demás hilos de Python (el servidor
HTTP, los eventos del panel) no esperen el GIL mientras el motor procesa. La copia y el soltar y
retomar el GIL cuestan algo por llamada; esta sonda lo mide corriendo lo mismo contra la extensión
que se le dé en `PYTHONPATH` (la de siempre, con detach, o una compilada sin él) y se comparan las
dos salidas.

Un bloque = 4096 muestras; lo que el motor llama por bloque con 8 parlantes, cada grupo aparte: el
EQ (8 `StreamingFIR` de 2048 coeficientes), el limitador de pico real (8, ruido de 0,1: detecta
sin limitar), los medidores (10 mono, como `QualityMeter`), la lectura sinc (8 lecturas de 4096
posiciones), un `StreamingFIR` de 1 coeficiente (8 veces: el FFT del bloque sin el costo de los
coeficientes) y una llamada vacía (`Reader.read` de una posición sobre 64 muestras, 8 veces: casi
solo el puente).
Mediana y p95 de 500 bloques tras 50 de calentamiento.

    cd host && nice -n 19 $(hatch env find hatch-test.py3.12)/bin/python ../probes/20-costo-sinc-rust/costo_soltar_gil.py
    PYTHONPATH=<extensión sin detach> nice -n 19 .../python ../probes/20-costo-sinc-rust/costo_soltar_gil.py
"""

from __future__ import annotations

import os
import platform
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "host" / "src"))

import aurasync_engine  # noqa: E402
from costo_fir import BLOCK, BLOCKS, SR, WARM, cronometrar  # noqa: E402

from aurasync.dsp import backend, eq, limiter, loudness  # noqa: E402

N = 8


def main() -> None:
    backend.reset()
    backend.use(backend.RUST)
    print(f"extension: {aurasync_engine.__file__}")
    print(f"load1 antes: {os.getloadavg()[0]:.2f}")
    print(f"equipo {platform.node()}, {platform.platform()}, python {platform.python_version()}, numpy {np.__version__}")
    x = 0.1 * np.random.default_rng(7).standard_normal((N + 2, (WARM + BLOCKS) * BLOCK))
    taps = eq.fir(np.full(len(eq.THIRDS), 3.0))
    eqs = [aurasync_engine.StreamingFIR(taps) for _ in range(N)]
    lims = [limiter.TruePeakLimiter(SR)._build_rust() for _ in range(N)]  # noqa: SLF001
    meters = [loudness.LoudnessMeter(SR, 1)._build_rust() for _ in range(N + 2)]  # noqa: SLF001
    reader = aurasync_engine.Reader()
    data = x[0, : BLOCK + 64].copy()
    position = 16.0 + np.arange(BLOCK) * 0.999
    tiny = [aurasync_engine.StreamingFIR(np.ones(1)) for _ in range(N)]
    small, one = data[:64].copy(), np.array([20.5])

    def bloque(i: int, k: int) -> np.ndarray:
        return x[k, i * BLOCK : (i + 1) * BLOCK]

    casos = {
        "eq x8": lambda i: [f.process(bloque(i, k)) for k, f in enumerate(eqs)],
        "limitador x8": lambda i: [lim.process(bloque(i, k)) for k, lim in enumerate(lims)],
        "medidores x10": lambda i: [m.push(bloque(i, k)) for k, m in enumerate(meters)],
        "lectura x8": lambda _i: [reader.read(data, position) for _ in range(N)],
        "fir de 1 x8": lambda i: [f.process(bloque(i, k)) for k, f in enumerate(tiny)],
        "vacia x8": lambda _i: [reader.read(small, one) for _ in range(N)],
    }
    print(f"{'caso':>14} {'med ms':>9} {'p95 ms':>9}")
    for nombre, paso in casos.items():
        med, p95 = cronometrar(paso)
        print(f"{nombre:>14} {med:>9.4f} {p95:>9.4f}")
    print(f"load1 despues: {os.getloadavg()[0]:.2f}")


if __name__ == "__main__":
    main()
