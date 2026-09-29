#!/usr/bin/env python3
"""Compara estrategias de calibración: cuál da el mejor retardo en el menor tiempo.

Simula lo que grabaría el micrófono con **retardos conocidos**, y mide cuánto se equivoca
cada combinación de estímulo y estimador. Al ser sintético, se puede barrer duraciones sin
ocupar los parlantes, y sobre todo **se conoce la respuesta correcta**, que con parlantes
de verdad nunca se sabe.

La simulación incluye a propósito las tres cosas que ensucian una medición real:

- **respuesta irregular del parlante**: los Go 4 son chicos, no dan graves y tienen picos;
- **reverberación de la pieza**: ecos decrecientes;
- **ruido de fondo** de la sala.

Uso:
    probes/calibracion/comparar.py
    probes/calibracion/comparar.py --repeticiones 20   # menos varianza, más lento
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "host" / "src"))

from aurasync import estimulos, medicion  # noqa: E402
from aurasync.dsp import decorrelate  # noqa: E402

SR = 48000
# A 48 kHz una muestra son 0,0208 ms. Estos retardos caen deliberadamente **en la mitad
# entre dos muestras** (350,5 y 1108,5 muestras): si cayeran cerca de una muestra entera,
# un estimador sin interpolación acertaría de casualidad y la comparación sería injusta.
RETARDOS_VERDADEROS = {"A": 0.0, "B": 7.302083, "C": 23.09375}
"""Los retardos que la simulación introduce, en ms."""

DURACIONES = (2.0, 5.0, 10.0, 15.0, 30.0)
RUIDOS = (0.03, 0.3, 1.0, 3.0, 10.0, 30.0)
DURACION_ESTRES = 10.0


def respuesta_del_parlante(x: np.ndarray, semilla: int, sr: int = SR) -> np.ndarray:
    """Un parlante chico: pasabanda de 150 Hz a 12 kHz, con picos y valles propios."""
    rng = np.random.default_rng(semilla)
    n = len(x)
    esp = np.fft.rfft(x)
    frec = np.fft.rfftfreq(n, 1 / sr)
    # Pasabanda suave.
    forma = 1.0 / (1 + (150.0 / np.maximum(frec, 1.0)) ** 4)
    forma *= 1.0 / (1 + (frec / 12000.0) ** 4)
    # Irregularidad: ±6 dB con variaciones cada ~500 Hz, distinta por unidad.
    n_nudos = max(4, int(frec[-1] / 500))
    nudos = rng.uniform(-6, 6, n_nudos)
    ondulacion = np.interp(frec, np.linspace(0, frec[-1], n_nudos), nudos)
    forma *= 10 ** (ondulacion / 20)
    return np.fft.irfft(esp * forma, n=n)


def reverberar(x: np.ndarray, sr: int = SR) -> np.ndarray:
    y = x.copy()
    for retardo_ms, ganancia in ((11, 0.40), (23, 0.25), (37, 0.15), (53, 0.09), (79, 0.05)):
        d = int(sr * retardo_ms / 1000)
        if d < len(x):
            y[d:] += ganancia * x[:-d]
    return y


def simular_microfono(
    pistas: dict[str, np.ndarray], semilla: int, ruido: float = 0.01, sr: int = SR
) -> np.ndarray:
    # `ruido` es la desviación estándar del ruido de sala como fracción del pico de la
    # señal. 0,01 es una pieza muy callada; 1,0 es ruido tan fuerte como la música.
    """Suma lo que emite cada parlante, con su retardo, su respuesta y la sala."""
    largo = max(len(p) for p in pistas.values()) + int(sr * 0.5)
    micro = np.zeros(largo)
    for i, (nombre, pista) in enumerate(pistas.items()):
        emitido = respuesta_del_parlante(pista, semilla * 10 + i, sr)
        # Retardo fraccionario de verdad: se aplica como rampa de fase en frecuencia. Con
        # un simple desplazamiento de muestras enteras, la "verdad" de la simulación se
        # redondearía y ningún estimador podría hacerlo mejor que media muestra.
        muestras = sr * RETARDOS_VERDADEROS[nombre] / 1000
        n_fft = 1 << (len(emitido) + int(muestras) + 2).bit_length()
        esp = np.fft.rfft(emitido, n=n_fft)
        frec = np.fft.rfftfreq(n_fft)
        desplazado = np.fft.irfft(esp * np.exp(-2j * np.pi * frec * muestras), n=n_fft)
        micro[: min(largo, n_fft)] += desplazado[: min(largo, n_fft)]
    micro = reverberar(micro, sr)
    micro += np.random.default_rng(semilla + 999).normal(0, ruido * np.abs(micro).max(), largo)
    return micro


# -- las estrategias que se comparan ---------------------------------------------


def estrategia_ruido_phat(segundos: float, semilla: int, ruido: float = 0.01):
    """Ruido rosa decorrelado + GCC-PHAT. Todos los parlantes de una vez."""
    pistas = estimulos.calibracion(3, segundos, SR, semilla=semilla)
    refs = dict(zip(RETARDOS_VERDADEROS, pistas, strict=True))
    micro = simular_microfono(refs, semilla, ruido)
    est = medicion.retardos_simultaneos(micro, refs, SR)
    return medicion.relativos_a(est, "A"), min(e.confianza for e in est.values())


def estrategia_ruido_sin_phat(segundos: float, semilla: int, ruido: float = 0.01):
    """Lo mismo pero con correlación cruzada a secas, para ver cuánto aporta PHAT."""
    pistas = estimulos.calibracion(3, segundos, SR, semilla=semilla)
    refs = dict(zip(RETARDOS_VERDADEROS, pistas, strict=True))
    micro = simular_microfono(refs, semilla, ruido)
    relativos, base = {}, None
    for nombre, ref in refs.items():
        n = 1
        while n < len(micro) + len(ref):
            n *= 2
        corr = np.fft.irfft(np.fft.rfft(micro, n=n) * np.conj(np.fft.rfft(ref, n=n)), n=n)
        pico = int(np.argmax(np.abs(corr[: int(SR * 0.5)])))
        ms = pico / SR * 1000
        if base is None:
            base = ms
        relativos[nombre] = ms - base
    return relativos, float("nan")


def estrategia_musica_phat(segundos: float, semilla: int, ruido: float = 0.01):
    """El caso de la recalibración: la referencia es música, no ruido.

    Se simula con ruido filtrado paso-bajo y con envolvente variable, que se parece mucho
    más a la música que el ruido plano: tiene silencios y golpes, y la energía se concentra
    abajo. Es la prueba de si el estimador aguanta **sin interrumpir la reproducción**.
    """
    n = int(SR * segundos)
    rng = np.random.default_rng(semilla + 4242)
    base = estimulos.ruido_rosa(n, SR, semilla)
    # Envolvente tipo música: golpes cada ~0,5 s con decaimiento.
    env = np.zeros(n)
    for inicio in range(0, n, int(SR * 0.5)):
        largo = min(int(SR * 0.45), n - inicio)
        env[inicio : inicio + largo] = np.exp(-np.linspace(0, 4, largo)) * rng.uniform(0.4, 1.0)
    # Paso-bajo: la música tiene mucha menos energía arriba de 5 kHz que el ruido rosa.
    esp = np.fft.rfft(base * env)
    frec = np.fft.rfftfreq(n, 1 / SR)
    esp *= 1.0 / (1 + (frec / 5000.0) ** 2)
    musica = np.fft.irfft(esp, n=n)

    pistas = estimulos.entradas_y_salidas(musica, 3, semilla)
    refs = dict(zip(RETARDOS_VERDADEROS, pistas, strict=True))
    micro = simular_microfono(refs, semilla, ruido)
    est = medicion.retardos_simultaneos(micro, refs, SR)
    return medicion.relativos_a(est, "A"), min(e.confianza for e in est.values())


def estrategia_rafagas(segundos: float, semilla: int, ruido: float = 0.01):
    """El método viejo: ráfagas tonales y arranque de la envolvente.

    Se reimplementa acá lo mínimo del analizador de `probes/e6-a2dp` para poder comparar en
    las mismas condiciones.
    """
    banda = 400.0
    frecs = [2350.0, 3250.0, 4150.0]
    reps = max(2, int(segundos))
    pistas = estimulos.rafagas_tonales(frecs, repeticiones=reps, sr=SR)
    refs = dict(zip(RETARDOS_VERDADEROS, pistas, strict=True))
    micro = simular_microfono(refs, semilla, ruido)

    def envolvente(x, centro):
        esp = np.fft.fft(x)
        f = np.fft.fftfreq(len(x), 1 / SR)
        filtrado = np.zeros_like(esp)
        mascara = (f > centro - banda) & (f < centro + banda)
        filtrado[mascara] = esp[mascara] * 2
        return np.abs(np.fft.ifft(filtrado))

    envs = {n: envolvente(micro, f) for n, f in zip(RETARDOS_VERDADEROS, frecs, strict=True)}
    arranques = {}
    for nombre, env in envs.items():
        # Se mide la primera ráfaga, que es lo que haría una calibración corta.
        ventana = env[: int(SR * 0.6)]
        umbral = 0.2 * ventana.max()
        idx = int(np.argmax(ventana >= umbral))
        arranques[nombre] = idx / SR * 1000
    base = arranques["A"]
    return {n: v - base for n, v in arranques.items()}, float("nan")


ESTRATEGIAS = {
    "ruido + GCC-PHAT": estrategia_ruido_phat,
    "ruido, sin PHAT": estrategia_ruido_sin_phat,
    "música + GCC-PHAT": estrategia_musica_phat,
    "ráfagas tonales": estrategia_rafagas,
}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repeticiones", type=int, default=8)
    args = ap.parse_args()

    print("# comparación de estrategias de calibración")
    print(f"# retardos verdaderos: {RETARDOS_VERDADEROS} ms")
    print(f"# {args.repeticiones} repeticiones por celda, con parlantes y sala distintos en cada una")
    print("# el número es el error absoluto máximo entre los parlantes, en ms\n")

    ancho = max(len(k) for k in ESTRATEGIAS) + 2
    print(f"{'estrategia':<{ancho}}" + "".join(f"{d:>9.0f}s" for d in DURACIONES))
    print("-" * (ancho + 10 * len(DURACIONES)))
    for nombre, fn in ESTRATEGIAS.items():
        celdas = []
        for dur in DURACIONES:
            errores = []
            for r in range(args.repeticiones):
                relativos, _ = fn(dur, semilla=r)
                errores.append(
                    max(abs(relativos[n] - RETARDOS_VERDADEROS[n]) for n in RETARDOS_VERDADEROS)
                )
            celdas.append(float(np.median(errores)))
        print(f"{nombre:<{ancho}}" + "".join(f"{c:>9.2f} " for c in celdas))

    # La duración casi no mueve la aguja con banda ancha, así que la dimensión que sí
    # informa es cuánto ruido de sala aguanta cada método antes de romperse.
    print(f"\n\n# dónde se rompe cada método, a {DURACION_ESTRES:.0f} s")
    print("# 'ruido' es la desviación del ruido de sala sobre el pico de la señal:")
    print("#   0,03 = pieza callada · 0,3 = conversación · 1 = ruido como la música · 30 = absurdo\n")
    print(f"{'estrategia':<{ancho}}" + "".join(f"{r:>9.2f} " for r in RUIDOS))
    print("-" * (ancho + 10 * len(RUIDOS)))
    for nombre, fn in ESTRATEGIAS.items():
        celdas = []
        for ruido in RUIDOS:
            errores = []
            for r in range(args.repeticiones):
                relativos, _ = fn(DURACION_ESTRES, semilla=r, ruido=ruido)
                errores.append(
                    max(abs(relativos[n] - RETARDOS_VERDADEROS[n]) for n in RETARDOS_VERDADEROS)
                )
            celdas.append(float(np.median(errores)))
        print(f"{nombre:<{ancho}}" + "".join(f"{c:>9.2f} " for c in celdas))

    print("\n# confianza del estimador (pico sobre mediana), ruido + GCC-PHAT a 10 s")
    for ruido in RUIDOS:
        _, conf = estrategia_ruido_phat(DURACION_ESTRES, semilla=0, ruido=ruido)
        marca = "  ← se descartaría" if conf < medicion.CONFIANZA_MINIMA else ""
        print(f"  ruido {ruido:>5.2f} → confianza {conf:>8.1f}{marca}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
