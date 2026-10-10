#!/usr/bin/env python3
"""Costo por bloque de las rampas y el corte (`dsp/ramps.py`) en numpy (experimentos/20, §12).

La pregunta de la tarea 13 del port: ¿vale la pena llevar `Smoothed`, `DecibelRamp` y `FadeGate` a
Rust? Se miden las llamadas que el motor hace en cada bloque (`motor.procesar`), con los valores por
defecto de la cadena, y nada más (lo que el motor hace con el resultado, `10 ** (dB / 20)` y los
productos, es del motor y no cambia con el port):

- una vez por bloque: `_mezcla_ambiente.block`, `_corte.block`, y `block_db` de `_volumen`,
  `_compensacion` y `_makeup`;
- por parlante: `_pan[...].block`, `_ambiente[...].block` y `_activo[...].block`, con la
  asignación de `target` que el motor hace antes de cada una.

Tres escenarios, con 4 y 8 parlantes:

- **reposo**: todo en su objetivo (el caso de casi todo el tiempo: cada rampa devuelve un escalar);
- **moviendo**: todas a la vez a su velocidad por defecto (pan, ambiente y mezcla a 2/s, volumen,
  compensación y makeup a 30 dB/s, silencio de 50 ms) y el corte bajando o subiendo: el peor caso,
  que en uso real dura lo que tarda una rampa (un par de bloques);
- **deslizando**: un fundido de transición (`glide` de 4096 muestras en cada rampa, como
  `motor._empezar_transicion`), repetido.

Mediana y p95 de 500 bloques tras 50 de calentamiento.

    nice -n 19 $(hatch env find hatch-test.py3.12)/bin/python ../probes/20-costo-sinc-rust/costo_rampas.py
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

from costo_fir import BLOCK, SR, cronometrar  # noqa: E402

from aurasync.dsp import backend, ramps  # noqa: E402

MOVE, VOLUME, MUTE_MS = 2.0, 30.0, 50.0
"""Los valores por defecto de `move_speed`, `volume_speed_db_s` y `mute_fade_ms` (`chain.py`)."""


class Rampas:
    """Las rampas del motor, con los mismos constructores."""

    def __init__(self, parlantes: int) -> None:
        self.mezcla = ramps.Smoothed(0.5, MOVE, SR)
        self.corte = ramps.FadeGate(SR)
        self.volumen = ramps.DecibelRamp(-20.0, VOLUME, SR)
        self.compensacion = ramps.DecibelRamp(0.0, VOLUME, SR)
        self.makeup = ramps.DecibelRamp(0.0, VOLUME, SR)
        self.pan = [ramps.Smoothed(0.0, MOVE, SR) for _ in range(parlantes)]
        self.ambiente = [ramps.Smoothed(0.3, MOVE, SR) for _ in range(parlantes)]
        self.activo = [ramps.Smoothed(1.0, 1000 / MUTE_MS, SR) for _ in range(parlantes)]
        self.objetivos_pan = [0.0] * parlantes
        self.objetivos_amb = [0.3] * parlantes
        self.objetivos_act = [1.0] * parlantes

    def bloque(self, n: int) -> None:
        """Lo que `motor.procesar` les pide en un bloque."""
        self.mezcla.block(n)
        self.corte.block(n)
        self.volumen.block_db(n)
        self.compensacion.block_db(n)
        self.makeup.block_db(n)
        for k in range(len(self.pan)):
            self.pan[k].target, self.ambiente[k].target = self.objetivos_pan[k], self.objetivos_amb[k]
            self.pan[k].block(n)
            self.ambiente[k].block(n)
            self.activo[k].target = self.objetivos_act[k]
            self.activo[k].block(n)

    def mover(self) -> None:
        """Que todas sigan moviéndose: la que llegó vuelve hacia el otro extremo."""
        for s, lejos in ((self.mezcla, (0.0, 1.0)),):
            if s.settled:
                s.target = lejos[1] if s.current == lejos[0] else lejos[0]
        for r, (a, b) in ((self.volumen, (-40.0, 0.0)), (self.compensacion, (-6.0, 6.0)), (self.makeup, (-6.0, 6.0))):
            if r.current_db == r.target_db:
                r.target_db = b if r.current_db == a else a
        if not self.corte.busy:
            self.corte.request()
        for k in range(len(self.pan)):
            if self.pan[k].settled:
                self.objetivos_pan[k] = 1.0 if self.pan[k].current == -1.0 else -1.0
            if self.ambiente[k].settled:
                self.objetivos_amb[k] = 1.0 if self.ambiente[k].current == 0.0 else 0.0
            if self.activo[k].settled:
                self.objetivos_act[k] = 0.0 if self.activo[k].current == 1.0 else 1.0

    def deslizar(self, largo: int) -> None:
        """Un fundido de transición nuevo en cada rampa (`motor._empezar_transicion`)."""
        for k in range(len(self.pan)):
            for s, objetivos in ((self.pan[k], self.objetivos_pan), (self.ambiente[k], self.objetivos_amb)):
                objetivos[k] = 0.5 if s.current != 0.5 else -0.5  # noqa: PLR2004
                s.target = objetivos[k]
                s.glide(largo)
        self.mezcla.target = 0.2 if self.mezcla.current != 0.2 else 0.8  # noqa: PLR2004
        self.compensacion.target_db = 3.0 if self.compensacion.current_db != 3.0 else -3.0  # noqa: PLR2004
        self.makeup.target_db = 2.0 if self.makeup.current_db != 2.0 else -2.0  # noqa: PLR2004
        for r in (self.mezcla, self.compensacion, self.makeup):
            r.glide(largo)


def caso(escenario: str, parlantes: int) -> tuple[float, float]:
    r = Rampas(parlantes)

    def paso(_i: int) -> None:
        if escenario == "moviendo":
            r.mover()
        elif escenario == "deslizando":
            r.deslizar(BLOCK)
        r.bloque(BLOCK)

    return cronometrar(paso)


def sh(*cmd: str) -> str:
    return subprocess.run(cmd, capture_output=True, text=True, check=False).stdout.strip()


def main() -> None:
    backend.reset()
    backend.use(backend.NUMPY)
    print("uptime antes:", sh("uptime"))
    print(f"load1 antes: {os.getloadavg()[0]:.2f}")
    print(f"equipo {platform.node()}, {platform.platform()}, {platform.processor() or platform.machine()}")
    print(f"python {platform.python_version()}, numpy {np.__version__}, cpus {os.cpu_count()}")
    print(f"bloque {BLOCK} muestras = {BLOCK / 48:.1f} ms; motor numpy (las rampas no tienen port)")
    cab = ("escenario", "parlantes", "llamadas", "np med", "np p95")
    print(" ".join(f"{c:>10}" for c in cab))
    for escenario in ("reposo", "moviendo", "deslizando"):
        for n in (4, 8):
            med, p95 = caso(escenario, n)
            print(f"{escenario:>10} {n:>10} {5 + 3 * n:>10} {med:>10.4f} {p95:>10.4f}")
    print("uptime despues:", sh("uptime"))
    print(f"load1 despues: {os.getloadavg()[0]:.2f}")


if __name__ == "__main__":
    main()
