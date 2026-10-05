#!/usr/bin/env python3
"""Costo por bloque del extractor de ambiente: numpy contra Rust (experimentos/20, §3).

Un bloque = `ambience.Extractor.procesar` de 4096 muestras estéreo, que da el ambiente en mono.
**El extractor corre una vez por entrada, no por parlante**: su costo no depende de cuántos
parlantes haya. Para mostrarlo se mide dentro del motor (`Motor.procesar`, render clásico con la
cadena por defecto) con 1, 3 y 8 parlantes, cronometrando solo la llamada del motor al extractor
(numpy, o Rust a través de `backend` con la conversión incluida), y además el objeto Rust solo
(`aurasync_engine.AmbienceExtractor.process`). Mediana y p95 de 500 bloques, tras 50 de
calentamiento.

    cd host && hatch test tests/test_ambience_rust.py   # deja compilada la extensión
    $(hatch env find hatch-test.py3.12)/bin/python ../probes/20-costo-sinc-rust/costo_ambiente.py
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

from aurasync.chain import ChainValues  # noqa: E402
from aurasync.config import Instalacion, Parlante  # noqa: E402
from aurasync.dsp import ambience, backend  # noqa: E402
from aurasync.motor import Motor  # noqa: E402

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


def senal() -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(6)
    n = (WARM + BLOCKS) * BLOCK
    centro = rng.standard_normal(n)
    return 0.1 * (centro + 0.5 * rng.standard_normal(n)), 0.1 * (centro + 0.5 * rng.standard_normal(n))


def instalacion(n: int) -> Instalacion:
    pans = np.linspace(-1.0, 1.0, n) if n > 1 else [0.0]
    return Instalacion(
        parlantes=[Parlante(f"s{i}", f"sink{i}", pan=float(p), ambiente=0.3) for i, p in enumerate(pans)]
    )


def en_el_motor(motor: str, n: int, izq: np.ndarray, der: np.ndarray) -> tuple[float, float]:
    """El tiempo de la llamada del motor al extractor, con `n` parlantes."""
    backend.reset()
    backend.use(motor)
    m = Motor(instalacion(n), SR, semilla=1, chain=ChainValues())
    ex = m._extractor  # noqa: SLF001 - se cronometra la etapa tal como la llama el motor
    assert (ex._rust is not None) == (motor == backend.RUST)  # noqa: SLF001
    original = ex.procesar
    tiempos: list[float] = []

    def cronometrado(l: np.ndarray, r: np.ndarray) -> np.ndarray:
        t = time.perf_counter()
        y = original(l, r)
        tiempos.append((time.perf_counter() - t) * 1e3)
        return y

    ex.procesar = cronometrado
    for i in range(WARM + BLOCKS):
        m.procesar(izq[i * BLOCK : (i + 1) * BLOCK], der[i * BLOCK : (i + 1) * BLOCK])
    assert backend.failure() is None
    medidos = tiempos[WARM:]
    return float(np.median(medidos)), float(np.percentile(medidos, 95))


def solo_rust(izq: np.ndarray, der: np.ndarray) -> float:
    ex = aurasync_engine.AmbienceExtractor(ambience.N_FFT, ambience.SALTO)
    tiempos = []
    for i in range(WARM + BLOCKS):
        l, r = izq[i * BLOCK : (i + 1) * BLOCK], der[i * BLOCK : (i + 1) * BLOCK]
        t = time.perf_counter()
        ex.process(l, r)
        if i >= WARM:
            tiempos.append((time.perf_counter() - t) * 1e3)
    return float(np.median(tiempos))


def main() -> None:
    print("uptime antes:", subprocess.run(["uptime"], capture_output=True, text=True, check=False).stdout.strip())
    print(f"load1 antes: {os.getloadavg()[0]:.2f}")
    print("energía antes:", energia())
    print(f"python {platform.python_version()}, numpy {np.__version__}, cpus {os.cpu_count()}")
    print(f"bloque {BLOCK} muestras a 48 kHz = {BLOCK / 48:.1f} ms; {BLOCKS} bloques")
    izq, der = senal()
    cab = ("parlantes", "numpy med", "numpy p95", "rust med", "rust p95", "x")
    print(f"{cab[0]:>9} {cab[1]:>10} {cab[2]:>10} {cab[3]:>9} {cab[4]:>9} {cab[5]:>6}")
    for n in (1, 3, 8):
        nm, n95 = en_el_motor(backend.NUMPY, n, izq, der)
        rm, r95 = en_el_motor(backend.RUST, n, izq, der)
        print(f"{n:>9} {nm:>10.3f} {n95:>10.3f} {rm:>9.3f} {r95:>9.3f} {nm / rm:>6.1f}")
    print(f"solo rust (el objeto de la extensión, sin motor): mediana {solo_rust(izq, der):.3f}")
    backend.reset()
    print("uptime despues:", subprocess.run(["uptime"], capture_output=True, text=True, check=False).stdout.strip())
    print(f"load1 despues: {os.getloadavg()[0]:.2f}")
    print("energía después:", energia())


if __name__ == "__main__":
    main()
