#!/usr/bin/env python3
"""Comprueba el criterio de terminado de P1: que la captura no deje huella.

**Qué dice el criterio** (roadmap, i-7c8794-fd5f03): tras un `kill -9` en plena captura,
`~/.local/state/wireplumber/` y la salida por defecto quedan **byte a byte** como estaban.

**Por qué `kill -9` y no un cierre ordenado.** Un cierre ordenado prueba poco: cualquier
programa limpia bien cuando se le da la oportunidad. Lo que importa es qué queda cuando el
proceso muere sin poder ejecutar nada, que es lo que pasa si el programa falla o si alguien
lo mata. La tarjeta *cleanup-belongs-to-the-supervisor* dice justamente eso: la limpieza
tiene que ser responsabilidad de quien supervisa, no de código que puede no llegar a correr.

**Lo que se vigila, y por qué esos dos:**

- **el estado de WirePlumber**, porque ahí persiste qué salida quedó por defecto. Si crear
  un dispositivo nuevo lo cambia y el cambio se guarda, el usuario se queda con el audio
  ruteado a un dispositivo que ya no existe;
- **la salida por defecto en vivo**, porque el daño puede ocurrir sin tocar el disco.

No reproduce ni graba nada audible: solo crea el nodo y lo mata.

Uso:  probes/p1-huella/verificar.py
"""

from __future__ import annotations

import hashlib
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

ESTADO = Path.home() / ".local/state/wireplumber"


def huella_del_estado() -> dict[str, str]:
    """Un hash por archivo del estado de WirePlumber."""
    if not ESTADO.exists():
        return {}
    huellas = {}
    for ruta in sorted(ESTADO.rglob("*")):
        if ruta.is_file():
            huellas[str(ruta.relative_to(ESTADO))] = hashlib.sha256(ruta.read_bytes()).hexdigest()
    return huellas


def salida_por_defecto() -> str:
    return subprocess.run(
        ["pactl", "get-default-sink"], capture_output=True, text=True, check=False
    ).stdout.strip()


def sinks() -> set[str]:
    salida = subprocess.run(
        ["pactl", "list", "short", "sinks"], capture_output=True, text=True, check=False
    ).stdout
    return {linea.split("\t")[1] for linea in salida.splitlines() if "\t" in linea}


def comparar(antes: dict[str, str], despues: dict[str, str]) -> list[str]:
    diferencias = []
    for clave in sorted(set(antes) | set(despues)):
        a, d = antes.get(clave), despues.get(clave)
        if a is None:
            diferencias.append(f"apareció {clave}")
        elif d is None:
            diferencias.append(f"desapareció {clave}")
        elif a != d:
            diferencias.append(f"cambió {clave}")
    return diferencias


def main() -> int:
    nombre = "aurasync_huella"
    print("# criterio de terminado de P1: la captura no deja huella")
    print(f"# estado vigilado: {ESTADO}\n")

    estado_antes = huella_del_estado()
    defecto_antes = salida_por_defecto()
    sinks_antes = sinks()
    print(f"antes:   {len(estado_antes)} archivos de estado · salida por defecto: {defecto_antes}")

    propiedades = (
        f"{{ media.class=Audio/Sink node.name={nombre} "
        f'node.description="aurasync (prueba de huella)" audio.position=[FL FR] }}'
    )
    proceso = subprocess.Popen(
        ["pw-record", "-P", propiedades, "--rate", "48000", "--channels", "2",
         "--format", "s16", "--raw", "-"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(3)

    presente = nombre in sinks()
    defecto_durante = salida_por_defecto()
    print(f"durante: el dispositivo {'aparece' if presente else 'NO aparece'}"
          f" · salida por defecto: {defecto_durante}")
    if not presente:
        print("\nEl nodo no llegó a crearse: la prueba no vale.", file=sys.stderr)
        proceso.kill()
        return 1

    # Aquí está el punto de la prueba: no se le da ninguna oportunidad de limpiar.
    os.kill(proceso.pid, signal.SIGKILL)
    proceso.wait(timeout=10)
    time.sleep(3)

    estado_despues = huella_del_estado()
    defecto_despues = salida_por_defecto()
    quedo = nombre in sinks()
    sinks_despues = sinks()

    print(f"después: el dispositivo {'QUEDÓ' if quedo else 'desapareció'}"
          f" · salida por defecto: {defecto_despues}\n")

    problemas = []
    if quedo:
        problemas.append("el dispositivo sobrevivió al proceso")
    if sinks_despues != sinks_antes:
        problemas.append(f"la lista de salidas cambió: {sinks_despues ^ sinks_antes}")
    if defecto_despues != defecto_antes:
        problemas.append(f"la salida por defecto cambió: {defecto_antes} → {defecto_despues}")
    diferencias = comparar(estado_antes, estado_despues)
    if diferencias:
        problemas.append(f"el estado de WirePlumber cambió: {diferencias}")

    if problemas:
        print("NO cumple el criterio:")
        for p in problemas:
            print(f"  ✗ {p}")
        return 1
    print("Cumple el criterio: tras un kill -9, nada quedó distinto.")
    print("  - el dispositivo desapareció solo")
    print("  - la lista de salidas y la salida por defecto quedaron iguales")
    print(f"  - los {len(estado_antes)} archivos de estado de WirePlumber, byte a byte iguales")
    return 0


if __name__ == "__main__":
    sys.exit(main())
