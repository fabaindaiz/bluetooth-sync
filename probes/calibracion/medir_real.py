#!/usr/bin/env python3
"""Corre la receta de calibración con parlantes de verdad, y la compara con el método viejo.

La simulación (`comparar.py`) dice que ruido decorrelado de banda ancha con GCC-PHAT es
~170 veces más preciso que las ráfagas tonales. Acá se comprueba con los parlantes, que es
la única forma de saber si eso aguanta el códec, la compresión del parlante y la sala.

**Lo que no se puede hacer acá, y por eso la comparación es indirecta:** con parlantes de
verdad **no se conoce la respuesta correcta**. Así que no se mide el error sino la
**dispersión entre ventanas** — si un método da 0,05 ms de dispersión y el otro 3 ms, el
primero es mejor aunque no sepamos el valor verdadero de ninguno.

Uso:
    probes/calibracion/medir_real.py                 # las dos pruebas
    probes/calibracion/medir_real.py --solo ruido
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "host" / "src"))

from aurasync import estimulos, medicion  # noqa: E402

SR = 48000
SINK = "jbl_3"
MIC = "alsa_input.usb-3142_fifine_Microphone-00.analog-stereo"
PARLANTES = ["Go 4 Black", "Go 4 Red", "Go 4 Blue"]  # el orden de los canales del sink
FRECUENCIAS = [2350.0, 3250.0, 4150.0]
AMPLITUD = 0.4
"""Amplitud del estímulo. Con 0,4 y los parlantes al 50 %%, la grabación llegaba a pico
0,058: el 6 %% del rango del micrófono, o sea ~24 dB de señal-ruido desperdiciados."""


def reproducir_y_grabar(
    pistas: list[np.ndarray], etiqueta: str, guardar: Path | None = None
) -> np.ndarray:
    """Toca las pistas por el sink combinado mientras graba con el micrófono.

    Si se le pasa `guardar`, deja la grabación en disco. Conviene: el análisis se puede
    reajustar muchas veces sobre la misma grabación, sin volver a ocupar los parlantes.
    """
    tmp = Path(tempfile.mkdtemp())
    estimulo = tmp / "est.wav"
    grabacion = guardar if guardar is not None else tmp / "grab.wav"
    if guardar is not None:
        guardar.parent.mkdir(parents=True, exist_ok=True)
    inter = np.empty(len(pistas[0]) * len(pistas))
    for i, p in enumerate(pistas):
        inter[i :: len(pistas)] = p
    with wave.open(str(estimulo), "wb") as w:
        w.setnchannels(len(pistas))
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((np.clip(inter, -1, 1) * 32767).astype("<i2").tobytes())

    print(f"  reproduciendo {etiqueta} ({len(pistas[0]) / SR:.0f} s)…", flush=True)
    rec = subprocess.Popen(
        ["pw-record", "--target", MIC, "--rate", str(SR), "--channels", "1",
         "--format", "s16", str(grabacion)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    time.sleep(0.7)
    try:
        subprocess.run(["pw-play", "--target", SINK, str(estimulo)], check=True)
    finally:
        time.sleep(0.7)
        rec.send_signal(2)
        rec.wait(timeout=20)

    with wave.open(str(grabacion)) as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(float) / 32768


def alinear_groseramente(
    micro: np.ndarray, referencias: dict[str, np.ndarray]
) -> dict[str, np.ndarray] | None:
    """Usa la alineación gruesa del paquete, que exige que los canales concuerden."""
    grueso = medicion.alineacion_gruesa(micro, referencias, SR)
    if grueso is None:
        print("  desfase grueso: RECHAZADO (los canales no concuerdan entre sí)")
        return None
    print(f"  desfase grueso: {grueso:.1f} ms")
    return medicion.alinear(micro, referencias, grueso, SR)


def analizar_ruido(micro: np.ndarray, pistas: list[np.ndarray], ventana: float = 2.0):
    """El análisis, separado de la reproducción para poder reajustarlo sin tocar audio."""
    refs = dict(zip(PARLANTES, pistas, strict=True))
    alineadas = alinear_groseramente(micro, refs)
    if alineadas is None:
        nada = dict.fromkeys(PARLANTES, float("nan"))
        return nada, dict.fromkeys(PARLANTES, float("inf")), {}

    # Diagnóstico por ventana: sin esto, cuando algo falla no se sabe si fue la alineación
    # gruesa, la confianza o el rango de búsqueda.
    n_v = int(SR * ventana)
    aceptadas = 0
    for k in range(max(1, len(micro) // n_v)):
        trozo = micro[k * n_v : (k + 1) * n_v]
        sub = {n: r[k * n_v : (k + 1) * n_v] for n, r in alineadas.items()}
        if any(len(v) < n_v for v in sub.values()) or len(trozo) < n_v:
            continue
        est = medicion.retardos_simultaneos(trozo, sub, SR, retardo_maximo_ms=120.0)
        confs = [f"{e.confianza:.0f}" for e in est.values()]
        ok = all(e.confiable for e in est.values())
        aceptadas += ok
        print(f"    ventana {k + 1}: confianzas {confs}  {'ok' if ok else 'DESCARTADA'}")
    print(f"    aceptadas: {aceptadas}")

    medianas, dispersiones = medicion.calibrar_por_ventanas(
        micro, alineadas, SR, ventana_s=ventana, retardo_maximo_ms=120.0
    )
    niveles = medicion.niveles(micro, alineadas, medianas, SR)

    # El criterio de validez: cuánto de la grabación explica el modelo estimado.
    residuo = medicion.residuo_relativo(micro, alineadas, medianas, niveles, SR)
    # Y con qué se compara: el residuo de no modelar nada es 1,0 por definición, y el de
    # un modelo con los retardos a cero dice cuánto aportó realmente estimarlos.
    sin_retardos = medicion.residuo_relativo(
        micro, alineadas, dict.fromkeys(PARLANTES, 0.0), niveles, SR
    )
    print(f"    residuo: {residuo:.4f}   (sin estimar retardos: {sin_retardos:.4f})")
    return medianas, dispersiones, niveles


def prueba_ruido(segundos: float = 10.0, guardar: Path | None = None, ventana: float = 2.0):
    """La receta nueva: ruido rosa decorrelado, ventanas de 2 s."""
    pistas = estimulos.calibracion(len(PARLANTES), segundos, SR, semilla=0)
    pistas = [AMPLITUD * p for p in pistas]
    micro = reproducir_y_grabar(pistas, "ruido rosa decorrelado", guardar)
    return analizar_ruido(micro, pistas, ventana)


def prueba_rafagas(segundos: float = 10.0, guardar: Path | None = None):
    """El método viejo, en las mismas condiciones, para comparar dispersión."""
    pistas = estimulos.rafagas_tonales(FRECUENCIAS, repeticiones=int(segundos), sr=SR)
    pistas = [AMPLITUD * p for p in pistas]
    micro = reproducir_y_grabar(pistas, "ráfagas tonales", guardar)

    banda = 400.0
    envs = {}
    for nombre, f in zip(PARLANTES, FRECUENCIAS, strict=True):
        esp = np.fft.fft(micro)
        frec = np.fft.fftfreq(len(micro), 1 / SR)
        filtrado = np.zeros_like(esp)
        mascara = (frec > f - banda) & (frec < f + banda)
        filtrado[mascara] = esp[mascara] * 2
        envs[nombre] = np.abs(np.fft.ifft(filtrado))

    suma = sum(envs.values())
    flancos = np.flatnonzero(np.diff((suma > 0.25 * suma.max()).astype(int)) == 1)
    disparos, minimo = [], int(0.5 * SR)
    for b in flancos:
        if not disparos or b - disparos[-1] >= minimo:
            disparos.append(int(b))

    por_parlante: dict[str, list[float]] = {n: [] for n in PARLANTES}
    for b in disparos:
        ini, fin = max(0, b - int(0.06 * SR)), min(len(micro), b + int(0.45 * SR))
        arranques = {}
        for nombre, env in envs.items():
            trozo = env[ini:fin]
            if trozo.size == 0 or trozo.max() <= 0:
                break
            umbral = 0.2 * trozo.max()
            idx = int(np.argmax(trozo >= umbral))
            arranques[nombre] = (ini + idx) / SR * 1000
        if len(arranques) == len(PARLANTES):
            base = min(arranques.values())
            for n, v in arranques.items():
                por_parlante[n].append(v - base)

    medianas, dispersiones = {}, {}
    for n, v in por_parlante.items():
        if not v:
            medianas[n], dispersiones[n] = float("nan"), float("inf")
            continue
        a = np.array(v)
        medianas[n] = float(np.median(a))
        dispersiones[n] = float(np.median(np.abs(a - medianas[n]))) * 1.4826
    return medianas, dispersiones, {}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--solo", choices=["ruido", "rafagas"])
    ap.add_argument("--segundos", type=float, default=10.0)
    ap.add_argument("--repeticiones", type=int, default=3)
    ap.add_argument("--guardar", help="carpeta donde dejar las grabaciones, para reanalizar")
    args = ap.parse_args()

    print(f"# calibración con parlantes — {time.strftime('%Y-%m-%dT%H:%M:%S%z')}")
    print(f"# sink={SINK}  parlantes={PARLANTES}")
    print("# El valor verdadero NO se conoce: lo que se compara es la DISPERSIÓN.\n")

    pruebas = {"ruido": prueba_ruido, "rafagas": prueba_rafagas}
    if args.solo:
        pruebas = {args.solo: pruebas[args.solo]}

    for nombre, fn in pruebas.items():
        print(f"\n{'=' * 62}\n{nombre.upper()}\n{'=' * 62}")
        acumulado: dict[str, list[float]] = {n: [] for n in PARLANTES}
        for r in range(args.repeticiones):
            print(f"\n-- corrida {r + 1} de {args.repeticiones} --")
            destino = Path(args.guardar) / f"{nombre}-{r + 1}.wav" if args.guardar else None
            medianas, dispersiones, niveles = fn(args.segundos, guardar=destino)
            base = medianas.get(PARLANTES[0], 0.0)
            print(f"  {'parlante':<14s} {'retardo':>9s} {'dispersión':>11s} {'nivel':>9s}")
            for p in PARLANTES:
                rel = medianas[p] - base if np.isfinite(medianas[p]) else float("nan")
                niv = f"{niveles[p]:>9.3f}" if p in niveles else f"{'—':>9s}"
                print(f"  {p:<14s} {rel:>+9.2f} {dispersiones[p]:>11.2f} {niv}")
                if np.isfinite(rel):
                    acumulado[p].append(rel)

        print(f"\n  -- entre corridas ({args.repeticiones}) --")
        for p in PARLANTES:
            v = np.array(acumulado[p])
            if len(v) < 2:
                print(f"  {p:<14s} sin datos suficientes")
                continue
            print(f"  {p:<14s} mediana {np.median(v):>+8.2f} ms   "
                  f"rango entre corridas {v.max() - v.min():>7.2f} ms")
    return 0


if __name__ == "__main__":
    sys.exit(main())
