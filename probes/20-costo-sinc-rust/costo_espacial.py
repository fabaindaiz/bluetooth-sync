#!/usr/bin/env python3
"""Costo por bloque del upmix espacial / frente intacto: numpy contra Rust (experimentos/20, §2).

Un bloque = `SpatialUpmix.process` de 4096 muestras estéreo, que entrega directo y ambiente para
cada parlante (1, 3 u 8 parlantes en un anillo; render espacial y frente intacto). Se mide la
etapa como la llama el motor, a través de `backend` (numpy, o Rust con la conversión y el armado
del diccionario incluidos), y además el objeto Rust solo (`aurasync_engine.SpatialUpmix.process`).
Mediana y p95 de 500 bloques, tras 50 de calentamiento.

    cd host && hatch test tests/test_spatial_rust.py   # deja compilada la extensión
    $(hatch env find hatch-test.py3.12)/bin/python ../probes/20-costo-sinc-rust/costo_espacial.py
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

from aurasync.dsp import backend, spatial  # noqa: E402
from aurasync.dsp.spatial import SpatialParams, SpatialUpmix  # noqa: E402

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


def anillo(n: int) -> tuple[list[str], dict[str, float]]:
    nombres = [f"s{i}" for i in range(n)]
    return nombres, {nm: -180 + 360 * (i + 0.5) / n for i, nm in enumerate(nombres)}


def senal() -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(6)
    n = (WARM + BLOCKS) * BLOCK
    centro = rng.standard_normal(n)
    return 0.1 * (centro + 0.5 * rng.standard_normal(n)), 0.1 * (centro + 0.5 * rng.standard_normal(n))


def medir(proceso, izq, der) -> tuple[float, float]:
    tiempos = []
    for i in range(WARM + BLOCKS):
        l, r = izq[i * BLOCK : (i + 1) * BLOCK], der[i * BLOCK : (i + 1) * BLOCK]
        t = time.perf_counter()
        proceso(l, r)
        if i >= WARM:
            tiempos.append((time.perf_counter() - t) * 1e3)
    return float(np.median(tiempos)), float(np.percentile(tiempos, 95))


def etapa(motor: str, n: int, frente: bool):
    backend.reset()
    backend.use(motor)
    nombres, angulos = anillo(n)
    up = SpatialUpmix(nombres, angulos, set(), SR, SpatialParams(front_intact=frente))
    assert (up._rust is not None) == (motor == backend.RUST)  # noqa: SLF001
    return up.process


def solo_rust(n: int, frente: bool):
    nombres, angulos = anillo(n)
    up = aurasync_engine.SpatialUpmix(n, SR, spatial.N_FFT, spatial.HOP)
    p = SpatialParams(front_intact=frente)
    up.set_params(p.arc_deg, p.ambience, p.ambient_level_db, p.haas_ms, p.threshold, p.lam, p.front_intact)
    up.set_layout([angulos[nm] for nm in nombres], [False] * n, None)
    return up.process


def main() -> None:
    print("uptime antes:", subprocess.run(["uptime"], capture_output=True, text=True, check=False).stdout.strip())
    print(f"load1 antes: {os.getloadavg()[0]:.2f}")
    print("energía antes:", energia())
    print(f"python {platform.python_version()}, numpy {np.__version__}, cpus {os.cpu_count()}")
    print(f"bloque {BLOCK} muestras a 48 kHz = {BLOCK / 48:.1f} ms; {BLOCKS} bloques")
    izq, der = senal()
    cab = ("parlantes", "render", "numpy med", "numpy p95", "rust med", "rust p95", "x", "solo rust")
    print(f"{cab[0]:>9} {cab[1]:>8} {cab[2]:>10} {cab[3]:>10} {cab[4]:>9} {cab[5]:>9} {cab[6]:>6} {cab[7]:>10}")
    for n in (1, 3, 8):
        for frente in (False, True):
            if frente and n < 3:  # noqa: PLR2004 - sin par de adelante no hay frente intacto
                continue
            nm, n95 = medir(etapa(backend.NUMPY, n, frente), izq, der)
            rm, r95 = medir(etapa(backend.RUST, n, frente), izq, der)
            sm, _ = medir(solo_rust(n, frente), izq, der)
            render = "frente" if frente else "espacial"
            print(f"{n:>9} {render:>8} {nm:>10.3f} {n95:>10.3f} {rm:>9.3f} {r95:>9.3f} {nm / rm:>6.1f} {sm:>10.3f}")
    backend.reset()
    print("uptime despues:", subprocess.run(["uptime"], capture_output=True, text=True, check=False).stdout.strip())
    print(f"load1 despues: {os.getloadavg()[0]:.2f}")
    print("energía después:", energia())


if __name__ == "__main__":
    main()
