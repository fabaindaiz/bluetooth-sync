#!/usr/bin/env python3
"""Mide con micrófono el desfase acústico entre dos parlantes (E6, i-7c8794-24ea65).

El método: se reproduce, **en el mismo instante**, un tono corto distinto por canal
—f1 en FL y f2 en FR— por el sink combinado que manda un canal a cada parlante. El
micrófono graba los dos. Separando la grabación en dos bandas se sabe qué ráfaga vino
de qué parlante, y la diferencia entre sus arranques es el desfase.

    Δt = arranque(f2) − arranque(f1)

Δt positivo significa que el parlante de FR llegó tarde.

**Lo que hay que cuidar, o el número no vale nada:**
- El micrófono tiene que estar **equidistante** de los dos parlantes. El sonido viaja
  a ~343 m/s, así que **cada 34 cm de diferencia son 1 ms** que se suma al desfase
  real. Con el micrófono a igual distancia, ese término se va.
- La precisión del método es de ~1 ms, por el ancho de los filtros (ver PRECISION_MS).
  Alcanza para los umbrales del proyecto (5 ms para estéreo, 20 ms para traseros),
  pero no para discutir microsegundos.
- Se repite N veces porque en A2DP lo que importa no es un valor sino **la dispersión**
  y si cambia entre arranques de reproducción.

Uso:
    probes/e6-a2dp/medir-desfase.py --repeticiones 10
    probes/e6-a2dp/medir-desfase.py --mic <fuente> --sink jbl_combine
"""

from __future__ import annotations

import argparse
import math
import struct
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path

import numpy as np

SR = 48000
BANDA_HZ = 500.0  # semiancho del filtro por banda
PRECISION_MS = 1000.0 / (2 * BANDA_HZ)  # ~1 ms
DUR_RAFAGA = 0.060
PERIODO = 1.000
SILENCIO_INICIAL = 1.0
UMBRAL_REL = 0.2  # el arranque se toma al 20% del pico de esa ráfaga


def generar_wav(ruta: Path, f1: float, f2: float, n: int, amp: float) -> None:
    """Un WAV estéreo con n ráfagas simultáneas: f1 en el canal izquierdo, f2 en el derecho."""
    total = int(SR * (SILENCIO_INICIAL + n * PERIODO))
    izq = np.zeros(total)
    der = np.zeros(total)
    largo = int(SR * DUR_RAFAGA)
    # Ventana Hann: sin ella, el golpe del arranque ensucia todas las bandas.
    ventana = np.hanning(largo)
    t = np.arange(largo) / SR
    for k in range(n):
        ini = int(SR * (SILENCIO_INICIAL + k * PERIODO))
        izq[ini : ini + largo] = amp * ventana * np.sin(2 * math.pi * f1 * t)
        der[ini : ini + largo] = amp * ventana * np.sin(2 * math.pi * f2 * t)
    inter = np.empty(total * 2)
    inter[0::2] = izq
    inter[1::2] = der
    pcm = (np.clip(inter, -1, 1) * 32767).astype("<i2")
    with wave.open(str(ruta), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


def leer_mono(ruta: Path) -> np.ndarray:
    with wave.open(str(ruta)) as w:
        datos = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(float) / 32768
        if w.getnchannels() > 1:
            datos = datos.reshape(-1, w.getnchannels()).mean(axis=1)
        if w.getframerate() != SR:
            msg = f"la grabación es a {w.getframerate()} Hz y se esperaba {SR}"
            raise SystemExit(msg)
    return datos


def envolvente(x: np.ndarray, centro: float, banda: float = BANDA_HZ) -> np.ndarray:
    """Envolvente de la señal analítica, filtrada alrededor de `centro`.

    Se hace por FFT: se dejan solo los bins de la banda y del lado positivo del
    espectro, y el módulo de la inversa es la envolvente. Equivale a un pasabanda más
    una transformada de Hilbert, sin depender de scipy.

    `banda` es el semiancho. Con más canales hay que bajarlo para que las bandas no se
    solapen, a costa de precisión temporal: la resolución es ~1/(2·banda).
    """
    n = len(x)
    esp = np.fft.fft(x)
    frec = np.fft.fftfreq(n, 1 / SR)
    mascara = (frec > centro - banda) & (frec < centro + banda)
    esp_filtrado = np.zeros_like(esp)
    esp_filtrado[mascara] = esp[mascara] * 2  # solo frecuencias positivas → analítica
    return np.abs(np.fft.ifft(esp_filtrado))


def arranque(env: np.ndarray, ini: int, fin: int) -> float | None:
    """Índice (fraccionario) donde la envolvente cruza el 20% del pico de esa ventana."""
    trozo = env[ini:fin]
    if len(trozo) == 0:
        return None
    pico = trozo.max()
    if pico <= 0:
        return None
    umbral = UMBRAL_REL * pico
    idx = np.argmax(trozo >= umbral)
    if trozo[idx] < umbral:
        return None
    if idx == 0:
        return float(ini)
    # Interpolación lineal entre la muestra previa y la que cruza: gana precisión
    # sin cambiar el método.
    y0, y1 = trozo[idx - 1], trozo[idx]
    frac = 0.0 if y1 == y0 else (umbral - y0) / (y1 - y0)
    return float(ini + idx - 1 + frac)


def detectar_rafagas(e1: np.ndarray, e2: np.ndarray) -> list[int]:
    """Índices donde arranca cada ráfaga, deduplicados.

    Un umbral simple sobre la envolvente dispara varias veces por ráfaga: dentro del
    tono la envolvente ondula y baja del umbral. Por eso los flancos que caen dentro de
    medio período se toman como la misma ráfaga. Sin esto, una medición de 6 ráfagas
    devuelve 13 filas con valores repetidos.
    """
    suma = e1 + e2
    encendido = suma > 0.25 * suma.max()
    flancos = np.flatnonzero(np.diff(encendido.astype(int)) == 1)
    minimo = int(0.5 * PERIODO * SR)
    limpios: list[int] = []
    for b in flancos:
        if not limpios or b - limpios[-1] >= minimo:
            limpios.append(int(b))
    return limpios


# El margen ANTES del disparo tiene que ser chico: el disparo lo produce la ráfaga que
# llega primero, así que atrás no hay nada que buscar, y un margen grande alcanza la cola
# de la ráfaga anterior. Con un desfase real de ~55 ms y 150 ms de margen, eso producía
# lecturas de −82 ms. El margen DESPUÉS sí tiene que ser amplio, para no cortar un
# parlante que llegue muy tarde.
MARGEN_ANTES = 0.060
MARGEN_DESPUES = 0.450


def medir(x: np.ndarray, f1: float, f2: float) -> tuple[list[tuple[float, float, float]], int]:
    """Devuelve [(t_FL, t_FR, Δt)] en ms, y cuántas ráfagas se descartaron."""
    e1, e2 = envolvente(x, f1), envolvente(x, f2)
    margen = int(MARGEN_ANTES * SR)
    # Una ráfaga sirve solo si las DOS bandas tienen un pico claro: así no se cuela
    # ruido de sala como si fuera un parlante.
    minimo_pico = 0.2
    filas, descartadas = [], 0
    for b in detectar_rafagas(e1, e2):
        ini, fin = max(0, b - margen), min(len(x), b + int(MARGEN_DESPUES * SR))
        if e1[ini:fin].max() < minimo_pico * e1.max() or e2[ini:fin].max() < minimo_pico * e2.max():
            descartadas += 1
            continue
        a1, a2 = arranque(e1, ini, fin), arranque(e2, ini, fin)
        if a1 is None or a2 is None:
            descartadas += 1
            continue
        t1, t2 = a1 / SR * 1000, a2 / SR * 1000
        filas.append((t1, t2, t2 - t1))
    return filas, descartadas


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sink", default="jbl_combine")
    ap.add_argument("--mic", default="alsa_input.usb-3142_fifine_Microphone-00.analog-stereo")
    ap.add_argument("--repeticiones", type=int, default=10)
    ap.add_argument("--f1", type=float, default=1000.0, help="tono del canal FL")
    ap.add_argument("--f2", type=float, default=3000.0, help="tono del canal FR")
    ap.add_argument("--amplitud", type=float, default=0.35)
    ap.add_argument("--guardar", type=Path, help="dónde dejar la grabación")
    args = ap.parse_args()

    tmp = Path(tempfile.mkdtemp())
    estimulo = tmp / "estimulo.wav"
    grabacion = args.guardar or (tmp / "grabacion.wav")
    generar_wav(estimulo, args.f1, args.f2, args.repeticiones, args.amplitud)
    dur = SILENCIO_INICIAL + args.repeticiones * PERIODO

    print(f"# desfase entre parlantes — {time.strftime('%Y-%m-%dT%H:%M:%S%z')}")
    print(f"# sink={args.sink}  mic={args.mic}")
    print(f"# FL={args.f1:.0f} Hz  FR={args.f2:.0f} Hz  {args.repeticiones} ráfagas  ~{dur:.0f} s")
    print(f"# precisión del método: ~{PRECISION_MS:.1f} ms. El micrófono TIENE que estar")
    print("# equidistante: cada 34 cm de diferencia mete 1 ms de error.\n")

    rec = subprocess.Popen(
        ["pw-record", "--target", args.mic, "--rate", str(SR), "--channels", "1",
         "--format", "s16", str(grabacion)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    time.sleep(0.7)  # que el nodo arranque antes de empezar a reproducir
    try:
        subprocess.run(["pw-play", "--target", args.sink, str(estimulo)], check=True)
    finally:
        time.sleep(0.7)
        rec.send_signal(2)  # SIGINT: pw-record cierra el WAV bien
        rec.wait(timeout=10)

    x = leer_mono(grabacion)
    filas, descartadas = medir(x, args.f1, args.f2)

    print(f"{'#':>3}  {'FL (ms)':>9}  {'FR (ms)':>9}  {'Δt (ms)':>9}")
    for k, (t1, t2, d) in enumerate(filas, 1):
        print(f"{k:>3}  {t1:>9.2f}  {t2:>9.2f}  {d:>+9.2f}")
    deltas = [f[2] for f in filas]
    if descartadas:
        print(f"\n({descartadas} ráfagas descartadas por pico débil en una de las bandas)")

    if not deltas:
        print("\nNo se detectó ninguna ráfaga. Subí el volumen, acercá el micrófono,")
        print("y confirmá que los dos parlantes están sonando.")
        print(f"La grabación quedó en {grabacion}")
        return 1

    d = np.array(deltas)
    print(f"\nráfagas medidas: {len(d)} de {args.repeticiones}")
    if len(d) != args.repeticiones:
        print("  (si difiere mucho, revisá volumen, ruido de sala y posición del micrófono)")
    mediana = float(np.median(d))
    # MAD escalada: el equivalente robusto del desvío estándar. Si difiere mucho del
    # desvío, hay detecciones malas y hay que mirar la tabla de arriba.
    mad = float(np.median(np.abs(d - mediana))) * 1.4826
    print(f"Δt mediana     : {mediana:+.2f} ms   ← el valor a usar")
    print(f"Δt medio       : {d.mean():+.2f} ms")
    print(f"MAD escalada   : {mad:.2f} ms   ← dispersión robusta")
    print(f"desvío estándar: {d.std(ddof=1) if len(d) > 1 else 0:.2f} ms")
    print(f"mínimo / máximo: {d.min():+.2f} / {d.max():+.2f} ms   (rango {d.max() - d.min():.2f} ms)")
    sospechosas = np.flatnonzero(np.abs(d - mediana) > max(5.0, 4 * mad))
    if len(sospechosas):
        print(f"ráfagas sospechosas (lejos de la mediana): {[int(i) + 1 for i in sospechosas]}")
    print(f"\ngrabación: {grabacion}")
    print("\nUmbrales del roadmap (E5): <5 ms para una imagen estéreo, <20 ms para traseros.")
    print("Lo que decide en A2DP no es el promedio sino el rango y si cambia entre")
    print("arranques de reproducción: un desfase fijo se compensa, uno variable no.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
