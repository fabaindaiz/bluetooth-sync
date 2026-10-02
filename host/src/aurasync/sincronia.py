"""El lazo cerrado: decide cuándo creerle a una medición y la escribe sin que se oiga.

Este módulo es la mitad que falta del diseño. La otra es `dsp/retardo.py`, que hace que
escribir un retardo nuevo **no suene**; acá se decide **qué** escribir.

**¿Hace falta un lazo, o alcanza con calibrar una vez? No está resuelto, y los documentos
del repositorio se contradicen.**

- **E6** midió que con 2 o 3 Go 4 iguales el desfase es **repetible a 0,1 ms entre
  reproducciones distintas**, y concluye explícitamente *"sin necesidad de recalibrar en cada
  arranque"* (`docs/research/experimentos/05-e6-a2dp-un-canal-por-parlante.md`).
- **La calibración rápida** vio lo contrario en un canal: Blue dio -7,45 ms en una corrida y
  -2,99 en la siguiente, con los parlantes sin tocar
  (`docs/research/experimentos/06-calibracion-rapida-y-recalibracion.md`). Ese documento
  mismo lo deja como pregunta abierta en *Lo que falta* §1.

**Y hay una hipótesis que reconciliaría las dos, que conviene probar antes de creerle al
lazo:** esos 4,5 ms de discrepancia se midieron con el error de retardos negativos de
`gcc_phat`, arreglado el 2026-09-29, y **tienen su firma exacta** —un canal muy corrido
mientras los otros coinciden a 0,01 ms, sin que el filtro de validez se entere
(`docs/research/experimentos/08-lazo-de-recalibracion-en-simulacion.md` §1)—. Si la
hipótesis es cierta, la variación entre arranques no existe y este lazo solo hace falta para
la deriva.

Así que el controlador **no supone ninguna deriva**: la estima sobre la marcha con
`deriva_ms_h`, y la sesión real termina siendo la medición. La deriva dentro de una sesión
tampoco está medida: la corrida de 30 minutos quedó invalidada por el estímulo.

**El problema de fondo es que el sensor miente a veces.** El estimador de retardo se
equivoca de pico y devuelve un número perfectamente formado que está mal por decenas de
ms; eso ya pasó, y es lo que se documenta en `medicion.calibrar`. Un lazo que escriba
todo lo que mide va a mover los parlantes a un lugar peor que donde estaban. De ahí que
todo este módulo sea, básicamente, **filtros contra el propio sensor**:

| Filtro | Qué descarta | De dónde sale el valor |
|---|---|---|
| estabilidad (`Calibracion.confiable`) | la medición que cambia según el tamaño de ventana | `medicion.ESTABILIDAD_MAXIMA_MS` |
| zona muerta | corregir por debajo del ruido del propio instrumento | el MAD de 0,12 a 0,35 ms de E6 |
| salto máximo | el pico equivocado, que casi siempre cae lejos | plausibilidad: ver `SALTO_MAXIMO_MS` |
| confirmación | deja pasar el salto grande **si se repite** | un resync real sí se repite; un error de pico, no |
| ganancia del lazo | que el lazo oscile alrededor del objetivo | `FACTOR_POR_DEFECTO` |

**`confirmar_todo` es para cuando la referencia es el propio contenido.** Recalibrar sin
interrumpir es tentador medir contra la música que ya está sonando, y en simulación funciona
—a 10 s de segmento el error queda en 0,01 ms—, pero **falla en silencio**: con referencias
correlacionadas entre sí, un segmento de 4 s dio 8,05 ms de error informando estabilidad de
0,32 ms, o sea pasando el filtro. Lo que sí distingue ese error de un desfase verdadero es
que **no se repite**: entre segmentos independientes el error saltaba de 8,05 a 0,00 mientras
que un desfase real está ahí las dos veces. Con `confirmar_todo` se le pide confirmación a
cualquier cambio, no solo a los grandes. Está en
`docs/research/experimentos/08-lazo-de-recalibracion-en-simulacion.md`.

**Y el detalle que es fácil de tener al revés:** cuando se recalibra mientras suena, lo que
el micrófono mide **no** es el desfase de los parlantes, es *lo que queda* después de las
correcciones que ya están aplicadas. Entonces la corrección nueva se **suma** a la vigente,
no la reemplaza. Tratarla como un reemplazo haría que el lazo deshiciera su propio trabajo
en cada vuelta.
"""

from __future__ import annotations

import multiprocessing
import time
from concurrent.futures import Future, ProcessPoolExecutor, ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from collections.abc import Callable

    from aurasync.config import Instalacion
    from aurasync.medicion import Calibracion
    from aurasync.motor import Motor

ZONA_MUERTA_MS = 0.5
"""Por debajo de esto no se corrige nada.

El instrumento tiene su propio ruido: repitiendo la medición sobre los mismos parlantes, el
MAD dio 0,12 a 0,35 ms (`docs/research/experimentos/05-e6-a2dp-un-canal-por-parlante.md`).
Corregir por debajo de eso no mejora la alineación, solo mueve los retardos siguiendo ruido.
El valor está por encima del peor MAD medido, con margen."""

ZONA_MUERTA_DB = 1.0
"""Lo mismo para el nivel. El estimador por energía con resta de línea de base repite
dentro de 0,3 dB, y una diferencia de 1 dB está en el límite de lo que se nota."""

SALTO_MAXIMO_MS = 15.0
"""Un cambio mayor que esto necesita confirmarse antes de aplicarse.

No es que sea imposible: si un stream A2DP se resincroniza, el salto es real. Es que un
error de pico del estimador **también** cae acá, y es más frecuente. La diferencia entre
los dos es que el salto real se repite en la medición siguiente y el error de pico no, así
que el criterio no es descartarlo sino **pedirle que se repita**."""

SALTO_MAXIMO_DB = 6.0
"""Ídem para el nivel."""

FACTOR_POR_DEFECTO = 0.5
"""Qué fracción del error medido se corrige en cada vuelta.

Con 1,0 el lazo intenta anular el error de una sola vez, y como entre medir y oír el efecto
pasa más de un ciclo, eso es una receta para oscilar alrededor del objetivo. Con 0,5 el
error se divide a la mitad en cada vuelta: desde 5 ms de desalineación se entra en la zona
muerta en cuatro vueltas, que con ciclos de 20 s es poco más de un minuto."""

CONFIRMACIONES = 2
"""Cuántas mediciones seguidas tienen que coincidir para aceptar un salto grande."""

_NADA_MS = 1e-12
"""Por debajo de esto un retardo es cero: evita reescribir la instalación por ruido de
coma flotante en cada vuelta del lazo."""


class VentanaDeEmision:
    """Guarda los últimos segundos de lo que se le mandó a cada parlante.

    Es la **referencia** contra la que el lazo correlaciona el micrófono. No hace falta
    emitir ningún estímulo: se usa el contenido que ya está sonando, y funciona porque el
    motor le manda a cada parlante una versión decorrelacionada — *la condición del efecto
    envolvente es la condición de la medición*.

    **Dos números y por qué son esos** (de
    `docs/research/experimentos/08-lazo-de-recalibracion-en-simulacion.md` §2 y §3):

    - **`segundos_de_medicion` = 10.** Con 4 s el estimador falla en silencio: dos de cada
      tres mediciones que pasan el filtro de validez están mal por más de 1 ms. Con 10 s
      ninguna de las que pasa se equivoca por más de 1 ms.
    - **`margen_s` = 1,0.** Lo último que se escribió **todavía no se oyó**: está en el
      buffer de `pw-play` y en el del parlante. Si entrara en la referencia, ese tramo no
      tendría correspondencia en la grabación. El margen tiene que ser **mayor que la
      latencia total de reproducción** —los 200 ms de `pw-play` más el buffer de A2DP y del
      parlante—, y un segundo le deja lugar de sobra.

    **La ventana del micrófono que va con esto es `segundos + 1,5`**, y el número no es
    libre: `medicion.alineacion_gruesa` busca el desfase entre grabación y referencia solo
    hasta **1500 ms**. Con esta combinación la referencia empieza a `1,0 s + latencia`
    dentro de la grabación, así que hay lugar para latencias de hasta medio segundo antes de
    quedarse sin rango. Si los parlantes resultaran más lentos, el lazo no alinearía y lo
    diría: el registro mostraría *no se pudo alinear la grabación con las referencias*.
    """

    SEGUNDOS_DE_MEDICION = 10.0
    MARGEN_S = 1.0
    MARGEN_DEL_MICROFONO_S = 1.5
    """Cuánto más larga que la ventana de medición tiene que ser la tajada del micrófono."""

    def __init__(self, nombres: list[str], sr: int = 48000, segundos: float = 12.0) -> None:
        if not nombres:
            msg = "no se indicó ningún parlante"
            raise ValueError(msg)
        self.sr = sr
        self.segundos = segundos
        self._n = max(1, int(sr * segundos))
        self._anillos = {n: np.zeros(self._n) for n in nombres}
        self._escritos = 0
        self._pos = 0

    def limpiar(self) -> None:
        for anillo in self._anillos.values():
            anillo[:] = 0.0
        self._escritos = 0
        self._pos = 0

    def agregar(self, bloques: dict[str, np.ndarray]) -> None:
        """Un bloque por parlante, exactamente lo que se le mandó al reproductor."""
        largos = {len(x) for x in bloques.values()}
        if len(largos) > 1:
            msg = f"los bloques tienen largos distintos: {sorted(largos)}"
            raise ValueError(msg)
        n = largos.pop() if largos else 0
        if n == 0:
            return
        if set(bloques) != set(self._anillos):
            msg = f"se esperaban los parlantes {sorted(self._anillos)}, llegaron {sorted(bloques)}"
            raise ValueError(msg)

        if n >= self._n:
            for nombre, x in bloques.items():
                self._anillos[nombre][:] = x[-self._n :]
            self._pos = 0
        else:
            fin = self._pos + n
            for nombre, x in bloques.items():
                anillo = self._anillos[nombre]
                if fin <= self._n:
                    anillo[self._pos : fin] = x
                else:
                    corte = self._n - self._pos
                    anillo[self._pos :] = x[:corte]
                    anillo[: n - corte] = x[corte:]
            self._pos = fin % self._n if fin <= self._n else n - (self._n - self._pos)
        self._escritos += n

    @property
    def lleno(self) -> bool:
        return self._escritos >= self._n

    def referencias(
        self, segundos: float = SEGUNDOS_DE_MEDICION, margen_s: float = MARGEN_S
    ) -> dict[str, np.ndarray] | None:
        """La ventana de medición, o `None` si todavía no hay suficiente emitido."""
        n = int(self.sr * segundos)
        margen = int(self.sr * margen_s)
        if n <= 0 or n + margen > self._n or self._escritos < n + margen:
            return None
        salida = {}
        for nombre, anillo in self._anillos.items():
            ordenado = np.concatenate([anillo[self._pos :], anillo[: self._pos]])
            salida[nombre] = ordenado[len(ordenado) - n - margen : len(ordenado) - margen].copy()
        return salida

    def hay_senal(self, referencias: dict[str, np.ndarray], minimo_rms: float = 0.005) -> bool:
        """Si vale la pena medir con esto.

        Sin señal no hay nada que correlacionar, y el estimador devolvería un número igual.
        El umbral es holgado a propósito: -46 dBFS de RMS es música muy baja pero audible.
        """
        return all(float(np.sqrt(np.mean(x**2))) >= minimo_rms for x in referencias.values())


class MedicionEnSegundoPlano:
    """Corre la medición en un hilo aparte, porque en el hilo de audio cuesta demasiado.

    **El número que obliga a esto:** `medicion.calibrar` sobre 10 s y tres parlantes tarda
    **1,00 s de CPU** (medido en `PC-Ryzen5`). El lazo de reproducción entrega bloques de
    85 ms con 200 ms de buffer, así que un segundo de cálculo no es una molestia: vacía el
    buffer de los parlantes.

    Y vaciarlo sería peor que no medir. Un stream A2DP que se queda sin datos se
    resincroniza, y al volver trae **un desfase distinto**
    (`docs/research/experimentos/05-e6-a2dp-un-canal-por-parlante.md`): la medición habría
    destruido justamente lo que quería medir.

    **Un hilo, no un proceso: MEDIDO el 2026-10-02.** Se probó `en_proceso=True` sospechando
    que la medición le quitaba el GIL al motor. Fue peor: con un proceso, el hilo que lanza
    se frenó hasta 15 ms (serializar ~2 MB de referencias) y el primer lanzamiento tardó 23 ms;
    con el hilo, la pausa máxima fue de 1 a 4 ms. La FFT de numpy suelta el GIL, como decía
    esta nota. Y un proceso con `forkserver` re-importa el script principal: sin la guarda
    `if __name__ == "__main__"`, el lanzamiento falló y tiró abajo la sesión. La opción queda
    para comparar; la sesión usa el hilo.
    """

    def __init__(self, funcion: Callable[..., object], *, en_proceso: bool = False) -> None:
        self.funcion = funcion
        if en_proceso:
            self._pool = ProcessPoolExecutor(max_workers=1, mp_context=multiprocessing.get_context("forkserver"))
        else:
            self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="aurasync-medicion")
        self._futuro: Future | None = None
        self.lanzadas = 0

    @property
    def ocupado(self) -> bool:
        return self._futuro is not None and not self._futuro.done()

    def lanzar(self, *args, **kwargs) -> bool:
        """Arranca una medición. Devuelve `False` si ya hay una corriendo.

        Nunca se encolan dos: si la anterior no terminó, esta vuelta se saltea. Medir con
        datos viejos no aporta, y acumular trabajo atrasado sí molesta.
        """
        if self.ocupado:
            return False
        self._futuro = self._pool.submit(self.funcion, *args, **kwargs)
        self.lanzadas += 1
        return True

    def recoger(self) -> tuple[bool, object]:
        """`(hay_resultado, resultado)`. Sin bloquear.

        Devuelve una tupla y no el resultado directo porque `calibrar` puede devolver
        `None` legítimamente —no se pudo alinear—, y eso hay que poder distinguirlo de
        "todavía no terminó".
        """
        if self._futuro is None or not self._futuro.done():
            return False, None
        futuro, self._futuro = self._futuro, None
        try:
            return True, futuro.result()
        except Exception as error:  # noqa: BLE001
            # Que falle una medición no puede cortar la reproducción: se informa y se sigue.
            return True, error

    def cerrar(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)


@dataclass(frozen=True)
class Ajuste:
    """Qué hizo el controlador con una medición, y por qué."""

    aceptado: bool
    motivo: str
    """Texto para el log y para la CLI. Explica la decisión, sea cual sea."""
    cambios_ms: dict[str, float] = field(default_factory=dict)
    """Cuánto se movió el objetivo de cada parlante. Vacío si no se aceptó."""
    cambios_db: dict[str, float] = field(default_factory=dict)

    @property
    def hubo_cambios(self) -> bool:
        return bool(self.cambios_ms or self.cambios_db)


class Controlador:
    """Recibe calibraciones, decide si valen y las escribe en la instalación.

    No mide ni reproduce: recibe una `Calibracion` ya hecha. Así se puede probar entero
    sin parlantes, que es lo que hace `tests/test_sincronia.py`.
    """

    def __init__(
        self,
        instalacion: Instalacion,
        motor: Motor | None = None,
        *,
        zona_muerta_ms: float = ZONA_MUERTA_MS,
        zona_muerta_db: float = ZONA_MUERTA_DB,
        salto_maximo_ms: float = SALTO_MAXIMO_MS,
        salto_maximo_db: float = SALTO_MAXIMO_DB,
        factor: float = FACTOR_POR_DEFECTO,
        confirmaciones: int = CONFIRMACIONES,
        confirmar_todo: bool = False,
        reloj: Callable[[], float] = time.monotonic,
    ) -> None:
        self.instalacion = instalacion
        self.motor = motor
        self.zona_muerta_ms = zona_muerta_ms
        self.zona_muerta_db = zona_muerta_db
        self.salto_maximo_ms = salto_maximo_ms
        self.salto_maximo_db = salto_maximo_db
        self.factor = factor
        self.confirmaciones = confirmaciones
        self.confirmar_todo = confirmar_todo
        self.reloj = reloj
        self._pendiente: dict[str, float] | None = None
        self._repeticiones = 0
        self._historial: list[tuple[float, dict[str, float]]] = []
        self.ajustes: list[Ajuste] = []

    # -- el lazo --------------------------------------------------------------------

    def proponer(self, calibracion: Calibracion | None) -> Ajuste:
        """El paso completo: valida, decide y, si corresponde, escribe."""
        ajuste = self._decidir(calibracion)
        self.ajustes.append(ajuste)
        return ajuste

    def _decidir(self, calibracion: Calibracion | None) -> Ajuste:
        if calibracion is None:
            return Ajuste(False, "no se pudo alinear la grabación con las referencias")

        # 1. Estabilidad. Si falla un solo parlante se descarta la medición **entera**: los
        # retardos son relativos entre sí y después se renormalizan, así que un canal mal
        # medido corre a todos los demás. No hay forma de salvar la parte buena.
        if not calibracion.confiable:
            dudosos = ", ".join(calibracion.dudosos())
            return Ajuste(False, f"la medición no es estable en: {dudosos}")

        propuesta = self._componer(calibracion.retardos_ms)
        deltas = {n: propuesta[n] - self._vigente()[n] for n in propuesta}

        # 2. Zona muerta: si nadie se movió más que el ruido del instrumento, no se toca.
        if all(abs(d) <= self.zona_muerta_ms for d in deltas.values()) and not self._hay_nivel_que_corregir(
            calibracion
        ):
            self._pendiente, self._repeticiones = None, 0
            return Ajuste(True, "ya está alineado dentro del ruido de la medición")

        # 3. Salto sospechoso: se retiene hasta que una medición siguiente lo confirme.
        umbral = 0.0 if self.confirmar_todo else self.salto_maximo_ms
        grandes = {n: d for n, d in deltas.items() if abs(d) > umbral}
        if grandes:
            return self._retener(propuesta, grandes)
        self._pendiente, self._repeticiones = None, 0

        return self._aplicar(deltas, calibracion)

    def _retener(self, propuesta: dict[str, float], grandes: dict[str, float]) -> Ajuste:
        """Guarda un salto grande a la espera de que se confirme."""
        parecido = self._pendiente is not None and all(
            abs(propuesta[n] - self._pendiente.get(n, float("inf"))) <= self.zona_muerta_ms for n in propuesta
        )
        if parecido:
            self._repeticiones += 1
        else:
            self._pendiente, self._repeticiones = propuesta, 1

        peor = max(grandes.items(), key=lambda kv: abs(kv[1]))
        if self._repeticiones >= self.confirmaciones:
            self._pendiente, self._repeticiones = None, 0
            deltas = {n: propuesta[n] - self._vigente()[n] for n in propuesta}
            return self._aplicar(deltas, None, confirmado=True)
        return Ajuste(
            False,
            f"cambio de {peor[1]:+.1f} ms en {peor[0]!r}: se espera confirmación "
            f"({self._repeticiones}/{self.confirmaciones})",
        )

    def _aplicar(
        self,
        deltas: dict[str, float],
        calibracion: Calibracion | None,
        *,
        confirmado: bool = False,
    ) -> Ajuste:
        """Escribe los objetivos nuevos, moviéndose solo una fracción del error."""
        cambios_ms = {}
        for nombre, delta in deltas.items():
            if abs(delta) <= self.zona_muerta_ms and not confirmado:
                continue
            parlante = self.instalacion.por_nombre(nombre)
            paso = delta * self.factor
            parlante.retardo_ms += paso
            cambios_ms[nombre] = paso

        cambios_db = self._aplicar_niveles(calibracion) if calibracion is not None else {}

        # Toda la instalación se corre para que el retardo más chico quede en cero: es la
        # forma de menor latencia del mismo alineamiento. El desplazamiento es común a
        # todos, así que no cambia la alineación relativa, que es lo que importa.
        self._normalizar()

        if self.motor is not None:
            self.motor.actualizar()
        self._historial.append((self.reloj(), self._vigente()))

        etiqueta = "confirmado, " if confirmado else ""
        peor = max((abs(v) for v in cambios_ms.values()), default=0.0)
        return Ajuste(
            True,
            f"{etiqueta}aplicado: el mayor ajuste fue de {peor:.2f} ms",
            cambios_ms=cambios_ms,
            cambios_db=cambios_db,
        )

    def _aplicar_niveles(self, calibracion: Calibracion) -> dict[str, float]:
        """Los niveles, con su propia zona muerta y su propio tope."""
        cambios = {}
        for nombre, correccion in calibracion.ganancias_db.items():
            if not np.isfinite(correccion) or abs(correccion) <= self.zona_muerta_db:
                continue
            # A diferencia del retardo, un nivel absurdo no se confirma: se recorta. Subir de
            # más un parlante lo satura, y eso sí se oye enseguida.
            acotada = float(np.clip(correccion, -self.salto_maximo_db, self.salto_maximo_db))
            parlante = self.instalacion.por_nombre(nombre)
            paso = acotada * self.factor
            parlante.ganancia_db += paso
            cambios[nombre] = paso
        return cambios

    # -- estado ---------------------------------------------------------------------

    def _vigente(self) -> dict[str, float]:
        return {p.nombre: p.retardo_ms for p in self.instalacion.parlantes}

    def _componer(self, medido: dict[str, float]) -> dict[str, float]:
        """Suma la corrección medida a la que ya está aplicada.

        Acá está el detalle que el módulo advierte arriba: el micrófono mide el **residuo**
        que queda con las correcciones puestas, no el desfase desnudo de los parlantes.
        """
        vigente = self._vigente()
        compuesta = {n: vigente.get(n, 0.0) + medido.get(n, 0.0) for n in vigente}
        minimo = min(compuesta.values(), default=0.0)
        return {n: v - minimo for n, v in compuesta.items()}

    def _normalizar(self) -> None:
        vigente = self._vigente()
        minimo = min(vigente.values(), default=0.0)
        if abs(minimo) < _NADA_MS:
            return
        for parlante in self.instalacion.parlantes:
            parlante.retardo_ms -= minimo

    def _hay_nivel_que_corregir(self, calibracion: Calibracion) -> bool:
        return any(np.isfinite(v) and abs(v) > self.zona_muerta_db for v in calibracion.ganancias_db.values())

    # -- lo que el lazo mide sin proponérselo ---------------------------------------

    MINIMO_PARA_DERIVA = 3

    def deriva_ms_h(self) -> dict[str, float] | None:
        """Cuánto se corre cada parlante por hora, según lo que el lazo tuvo que corregir.

        **Es la medición de deriva que falta en el repositorio**, hecha por el propio sistema
        en uso en vez de en una corrida dedicada. Si da casi cero, calibrar una vez por sesión
        alcanza y el lazo puede espaciarse; si da varios ms por hora, el lazo es obligatorio.

        Devuelve `None` hasta tener suficientes puntos separados en el tiempo. El resultado
        es INFERIDO, no MEDIDO: sale del mismo estimador cuyos errores este módulo filtra.
        """
        if len(self._historial) < self.MINIMO_PARA_DERIVA:
            return None
        t = np.array([p[0] for p in self._historial])
        lapso = t[-1] - t[0]
        if lapso <= 0:
            return None
        t_h = (t - t[0]) / 3600
        nombres = self._historial[-1][1]
        salida = {}
        for nombre in nombres:
            y = np.array([h.get(nombre, np.nan) for _, h in self._historial])
            if not np.all(np.isfinite(y)):
                continue
            salida[nombre] = float(np.polyfit(t_h, y, 1)[0])
        return salida

    @property
    def historial(self) -> list[tuple[float, dict[str, float]]]:
        """Los retardos aplicados a lo largo del tiempo, con su marca de reloj."""
        return list(self._historial)
