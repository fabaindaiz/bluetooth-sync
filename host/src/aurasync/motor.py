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

**La cadena** (`chain.py`, spec 2026-10-02 §4). Los parámetros de cada etapa —los del
extractor, el decorrelador, la ecualización, el limitador y las velocidades— salen de un
`ChainValues`; con sus valores por defecto el motor suena exactamente como antes
(`tests/test_chain_golden.py`). `aplicar_cadena` los cambia mientras suena: lo que el
descriptor marca `live` se mueve con las rampas de siempre, lo que marca `cut` pasa por el
corte. Las etapas que se agregaron con la cadena —`diffuse`, `bass`, `limiter.true_peak`—
viven en `chain_stages.py` (en inglés) y acá solo se enchufan; apagadas (por defecto) no
tocan el sonido. `volume.avrcp` no es del motor: lo maneja el servicio, que deja el volumen
digital en 0 dB (`bt_volume.py`).

**Compensación del A/B** (`ganancia_comparacion_db`): una ganancia de salida más, con rampa,
que el servicio usa para igualar la sonoridad de dos presets mientras se comparan. En 0 dB
multiplica por 1 exacto.
"""

from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING

import numpy as np

from aurasync import chain as cadena
from aurasync import chain_stages
from aurasync.chain import ChainValues
from aurasync.dsp import ambience, decorrelate, eq, interpolation, limiter, profiles
from aurasync.dsp.ramps import DecibelRamp, FadeGate, Smoothed
from aurasync.dsp.retardo import LineaDeRetardo

if TYPE_CHECKING:
    from collections.abc import Callable

    from aurasync.config import Instalacion

SR = 48000

_NADA = 1e-12
"""Por debajo de esto dos ganancias son la misma: evita armar una rampa por ruido de coma
flotante en cada bloque."""

VELOCIDAD_PAN_AMBIENTE = cadena.default("ambience", "move_speed")
"""Unidades por segundo para `pan`, `ambiente` y la mezcla del extractor: de 0 a 1 en 0,5 s.
Es el valor por defecto de `ambience.move_speed`; los de abajo, de la etapa `volume`."""
VELOCIDAD_VOLUMEN_DB_S = cadena.default("volume", "volume_speed_db_s")
"""El volumen es una perilla de mano: más rápido que la ganancia del lazo (6 dB/s)."""
VELOCIDAD_SILENCIO = 1000 / cadena.default("volume", "mute_fade_ms")
"""Silenciar o volver a activar un parlante tarda 50 ms: rápido como un botón, sin clic."""
VENTANA_METRICAS_S = 5.0
"""Sobre cuánto tiempo se cuenta el porcentaje de tiempo activo del limitador."""
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
        semilla: int | None = None,
        velocidad_retardo_ms_s: float | None = None,
        velocidad_ganancia_db_s: float | None = None,
        volumen_db: float = 0.0,
        ecualizar: bool = False,
        chain: ChainValues | None = None,
        bloque: int = 4096,
    ) -> None:
        """`ecualizar`: si la cadena lleva la etapa de ecualización por parlante. La lleva el
        servicio (y `run`); los tests de pan, retardo y ganancia la dejan afuera para medir
        esas etapas sin los 21 ms de latencia del filtro de fase lineal.

        `chain`: los valores de la cadena (None: los de por defecto, que son el sonido de
        siempre). `semilla` y las dos velocidades, si se pasan, mandan sobre los de la cadena:
        son de antes de que existiera y los usan los tests.

        `bloque`: el tamaño de bloque con que se va a llamar a `procesar`; las convoluciones
        particionadas de la difusión y los armónicos lo usan como partición (otro tamaño
        funciona igual, más caro)."""
        if not instalacion.parlantes:
            msg = "la instalación no tiene parlantes"
            raise ValueError(msg)
        self.instalacion = instalacion
        self.sr = sr
        self.bloque = bloque
        self._cadena = (chain or ChainValues()).copy()
        c = self._cadena
        self.extraer_ambiente = extraer_ambiente
        self.decorrelar = decorrelar
        self.velocidad_retardo_ms_s = (
            c.param("align", "delay_speed_ms_s") if velocidad_retardo_ms_s is None else velocidad_retardo_ms_s
        )
        self.velocidad_ganancia_db_s = (
            c.param("volume", "gain_speed_db_s") if velocidad_ganancia_db_s is None else velocidad_ganancia_db_s
        )
        self.decorrelacion_activa = decorrelar and c.algorithm("decorrelate") != "off"
        """Si la salida pasa por el decorrelador. Solo cambia a través del corte."""
        self.silenciados: set[str] = set()
        """Los parlantes en silencio. Es un estado del oyente: no va a la instalación."""
        self.ecualizar = ecualizar
        self.ecualizacion_activa = c.algorithm("eq") != "off"
        """Si se aplica la ecualización de cada parlante. Solo cambia a través del corte."""
        # Arranca ya en su valor: antes de sonar no hay nada que una rampa tenga que disimular.
        self._volumen = DecibelRamp(volumen_db, c.param("volume", "volume_speed_db_s"), sr)
        self._mezcla = c.param("ambience", "mix")
        """La mezcla del extractor cuando está prendido (`ambience.mix`)."""

        n = len(instalacion.parlantes)
        if decorrelar and n > decorrelate.MAXIMO_FIJOS:
            msg = (
                f"{n} parlantes: con filtros fijos solo se consiguen "
                f"{decorrelate.MAXIMO_FIJOS} señales bien decorrelacionadas"
            )
            raise ValueError(msg)
        self._semilla_fija = semilla
        self._bancos: dict[tuple[int, int, int], list[np.ndarray]] = {}
        filtros = self._banco() if decorrelar else [None] * n
        self._filtros = dict(zip((p.nombre for p in instalacion.parlantes), filtros, strict=True))
        self._extractor = ambience.Extractor(self._parametros_ambiente()) if extraer_ambiente else None
        # La extracción de ambiente tiene latencia propia: hay que retrasar el camino
        # directo lo mismo, o el ambiente llegaría corrido respecto de él y el efecto de
        # precedencia haría lo contrario de lo que se busca.
        self.latencia = self._extractor.latencia if self._extractor else 0
        self.latencia_retardo = interpolation.HALF
        """Latencia fija de la lectura de banda limitada de las líneas de retardo, igual en
        todos los parlantes."""
        self.reiniciar()

    # -- la cadena ---------------------------------------------------------------------

    @property
    def cadena(self) -> ChainValues:
        """Los valores de la cadena que el motor tiene aplicados (o pedidos al corte)."""
        return self._cadena.copy()

    def _parametros_ambiente(self) -> ambience.Parametros:
        c = self._cadena
        return ambience.Parametros(
            lam=c.param("ambience", "lam"),
            umbral=c.param("ambience", "threshold"),
            sigma=c.param("ambience", "sigma"),
            energia_minima=c.param("ambience", "min_energy"),
        )

    def _banco(self) -> list[np.ndarray]:
        """El banco de filtros del decorrelador para la cadena actual, con caché: el A/B entre
        dos presets con otra semilla no lo recalcula cada vez."""
        c = self._cadena
        semilla = c.param("decorrelate", "seed") if self._semilla_fija is None else self._semilla_fija
        clave = (len(self.instalacion.parlantes), c.param("decorrelate", "length"), semilla)
        medio, variacion = c.param("decorrelate", "mean_ms"), c.param("decorrelate", "spread_ms")
        largo = decorrelate.largo_necesario(c.param("decorrelate", "length"), medio, variacion, self.sr)
        clave = (clave[0], largo, clave[2], medio, variacion)
        if clave not in self._bancos:
            self._bancos[clave] = decorrelate.banco_decorrelador(
                clave[0], largo=largo, semilla=clave[2], retardo_medio_ms=medio, variacion_ms=variacion
            )
        return self._bancos[clave]

    def _nuevo_limitador(self) -> limiter.PeakLimiter | limiter.TruePeakLimiter:
        return chain_stages.new_limiter(self._cadena, self.sr)

    def _tipos(self) -> list[tuple[str, str | None]]:
        return [(p.nombre, p.tipo or profiles.guess(p.nombre)) for p in self.instalacion.parlantes]

    def _nueva_difusion(self) -> chain_stages.DiffuseStage:
        nombres = [p.nombre for p in self.instalacion.parlantes]
        return chain_stages.DiffuseStage(self._cadena, nombres, self.sr, self.bloque)

    def _nuevos_graves(self) -> chain_stages.BassStage:
        return chain_stages.BassStage(self._cadena, self._tipos(), self.sr, self.bloque)

    @property
    def latencia_limitador(self) -> int:
        """Muestras que el limitador atrasa a todos los parlantes (la anticipación del de pico real)."""
        return chain_stages.limiter_latency(self._cadena, self.sr)

    def _objetivo_mezcla(self, activo: bool) -> float:  # noqa: FBT001
        return self._mezcla if activo else 0.0

    def aplicar_cadena(self, valores: ChainValues, *, en_corte: bool = False) -> str:
        """Lleva el motor a `valores`. Devuelve cómo: `"none"`, `"live"` o `"cut"`.

        Lo `live` se mueve ya, con sus rampas; lo `cut` se pide al corte (o, con
        `en_corte`, se aplica ya: quien llama está en el fondo de uno, como `preset_load`).
        Los algoritmos de `ambience`, `decorrelate` y `eq` se comparan con el estado que el
        motor tiene de verdad, no con la cadena anterior: los atributos de antes
        (`decorrelacion_activa`, …) se pueden haber cambiado a mano.
        """
        anterior, nuevo = self._cadena, valores.copy()
        cambios = set(cadena.changed_params(anterior, nuevo))
        self._cadena = nuevo
        hubo_vivo = self._aplicar_vivo(cambios, nuevo)

        al_corte = []
        if {("ambience", p) for p in ("lam", "threshold", "sigma", "min_energy")} & cambios and self._extractor:
            parametros = self._parametros_ambiente()
            al_corte.append(lambda: setattr(self._extractor, "p", parametros))
        claves_banco = {("decorrelate", k) for k in ("length", "seed", "mean_ms", "spread_ms")}
        if self.decorrelar and claves_banco & cambios:
            # El banco se calcula ahora y no en el fondo del corte, que corre dentro de `procesar`.
            filtros = dict(zip((p.nombre for p in self.instalacion.parlantes), self._banco(), strict=True))
            al_corte.append(lambda: self._cambiar_filtros(filtros))
        decorrelar = self.decorrelar and nuevo.algorithm("decorrelate") != "off"
        if decorrelar != self.decorrelacion_activa:
            al_corte.append(lambda: setattr(self, "decorrelacion_activa", decorrelar))
        ecualizar = nuevo.algorithm("eq") != "off"
        if not self.ecualizar:
            # Sin la etapa en el camino no hay nada que cambie el sonido: no hace falta el corte.
            self.ecualizacion_activa = ecualizar
        elif (
            ecualizar != self.ecualizacion_activa
            or {
                ("eq", "max_boost_db"),
                ("eq", "budget_db"),
                ("eq", "treble_cap_db"),
            }
            & cambios
        ):

            def cambiar_ecualizacion() -> None:
                self.ecualizacion_activa = ecualizar
                self._cambiar_taps()

            al_corte.append(cambiar_ecualizacion)
        # Las etapas nuevas (`chain_stages.py`): se arman ahora, fuera del fondo del corte que
        # corre dentro de `procesar`, y se cambian enteras en el fondo.
        if any(etapa == "diffuse" and param != "level_db" for etapa, param in cambios):
            difusion = self._nueva_difusion()
            al_corte.append(lambda: setattr(self, "_difusion", difusion))
        if any(etapa == "bass" and param != "harmonics_db" for etapa, param in cambios):
            graves = self._nuevos_graves()
            al_corte.append(lambda: setattr(self, "_graves", graves))
        if ("limiter", None) in cambios or ("limiter", "lookahead_ms") in cambios:

            def cambiar_limitadores() -> None:
                self._limitadores = {p.nombre: self._nuevo_limitador() for p in self.instalacion.parlantes}

            al_corte.append(cambiar_limitadores)
        # `volume.avrcp` no es del motor: el servicio mueve el volumen digital (`bt_volume.py`).

        if not al_corte:
            return "live" if hubo_vivo else "none"
        if en_corte:
            for accion in al_corte:
                accion()
            return "cut"
        self.cortar(lambda: [accion() for accion in al_corte])
        return "cut"

    def _aplicar_vivo(self, cambios: set, nuevo: ChainValues) -> bool:
        hubo = False
        self._mezcla = nuevo.param("ambience", "mix")
        objetivo = self._objetivo_mezcla(nuevo.algorithm("ambience") != "off")
        if self._extractor is not None and self._mezcla_ambiente.target != objetivo:
            self._mezcla_ambiente.target = objetivo
            hubo = True
        if ("ambience", "move_speed") in cambios:
            velocidad = nuevo.param("ambience", "move_speed")
            for suave in [*self._pan.values(), *self._ambiente.values(), self._mezcla_ambiente]:
                suave.rate = velocidad
            hubo = True
        if ("align", "delay_speed_ms_s") in cambios:
            self.velocidad_retardo_ms_s = nuevo.param("align", "delay_speed_ms_s")
            for linea in self._lineas.values():
                linea.velocidad_ms_s = self.velocidad_retardo_ms_s
            hubo = True
        if ("volume", "volume_speed_db_s") in cambios:
            self._volumen._db.rate = nuevo.param("volume", "volume_speed_db_s")  # noqa: SLF001
            hubo = True
        if ("volume", "gain_speed_db_s") in cambios:
            self.velocidad_ganancia_db_s = nuevo.param("volume", "gain_speed_db_s")
            hubo = True
        if ("volume", "mute_fade_ms") in cambios:
            for suave in self._activo.values():
                suave.rate = 1000 / nuevo.param("volume", "mute_fade_ms")
            hubo = True
        if {("limiter", "ceiling_db"), ("limiter", "release_ms")} & cambios:
            techo_db = nuevo.param("limiter", "ceiling_db")
            liberacion_ms = nuevo.param("limiter", "release_ms")
            techo = 10 ** (techo_db / 20)
            paso = 1.0 / (liberacion_ms / 1000 * self.sr)
            for lim in self._limitadores.values():
                if isinstance(lim, limiter.TruePeakLimiter):
                    lim.configure(techo_db, liberacion_ms)
                else:
                    lim.ceiling, lim.step = techo, paso
            hubo = True
        if ("diffuse", "level_db") in cambios and nuevo.algorithm("diffuse") == self._cadena_difusion():
            self._difusion.set_level(nuevo.param("diffuse", "level_db"))
            hubo = True
        if ("bass", "harmonics_db") in cambios and nuevo.algorithm("bass") == self._graves.algorithm:
            self._graves.set_harmonics(nuevo.param("bass", "harmonics_db"))
            hubo = True
        return hubo

    def _cadena_difusion(self) -> str:
        return "noise_tail" if self._difusion.active else "off"

    def _cambiar_filtros(self, filtros: dict[str, np.ndarray]) -> None:
        self._filtros = filtros
        self._cola_filtro = {n: np.zeros(len(h) - 1) for n, h in filtros.items()}

    def _cambiar_taps(self) -> None:
        for p in self.instalacion.parlantes:
            if p.nombre in self._ecualizador:
                self._ecualizador[p.nombre].set_taps(self._taps_de(p))

    def metricas_cadena(self) -> dict[str, dict]:
        """Lo que cada etapa informa mientras suena (spec §4.1): chico y barato de leer."""
        pendiente = cadena.pending(self._cadena)
        m: dict[str, dict] = {s.id: {"pending": pendiente[s.id]} for s in cadena.CHAIN}
        m["ambience"].update(
            {
                "mix_now": round(float(self._mezcla_ambiente.current), 3) if self._extractor else 0.0,
                "share": round(self._ambiente_energia / self._entrada_energia, 3) if self._entrada_energia else None,
            }
        )
        m["decorrelate"]["active"] = self.decorrelacion_activa
        m["align"].update(
            {
                "delay_now_ms": {n: round(v, 3) for n, v in self.retardos_actuales_ms().items()},
                "moving": not all(linea.en_objetivo for linea in self._lineas.values()),
            }
        )
        curvas = {p.nombre: self._curva_de(p) for p in self.instalacion.parlantes}
        m["decorrelate"].update(
            {
                "length": len(next(iter(self._filtros.values()))) if self.decorrelar else None,
                "mean_ms": self._cadena.param("decorrelate", "mean_ms"),
            }
        )
        m["diffuse"].update(self._difusion.metrics())
        m["eq"].update(
            {
                "active": self.ecualizar and self.ecualizacion_activa,
                "max_boost_db": {n: round(float(np.max(c)), 2) if c is not None else 0.0 for n, c in curvas.items()},
                "boost_energy_db": {
                    n: round(eq.boost_energy_db(c), 2) if c is not None else 0.0 for n, c in curvas.items()
                },
            }
        )
        m["bass"].update(self._graves.metrics())
        m["volume"]["volume_db_now"] = round(self._volumen.current_db, 2)
        m["limiter"].update(
            {
                "kind": "true_peak" if self.latencia_limitador else "peak",
                "latency_ms": round(self.latencia_limitador / self.sr * 1000, 3),
                "reduction_db": {n: round(v, 2) for n, v in self.reduccion_limitador_db().items()},
                "active_pct": self.uso_limitador_pct(),
            }
        )
        return m

    def uso_limitador_pct(self) -> dict[str, float]:
        """El porcentaje de los últimos 5 s en que el limitador de cada parlante bajó la ganancia."""
        return {
            n: round(100 * activas / total, 1) if total else 0.0 for n, (total, activas) in self._uso_limitador.items()
        }

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
        c = self._cadena
        velocidad = c.param("ambience", "move_speed")
        self._pan = {p.nombre: Smoothed(p.pan, velocidad, self.sr) for p in self.instalacion.parlantes}
        self._ambiente = {p.nombre: Smoothed(p.ambiente, velocidad, self.sr) for p in self.instalacion.parlantes}
        mezcla = getattr(self, "_mezcla_ambiente", None)
        inicial = self._objetivo_mezcla(c.algorithm("ambience") != "off") if mezcla is None else mezcla.target
        self._mezcla_ambiente = Smoothed(inicial, velocidad, self.sr)
        self._corte = FadeGate(self.sr)
        self._al_saltar: list[Callable[[], None]] = []
        silencio = 1000 / c.param("volume", "mute_fade_ms")
        self._activo = {p.nombre: Smoothed(1.0, silencio, self.sr) for p in self.instalacion.parlantes}
        # La ecualización siempre está en el camino, aunque sea neutra: un filtro de fase
        # lineal del mismo largo para todos los parlantes los atrasa a todos igual, y
        # prenderla o apagarla no los corre en el tiempo.
        self._ecualizador = (
            {p.nombre: eq.StreamingFIR(self._taps_de(p)) for p in self.instalacion.parlantes} if self.ecualizar else {}
        )
        self.latencia_ecualizador = eq.LATENCY_SAMPLES if self.ecualizar else 0
        self._limitadores = {p.nombre: self._nuevo_limitador() for p in self.instalacion.parlantes}
        self._difusion = self._nueva_difusion()
        self._graves = self._nuevos_graves()
        compensacion = getattr(self, "_compensacion", None)
        self._compensacion = DecibelRamp(
            compensacion.target_db if compensacion is not None else 0.0,
            cadena.default("volume", "volume_speed_db_s"),
            self.sr,
        )
        self._compensacion.jump()
        # Para las métricas: (muestras, muestras con el limitador actuando) por parlante, en
        # bloques de la ventana, y la energía del ambiente frente a la de la entrada.
        self._historia_limitador: dict[str, deque] = {p.nombre: deque() for p in self.instalacion.parlantes}
        self._uso_limitador = {p.nombre: (0, 0) for p in self.instalacion.parlantes}
        self._ambiente_energia = self._entrada_energia = 0.0

    # -- lo que mueve un control mientras suena --------------------------------------

    @property
    def volumen_db(self) -> float:
        return self._volumen.target_db

    @volumen_db.setter
    def volumen_db(self, valor: float) -> None:
        self._volumen.target_db = valor

    def actualizar_tipos(self) -> None:
        """Cambió el tipo de un parlante: la etapa de graves decide con eso quién es chico."""
        if self._graves.algorithm == "off":
            return
        graves = self._nuevos_graves()
        self.cortar(lambda: setattr(self, "_graves", graves))

    def saltar_volumen(self, valor_db: float) -> None:
        """Fija el volumen digital sin rampa. Solo en el fondo de un corte (el modo `avrcp`)."""
        self._volumen.target_db = valor_db
        self._volumen.jump()

    @property
    def ganancia_comparacion_db(self) -> float:
        return self._compensacion.target_db

    @ganancia_comparacion_db.setter
    def ganancia_comparacion_db(self, valor: float) -> None:
        """Se mueve con rampa (30 dB/s); en el fondo de un corte salta con todo lo demás."""
        self._compensacion.target_db = valor

    @property
    def extraer_ambiente_activo(self) -> bool:
        return self._extractor is not None and self._mezcla_ambiente.target > 0

    @extraer_ambiente_activo.setter
    def extraer_ambiente_activo(self, activo: bool) -> None:
        if activo and self._extractor is None:
            msg = "este motor se construyó sin extractor de ambiente"
            raise ValueError(msg)
        self._mezcla_ambiente.target = self._objetivo_mezcla(activo)

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
        self._compensacion.jump()
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

    def _curva_de(self, parlante) -> list[float] | np.ndarray | None:
        """La curva que suena: la guardada, con el tope de `eq.max_boost_db` aplicado al leerla
        (la guardada no se toca: bajar el tope y volver a subirlo la recupera)."""
        curva = parlante.ecualizacion_db
        if curva is None or not self.ecualizacion_activa:
            return None
        tope = self._cadena.param("eq", "max_boost_db")
        if max(curva) > tope:
            curva = np.minimum(np.asarray(curva, dtype=float), tope)
        # El tope de agudos y el presupuesto (apagados por defecto: la curva queda tal cual).
        agudos, presupuesto = self._cadena.param("eq", "treble_cap_db"), self._cadena.param("eq", "budget_db")
        if agudos < tope or presupuesto:
            curva = eq.limited(curva, presupuesto or None, agudos if agudos < tope else None)
        return curva

    def curva_sonando(self, parlante) -> list[float] | None:
        """La curva de ecualización que suena ahora (None: plana), para que la calibración mida a
        través de la misma."""
        curva = self._curva_de(parlante)
        return None if curva is None else [float(v) for v in curva]

    def _taps_de(self, parlante) -> np.ndarray:
        return eq.fir(self._curva_de(parlante))

    def actualizar_ecualizacion(self) -> None:
        """Relee la ecualización de cada parlante y la cambia en el fondo de un corte.

        Cambiar los coeficientes de un filtro mientras suena es un salto en la señal.
        """

        self.cortar(self._cambiar_taps)

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
            # Métrica: la parte del ambiente en la entrada, suavizada (no toca el sonido).
            self._ambiente_energia = 0.8 * self._ambiente_energia + 0.2 * float(amb @ amb)
            self._entrada_energia = 0.8 * self._entrada_energia + 0.1 * float(izq_d @ izq_d + der_d @ der_d)
        else:
            amb = np.zeros(n)
            izq_d, der_d = izq, der
            mezcla = 0.0

        # El volumen y el corte se calculan una vez por bloque, igual para todos.
        envolvente, saltar = self._corte.block(n)
        salida_global = self._volumen.block(n) * envolvente
        compensacion = self._compensacion.block(n)
        if not (isinstance(compensacion, float) and compensacion == 1.0):
            salida_global = salida_global * compensacion
        # El cruce de graves: lo bajo del centro para el parlante de graves, atrasado como su
        # propia señal por el decorrelador (`chain_stages.py`).
        atraso = (
            round(self._cadena.param("decorrelate", "mean_ms") * self.sr / 1000) if self.decorrelacion_activa else 0
        )
        graves = self._graves.feed(izq_d, der_d, atraso)

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
            mezcla_propia = x
            decorrelado = self._convolucionar(p.nombre, x)
            if self.decorrelacion_activa:
                x = decorrelado
            if self._difusion.active:
                x = self._difusion.process(p.nombre, x, mezcla_propia)
            if graves is not None:
                x = self._graves.before_delay(p.nombre, x, graves)
            x = self._lineas[p.nombre].procesar(x)
            if self.ecualizar:
                x = self._ecualizador[p.nombre].process(x)
            x = self._graves.process(p.nombre, x)
            activo = self._activo[p.nombre]
            activo.target = 0.0 if p.nombre in self.silenciados else 1.0
            x = x * self._rampa_de_ganancia(p.nombre, p.ganancia_db, n) * salida_global * activo.block(n)
            # La ecualización solo realza: un pasaje fuerte puede pasar de escala completa, y
            # el limitador baja la ganancia en vez de recortar (`dsp/limiter.py`).
            if p.nombre not in self._limitadores:
                self._limitadores[p.nombre] = self._nuevo_limitador()
            lim = self._limitadores[p.nombre]
            limitado = lim.process(x)
            if isinstance(lim, limiter.TruePeakLimiter):
                self._contar_limitador(p.nombre, n, activas=round(lim.active_fraction * n))
            else:
                self._contar_limitador(p.nombre, n, activas=n if limitado is not x else 0)
            salida[p.nombre] = limitado
        if saltar:
            self._saltar()
        return salida

    def _contar_limitador(self, nombre: str, n: int, *, activas: int) -> None:
        """`PeakLimiter.process` devuelve el mismo arreglo cuando no toca nada: así se cuenta
        el tiempo activo sin mirar muestra por muestra. El de pico real dice qué fracción del
        bloque estuvo bajo la unidad."""
        historia = self._historia_limitador.setdefault(nombre, deque())
        nuevas = activas
        total, activas = self._uso_limitador.get(nombre, (0, 0))
        historia.append((n, nuevas))
        total, activas = total + n, activas + nuevas
        while historia and total - historia[0][0] >= VENTANA_METRICAS_S * self.sr:
            viejo, viejas = historia.popleft()
            total, activas = total - viejo, activas - viejas
        self._uso_limitador[nombre] = (total, activas)

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
