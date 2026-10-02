"""La cadena de proceso: de estéreo a una señal por parlante.

El orden de las etapas no es arbitrario: sale de la figura 9 de Avendaño y Jot y de la
advertencia de Potard y Burnett, las dos en
`docs/research/09-efecto-ambiental-y-diseno-de-la-experiencia.md` §11.

```
estéreo
  ├─► ambiente (coherencia entre canales)  ──┐
  └─► directo (mezcla L/R según el pan)    ──┤
                                             ▼
                      mezcla por parlante (campo `ambiente`)
                                             │
                      decorrelador todo-paso, distinto por parlante
                                             │
                      retardo: calibración + Haas si lleva ambiente
                                             │
                      ganancia
                                             ▼
                                        un parlante
```

**Por qué el decorrelador va antes del retardo y no al revés.** El retardo sirve para
alejar perceptualmente al parlante (efecto Haas), y el decorrelador para que su señal no
se funda con las de los demás. Si se decorrelara después del retardo, el retardo actuaría
sobre una señal todavía correlacionada con el frente y produciría filtrado peine. El paper
las dibuja en este orden por esa razón.

**Y por qué la decorrelación no se hace con retardos**, que sería más simple: en parlantes
produce filtrado peine (Potard y Burnett). El retardo y la decorrelación son dos etapas
distintas con dos propósitos distintos.

El motor procesa **por bloques** y guarda el estado entre ellos, para poder alimentar un
flujo continuo. `procesar` devuelve exactamente tantas muestras como recibió.

**Lo que se mueve mientras suena** (el servicio de control,
`docs/superpowers/specs/2026-09-29-control-service-design.md` §6). Los mecanismos viven en
`dsp/ramps.py`; acá solo se enchufan:

- `pan` y `ambiente` se suavizan a 2 unidades/s: se leen de la instalación en cada bloque,
  pero el valor que suena se acerca al nuevo sin saltar;
- el extractor de ambiente **no se apaga nunca**: `extraer_ambiente_activo` mueve un factor
  global que multiplica el `ambiente` de cada parlante, y así la latencia no cambia;
- `volumen_db` es la ganancia de salida, con rampa de 30 dB/s;
- lo que ninguna rampa disimula —un preset, prender o apagar el decorrelador, un retardo que
  tardaría más de 2 s en llegar— pasa por el **corte**: baja a cero en 80 ms, salta todo con
  la salida en cero y vuelve a subir.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

from aurasync.dsp import ambience, decorrelate, eq, interpolation, limiter
from aurasync.dsp.ramps import DecibelRamp, FadeGate, Smoothed
from aurasync.dsp.retardo import LineaDeRetardo

if TYPE_CHECKING:
    from collections.abc import Callable

    from aurasync.config import Instalacion

SR = 48000

_NADA = 1e-12
"""Por debajo de esto dos ganancias son la misma: evita armar una rampa por ruido de coma
flotante en cada bloque."""

VELOCIDAD_PAN_AMBIENTE = 2.0
"""Unidades por segundo para `pan`, `ambiente` y la mezcla del extractor: de 0 a 1 en 0,5 s."""
VELOCIDAD_VOLUMEN_DB_S = 30.0
"""El volumen es una perilla de mano: más rápido que la ganancia del lazo (6 dB/s)."""
VELOCIDAD_SILENCIO = 20.0
"""Silenciar o volver a activar un parlante tarda 50 ms: rápido como un botón, sin clic."""
MAXIMO_RAMPA_S = 2.0
"""Un cambio de retardo pedido desde un control que la rampa tardaría más que esto en
alcanzar pasa por el corte en vez de arrastrarse."""


class Motor:
    """Convierte estéreo en una señal por parlante, manteniendo estado entre bloques."""

    def __init__(
        self,
        instalacion: Instalacion,
        sr: int = SR,
        *,
        extraer_ambiente: bool = True,
        decorrelar: bool = True,
        semilla: int = 0,
        velocidad_retardo_ms_s: float = 0.5,
        velocidad_ganancia_db_s: float = 6.0,
        volumen_db: float = 0.0,
        ecualizar: bool = False,
    ) -> None:
        """`ecualizar`: si la cadena lleva la etapa de ecualización por parlante. La lleva el
        servicio (y `run`); los tests de pan, retardo y ganancia la dejan afuera para medir
        esas etapas sin los 21 ms de latencia del filtro de fase lineal."""
        if not instalacion.parlantes:
            msg = "la instalación no tiene parlantes"
            raise ValueError(msg)
        self.instalacion = instalacion
        self.sr = sr
        self.extraer_ambiente = extraer_ambiente
        self.decorrelar = decorrelar
        self.velocidad_retardo_ms_s = velocidad_retardo_ms_s
        self.velocidad_ganancia_db_s = velocidad_ganancia_db_s
        self.decorrelacion_activa = decorrelar
        self.silenciados: set[str] = set()
        self.ecualizar = ecualizar
        self.ecualizacion_activa = True
        """Si se aplica la ecualización de cada parlante. Solo cambia a través del corte."""
        """Los parlantes en silencio. Es un estado del oyente: no va a la instalación."""
        """Si la salida pasa por el decorrelador. Solo cambia a través del corte."""
        # Arranca ya en su valor: antes de sonar no hay nada que una rampa tenga que disimular.
        self._volumen = DecibelRamp(volumen_db, VELOCIDAD_VOLUMEN_DB_S, sr)

        n = len(instalacion.parlantes)
        if decorrelar and n > decorrelate.MAXIMO_FIJOS:
            msg = (
                f"{n} parlantes: con filtros fijos solo se consiguen "
                f"{decorrelate.MAXIMO_FIJOS} señales bien decorrelacionadas"
            )
            raise ValueError(msg)
        filtros = decorrelate.banco_decorrelador(n, semilla=semilla) if decorrelar else [None] * n
        self._filtros = dict(zip((p.nombre for p in instalacion.parlantes), filtros, strict=True))
        self._extractor = ambience.Extractor() if extraer_ambiente else None
        # La extracción de ambiente tiene latencia propia: hay que retrasar el camino
        # directo lo mismo, o el ambiente llegaría corrido respecto de él y el efecto de
        # precedencia haría lo contrario de lo que se busca.
        self.latencia = self._extractor.latencia if self._extractor else 0
        self.latencia_retardo = interpolation.HALF
        """Latencia fija de la lectura de banda limitada de las líneas de retardo, igual en
        todos los parlantes."""
        self.reiniciar()

    def reiniciar(self) -> None:
        """Vacía el estado. Hay que llamarlo al empezar una reproducción nueva."""
        # Cola de la convolución: la parte del bloque anterior que todavía no salió.
        self._cola_filtro = {n: np.zeros(len(h) - 1 if h is not None else 0) for n, h in self._filtros.items()}
        # Una línea de retardo **variable** por parlante. Se arranca con `saltar_a`, que no
        # usa rampa: antes de que empiece a sonar no hay nada que pueda producir un clic.
        efectivos = self.retardos_efectivos_ms()
        self._lineas = {}
        for parlante in self.instalacion.parlantes:
            linea = LineaDeRetardo(
                self.sr,
                maximo_ms=max(250.0, 2 * max(efectivos.values(), default=0.0)),
                velocidad_ms_s=self.velocidad_retardo_ms_s,
                # De banda limitada: la lineal le quitaba hasta 3,5 dB a 12,7 kHz, distinto a
                # cada parlante según su retardo (`dsp/interpolation.py`).
                sinc=True,
            )
            linea.saltar_a(efectivos[parlante.nombre])
            self._lineas[parlante.nombre] = linea
        # Ganancia lineal, que arranca ya en su objetivo por el mismo motivo.
        self._ganancia = {parlante.nombre: 10 ** (parlante.ganancia_db / 20) for parlante in self.instalacion.parlantes}
        if self._extractor is not None:
            self._extractor.reiniciar()
        # Compensación de la latencia del extractor sobre el camino directo.
        self._cola_directo_izq = np.zeros(self.latencia)
        self._cola_directo_der = np.zeros(self.latencia)
        self._pan = {p.nombre: Smoothed(p.pan, VELOCIDAD_PAN_AMBIENTE, self.sr) for p in self.instalacion.parlantes}
        self._ambiente = {
            p.nombre: Smoothed(p.ambiente, VELOCIDAD_PAN_AMBIENTE, self.sr) for p in self.instalacion.parlantes
        }
        mezcla = getattr(self, "_mezcla_ambiente", None)
        self._mezcla_ambiente = Smoothed(1.0 if mezcla is None else mezcla.target, VELOCIDAD_PAN_AMBIENTE, self.sr)
        self._corte = FadeGate(self.sr)
        self._al_saltar: list[Callable[[], None]] = []
        self._activo = {p.nombre: Smoothed(1.0, VELOCIDAD_SILENCIO, self.sr) for p in self.instalacion.parlantes}
        # La ecualización siempre está en el camino, aunque sea neutra: un filtro de fase
        # lineal del mismo largo para todos los parlantes los atrasa a todos igual, y
        # prenderla o apagarla no los corre en el tiempo.
        self._ecualizador = (
            {p.nombre: eq.StreamingFIR(self._taps_de(p)) for p in self.instalacion.parlantes} if self.ecualizar else {}
        )
        self.latencia_ecualizador = eq.LATENCY_SAMPLES if self.ecualizar else 0
        self._limitadores = {p.nombre: limiter.PeakLimiter(self.sr) for p in self.instalacion.parlantes}

    # -- lo que mueve un control mientras suena --------------------------------------

    @property
    def volumen_db(self) -> float:
        return self._volumen.target_db

    @volumen_db.setter
    def volumen_db(self, valor: float) -> None:
        self._volumen.target_db = valor

    @property
    def extraer_ambiente_activo(self) -> bool:
        return self._extractor is not None and self._mezcla_ambiente.target > 0

    @extraer_ambiente_activo.setter
    def extraer_ambiente_activo(self, activo: bool) -> None:
        if activo and self._extractor is None:
            msg = "este motor se construyó sin extractor de ambiente"
            raise ValueError(msg)
        self._mezcla_ambiente.target = 1.0 if activo else 0.0

    @property
    def tiene_extractor(self) -> bool:
        return self._extractor is not None

    @property
    def en_corte(self) -> bool:
        """Si hay un corte en curso. El lazo de recalibración no mide mientras tanto."""
        return self._corte.busy

    def cortar(self, accion: Callable[[], None] | None = None) -> None:
        """Pide un corte: baja a cero, corre `accion` y salta todo con la salida en cero.

        `accion` es lo que hay que cambiar en el fondo (cargar un preset, prender el
        decorrelador). Se ejecuta en el hilo que llama a `procesar`, que es el único que
        escribe el motor y la instalación.
        """
        if accion is not None:
            self._al_saltar.append(accion)
        self._corte.request()

    def _saltar(self) -> None:
        """Con la salida en cero: aplica lo pendiente y lleva cada parámetro a su objetivo."""
        acciones, self._al_saltar = self._al_saltar, []
        for accion in acciones:
            accion()
        for p in self.instalacion.parlantes:
            self._pan[p.nombre].target = p.pan
            self._pan[p.nombre].jump()
            self._ambiente[p.nombre].target = p.ambiente
            self._ambiente[p.nombre].jump()
            self._ganancia[p.nombre] = 10 ** (p.ganancia_db / 20)
        self._mezcla_ambiente.jump()
        for nombre, objetivo in self.retardos_efectivos_ms().items():
            self._lineas[nombre].saltar_a(objetivo)

    def actualizar(self) -> None:
        """Relee la instalación y fija los nuevos objetivos, **sin cortar el sonido**.

        Es lo que usa el lazo de recalibración: se cambian `retardo_ms` y `ganancia_db` de
        los parlantes y se llama a esto. Los cambios se alcanzan gradualmente —el retardo por
        rampa de velocidad limitada, la ganancia por rampa en dB—, así que ninguno se oye
        como un salto.
        """
        for nombre, objetivo in self.retardos_efectivos_ms().items():
            self._lineas[nombre].objetivo_ms = objetivo

    def _taps_de(self, parlante) -> np.ndarray:
        return eq.fir(parlante.ecualizacion_db if self.ecualizacion_activa else None)

    def actualizar_ecualizacion(self) -> None:
        """Relee la ecualización de cada parlante y la cambia en el fondo de un corte.

        Cambiar los coeficientes de un filtro mientras suena es un salto en la señal.
        """

        def cambiar() -> None:
            for p in self.instalacion.parlantes:
                if p.nombre in self._ecualizador:
                    self._ecualizador[p.nombre].set_taps(self._taps_de(p))

        self.cortar(cambiar)

    def actualizar_desde_control(self) -> None:
        """Como `actualizar`, pero un retardo que tardaría más de 2 s en llegar va por el corte.

        El lazo de recalibración sigue usando `actualizar`: sus correcciones se arrastran sin
        cortar, que es lo que lo hace inaudible. Un control, en cambio, pide un valor nuevo y
        quiere oírlo ya (spec §6.5).
        """
        lento = any(
            abs(objetivo - self._lineas[nombre].actual_ms) / self.velocidad_retardo_ms_s > MAXIMO_RAMPA_S
            for nombre, objetivo in self.retardos_efectivos_ms().items()
        )
        if lento:
            self.cortar()
        else:
            self.actualizar()

    def retardos_actuales_ms(self) -> dict[str, float]:
        """Dónde está cada retardo ahora, que puede no ser el objetivo si sigue moviéndose."""
        return {n: linea.actual_ms for n, linea in self._lineas.items()}

    def retardos_efectivos_ms(self) -> dict[str, float]:
        """El retardo total de cada parlante: el de calibración más el de Haas.

        El de Haas se aplica **en proporción a cuánto ambiente lleva** el parlante: un
        parlante que solo reproduce el directo no debe alejarse, y uno que solo lleva
        ambiente se retrasa el valor completo.
        """
        return {
            p.nombre: p.retardo_ms + p.ambiente * self.instalacion.retardo_traseros_ms
            for p in self.instalacion.parlantes
        }

    def procesar(self, izq: np.ndarray, der: np.ndarray) -> dict[str, np.ndarray]:
        """Un bloque estéreo de entrada, un bloque por parlante de salida."""
        if len(izq) != len(der):
            msg = f"los canales tienen largos distintos: {len(izq)} y {len(der)}"
            raise ValueError(msg)
        n = len(izq)
        if n == 0:
            return {p.nombre: np.zeros(0) for p in self.instalacion.parlantes}

        if self._extractor is not None:
            amb = self._extractor.procesar(izq, der)
            izq_d, der_d = self._directo_retrasado(izq, der)
            mezcla = self._mezcla_ambiente.block(n)
        else:
            amb = np.zeros(n)
            izq_d, der_d = izq, der
            mezcla = 0.0

        # El volumen y el corte se calculan una vez por bloque, igual para todos.
        envolvente, saltar = self._corte.block(n)
        salida_global = self._volumen.block(n) * envolvente

        salida = {}
        for p in self.instalacion.parlantes:
            # Directo: mezcla L/R según el pan. Con pan 0 los dos por igual. Los dos pesos
            # son escalares en reposo y arreglos solo mientras se mueven.
            suave_pan, suave_amb = self._pan[p.nombre], self._ambiente[p.nombre]
            suave_pan.target, suave_amb.target = p.pan, p.ambiente
            pan = suave_pan.block(n)
            ambiente = suave_amb.block(n) * mezcla
            directo = (1 - pan) / 2 * izq_d + (1 + pan) / 2 * der_d
            x = (1 - ambiente) * directo + ambiente * amb
            # El decorrelador convoluciona aunque esté desviado, para que su cola esté lista
            # cuando vuelva.
            decorrelado = self._convolucionar(p.nombre, x)
            if self.decorrelacion_activa:
                x = decorrelado
            x = self._lineas[p.nombre].procesar(x)
            if self.ecualizar:
                x = self._ecualizador[p.nombre].process(x)
            activo = self._activo[p.nombre]
            activo.target = 0.0 if p.nombre in self.silenciados else 1.0
            x = x * self._rampa_de_ganancia(p.nombre, p.ganancia_db, n) * salida_global * activo.block(n)
            # La ecualización solo realza: un pasaje fuerte puede pasar de escala completa, y
            # el limitador baja la ganancia en vez de recortar (`dsp/limiter.py`).
            salida[p.nombre] = self._limitadores.setdefault(p.nombre, limiter.PeakLimiter(self.sr)).process(x)
        if saltar:
            self._saltar()
        return salida

    def reduccion_limitador_db(self) -> dict[str, float]:
        """Cuánto está bajando el limitador a cada parlante ahora, en dB (0: nada)."""
        return {n: lim.reduction_db for n, lim in self._limitadores.items()}

    def _rampa_de_ganancia(self, nombre: str, objetivo_db: float, n: int) -> np.ndarray:
        """Una rampa de la ganancia actual a la objetivo, limitada en velocidad.

        Aplicar un cambio de ganancia de golpe en medio de un bloque es otro clic. Y hacerlo
        en un solo bloque, aunque sea continuo, se puede oír como un escalón si el cambio es
        grande; de ahí el límite en dB por segundo. El límite va en dB, que es donde el oído
        mide.
        """
        actual = self._ganancia[nombre]
        objetivo = 10 ** (objetivo_db / 20)
        if abs(objetivo - actual) < _NADA:
            return np.full(n, actual)
        margen_db = self.velocidad_ganancia_db_s * n / self.sr
        actual_db = 20 * np.log10(max(actual, 1e-9))
        alcanzable_db = float(np.clip(objetivo_db, actual_db - margen_db, actual_db + margen_db))
        fin = 10 ** (alcanzable_db / 20)
        rampa = np.linspace(actual, fin, n)
        self._ganancia[nombre] = fin
        return rampa

    def _directo_retrasado(self, izq: np.ndarray, der: np.ndarray):
        """Retrasa el camino directo tanto como tarda la extracción de ambiente."""
        if self.latencia == 0:
            return izq, der
        ext_izq = np.concatenate([self._cola_directo_izq, izq])
        ext_der = np.concatenate([self._cola_directo_der, der])
        n = len(izq)
        self._cola_directo_izq = ext_izq[n:]
        self._cola_directo_der = ext_der[n:]
        return ext_izq[:n], ext_der[:n]

    def _convolucionar(self, nombre: str, x: np.ndarray) -> np.ndarray:
        """Overlap-add exacto: el resultado es idéntico a convolucionar todo de una vez."""
        h = self._filtros[nombre]
        if h is None:
            return x
        completa = np.convolve(x, h)
        cola = self._cola_filtro[nombre]
        salida = completa[: len(x)].copy()
        solape = min(len(cola), len(salida))
        salida[:solape] += cola[:solape]
        nueva_cola = completa[len(x) :]
        if len(cola) > solape:
            resto = cola[solape:]
            largo = max(len(nueva_cola), len(resto))
            acumulada = np.zeros(largo)
            acumulada[: len(nueva_cola)] += nueva_cola
            acumulada[: len(resto)] += resto
            nueva_cola = acumulada
        self._cola_filtro[nombre] = nueva_cola
        return salida


def procesar_completo(motor: Motor, izq: np.ndarray, der: np.ndarray, bloque: int = 4096) -> dict[str, np.ndarray]:
    """Procesa una señal entera en bloques. Existe sobre todo para los tests."""
    partes: dict[str, list[np.ndarray]] = {p.nombre: [] for p in motor.instalacion.parlantes}
    for i in range(0, len(izq), bloque):
        salida = motor.procesar(izq[i : i + bloque], der[i : i + bloque])
        for nombre, trozo in salida.items():
            partes[nombre].append(trozo)
    return {n: np.concatenate(v) if v else np.zeros(0) for n, v in partes.items()}
