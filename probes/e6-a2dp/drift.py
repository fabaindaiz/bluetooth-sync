#!/usr/bin/env python3
"""Mide si el desfase entre parlantes **crece con el tiempo** (E6, i-7c8794-24ea65).

Es la pregunta que A2DP no puede descartar por diseño: no hay reloj común, así que cada
parlante corre con su propio cristal. Un desfase fijo se compensa una vez; uno que crece
termina en zona de eco audible (~100 ms en música,
[09](../../docs/research/09-efecto-ambiental-y-diseno-de-la-experiencia.md) §3).

**Tres diferencias con `medir-n.py`, y las tres importan:**

1. **El estímulo es continuo.** Debajo de las ráfagas hay una cama de ruido rosa muy
   suave. Sin eso, PipeWire suspende el nodo entre ráfagas y el stream A2DP se
   reinicia: se estaría midiendo *variación entre arranques* —que ya se midió— en vez de
   *drift dentro de un stream*.
2. **El audio se genera al vuelo y va por stdin a `pw-play`.** Media hora de 3 canales en
   un WAV son ~500 MB; así no toca el disco.
3. **El análisis va por ventanas.** Una FFT sobre 30 minutos son 86 millones de muestras
   por banda. Se procesa ráfaga por ráfaga, buscándolas por bloques, lo que además absorbe
   la deriva entre el reloj de grabación y el de reproducción.

**Por qué el reloj del micrófono no contamina:** se miden **diferencias entre canales
dentro de la misma grabación**, así que el reloj del micrófono se cancela. Solo correría
la posición absoluta de las ráfagas, y eso el buscador por bloques lo absorbe.

Uso:
    probes/e6-a2dp/drift.py --minutos 2     # prueba de humo
    probes/e6-a2dp/drift.py --minutos 30    # la corrida de verdad
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

BANDA = 400.0
PERIODO = 10.0  # segundos entre ráfagas
DUR_RAFAGA = 0.060
NIVEL_CAMA = 0.02  # ruido rosa de fondo: mantiene el stream vivo sin molestar
NIVEL_RAFAGA = 0.30
CANALES_POR_DEFECTO = "FL:2350:Go 4 Black,FR:3250:Go 4 Red,RL:4150:Go 4 Blue"


def cargar_dsp():
    ruta = Path(__file__).with_name("medir-desfase.py")
    spec = importlib.util.spec_from_file_location("medir_desfase", ruta)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def parsear_canales(texto: str):
    canales = []
    for parte in texto.split(","):
        trozos = parte.split(":")
        pos, frec = trozos[0].strip(), float(trozos[1])
        nombre = trozos[2].strip() if len(trozos) > 2 else pos
        canales.append((pos, frec, nombre))
    return canales


def generar(proc, canales, sr: int, segundos: float) -> None:
    """Escribe PCM entrelazado s16 a stdin de pw-play, en bloques de un período."""
    n_ch = len(canales)
    largo = int(sr * DUR_RAFAGA)
    ventana = np.hanning(largo)
    t = np.arange(largo) / sr
    tonos = [NIVEL_RAFAGA * ventana * np.sin(2 * math.pi * f * t) for _, f, _ in canales]
    rng = np.random.default_rng(1)
    muestras_bloque = int(sr * PERIODO)
    # Ruido rosa conformando el espectro por FFT (1/sqrt(f)). Vectorizado a propósito:
    # un filtro de Voss muestra a muestra en Python puro no le sigue el ritmo al tiempo
    # real con bloques de 480k muestras.
    frec = np.fft.rfftfreq(muestras_bloque, 1 / sr)
    forma = np.zeros_like(frec)
    forma[1:] = 1.0 / np.sqrt(frec[1:])

    escritos = 0.0
    while escritos < segundos:
        espectro = np.fft.rfft(rng.standard_normal(muestras_bloque)) * forma
        rosa = np.fft.irfft(espectro, n=muestras_bloque)
        rosa *= NIVEL_CAMA / max(np.abs(rosa).max(), 1e-9)

        bloque = np.tile(rosa[:, None], (1, n_ch))
        for c in range(n_ch):
            bloque[:largo, c] += tonos[c]
        pcm = (np.clip(bloque.reshape(-1), -1, 1) * 32767).astype("<i2")
        try:
            proc.stdin.write(pcm.tobytes())
        except BrokenPipeError:
            return
        escritos += PERIODO


def analizar(m, ruta: Path, canales, sr: int):
    """Recorre la grabación por bloques y mide el desfase de cada ráfaga."""
    nombres = [c[2] for c in canales]
    ref = nombres[0]
    filas = []
    with wave.open(str(ruta)) as w:
        n_total = w.getnframes()
        paso = int(sr * PERIODO)
        solape = int(sr * 1.0)
        pos = 0
        pendiente = np.zeros(0)
        while pos < n_total:
            crudo = w.readframes(paso)
            if not crudo:
                break
            trozo = np.frombuffer(crudo, dtype="<i2").astype(float) / 32768
            if len(trozo) < paso:  # último bloque incompleto: no se analiza
                break
            x = np.concatenate([pendiente, trozo])
            base = pos - len(pendiente)
            envs = {n: m.envolvente(x, f, BANDA) for _, f, n in canales}
            suma = sum(envs.values())
            if suma.max() > 0:
                pico = int(np.argmax(suma))
                ini, fin = pico - int(0.15 * sr), pico + int(0.45 * sr)
                # La ventana tiene que caber entera en el bloque: si la ráfaga cae en el
                # borde, el análisis se corta y da valores degenerados.
                if ini < 0 or fin > len(x):
                    pendiente = x[-solape:] if len(x) > solape else x
                    pos += paso
                    continue
                # Solo se acepta la ráfaga si TODAS las bandas tienen nivel comparable:
                # una banda floja significa que ese parlante no sonó (ver medir-n.py).
                picos = {n: envs[n][ini:fin].max() for n in envs}
                mediana = float(np.median(list(picos.values())))
                if mediana > 0 and all(v >= 0.15 * mediana for v in picos.values()):
                    arranques = {n: m.arranque(envs[n], ini, fin) for n in envs}
                    if all(a is not None for a in arranques.values()):
                        t_abs = (base + arranques[ref]) / sr
                        fila = {"t": t_abs}
                        for n in nombres[1:]:
                            fila[n] = (arranques[n] - arranques[ref]) / sr * 1000
                        filas.append(fila)
            pendiente = x[-solape:] if len(x) > solape else x
            pos += paso
    return filas, ref


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sink", default="jbl_3")
    ap.add_argument("--mic", default="alsa_input.usb-3142_fifine_Microphone-00.analog-stereo")
    ap.add_argument("--canales", default=CANALES_POR_DEFECTO)
    ap.add_argument("--minutos", type=float, default=30.0)
    ap.add_argument("--guardar", type=Path)
    args = ap.parse_args()

    m = cargar_dsp()
    sr = m.SR
    canales = parsear_canales(args.canales)
    segundos = args.minutos * 60
    tmp = Path(tempfile.mkdtemp())
    grabacion = args.guardar or (tmp / "drift.wav")

    print(f"# drift entre parlantes — {time.strftime('%Y-%m-%dT%H:%M:%S%z')}")
    print(f"# sink={args.sink}  mic={args.mic}")
    for pos, frec, nombre in canales:
        print(f"#   {pos:3s} {frec:6.0f} Hz  {nombre}")
    print(f"# {args.minutos:.0f} min, una ráfaga cada {PERIODO:.0f} s, sobre cama de ruido rosa")
    print(f"# ~{int(segundos / PERIODO)} ráfagas esperadas\n")
    sys.stdout.flush()

    rec = subprocess.Popen(
        ["pw-record", "--target", args.mic, "--rate", str(sr), "--channels", "1",
         "--format", "s16", str(grabacion)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    time.sleep(0.7)
    play = subprocess.Popen(
        ["pw-play", "--target", args.sink, "--rate", str(sr),
         "--channels", str(len(canales)), "--format", "s16", "--raw", "-"],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    t0 = time.time()
    try:
        generar(play, canales, sr, segundos)
        play.stdin.close()
        play.wait(timeout=60)
    finally:
        time.sleep(1.0)
        rec.send_signal(2)
        rec.wait(timeout=20)
    print(f"reproducción terminada en {time.time() - t0:.0f} s; analizando…\n")
    sys.stdout.flush()

    filas, ref = analizar(m, grabacion, canales, sr)
    if len(filas) < 3:
        print(f"Solo {len(filas)} ráfagas utilizables. Revisá volumen y que suenen los tres.")
        print(f"grabación: {grabacion}")
        return 1

    nombres = [c[2] for c in canales if c[2] != ref]
    print(f"{'min':>6}  " + "  ".join(f"{n[:12]:>13s}" for n in nombres))
    for fila in filas:
        print(f"{fila['t'] / 60:>6.1f}  " + "  ".join(f"{fila[n]:>+13.2f}" for n in nombres))

    print(f"\nráfagas medidas: {len(filas)}   referencia: {ref}")
    t = np.array([f["t"] for f in filas])
    duracion_min = (t[-1] - t[0]) / 60
    ventana = min(300.0, (t[-1] - t[0]) / 4)
    print(f"\n{'parlante':<14s} {'mediana':>9s} {'MAD':>7s} {'primer cuarto':>14s} "
          f"{'último cuarto':>14s} {'deriva (ms/h)':>20s}")
    significativas = []
    for n in nombres:
        d = np.array([f[n] for f in filas])
        med = float(np.median(d))
        mad = float(np.median(np.abs(d - med))) * 1.4826
        pri = d[t < t[0] + ventana]
        ult = d[t > t[-1] - ventana]
        # Pendiente por mínimos cuadrados, CON su error estándar. Sin el error, cualquier
        # ruido extrapolado a una hora parece una deriva enorme.
        n_p = len(t)
        pend, corte = np.polyfit(t, d, 1)
        resid = d - (pend * t + corte)
        s_err = np.sqrt((resid**2).sum() / max(n_p - 2, 1)) / np.sqrt(((t - t.mean())**2).sum())
        pend_h, err_h = pend * 3600, s_err * 3600
        sig = abs(pend_h) > 2 * err_h
        significativas.append((n, pend_h, err_h, sig))
        marca = "" if sig else "  (no significativa)"
        print(f"{n[:14]:<14s} {med:>+9.2f} {mad:>7.2f} {np.median(pri):>+14.2f} "
              f"{np.median(ult):>+14.2f} {pend_h:>+11.1f} ± {err_h:.1f}{marca}")

    print()
    if duracion_min < 10:
        print(f"AVISO: la corrida duró {duracion_min:.1f} min. Extrapolar a una hora desde")
        print("tan poco amplifica el ruido. Para un número confiable hacen falta ≥30 min.")
    reales = [(n, p) for n, p, e, sig in significativas if sig]
    if not reales:
        print("→ No hay deriva estadísticamente distinguible del ruido.")
        print("  Una calibración por sesión alcanzaría.")
    else:
        peor_n, peor = max(reales, key=lambda x: abs(x[1]))
        print(f"→ Deriva significativa. La peor es {peor_n}: {peor:+.1f} ms/h.")
        if abs(peor) < 20:
            print("  Lenta: recalibrar al empezar cada sesión alcanza.")
        else:
            horas = 100 / abs(peor)
            print(f"  A este ritmo se llega a ~100 ms (eco audible en música) en {horas:.1f} h.")
            print("  El MVP necesita recalibración periódica, no una sola al empezar.")
    print(f"\ngrabación: {grabacion}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
