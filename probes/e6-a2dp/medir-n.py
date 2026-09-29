#!/usr/bin/env python3
"""Mide el desfase acústico entre N parlantes a la vez (E6/E5, i-7c8794-24ea65).

Extiende `medir-desfase.py` a más de dos parlantes, reusando su DSP. Se reproduce una
ráfaga simultánea con **un tono distinto por canal** del sink combinado, y el micrófono
graba todas. Separando la grabación en una banda por tono se sabe qué ráfaga vino de qué
parlante, y todos los desfases se informan **respecto del canal de referencia**.

Con el micrófono quieto, el término de distancia de cada parlante es un corrimiento
constante: contamina el valor absoluto de ese parlante, pero **no** la variación entre
reproducciones, que es lo que decide si hace falta recalibrar.

Uso:
    probes/e6-a2dp/medir-n.py --repeticiones 10
    probes/e6-a2dp/medir-n.py --canales "FL:1000:Go 4 Black,FR:2000:Go 4 Red"
"""

from __future__ import annotations

import argparse
import importlib.util
import math
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path

import numpy as np

# Semiancho de cada banda. Con centros cada 1000 Hz, 400 Hz deja 200 Hz de guarda entre
# bandas vecinas. La resolución temporal es ~1/(2·banda) ≈ 1,25 ms.
BANDA = 400.0
# Resolución temporal del filtro: ~1/(2·banda) = 1,25 ms. Es el piso teórico.
PRECISION_MS = 1000.0 / (2 * BANDA)
# Lo que el test demuestra de verdad, con `test-medir-n.py`:
#   - señal limpia: error < 0,05 ms;
#   - sala muy reverberante (primer eco a −7 dB a los 11 ms): error hasta ~2,2 ms.
# El error con reverberación NO baja restando la base ni subiendo el umbral del flanco al
# 50 % (se probaron los dos, y el 50 % empeora a 3,7 ms). Tampoco sirve medir el pico en
# vez del flanco: con eco el pico se corre y el error sube a 7,6 ms.
PRECISION_REVERB_MS = 2.5

# Las frecuencias NO pueden estar en relación armónica. Con 1000/2000/3000/4000, si un
# parlante se queda sin stream su banda capta el armónico de otro y la medición devuelve
# un falso ~0 ms con dispersión bajísima, que parece un resultado excelente. Pasó de
# verdad con el Go 4 Blue en 3000 Hz (3.º armónico de los 1000 Hz del Black).
#
# Este conjunto se buscó exigiendo: bandas sin solaparse (con guarda de 100 Hz) y ningún
# 2.º, 3.er ni 4.º armónico de un tono dentro de la banda de otro. Es el más compacto que
# cumple, para que la eficiencia del parlante no varíe demasiado entre canales.
FRECUENCIAS = (2350.0, 3250.0, 4150.0, 5200.0)
CANALES_POR_DEFECTO = (
    f"FL:{FRECUENCIAS[0]:.0f}:Go 4 Black,FR:{FRECUENCIAS[1]:.0f}:Go 4 Red,"
    f"RL:{FRECUENCIAS[2]:.0f}:Go 4 Blue,RR:{FRECUENCIAS[3]:.0f}:Charge 6"
)

# Un canal cuyo pico queda muy por debajo de la mediana de los demás no está sonando: lo
# que se ve es fuga. Con armónicos a −30 dB (0,032) y este margen, se rechaza.
NIVEL_MINIMO_RELATIVO = 0.15


def cargar_dsp():
    ruta = Path(__file__).with_name("medir-desfase.py")
    spec = importlib.util.spec_from_file_location("medir_desfase", ruta)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def parsear_canales(texto: str) -> list[tuple[str, float, str]]:
    canales = []
    for parte in texto.split(","):
        trozos = parte.split(":")
        if len(trozos) < 2:
            msg = f"canal mal escrito: {parte!r}; formato POSICION:FRECUENCIA[:nombre]"
            raise SystemExit(msg)
        pos, frec = trozos[0].strip(), float(trozos[1])
        nombre = trozos[2].strip() if len(trozos) > 2 else pos
        canales.append((pos, frec, nombre))
    return canales


def generar_wav(m, ruta: Path, canales, n: int, amp: float) -> None:
    total = int(m.SR * (m.SILENCIO_INICIAL + n * m.PERIODO))
    largo = int(m.SR * m.DUR_RAFAGA)
    ventana = np.hanning(largo)
    t = np.arange(largo) / m.SR
    pistas = [np.zeros(total) for _ in canales]
    for k in range(n):
        ini = int(m.SR * (m.SILENCIO_INICIAL + k * m.PERIODO))
        for pista, (_, frec, _) in zip(pistas, canales, strict=True):
            pista[ini : ini + largo] = amp * ventana * np.sin(2 * math.pi * frec * t)
    inter = np.empty(total * len(canales))
    for i, pista in enumerate(pistas):
        inter[i :: len(canales)] = pista
    pcm = (np.clip(inter, -1, 1) * 32767).astype("<i2")
    with wave.open(str(ruta), "wb") as w:
        w.setnchannels(len(canales))
        w.setsampwidth(2)
        w.setframerate(m.SR)
        w.writeframes(pcm.tobytes())


def medir_n(m, x: np.ndarray, canales) -> tuple[list[dict[str, float]], int, dict[str, float]]:
    """Por ráfaga, el instante de arranque de cada canal en ms.

    Devuelve (filas, descartadas, nivel_relativo). `nivel_relativo` es el pico mediano de
    cada canal dividido por la mediana de todos: un valor muy bajo significa que ese
    parlante **no está sonando** y que lo que se ve en su banda es fuga de otro.
    """
    envs = {nombre: m.envolvente(x, frec, BANDA) for _, frec, nombre in canales}
    suma = sum(envs.values())
    encendido = suma > 0.25 * suma.max()
    flancos = np.flatnonzero(np.diff(encendido.astype(int)) == 1)
    minimo = int(0.5 * m.PERIODO * m.SR)
    disparos: list[int] = []
    for b in flancos:
        if not disparos or b - disparos[-1] >= minimo:
            disparos.append(int(b))

    antes, despues = int(m.MARGEN_ANTES * m.SR), int(m.MARGEN_DESPUES * m.SR)
    ventanas = [(max(0, b - antes), min(len(x), b + despues)) for b in disparos]

    # Nivel de cada canal, comparado con el resto. Se calcula antes de medir tiempos,
    # porque si un canal no suena no tiene sentido darle un instante de arranque.
    picos = {n: float(np.median([envs[n][i:f].max() for i, f in ventanas])) if ventanas else 0.0
             for n in envs}
    referencia = float(np.median(list(picos.values()))) if picos else 0.0
    nivel = {n: (p / referencia if referencia > 0 else 0.0) for n, p in picos.items()}
    mudos = {n for n, v in nivel.items() if v < NIVEL_MINIMO_RELATIVO}

    filas, descartadas = [], 0
    for ini, fin in ventanas:
        fila, ok = {}, True
        for nombre, env in envs.items():
            if nombre in mudos or env[ini:fin].max() < 0.2 * env.max():
                ok = False
                break
            a = m.arranque(env, ini, fin)
            if a is None:
                ok = False
                break
            fila[nombre] = a / m.SR * 1000
        if ok:
            filas.append(fila)
        else:
            descartadas += 1
    return filas, descartadas, nivel


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sink", default="jbl_quad")
    ap.add_argument("--mic", default="alsa_input.usb-3142_fifine_Microphone-00.analog-stereo")
    ap.add_argument("--canales", default=CANALES_POR_DEFECTO)
    ap.add_argument("--repeticiones", type=int, default=10)
    ap.add_argument("--amplitud", type=float, default=0.30)
    ap.add_argument("--referencia", help="nombre del canal de referencia (por defecto, el primero)")
    ap.add_argument("--guardar", type=Path)
    args = ap.parse_args()

    m = cargar_dsp()
    canales = parsear_canales(args.canales)
    nombres = [c[2] for c in canales]
    ref = args.referencia or nombres[0]
    if ref not in nombres:
        raise SystemExit(f"la referencia {ref!r} no está entre {nombres}")

    tmp = Path(tempfile.mkdtemp())
    estimulo = tmp / "estimulo.wav"
    grabacion = args.guardar or (tmp / "grabacion.wav")
    generar_wav(m, estimulo, canales, args.repeticiones, args.amplitud)

    print(f"# desfase entre {len(canales)} parlantes — {time.strftime('%Y-%m-%dT%H:%M:%S%z')}")
    print(f"# sink={args.sink}  mic={args.mic}")
    for pos, frec, nombre in canales:
        print(f"#   {pos:3s} {frec:6.0f} Hz  {nombre}")
    print(f"# referencia: {ref} · {args.repeticiones} ráfagas")
    print(f"# precisión: ~{PRECISION_MS:.2f} ms con señal limpia, hasta ~{PRECISION_REVERB_MS:.1f} ms en sala reverberante")
    print("# El micrófono NO se mueve entre corridas: así el término de distancia es")
    print("# constante y no afecta la variación entre reproducciones.\n")

    rec = subprocess.Popen(
        ["pw-record", "--target", args.mic, "--rate", str(m.SR), "--channels", "1",
         "--format", "s16", str(grabacion)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    time.sleep(0.7)
    try:
        subprocess.run(["pw-play", "--target", args.sink, str(estimulo)], check=True)
    finally:
        time.sleep(0.7)
        rec.send_signal(2)
        rec.wait(timeout=10)

    x = m.leer_mono(grabacion)
    filas, descartadas, nivel = medir_n(m, x, canales)

    print("nivel relativo de cada canal (1,0 = como los demás):")
    for _, _, nombre in canales:
        v = nivel.get(nombre, 0.0)
        aviso = "  ← NO SUENA: lo que se ve en su banda es fuga" if v < NIVEL_MINIMO_RELATIVO else ""
        print(f"  {nombre[:14]:<14s} {v:>5.2f}{aviso}")
    if any(v < NIVEL_MINIMO_RELATIVO for v in nivel.values()):
        print("\nHay al menos un parlante sin sonar. Revisá que su transporte A2DP esté vivo:")
        print("  journalctl --user -u wireplumber -b | grep -i 'Acquire.*error'")
    print()

    if not filas:
        print("No se detectó ninguna ráfaga completa. Subí el volumen o acercá el micrófono.")
        print(f"grabación: {grabacion}")
        return 1

    otros = [n for n in nombres if n != ref]
    print("  #  " + "  ".join(f"{n[:12]:>13s}" for n in otros))
    for k, fila in enumerate(filas, 1):
        print(f"{k:>3}  " + "  ".join(f"{fila[n] - fila[ref]:>+13.2f}" for n in otros))

    print(f"\nráfagas medidas: {len(filas)} de {args.repeticiones}" + (f" ({descartadas} descartadas)" if descartadas else ""))
    print(f"\n{'parlante':<14s} {'mediana':>10s} {'MAD':>8s} {'rango':>8s}   (ms, respecto de {ref})")
    for n in otros:
        d = np.array([f[n] - f[ref] for f in filas])
        med = float(np.median(d))
        mad = float(np.median(np.abs(d - med))) * 1.4826
        print(f"{n[:14]:<14s} {med:>+10.2f} {mad:>8.2f} {d.max() - d.min():>8.2f}")
    print("\nEl valor absoluto lleva la distancia del parlante al micrófono (1 ms por 34 cm).")
    print("La MAD y el rango no: son la dispersión real dentro de la reproducción.")
    print(f"\ngrabación: {grabacion}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
