"""Estimar el retardo de cada parlante a partir de lo que capta el micrófono.

**La idea que hace que la calibración inicial y la recalibración continua sean lo mismo:**
si a cada parlante se le manda una señal **distinta y conocida**, entonces correlacionar lo
que graba el micrófono contra la señal de cada parlante devuelve su tiempo de llegada.
Sirve con ruido —calibración rápida, mucha relación señal-ruido— y sirve con la música
—recalibración mientras suena, sin interrumpir nada—, porque el estimador es el mismo.

Y esto se apoya en algo que el proyecto **ya necesita** por otro motivo: para producir
envolvimiento hay que mandarle a cada parlante una versión decorrelacionada del material
(`dsp.decorrelate`). Esas señales decorrelacionadas son, justamente, las referencias
mutuamente incorreladas que este estimador necesita. La condición del efecto es la
condición de la medición.

**Por qué GCC-PHAT y no una correlación cruzada a secas.** PHAT blanquea el espectro antes
de la transformada inversa, así que el pico queda angosto y —esto es lo que importa acá—
**la respuesta en frecuencia del parlante y de la sala deja de sesgar la estimación**. Con
parlantes Bluetooth chicos, que tienen una respuesta muy irregular, la diferencia es
grande. La contrapartida es que amplifica el ruido en las bandas donde no hay señal, y por
eso `gcc_phat` acepta limitar la banda.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

SR = 48000


@dataclass(frozen=True)
class Estimacion:
    """El retardo estimado y qué tan creíble es."""

    retardo_ms: float
    confianza: float
    """Razón entre el pico y el resto de la correlación. Por debajo de `CONFIANZA_MINIMA`
    la estimación es ruido: puede pasar si ese parlante no está sonando, si el micrófono
    está muy lejos o si la referencia no corresponde."""

    @property
    def confiable(self) -> bool:
        return self.confianza >= CONFIANZA_MINIMA


_PISO_ESPECTRO = 1e-12
"""Por debajo de esto no hay señal que blanquear: dividir amplificaría ruido numérico."""

_NIVEL_AUDIBLE = 1e-6
"""Un parlante por debajo de esto no está sonando. Pedirle ganancia sería dividir por
casi cero."""

CONFIANZA_MINIMA = 0.0
"""Desactivado. Se deja en 0 y `Estimacion.confiable` siempre da verdadero.

**La confianza resultó no servir como criterio, y conviene que quede escrito**, porque la
idea de "usar la nitidez del pico para saber si la medición vale" es tentadora y vuelve.

Se probaron tres valores. Con 3, el estimador no detectaba sus propios fallos: con ruido de
sala 30 veces más fuerte que la señal informaba "confiable" y erraba por 256 ms. Se subió a
15 con el barrido en simulación, donde una medición buena daba **500**; pero en una sala de
verdad una medición **buena** da **12**, así que 15 descartaba todo.

Lo que lo liquidó fue medir el otro extremo: correlacionando **ruido puro** contra una
referencia ausente, la confianza da **5,8 a 7,4**, y las mediciones reales buenas daban
**6,4 a 16**. Los rangos se superponen: no hay umbral que separe "no hay señal" de "hay
señal débil".

Lo que sí distingue esos dos casos es el **nivel** (`niveles`, que resta la línea de base y
devuelve casi cero cuando el parlante no suena), y lo que dice si la medición es correcta es
la **estabilidad** ante el tamaño de ventana (`calibrar`).
"""

DISPERSION_MAXIMA_MS = 1.0
"""Cuánto pueden discrepar entre sí las ventanas de un mismo tamaño.

**Necesario pero no suficiente**, y por eso no es el criterio final: varias ventanas pueden
encontrar el mismo pico equivocado y coincidir perfectamente en el error. El criterio es
`ESTABILIDAD_MAXIMA_MS`, que compara entre tamaños de ventana distintos."""


def gcc_phat(
    micro: np.ndarray,
    referencia: np.ndarray,
    sr: int = SR,
    retardo_maximo_ms: float = 500.0,
    banda_hz: tuple[float, float] | None = (200.0, 8000.0),
) -> Estimacion:
    """Retardo de `referencia` dentro de `micro`, por correlación cruzada generalizada.

    El retardo que devuelve incluye **todo** el camino: el buffer de A2DP, el códec y el
    tiempo de vuelo por el aire. Para este proyecto eso está bien, porque lo que se corrige
    es la **diferencia** entre parlantes, y los términos comunes se cancelan.

    `banda_hz` acota dónde se confía en la señal. Fuera de esa banda, PHAT estaría
    amplificando ruido: los parlantes chicos no dan graves y el micrófono no da agudos.
    """
    n = 1
    while n < len(micro) + len(referencia):
        n *= 2
    esp_micro = np.fft.rfft(micro, n=n)
    esp_ref = np.fft.rfft(referencia, n=n)
    cruzado = esp_micro * np.conj(esp_ref)

    if banda_hz is not None:
        frec = np.fft.rfftfreq(n, 1 / sr)
        cruzado[(frec < banda_hz[0]) | (frec > banda_hz[1])] = 0.0

    # El blanqueo de PHAT: solo se conserva la fase.
    magnitud = np.abs(cruzado)
    cruzado = np.divide(cruzado, magnitud, out=np.zeros_like(cruzado), where=magnitud > _PISO_ESPECTRO)

    correlacion = np.fft.irfft(cruzado, n=n)
    maximo = int(sr * retardo_maximo_ms / 1000)
    # Solo retardos positivos: la referencia siempre llega después de haberse emitido.
    ventana = np.abs(correlacion[: min(maximo, len(correlacion))])
    if ventana.size == 0 or ventana.max() <= 0:
        return Estimacion(0.0, 0.0)

    pico = int(np.argmax(ventana))
    confianza = float(ventana.max() / (np.median(ventana) + 1e-12))
    return Estimacion(_interpolar(ventana, pico) / sr * 1000.0, confianza)


def _interpolar(v: np.ndarray, i: int) -> float:
    """Ajuste parabólico con las muestras vecinas.

    Sin esto la resolución sería de una muestra (~21 µs a 48 kHz), que igual alcanzaría;
    con esto se gana casi un orden de magnitud y sale gratis.
    """
    if i <= 0 or i >= len(v) - 1:
        return float(i)
    a, b, c = v[i - 1], v[i], v[i + 1]
    denominador = a - 2 * b + c
    if denominador == 0:
        return float(i)
    return float(i + 0.5 * (a - c) / denominador)


def retardos_simultaneos(
    micro: np.ndarray,
    referencias: dict[str, np.ndarray],
    sr: int = SR,
    **kwargs,
) -> dict[str, Estimacion]:
    """Mide todos los parlantes de una sola vez, con una única grabación.

    Funciona porque las referencias están decorrelacionadas entre sí: al correlacionar el
    micrófono contra la de un parlante, los aportes de los demás no forman pico. Si las
    referencias estuvieran correlacionadas, cada medición traería los picos de los otros y
    el resultado sería basura, así que **la calidad de la decorrelación es también la
    calidad de la medición**.
    """
    return {n: gcc_phat(micro, ref, sr, **kwargs) for n, ref in referencias.items()}


def niveles(
    micro: np.ndarray,
    referencias: dict[str, np.ndarray],
    retardos_ms: dict[str, float],
    sr: int = SR,
    ventana_ms: float = 80.0,
) -> dict[str, float]:
    """Cuánto aporta cada parlante al micrófono. Solo importan las **razones** entre ellos.

    Se calcula como la energía del filtro adaptado —la correlación cruzada sin blanquear—
    en una ventana alrededor del pico, que cubre el sonido directo y las reflexiones
    tempranas de ese parlante.

    **Por qué así y no por mínimos cuadrados**, que es lo que hacía antes y parecía más
    elegante: la proyección de mínimos cuadrados exige que la referencia esté alineada con
    precisión de muestra, y a 48 kHz medio milisegundo son 24 muestras, suficiente para
    destruir la correlación. Además, en una sala el micrófono capta más campo reverberante
    que sonido directo, así que el ajuste termina intentando explicar la reverberación con
    copias retardadas y queda mal condicionado.

    Medido con parlantes, entre dos corridas de la misma sala sin mover nada: el estimador
    por mínimos cuadrados daba diferencias de hasta **16 dB** entre corridas; este da
    **0,3 dB**. El retardo, en cambio, coincidía en las dos (0,80 y 0,79 ms), así que el
    problema era del estimador de nivel, no de la medición.

    `retardos_ms` se usa solo como pista de dónde buscar el pico; no hace falta que sea
    exacto.
    """
    resultado = {}
    for nombre, ref in referencias.items():
        energia_ref = float((ref**2).sum())
        if energia_ref <= 0:
            resultado[nombre] = 0.0
            continue
        n = 1
        while n < len(micro) + len(ref):
            n *= 2
        correlacion = np.fft.irfft(np.fft.rfft(micro, n=n) * np.conj(np.fft.rfft(ref, n=n)), n=n)
        pedido = retardos_ms.get(nombre, 0.0)
        limite = round(sr * 1.5)
        if np.isfinite(pedido):
            centro = round(sr * pedido / 1000)
            centro = min(max(centro, 0), max(limite - 1, 0))
        else:
            centro = int(np.argmax(np.abs(correlacion[:limite])))
        ancho = int(sr * ventana_ms / 1000)
        # Un poco antes del pico y bastante después: las reflexiones llegan detrás.
        ini, fin = max(0, centro - ancho // 4), min(len(correlacion), centro + ancho)
        trozo = correlacion[ini:fin]
        if trozo.size == 0:
            resultado[nombre] = 0.0
            continue
        # Línea de base: lejos del pico solo hay diafonía de los otros parlantes y ruido.
        # Restarla evita sobreestimar a los parlantes flojos, que es hacia donde se iba el
        # estimador: con ganancias reales de 0,5 y 0,25 devolvía 0,568 y 0,310; con la resta
        # devuelve 0,538 y 0,242. Y vuelve el resultado casi cero cuando el parlante no
        # suena, que es lo que permite detectarlo.
        lejos = np.concatenate([correlacion[fin : fin + 10 * ancho], correlacion[max(0, ini - 10 * ancho) : ini]])
        piso = float((lejos**2).mean()) if lejos.size else 0.0
        energia = max(0.0, float((trozo**2).sum()) - piso * trozo.size)
        resultado[nombre] = float(np.sqrt(energia) / energia_ref)
    return resultado


def parlantes_sin_sonar(niveles_medidos: dict[str, float], fraccion: float = 0.1) -> list[str]:
    """Los parlantes cuyo aporte al micrófono es despreciable frente a los demás.

    Reemplaza a la confianza como detector de "este parlante no está sonando", que es para
    lo que la confianza no alcanzaba: su valor con ruido puro se superpone con el de una
    señal débil pero real.

    **Es un detector relativo, y con un solo parlante no puede decir nada**, porque no hay
    con qué comparar. En términos absolutos la diferencia existe —una referencia ausente dio
    0,36 y una presente 12,4 en la misma medición—, pero esa escala depende del nivel de
    reproducción y del ruido de la sala, así que no se puede fijar un umbral portátil. Con
    dos o más parlantes, que es siempre el caso real, la comparación entre ellos sí sirve.
    """
    if len(niveles_medidos) < 2:  # noqa: PLR2004
        return []
    if not niveles_medidos:
        return []
    referencia = max(abs(v) for v in niveles_medidos.values())
    if referencia <= 0:
        return sorted(niveles_medidos)
    return sorted(n for n, v in niveles_medidos.items() if abs(v) < fraccion * referencia)


def ganancias_para_igualar(niveles_medidos: dict[str, float]) -> dict[str, float]:
    """Pasa los niveles medidos a la corrección en dB que iguala a todos.

    Se toma como objetivo el parlante **más flojo**: subirle el nivel a los demás podría
    saturarlos, y bajarlos es siempre seguro. Un parlante que no suena (nivel ~0) se deja
    sin corregir en vez de pedirle una ganancia infinita.
    """
    utiles = {n: abs(v) for n, v in niveles_medidos.items() if abs(v) > _NIVEL_AUDIBLE}
    if not utiles:
        return dict.fromkeys(niveles_medidos, 0.0)
    objetivo = min(utiles.values())
    return {
        n: (20.0 * np.log10(objetivo / abs(v)) if abs(v) > _NIVEL_AUDIBLE else 0.0) for n, v in niveles_medidos.items()
    }


def alineacion_gruesa(
    micro: np.ndarray,
    referencias: dict[str, np.ndarray],
    sr: int = SR,
    retardo_maximo_ms: float = 1500.0,
    tolerancia_ms: float = 20.0,
) -> float | None:
    """El desfase global entre la grabación y las referencias, o `None` si no se puede.

    Hace falta porque la grabación arranca antes que la reproducción y el camino A2DP mete
    cientos de milisegundos de buffer. Sin corregirlo, las ventanas de la calibración
    compararían trozos que no se corresponden.

    **Se estima con todas las referencias y se busca un consenso.** Todos los parlantes
    arrancan en el mismo instante, así que el desfase grueso es **uno solo**: cada canal es
    una medición independiente del mismo número.

    Dos versiones anteriores fallaron, y las dos dejaron enseñanza:

    1. Estimarlo con **un solo canal** daba un pico espurio de vez en cuando: en una
       medición real devolvió 59 ms donde el valor era 985, y todo lo de después quedó sin
       sentido.
    2. Exigir que **todos** los canales coincidieran resultó demasiado estricto: en otra
       medición, dos canales daban 960,4 y 958,0 ms —el valor correcto— y el tercero, el
       más flojo, daba 116,2. La unanimidad descartaba la medición entera por culpa del peor
       canal.

    Ahora se toma el **grupo más grande que concuerda** dentro de `tolerancia_ms`, y se
    acepta si reúne al menos a la mitad de los canales. El que se va del consenso no arrastra
    a los demás: no aporta al desfase grueso, y después la medición fina dirá si ese parlante
    sirve.
    """
    estimaciones = [
        gcc_phat(micro, ref, sr, retardo_maximo_ms=retardo_maximo_ms).retardo_ms for ref in referencias.values()
    ]
    if not estimaciones:
        return None

    mejor: list[float] = []
    for centro in estimaciones:
        grupo = [e for e in estimaciones if abs(e - centro) <= tolerancia_ms]
        if len(grupo) > len(mejor):
            mejor = grupo
    # Con un solo canal no hay con qué corroborar; con dos o más, hacen falta al menos dos
    # que coincidan, o "consenso" no significaría nada.
    minimo = 1 if len(estimaciones) == 1 else max(2, (len(estimaciones) + 1) // 2)
    if len(mejor) < minimo:
        return None
    return float(np.median(mejor))


def alinear(
    micro: np.ndarray, referencias: dict[str, np.ndarray], desfase_ms: float, sr: int = SR
) -> dict[str, np.ndarray]:
    """Corre las referencias `desfase_ms` para que coincidan con la grabación."""
    d = max(0, round(sr * desfase_ms / 1000))
    alineadas = {}
    for nombre, ref in referencias.items():
        pad = np.zeros(len(micro))
        largo = min(len(ref), len(micro) - d)
        if largo > 0:
            pad[d : d + largo] = ref[:largo]
        alineadas[nombre] = pad
    return alineadas


def calibrar_por_ventanas(
    micro: np.ndarray,
    referencias: dict[str, np.ndarray],
    sr: int = SR,
    ventana_s: float = 2.0,
    **kwargs,
) -> tuple[dict[str, float], dict[str, float]]:
    """Mide en varias ventanas cortas y devuelve (mediana, dispersión) por parlante.

    **Por qué en ventanas y no de una sola pasada.** El barrido de
    `probes/calibracion/comparar.py` mostró que con ruido de banda ancha **2 segundos ya
    dan toda la precisión**: alargar la medición no mejora el número. Lo que sí aporta el
    tiempo extra es **poder comparar mediciones independientes entre sí**, y eso da dos
    cosas que una sola pasada no puede dar:

    - una **mediana**, que descarta una ventana arruinada por un portazo o un bache del
      stream;
    - una **dispersión**, que es la única forma de saber si la calibración sirvió, sin
      conocer la respuesta correcta.

    Con 10 segundos salen 5 ventanas: rápido de aguantar para quien está en la sala, y
    suficiente para que la mediana signifique algo.
    """
    n_ventana = int(sr * ventana_s)
    if n_ventana <= 0:
        msg = f"la ventana tiene que ser positiva, se pidió {ventana_s} s"
        raise ValueError(msg)
    n_ventanas = max(1, len(micro) // n_ventana)

    por_parlante: dict[str, list[float]] = {n: [] for n in referencias}
    for k in range(n_ventanas):
        ini, fin = k * n_ventana, (k + 1) * n_ventana
        trozo_micro = micro[ini:fin]
        trozo_refs = {n: r[ini:fin] for n, r in referencias.items() if len(r) >= fin}
        if len(trozo_refs) != len(referencias):
            continue
        est = retardos_simultaneos(trozo_micro, trozo_refs, sr, **kwargs)
        # Solo se tira lo que es ruido puro. Una ventana con confianza modesta pero que
        # coincide con las demás es información buena, y descartarla fue el error que hizo
        # que la primera calibración real no devolviera nada.
        if not all(e.confiable for e in est.values()):
            continue
        # Cada ventana se vuelve relativa por separado: así un corrimiento común entre
        # ventanas (por ejemplo un rebuffer del stream) no ensucia la dispersión.
        base = min(e.retardo_ms for e in est.values())
        for nombre, e in est.items():
            por_parlante[nombre].append(e.retardo_ms - base)

    medianas, dispersiones = {}, {}
    for nombre, valores in por_parlante.items():
        if not valores:
            medianas[nombre], dispersiones[nombre] = float("nan"), float("inf")
            continue
        v = np.array(valores)
        mediana = float(np.median(v))
        medianas[nombre] = mediana
        dispersiones[nombre] = float(np.median(np.abs(v - mediana))) * 1.4826
    return medianas, dispersiones


VENTANAS_DE_CONTROL = (0.25, 0.5, 1.0)
"""Tamaños de ventana con los que se repite el análisis para comprobar la estabilidad."""

ESTABILIDAD_MAXIMA_MS = 1.5
"""Cuánto puede moverse el resultado al cambiar el tamaño de ventana para darlo por bueno."""


@dataclass(frozen=True)
class Calibracion:
    """El resultado de calibrar: qué corregir en cada parlante y si se puede confiar."""

    retardos_ms: dict[str, float]
    """Relativos al parlante que llega primero. Ya son la corrección a aplicar."""
    ganancias_db: dict[str, float]
    estabilidad_ms: dict[str, float]
    """Cuánto se movió cada estimación al cambiar el tamaño de ventana de análisis."""
    desfase_grueso_ms: float

    @property
    def confiable(self) -> bool:
        return bool(self.estabilidad_ms) and all(
            np.isfinite(v) and v <= ESTABILIDAD_MAXIMA_MS for v in self.estabilidad_ms.values()
        )

    def dudosos(self) -> list[str]:
        """Los parlantes cuya medición no se sostiene."""
        return sorted(n for n, v in self.estabilidad_ms.items() if not np.isfinite(v) or v > ESTABILIDAD_MAXIMA_MS)


def calibrar(micro: np.ndarray, referencias: dict[str, np.ndarray], sr: int = SR) -> Calibracion | None:
    """La calibración completa, sin que nadie escriba un número.

    Devuelve `None` si ni siquiera se pudo alinear la grabación con las referencias.

    **El criterio de validez es la estabilidad ante el tamaño de ventana**, y no otros dos
    que se probaron antes y fallaron:

    - *la coincidencia entre ventanas del mismo tamaño* es insuficiente: varias ventanas
      pueden encontrar el mismo pico equivocado y coincidir perfectamente en el error;
    - *el residuo de reconstrucción* no sirve en una sala reverberante, porque el micrófono
      capta mucho más campo reverberante que sonido directo: en una medición real el residuo
      dio 0,999 con una estimación que después resultó correcta.

    La estabilidad sí discrimina. Medido con parlantes: con referencias contaminadas entre sí,
    un canal se movía **5,45 ms** al cambiar la ventana; con referencias independientes, el
    mismo canal se movía **0,14 ms**.
    """
    grueso = alineacion_gruesa(micro, referencias, sr)
    if grueso is None:
        return None
    alineadas = alinear(micro, referencias, grueso, sr)

    por_ventana: dict[str, list[float]] = {n: [] for n in referencias}
    for ventana in VENTANAS_DE_CONTROL:
        medianas, _ = calibrar_por_ventanas(micro, alineadas, sr, ventana_s=ventana, retardo_maximo_ms=120.0)
        primera = next(iter(referencias))
        base = medianas.get(primera, float("nan"))
        if not np.isfinite(base):
            continue
        for nombre, valor in medianas.items():
            if np.isfinite(valor):
                por_ventana[nombre].append(valor - base)

    retardos, estabilidad = {}, {}
    for nombre, valores in por_ventana.items():
        if not valores:
            retardos[nombre], estabilidad[nombre] = float("nan"), float("inf")
            continue
        retardos[nombre] = float(np.median(valores))
        estabilidad[nombre] = float(max(valores) - min(valores))

    # Los retardos son relativos al primer parlante, así que algunos son negativos. Para
    # medir niveles hay que alinear cada referencia **hacia adelante**, porque un
    # desplazamiento negativo no se puede aplicar sobre la grabación: se corren todos para
    # que el más temprano quede en cero. Sin esto, los parlantes con retardo negativo
    # quedaban con una columna de ceros y su ganancia salía 0, que fue exactamente el
    # síntoma que destapó el error.
    finitos = [v for v in retardos.values() if np.isfinite(v)]
    origen = min(finitos) if finitos else 0.0
    para_nivel = {n: (v - origen if np.isfinite(v) else 0.0) for n, v in retardos.items()}
    niveles_medidos = niveles(micro, alineadas, para_nivel, sr)
    return Calibracion(
        retardos_ms=correcciones(retardos),
        ganancias_db=ganancias_para_igualar(niveles_medidos),
        estabilidad_ms=estabilidad,
        desfase_grueso_ms=grueso,
    )


def residuo_relativo(
    micro: np.ndarray,
    referencias: dict[str, np.ndarray],
    retardos_ms: dict[str, float],
    ganancias: dict[str, float],
    sr: int = SR,
) -> float:
    """Qué fracción de la grabación **no** explica el modelo estimado, entre 0 y ~1.

    Se reconstruye lo que tendría que haber captado el micrófono con los retardos y
    ganancias estimados, y se compara con lo que captó de verdad. Cerca de 0 significa que
    el modelo explica la grabación; cerca de 1, que no explica nada.

    **Este es el criterio de validez, y reemplaza al que había antes.** Se usaba la
    coincidencia entre ventanas, y resultó insuficiente: en un barrido con niveles
    desparejos, las estimaciones se equivocaban por **22 ms** mientras informaban una
    dispersión de 0,000 ms entre ventanas. Es que varias ventanas pueden encontrar **el
    mismo** pico equivocado —una fuga sistemática de otro parlante— y coincidir
    perfectamente en el error.

    El residuo no tiene ese problema: un retardo equivocado no reconstruye la grabación,
    por mucho que varias ventanas se pongan de acuerdo en él.

    **Lo que el residuo tampoco puede hacer:** bajar de lo que aporta la sala. La
    reverberación y el ruido de fondo no están en el modelo, así que el residuo nunca da
    cero en una grabación real. Sirve para comparar, no como valor absoluto.
    """
    reconstruido = np.zeros(len(micro))
    for nombre, ref in referencias.items():
        retardo = retardos_ms.get(nombre, 0.0)
        ganancia = ganancias.get(nombre, 0.0)
        if not np.isfinite(retardo) or not np.isfinite(ganancia):
            continue
        d = round(sr * retardo / 1000)
        if d < 0 or d >= len(micro):
            continue
        largo = min(len(ref), len(micro) - d)
        reconstruido[d : d + largo] += ganancia * ref[:largo]
    energia = float((micro**2).sum())
    if energia <= 0:
        return 1.0
    return float(((micro - reconstruido) ** 2).sum() / energia)


def calibracion_confiable(dispersiones: dict[str, float]) -> bool:
    """Coincidencia entre ventanas. **Necesaria pero NO suficiente.**

    Se conserva como primer filtro barato, pero la validez de verdad la decide
    `residuo_relativo`: varias ventanas pueden coincidir en el mismo valor equivocado si hay
    una fuga sistemática entre canales.
    """
    return bool(dispersiones) and all(np.isfinite(d) and d <= DISPERSION_MAXIMA_MS for d in dispersiones.values())


def relativos_a(estimaciones: dict[str, Estimacion], referencia: str) -> dict[str, float]:
    """Pasa los retardos absolutos a diferencias contra un parlante.

    Es lo que se usa para corregir: el retardo absoluto incluye el buffer de A2DP, que es
    grande y no importa mientras sea igual para todos.
    """
    if referencia not in estimaciones:
        msg = f"{referencia!r} no está entre {sorted(estimaciones)}"
        raise KeyError(msg)
    base = estimaciones[referencia].retardo_ms
    return {n: e.retardo_ms - base for n, e in estimaciones.items()}


def correcciones(relativos: dict[str, float]) -> dict[str, float]:
    """Convierte las diferencias medidas en el retardo que hay que **agregar** a cada uno.

    Al que llega último no se le agrega nada; a los demás, lo que les falta. Así no se suma
    latencia de más al sistema entero.
    """
    if not relativos:
        return {}
    ultimo = max(relativos.values())
    return {n: ultimo - v for n, v in relativos.items()}
