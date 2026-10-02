"""Ensayo en seco del paso 3: la misma secuencia de cambios, sin parlantes ni PipeWire.

Corre el **servicio real** (`Service`, `control.py`, el motor) con una sesión falsa que, en
vez de leer del sink virtual y escribir a `pw-play`, procesa la señal de `senal.py` y guarda
lo que le habría mandado a cada parlante. Después busca clics en esa salida digital con
`clics.py`. Sin aire ni micrófono de por medio no hay ruido de la sala: cualquier pico que
aparezca es un defecto del programa.

También cuenta los cortes (fundidos a cero) y los compara con los que la secuencia espera,
para que la lista de "con corte / sin corte" que se le dice al oyente sea la verdadera.

**Se vio fallar:** con `--romper`, el suavizado de `pan` y `ambiente` se vuelve instantáneo
y el ensayo tiene que encontrar clics.

Uso (desde host/): hatch run python ../probes/10-servicio-de-control/seco.py [--romper]
"""

import json
import sys
import tempfile
import threading
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import clics  # noqa: E402
import senal  # noqa: E402

from aurasync.config import Instalacion, Parlante  # noqa: E402
from aurasync.service import Service  # noqa: E402

SR = 48000
BLOQUE = 4096
SEGUNDOS_POR_CAMBIO = 3.0
RAPIDEZ = 20.0
"""Cuántas veces más rápido que el tiempo real corre el ensayo."""

AQUI = Path(__file__).parent
CAMBIOS = json.loads((AQUI / "cambios-paso3.json").read_text())
ROMPER = "--romper" in sys.argv


class SesionDeEnsayo:
    """La forma de `AudioSession`, pero con la señal de prueba en vez de PipeWire."""

    instancia = None

    def __init__(self, _instalacion, motor, _opciones, _log):
        self.motor = motor
        self.lost, self.routing_repairs, self.last_recalibration = [], 0, None
        self.entrada = senal.generar(SEGUNDOS_POR_CAMBIO * (len(CAMBIOS) + 2))
        self.pos = 0
        self.salida: dict[str, list[np.ndarray]] = {}
        self.cortes = 0
        self._en_corte = False
        SesionDeEnsayo.instancia = self
        if ROMPER:
            for suave in [*motor._pan.values(), *motor._ambiente.values()]:  # noqa: SLF001
                suave.rate = 1e9

    def open(self):
        pass

    def close(self):
        pass

    def step(self):
        if self.pos >= len(self.entrada):
            time.sleep(0.001)
            return
        trozo = self.entrada[self.pos : self.pos + BLOQUE]
        self.pos += BLOQUE
        for nombre, x in self.motor.procesar(trozo[:, 0], trozo[:, 1]).items():
            self.salida.setdefault(nombre, []).append(x)
        if self.motor.en_corte and not self._en_corte:
            self.cortes += 1
        self._en_corte = self.motor.en_corte
        time.sleep(BLOQUE / SR / RAPIDEZ)


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        inst = Path(tmp) / "instalacion.json"
        Instalacion(
            parlantes=[
                Parlante("JBL Go 4 Red", "s0", pan=-0.7, ambiente=0.15),
                Parlante("JBL Go 4 Black", "s1", pan=0.7, ambiente=0.15),
                Parlante("JBL Go 4 Blue", "s2", pan=0.0, ambiente=0.55),
            ]
        ).guardar(inst)
        servicio = Service(inst, Path(tmp) / "presets.json", session_factory=SesionDeEnsayo, log=lambda _: None)
        motor_hilo = threading.Thread(target=servicio.run, daemon=True)
        motor_hilo.start()

        def pedir(**mensaje):
            r = servicio.handle({"v": 1, **mensaje})
            if not r["ok"]:
                print(f"  ✗ {mensaje}: {r['error']}")
            return r

        pedir(op="start")
        sesion = SesionDeEnsayo.instancia
        momentos = []
        time.sleep(SEGUNDOS_POR_CAMBIO / RAPIDEZ)
        for cambio in CAMBIOS:
            partes = cambio["ruta"].strip("/").split("/")
            antes = sesion.cortes
            if partes[0] == "speakers":
                pedir(op="set", speaker=partes[1].replace("%20", " "), changes=cambio["cuerpo"])
            else:
                pedir(op="set", changes=cambio["cuerpo"])
            momentos.append(sesion.pos / SR)
            time.sleep(SEGUNDOS_POR_CAMBIO / RAPIDEZ)
            cambio["_cortes"] = sesion.cortes - antes
        while sesion.pos < len(sesion.entrada):
            time.sleep(0.01)
        pedir(op="shutdown")
        motor_hilo.join(timeout=5)

    malos = 0
    print("  cortes por cambio (lo que oirá el oyente):")
    for cambio in CAMBIOS:
        espera_corte = "CON un corte" in cambio["escuchar"] or "corte breve" in cambio["escuchar"]
        hubo = cambio["_cortes"] > 0
        ok = espera_corte == hubo
        malos += not ok
        print(f"    {'✓' if ok else '✗'} {json.dumps(cambio['cuerpo']):<28} corte: {'sí' if hubo else 'no'}")
    print("  clics en la salida digital:")
    for nombre, trozos in sesion.salida.items():
        x = np.concatenate(trozos)
        encontrados = clics.picos(x)
        malos += len(encontrados)
        detalle = ", ".join(f"{s:.2f} s (+{e:.0f} dB)" for s, e in encontrados) or "ninguno"
        print(f"    {'✓' if not encontrados else '✗'} {nombre:<16} {detalle}")
    print(f"  {'✓ el ensayo en seco pasa' if not malos else f'✗ {malos} problema(s)'}")
    return 1 if malos else 0


if __name__ == "__main__":
    sys.exit(main())
