"""Señales para calibrar: qué se le manda a cada parlante para poder medirlo.

**El criterio que ordena todo este módulo:** la resolución temporal de una medición de
retardo va como **1 / ancho de banda**. Un tono de 400 Hz de ancho da ~1,25 ms; ruido de
8 kHz de ancho da ~0,06 ms. Por eso la calibración usa **ruido de banda ancha** y no
ráfagas tonales, aunque los tonos sean más fáciles de reconocer de oído.

Se aprendió midiendo: una corrida de 30 minutos con ráfagas tonales sobre una cama de
ruido dio 25 ms de dispersión, tanta que el drift quedó irrecuperable
(`docs/research/experimentos/05-e6-a2dp-un-canal-por-parlante.md`).

**Y la segunda condición: las señales de distintos parlantes tienen que poder separarse en
el micrófono.** Hay dos grados de eso, y confundirlos cuesta caro:

- **Independientes** (ruidos distintos): ortogonales de verdad. Es lo que usa `calibracion`.
- **Decorreladas** (un mismo material por filtros todo-paso distintos): *casi* ortogonales.
  Es lo que necesariamente pasa cuando la referencia es la música, porque todos los
  parlantes tocan la misma obra. Alcanza si los niveles son parejos; **si no lo son, falla
  de forma catastrófica y silenciosa** (ver `calibracion`).
"""

from __future__ import annotations

import numpy as np

from aurasync.dsp import decorrelate

SR = 48000


def ruido_rosa(n: int, sr: int = SR, semilla: int | None = None) -> np.ndarray:
    """Ruido rosa por conformado espectral (1/sqrt(f)).

    Rosa y no blanco porque suena bastante menos áspero a igual energía, y porque reparte
    la energía como la música: eso importa cuando la calibración se hace con alguien en la
    sala, que es el caso.
    """
    rng = np.random.default_rng(semilla)
    frec = np.fft.rfftfreq(n, 1 / sr)
    forma = np.zeros_like(frec)
    forma[1:] = 1.0 / np.sqrt(frec[1:])
    x = np.fft.irfft(np.fft.rfft(rng.standard_normal(n)) * forma, n=n)
    pico = np.abs(x).max()
    return x / pico if pico > 0 else x


def entradas_y_salidas(x: np.ndarray, n: int, semilla: int = 0) -> list[np.ndarray]:
    """`n` versiones decorrelacionadas de `x`, una por parlante.

    Suenan igual que `x` —los filtros son todo-paso— pero sus formas de onda son
    ortogonales, que es lo que permite separarlas en el micrófono.
    """
    return [decorrelate.aplicar(x, h) for h in decorrelate.banco_decorrelador(n, semilla=semilla)]


def calibracion(
    n_parlantes: int,
    segundos: float = 15.0,
    sr: int = SR,
    semilla: int = 0,
    entrada: float = 0.05,
) -> list[np.ndarray]:
    """El estímulo de calibración: ruido rosa **independiente** por parlante.

    **Independiente, no decorrelado, y la diferencia importa mucho.** Una versión anterior
    usaba un solo ruido pasado por el banco de filtros todo-paso, igual que hace el motor de
    reproducción. Los filtros dejan las señales *casi* ortogonales, y eso alcanza cuando los
    parlantes suenan al mismo nivel; **cuando no, falla de forma catastrófica y silenciosa**.

    Medido en simulación (`probes/calibracion/comparar.py` y el barrido de niveles): con
    ganancias de 1 / 0,5 / 0,25, las referencias decorreladas daban **22,6 ms de error**
    mientras informaban una dispersión entre ventanas de 0,000 ms. Con ruidos independientes,
    el error en el mismo caso es de 0,011 ms.

    Para calibrar no hay ninguna razón para que los parlantes reciban el mismo material, así
    que se usa ruido independiente y el problema desaparece. **La recalibración con música sí
    tiene que convivir con él**, porque ahí las señales son versiones decorreladas de lo
    mismo; ver `medicion.residuo_relativo`.

    `entrada` es la rampa de los extremos. Sin ella, el golpe del arranque es un transitorio
    que los parlantes comprimen, y esa compresión corre el tiempo de llegada aparente justo
    al principio de la medición.
    """
    n = int(sr * segundos)
    rampa = int(sr * entrada)
    ventana = np.ones(n)
    if rampa > 0 and 2 * rampa < n:
        ventana[:rampa] = np.linspace(0, 1, rampa)
        ventana[-rampa:] = np.linspace(1, 0, rampa)
    return [ruido_rosa(n, sr, semilla * 1000 + i) * ventana for i in range(n_parlantes)]


def rafagas_tonales(
    frecuencias: list[float],
    repeticiones: int = 10,
    sr: int = SR,
    periodo: float = 1.0,
    duracion: float = 0.060,
) -> list[np.ndarray]:
    """Ráfagas de un tono distinto por canal.

    **Se conserva para poder comparar, no porque convenga.** Tiene dos virtudes: se
    reconoce de oído qué parlante suena, lo que ayuda a verificar el ruteo, y no necesita
    que el analizador conozca la forma de onda. Pero su resolución temporal es más de un
    orden de magnitud peor que la del ruido de banda ancha.
    """
    n = int(sr * repeticiones * periodo)
    largo = int(sr * duracion)
    ventana = np.hanning(largo)
    t = np.arange(largo) / sr
    pistas = []
    for f in frecuencias:
        pista = np.zeros(n)
        for k in range(repeticiones):
            ini = int(sr * k * periodo)
            pista[ini : ini + largo] = ventana * np.sin(2 * np.pi * f * t)
        pistas.append(pista)
    return pistas


def barrido_logaritmico(
    segundos: float = 3.0,
    f0: float = 100.0,
    f1: float = 12000.0,
    sr: int = SR,
) -> np.ndarray:
    """Barrido seno exponencial, el estímulo clásico de medición de salas.

    **No se usa para medir varios parlantes a la vez**, porque dos barridos simultáneos no
    están decorrelacionados: barren las mismas frecuencias y se pisan. Sirve para medir de
    a uno, y está acá porque da la mejor relación señal-ruido por segundo cuando eso es lo
    que hace falta.
    """
    n = int(sr * segundos)
    t = np.arange(n) / sr
    k = np.log(f1 / f0)
    return np.sin(2 * np.pi * f0 * segundos / k * (np.exp(t * k / segundos) - 1))
