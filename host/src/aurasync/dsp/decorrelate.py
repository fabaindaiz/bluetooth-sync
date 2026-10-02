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

LARGO_POR_DEFECTO = 256
"""Largo del filtro en muestras (5,3 ms a 48 kHz). Subió de 128 a 256 el 2026-10-01: con fase
suave (ver `filtro_todo_paso`) hace falta lugar para un retardo de grupo que varía entre
1 y 4 ms."""

RETARDO_MEDIO_MS = 2.5
"""El retardo de grupo medio, **igual en todos los filtros**: así el decorrelador no corre a un
parlante respecto de otro, que era otro efecto lateral del diseño anterior (hasta 1-2 ms)."""
VARIACION_MS = 1.5
"""Cuánto se aparta el retardo de grupo del medio, de una frecuencia a otra."""
SUAVIZADO_BINS = 6
"""Ancho del suavizado del retardo de grupo, en bins: lo que hace que la fase no salte."""

MAXIMO_FIJOS = 6
"""Cuántas salidas totalmente decorrelacionadas se pueden sacar con filtros fijos."""


def filtro_todo_paso(largo: int = LARGO_POR_DEFECTO, semilla: int | None = None, sr: int = 48000) -> np.ndarray:
    """Un filtro FIR todo-paso con fase aleatoria **y suave**.

    **Por qué suave, y qué falló antes** (MEDIDO el 2026-10-01, experimentos/10 §6). La versión
    anterior ponía magnitud 1 y una fase independiente al azar en cada uno de sus bins de
    diseño. Era exactamente plana *en esos bins*, pero entre ellos —que es donde está casi
    toda la música— la fase saltaba y la respuesta tenía huecos de hasta 46 dB (±9,5 dB en
    tercios de octava): un ecualizador al azar distinto en cada parlante. El test que la
    validaba miraba solo los bins de diseño.

    Ahora lo aleatorio es el **retardo de grupo**: ruido suavizado entre bins, con media
    `RETARDO_MEDIO_MS` y apartamiento `VARIACION_MS`, y la fase es su integral. Una fase sin
    saltos interpola bien entre bins: la respuesta continua queda plana a ±0,1 dB, y la
    correlación entre salidas con ruido rosa baja de 0,67 a ~0,53.
    """
    rng = np.random.default_rng(semilla)
    n_bins = largo // 2 + 1
    borde = 2 * SUAVIZADO_BINS
    ruido = rng.standard_normal(n_bins + 2 * borde)
    ventana = np.hanning(2 * SUAVIZADO_BINS + 1)
    ventana /= ventana.sum()
    retardo = np.convolve(ruido, ventana, mode="same")[borde : borde + n_bins]
    retardo = (retardo - retardo.mean()) / (np.abs(retardo - retardo.mean()).max() + 1e-12)
    retardo_s = (RETARDO_MEDIO_MS + VARIACION_MS * retardo) / 1000
    fase = -np.cumsum(2 * np.pi * (sr / largo) * retardo_s)
    fase -= fase[0]
    # Los bins de continua y de Nyquist tienen que ser reales, o la respuesta al impulso
    # sale compleja.
    fase[0] = 0.0
    if largo % 2 == 0:
        fase[-1] = np.round(fase[-1] / np.pi) * np.pi
    h = np.fft.irfft(np.exp(1j * fase), n=largo)
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


def correlacion_rosa(a: np.ndarray, b: np.ndarray, sr: int = 48000) -> float:
    """La correlación entre las salidas de dos filtros con material de espectro rosa, en [0, 1].

    Es la medida que importa para el envolvimiento: cuánto se parecen, a retardo cero, lo que
    suenan dos parlantes con música (que tiene más energía en graves). Reemplazó al pico de la
    correlación de las respuestas al impulso, que con fase suave elegía filtros cuyas salidas
    se correlacionaban 0,68.
    """
    n = 16384
    f = np.fft.rfftfreq(n, 1 / sr)
    peso = np.where(f > 20, 1 / np.maximum(f, 1), 0.0)  # noqa: PLR2004
    ha, hb = np.fft.rfft(a, n), np.fft.rfft(b, n)
    cruzada = np.sum(peso * np.real(ha * np.conj(hb)))
    norma = np.sqrt(np.sum(peso * np.abs(ha) ** 2) * np.sum(peso * np.abs(hb) ** 2))
    return float(abs(cruzada) / norma) if norma > 0 else 1.0


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
            peor = max(correlacion_rosa(cand, e) for e in elegidos)
            if peor < mejor_peor:
                mejor_idx, mejor_peor = i, peor
        elegidos.append(pool.pop(mejor_idx))
    return elegidos


def ortogonalidad(filtros: list[np.ndarray]) -> float:
    """La peor correlación (con espectro rosa) entre cualquier par del banco. Más chico es mejor."""
    minimo_para_comparar = 2
    if len(filtros) < minimo_para_comparar:
        return 0.0
    return max(
        correlacion_rosa(filtros[i], filtros[j]) for i in range(len(filtros)) for j in range(i + 1, len(filtros))
    )
