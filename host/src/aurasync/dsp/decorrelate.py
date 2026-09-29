"""Decorrelación por filtros todo-paso de fase aleatoria.

Es la pieza que produce el envolvimiento: mandarle a cada parlante una versión del mismo
material con forma de onda distinta pero que **suena igual**. Baja la correlación entre
canales, y con eso aparece el campo difuso en vez de una imagen fantasma entre parlantes.

**De dónde salen los parámetros** (Potard y Burnett, DAFx'04, *Decorrelation Techniques
for the Rendering of Apparent Sound Source Width in 3D Audio Displays*; ver
`docs/research/09-efecto-ambiental-y-diseno-de-la-experiencia.md` §11.1):

- el mecanismo es un **todo-paso con respuesta de fase aleatoria, tipo ruido**: preserva
  el espectro de amplitud, y como el oído es insensible a la fase, las salidas quedan
  *perceptualmente iguales pero estadísticamente ortogonales*;
- el largo típico es de **~100 polos y ceros**, de ahí `LARGO_POR_DEFECTO`;
- con filtros fijos **solo se consiguen 5 o 6 señales totalmente decorrelacionadas**: más
  allá, el largo finito hace que algún par termine correlacionado. Por eso
  `banco_decorrelador` avisa al pasarse;
- las fases tienen que ser **máximamente ortogonales**, elegidas por un proceso de
  selección por mejor desempeño. Eso es lo que hace `banco_decorrelador`.

**Lo que este módulo NO hace, a propósito:** decorrelar con retardos. El mismo paper dice
que en parlantes hay que evitarlo, por el filtrado peine que introduce. El retardo sirve
para otra cosa (el efecto Haas en los canales traseros) y va **después**, no en lugar de
esto.
"""

from __future__ import annotations

import numpy as np

LARGO_POR_DEFECTO = 128
"""Largo del filtro en muestras. El paper usa ~100 polos y ceros; 128 es la potencia de
dos más cercana, que hace la convolución por FFT más barata."""

MAXIMO_FIJOS = 6
"""Cuántas salidas totalmente decorrelacionadas se pueden sacar con filtros fijos."""


def filtro_todo_paso(largo: int = LARGO_POR_DEFECTO, semilla: int | None = None) -> np.ndarray:
    """Un filtro FIR todo-paso con fase aleatoria.

    Se construye en frecuencia: magnitud exactamente 1 en todos los bins y fase aleatoria,
    con simetría hermítica para que la respuesta al impulso sea real. Así el filtro no
    colorea —la magnitud del espectro de la señal no cambia— y solo revuelve la fase.
    """
    rng = np.random.default_rng(semilla)
    n_bins = largo // 2 + 1
    fase = rng.uniform(-np.pi, np.pi, n_bins)
    # Los bins de continua y de Nyquist tienen que ser reales, o la respuesta al impulso
    # sale compleja.
    fase[0] = 0.0
    if largo % 2 == 0:
        fase[-1] = 0.0
    h = np.fft.irfft(np.exp(1j * fase), n=largo)
    # La energía queda repartida por todo el filtro; normalizar la deja comparable entre
    # semillas sin tocar la planitud de la magnitud.
    return h / np.sqrt((h**2).sum())


def aplicar(x: np.ndarray, h: np.ndarray) -> np.ndarray:
    """Convoluciona y devuelve la misma cantidad de muestras que entraron.

    Se descarta la cola de la convolución en vez de alargar la señal: en un flujo continuo
    la cola del bloque anterior se pierde igual, y para medir tiempos conviene que la
    salida tenga el mismo largo que la entrada.
    """
    return np.convolve(x, h, mode="full")[: len(x)]


def correlacion_cruzada_maxima(a: np.ndarray, b: np.ndarray) -> float:
    """El pico de la correlación cruzada normalizada entre dos filtros, en [0, 1].

    Es la medida de ortogonalidad que usa la selección: dos filtros con un pico alto
    producen salidas que el sistema binaural va a fusionar en una sola fuente.
    """
    na, nb = np.sqrt((a**2).sum()), np.sqrt((b**2).sum())
    if na == 0 or nb == 0:
        return 1.0
    return float(np.abs(np.correlate(a, b, mode="full")).max() / (na * nb))


def banco_decorrelador(
    n: int,
    largo: int = LARGO_POR_DEFECTO,
    candidatos: int = 64,
    semilla: int = 0,
) -> list[np.ndarray]:
    """`n` filtros todo-paso lo más ortogonales entre sí que se pueda.

    Genera `candidatos` filtros y los elige de a uno con un criterio voraz: en cada paso
    se queda con el que menos se parece al peor de los ya elegidos. Es el "best performance
    selection process" que pide el paper, y hace falta porque dos fases aleatorias
    cualesquiera pueden salir parecidas por casualidad.

    Con `n` por encima de `MAXIMO_FIJOS` el resultado deja de estar bien decorrelacionado;
    para más canales hace falta decorrelación dinámica (fase nueva en cada trama), que el
    paper describe pero también advierte que puede cansar al oyente.
    """
    if n < 1:
        msg = f"se pidieron {n} filtros"
        raise ValueError(msg)
    if candidatos < n:
        msg = f"hacen falta al menos {n} candidatos, se pidieron {candidatos}"
        raise ValueError(msg)

    rng = np.random.default_rng(semilla)
    semillas = rng.integers(0, 2**31 - 1, candidatos)
    pool = [filtro_todo_paso(largo, int(s)) for s in semillas]

    elegidos = [pool.pop(0)]
    while len(elegidos) < n:
        mejor_idx, mejor_peor = 0, np.inf
        for i, cand in enumerate(pool):
            peor = max(correlacion_cruzada_maxima(cand, e) for e in elegidos)
            if peor < mejor_peor:
                mejor_idx, mejor_peor = i, peor
        elegidos.append(pool.pop(mejor_idx))
    return elegidos


def ortogonalidad(filtros: list[np.ndarray]) -> float:
    """La peor correlación cruzada entre cualquier par del banco. Más chico es mejor."""
    minimo_para_comparar = 2
    if len(filtros) < minimo_para_comparar:
        return 0.0
    return max(
        correlacion_cruzada_maxima(filtros[i], filtros[j])
        for i in range(len(filtros))
        for j in range(i + 1, len(filtros))
    )
