"""¿Dónde nacen los saltos de un cuantum: en el PC o en el Bluetooth y el parlante?

Graba **a la vez y en una sola captura** el monitor de los tres sinks Bluetooth (lo que cada
sink recibe antes de mandarlo por el aire) mientras el servicio calibra. Cada canal del
monitor se correlaciona con la referencia de su parlante (del volcado de la calibración):
la llegada en el monitor, menos lo que ya se le aplicó, es el desfase **del lado del PC**.
Comparado con lo que mide el micrófono en la misma calibración:

- si el salto de 42,67 ms está en el monitor, nace en el PC (pw-play o el grafo) y el propio
  programa lo puede ver sin micrófono;
- si no está, nace en el Bluetooth o en el parlante.

Una sola captura de 3 canales, y no tres `pw-record`: tres procesos arrancan en instantes
distintos y sus relojes de inicio no se pueden comparar.

Uso: python3 monitores.py [repeticiones]
"""

import json
import subprocess
import sys
import time
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import campana  # noqa: E402

SR = 48000
NOMBRE = "aurasync-sonda-monitores"


def gcc(x: np.ndarray, ref: np.ndarray, maximo_s: float = 3.0) -> tuple[float, float]:
    n = 1 << int(np.ceil(np.log2(len(x) + len(ref))))
    espectro = np.fft.rfft(x, n) * np.conj(np.fft.rfft(ref, n))
    espectro /= np.abs(espectro) + 1e-12
    c = np.fft.irfft(espectro, n)[: int(maximo_s * SR)]
    k = int(np.argmax(np.abs(c)))
    orden = np.sort(np.abs(c))
    # Interpolación parabólica alrededor del pico.
    if 0 < k < len(c) - 1:
        a, b, d = c[k - 1], c[k], c[k + 1]
        k = k + 0.5 * (a - d) / (a - 2 * b + d)
    return k / SR * 1000, float(orden[-1] / orden[-200])


def capturar(wav: Path) -> subprocess.Popen:
    proceso = subprocess.Popen(
        ["pw-record", "-P", f"{{ node.name={NOMBRE} node.autoconnect=false }}", "--channels", "3",
         "--rate", str(SR), "--format", "s16", str(wav)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    time.sleep(1.0)
    entradas = sorted(p for p in subprocess.run(["pw-link", "-i"], capture_output=True, text=True, check=False).stdout.split() if p.startswith(NOMBRE + ":"))
    salidas = subprocess.run(["pw-link", "-o"], capture_output=True, text=True, check=False).stdout.split()
    for direccion, entrada in zip(campana.protocolo.DIRECCIONES, entradas, strict=False):
        prefijo = f"bluez_output.{direccion.replace(':', '_')}.1:monitor_"
        # El primer canal del monitor: monitor_FL en estéreo, monitor_MONO en mono.
        salida = sorted(o for o in salidas if o.startswith(prefijo))[0]
        subprocess.run(["pw-link", salida, entrada], check=False, capture_output=True)
    time.sleep(0.3)
    enlaces = subprocess.run(["pw-link", "-l"], capture_output=True, text=True, check=False).stdout
    if enlaces.count(NOMBRE) < 3:  # noqa: PLR2004
        proceso.terminate()
        raise RuntimeError(f"no se pudieron enlazar los monitores: {entradas}")
    return proceso


def una(segundos: float) -> dict:
    wav = campana.DATOS / f"monitores-{time.strftime('%H%M%S')}.wav"
    captura = capturar(wav)
    try:
        filas = campana.calibrar(segundos, "monitores: misma calibración por el aire y por el monitor")
        volcado = Path(campana.orden("calibration_dump")["path"])
    finally:
        captura.send_signal(2)
        captura.wait(timeout=10)
    d = np.load(volcado)
    nombres = [str(n) for n in d["names"]]
    with wave.open(str(wav)) as w:
        x = np.frombuffer(w.readframes(w.getnframes()), "<i2").astype(float).reshape(-1, 3) / 32768
    pc, calidad = {}, {}
    for i, n in enumerate(nombres):
        llegada, q = gcc(x[:, i], d["references"][i])
        pc[n] = llegada - float(d["applied"][i][0])   # desfase del lado del PC, sin lo aplicado
        calidad[n] = q
    aire = campana.llegadas(filas)
    base = nombres[0]
    resultado = {
        "pc_ms": {n: round(pc[n] - pc[base], 2) for n in nombres},
        "aire_ms": {n: round(aire[n] - aire[base], 2) for n in nombres},
        "calidad_monitor": {n: round(calidad[n], 1) for n in nombres},
        "wav": wav.name,
        "volcado": volcado.name,
    }
    campana.anotar("monitores", **resultado)
    print(f"    PC (monitor):       {resultado['pc_ms']}  calidad {resultado['calidad_monitor']}")
    print(f"    aire (micrófono):   {resultado['aire_ms']}")
    return resultado


def main() -> int:
    repeticiones = int(sys.argv[1]) if len(sys.argv) > 1 else 6
    campana.preparar()
    print(f"registro: {campana.REGISTRO}")
    for i in range(repeticiones):
        print(f"\n· {i + 1}/{repeticiones}")
        una(10.0)
        time.sleep(3.0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
