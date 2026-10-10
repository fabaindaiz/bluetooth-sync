#!/usr/bin/env python3
"""Costo por bloque de los medidores de sonoridad (`dsp/loudness.py`): numpy contra Rust (experimentos/20, §13).

La pregunta de la tarea 14 del port: ¿vale la pena llevar `LoudnessMeter` a Rust? Se midió primero
con numpy solo (las corridas `hp-o16-medidores-corrida-*`, antes del port); con el port, cada caso
corre con los dos motores (`--motores numpy rust`, las corridas `hp-o16-medidores-rust-*`). Se mide lo que el
hilo del motor hace en cada bloque, `quality.QualityMeter.push`: un medidor mono por canal de la
entrada (2) y uno por parlante, todos sin historia (`history=False`), con la ponderación K por
paso de 100 ms (FFT) y el pico verdadero (4× sobre las posiciones cerca del pico). Además, aparte:

- **un medidor** mono solo, para separar el costo por medidor del de `QualityMeter`;
- **el monitor** (`monitor.py`): uno estéreo de referencia y uno del candidato (2 canales en
  `mix`), que solo corre con el monitor de audífonos prendido.

La lectura (`summary()`, a 2 Hz) no se mide: no es por bloque. Al final, la mayor diferencia
absoluta entre los dos motores de las lecturas (momentánea, de corto plazo, pico verdadero y los
pasos) de `QualityMeter` con 8 parlantes sobre la señal **música**, bloque a bloque.

Dos señales, porque el pico verdadero cambia de trabajo según cuántas posiciones están cerca del
pico del bloque: **ruido** gaussiano (0,1; muchas posiciones cerca) y **música**, ruido rosa con
una envolvente de 2 Hz (menos posiciones cerca, más parecido a lo que suena). Con 4 y 8 parlantes.

Mediana y p95 de 500 bloques tras 50 de calentamiento.

    nice -n 19 $(hatch env find hatch-test.py3.12)/bin/python ../probes/20-costo-sinc-rust/costo_medidores.py
    nice -n 19 $(hatch env find hatch-test.py3.12)/bin/python ../probes/20-costo-sinc-rust/costo_medidores.py \
        --motores numpy rust
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

from aurasync import quality  # noqa: E402
from aurasync.dsp import backend, loudness  # noqa: E402

LARGO = (WARM + BLOCKS) * BLOCK


def ruido(canales: int, semilla: int) -> np.ndarray:
    return 0.1 * np.random.default_rng(semilla).standard_normal((canales, LARGO))


def musica(canales: int, semilla: int) -> np.ndarray:
    """Ruido rosa (1/f por FFT) con una envolvente de 2 Hz, a ~-20 dBFS RMS."""
    rng = np.random.default_rng(semilla)
    blanco = rng.standard_normal((canales, LARGO))
    espectro = np.fft.rfft(blanco, axis=1)
    f = np.fft.rfftfreq(LARGO, 1 / SR)
    f[0] = f[1]
    rosa = np.fft.irfft(espectro / np.sqrt(f), LARGO, axis=1)
    envolvente = 0.6 + 0.4 * np.sin(2 * np.pi * 2.0 * np.arange(LARGO) / SR)
    rosa *= envolvente
    return 0.1 * rosa / rosa.std(axis=1, keepdims=True)


SENALES = {"ruido": ruido, "musica": musica}


def calidad(parlantes: int, x: np.ndarray) -> tuple[float, float]:
    """`QualityMeter.push`: la entrada (2 canales) y una salida por parlante."""
    nombres = [f"p{k}" for k in range(parlantes)]
    medidor = quality.QualityMeter(SR, nombres)
    try:

        def paso(i: int) -> None:
            tramo = slice(i * BLOCK, (i + 1) * BLOCK)
            medidor.push((x[0, tramo], x[1, tramo]), {n: x[2 + k, tramo] for k, n in enumerate(nombres)})

        return cronometrar(paso)
    finally:
        medidor.close()


def uno(x: np.ndarray) -> tuple[float, float]:
    medidor = loudness.LoudnessMeter(SR, 1, history=False)
    return cronometrar(lambda i: medidor.push(x[0, i * BLOCK : (i + 1) * BLOCK]))


def monitor(x: np.ndarray) -> tuple[float, float]:
    """Lo que `monitor.py` mide por bloque en `mix`: la referencia y el candidato, estéreo los dos."""
    referencia = loudness.LoudnessMeter(SR, 2, history=False)
    candidato = loudness.LoudnessMeter(SR, 2, history=False)

    def paso(i: int) -> None:
        tramo = slice(i * BLOCK, (i + 1) * BLOCK)
        referencia.push(x[0:2, tramo].T)
        _ = referencia.short_term, referencia.momentary  # monitor.py los lee en cada bloque
        candidato.push(x[2:4, tramo].T)
        _ = candidato.short_term

    return cronometrar(paso)


def sh(*cmd: str) -> str:
    return subprocess.run(cmd, capture_output=True, text=True, check=False).stdout.strip()


def con_motor(motor: str) -> None:
    backend.reset()
    backend.use(motor)


def lecturas(motor: str, x: np.ndarray) -> np.ndarray:
    """Las lecturas de `QualityMeter` con 8 parlantes, bloque a bloque, con `motor`."""
    con_motor(motor)
    nombres = [f"p{k}" for k in range(8)]
    medidor = quality.QualityMeter(SR, nombres)
    filas = []
    try:
        for i in range(WARM + BLOCKS):
            tramo = slice(i * BLOCK, (i + 1) * BLOCK)
            medidor.push((x[0, tramo], x[1, tramo]), {n: x[2 + k, tramo] for k, n in enumerate(nombres)})
            fila = []
            for m in (*medidor.channels, *medidor.outputs.values()):
                fila += [m.momentary, m.short_term, m.true_peak_short_dbtp, *(m.last_steps(1) or [0.0])]
            filas.append(fila)
    finally:
        medidor.close()
    assert backend.failure() is None
    return np.array(filas)


def main() -> None:
    motores = sys.argv[sys.argv.index("--motores") + 1 :] if "--motores" in sys.argv else [backend.NUMPY]
    print("uptime antes:", sh("uptime"))
    print(f"load1 antes: {os.getloadavg()[0]:.2f}")
    print(f"equipo {platform.node()}, {platform.platform()}, {platform.processor() or platform.machine()}")
    print(f"python {platform.python_version()}, numpy {np.__version__}, cpus {os.cpu_count()}")
    print(f"bloque {BLOCK} muestras = {BLOCK / 48:.1f} ms; motores {motores}")
    cab = ("senal", "caso", "medidores", "motor", "med", "p95")
    print(" ".join(f"{c:>12}" for c in cab))
    for nombre, hacer in SENALES.items():
        x = hacer(10, 11)
        for motor in motores:
            con_motor(motor)
            casos = [("uno", 1, lambda: uno(x))]
            casos += [(f"calidad {n}", 2 + n, lambda n=n: calidad(n, x)) for n in (4, 8)]
            casos.append(("monitor mix", 2, lambda: monitor(x)))
            for caso, medidores, medir in casos:
                med, p95 = medir()
                assert backend.failure() is None
                print(f"{nombre:>12} {caso:>12} {medidores:>12} {motor:>12} {med:>12.4f} {p95:>12.4f}")
    if backend.RUST in motores:
        x = musica(10, 11)
        diferencia = np.abs(lecturas(backend.RUST, x) - lecturas(backend.NUMPY, x))
        print(f"mayor |rust - numpy| de las lecturas (musica, calidad 8): {np.nanmax(diferencia):.3g}")
    backend.reset()
    print("uptime despues:", sh("uptime"))
    print(f"load1 despues: {os.getloadavg()[0]:.2f}")


if __name__ == "__main__":
    main()
