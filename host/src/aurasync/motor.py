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
- **`cambiar`** es el corte o el **fundido cruzado**, según la etapa `transition` (spec
  2026-10-08-seamless-transitions §3 y §4, etapa 1). En el fundido nada salta: al empezar el
  bloque siguiente corren las acciones pedidas, cada rampa (pan, ambiente, mezcla del extractor,
  compensación del A/B, makeup del render y la ganancia de cada parlante) llega a su objetivo en
  exactamente `fade_ms`, y cada línea cuyo retardo cambia funde su lectura vieja con la nueva en
  el mismo largo, siempre a igual potencia (`equal_power`). El reloj y la cola son `dsp/transition.py`.
  Etapa 2: la difusión, los graves, la ecualización, el banco del decorrelador, el extractor y un
  limitador de la misma latencia se cruzan enteros (un `Crossfaded`): la etapa nueva se calienta a
  la sombra con la misma entrada (WARM, hasta 1 s; las rampas y las líneas esperan), se mezcla con
  la vieja en `fade_ms` según `shape`, y queda sola. El limitador se mezcla siempre a igual ganancia
  y el decorrelador a igual potencia. Prender o apagar el decorrelador mezcla la señal seca con la
  decorrelada, que el motor ya calcula, y la bandera cambia al terminar. Lo demás que tiene estado
  (el render, un limitador de otra latencia) sigue pasando por el corte.

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

**The `direct` render** (spec 2026-10-05-virtual-speakers-and-hot-join §9): "pure aligned
stereo". Each speaker plays its side of L/R by its pan at constant power, and nothing of the chain
touches it: no ambience, decorrelator, EQ curve, bass stage, diffuse tail, Haas delay or spatial
upmix. It keeps the alignment delay, the speaker's gain, mute, the volume and the limiter, and the
fixed latencies of the extractor and of the EQ (which plays a flat filter). It does skip the
decorrelator's group delay (`decorrelate.mean_ms`, ~2.5 ms): a switch moves every speaker by that,
equally and through the cut, and `chain.latency_ms` reports it. `render` is the render playing; it
changes at a cut's bottom, like the other renders of the `spatial` stage. The stages `direct` does
not feed (the diffuse tail, the bass filters) are rebuilt for the switch and swapped at the bottom,
so leaving `direct` never replays what played before it.

**The render's makeup** (`render_makeup_db`): one more ramped output gain, the same path as the
A/B's, that keeps every render at `classic`'s loudness. Whoever keeps the makeups
(`render_match.RenderMatch`) moves it slowly while a render plays and answers `on_render_switch`
at a cut's bottom, where it jumps with the output at zero. At 0 dB it multiplies by an exact 1.
`render_makeup_block_db` and `comparison_block_db` say what each block was made with, so the
match takes both gains back out of what it measures.
"""

from __future__ import annotations

from collections import deque
from dataclasses import replace
from typing import TYPE_CHECKING

import numpy as np

from aurasync import chain as cadena
from aurasync import chain_stages, control
from aurasync.chain import ChainValues
from aurasync.dsp import ambience, decorrelate, decorrelation_bank, eq, interpolation, limiter, profiles
from aurasync.dsp.ramps import DecibelRamp, FadeGate, Smoothed
from aurasync.dsp.retardo import LineaDeRetardo
from aurasync.dsp.spatial import SPATIAL_RENDERS, SpatialParams, SpatialUpmix, from_character
from aurasync.dsp.transition import Crossfaded, Transition

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
DIRECT = "direct"
"""The `spatial` stage's pure aligned stereo render."""
ETAPAS_CRUZABLES = ("_difusion", "_graves", "_extractor")
"""Los atributos del motor que un fundido puede tener cruzados (un `Crossfaded`, etapa 2 de la spec)."""
DICCIONARIOS_CRUZABLES = ("_limitadores", "_ecualizador", "_decorreladores")
"""Lo mismo, por parlante: cada valor del diccionario puede ser un `Crossfaded`."""


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
        """Si la salida pasa por el decorrelador. Cambia en el fondo de un corte o al terminar un fundido."""
        self._decorrelacion_destino: bool | None = None
        """Durante un fundido que prende o apaga el decorrelador, lo que `decorrelacion_activa` va a
        valer al terminar (`_cruzar_decorrelacion`); mientras tanto se mezclan las dos señales."""
        self.silenciados: set[str] = set()
        """Los parlantes en silencio. Es un estado del oyente: no va a la instalación."""
        self.sonda = None
        """La sonda enmascarada (`dsp/probe.py`, un `MaskedProbe`) que pone la sesión cuando el
        lazo la pide. Se suma a la entrada del limitador de cada parlante. Con None, o apagada
        y quieta, el motor no la llama: la salida es bit a bit la de siempre."""
        self.ecualizar = ecualizar
        self.ecualizacion_activa = c.algorithm("eq") != "off"
        """Si se aplica la ecualización de cada parlante. Cambia en el fondo de un corte, o al empezar un
        fundido, con los filtros nuevos (los viejos siguen con sus coeficientes hasta que terminan)."""
        self.render: str = c.algorithm("spatial")
        """The render playing (`classic`, `spatial`, `front` or `direct`): it changes at a cut's bottom."""
        self.on_render_switch: Callable[[str], float] | None = None
        """Asked at the cut's bottom where the render changes: the makeup (dB) the new render starts
        at (`render_match.RenderMatch.select`). None: the makeup stays where it is."""
        # Arranca ya en su valor: antes de sonar no hay nada que una rampa tenga que disimular.
        self._volumen = DecibelRamp(volumen_db, c.param("volume", "volume_speed_db_s"), sr)
        self.volumen_del_bloque_db: float | np.ndarray = float(volumen_db)
        """The digital volume (dB) the last block was made with, per sample while it ramps: what
        the headphone monitor takes back out of the speakers' blocks. After a cut's bottom the
        target has already jumped, but the block was made before (monitor.Levels)."""
        self._mezcla = c.param("ambience", "mix")
        """La mezcla del extractor cuando está prendido (`ambience.mix`)."""

        n = len(instalacion.parlantes)
        # Con más de `decorrelate.MAXIMO_FIJOS` parlantes el banco se arma igual y las métricas
        # avisan cuánto se separan (`notice`). Hasta el 2026-10-02 era un `ValueError` que no
        # dejaba arrancar al servicio con 7 u 8 (experimentos/16 §2 y §9: sobre 500 Hz el peor par
        # pasa de 0,46 con 3 a ~0,50 con 8; debajo de 2 kHz no separa ningún banco fijo).
        self._semilla_fija = semilla
        self._extractor = ambience.Extractor(self._parametros_ambiente()) if extraer_ambiente else None
        self._orden_pendiente: list[int] | None = None
        """Una asignación de filtros nueva que espera al próximo corte (`actualizar_desde_control`)."""
        self._aviso: str | None = None
        if decorrelar:
            banco = self._banco()
            self._banco_actual, self._orden, self._aviso = banco, self._asignacion(banco), self._aviso_de(banco)
            filtros = self._filtros_en_orden(banco, self._orden)
        else:
            self._banco_actual, self._orden = None, list(range(n))
            filtros = dict.fromkeys((p.nombre for p in instalacion.parlantes), None)
        self._filtros = filtros
        self._decorreladores = self._nuevos_decorreladores()
        # La extracción de ambiente tiene latencia propia: hay que retrasar el camino
        # directo lo mismo, o el ambiente llegaría corrido respecto de él y el efecto de
        # precedencia haría lo contrario de lo que se busca.
        self.latencia = self._extractor.latencia if self._extractor else 0
        self._layout_espacial = self._disposicion_espacial()
        self.espacial: SpatialUpmix | None = (
            self._nuevo_espacial() if c.algorithm("spatial") in SPATIAL_RENDERS else None
        )
        """The spatial renderer (spec 2026-10-04), or None in the classic mode."""
        if self.espacial is not None and self._extractor is None:
            self.latencia = self.espacial.latency
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

    def _parametros_banco(self) -> tuple[int, int, float, float]:
        """(largo, semilla, retardo medio, variación) del banco para la cadena actual."""
        c = self._cadena
        semilla = c.param("decorrelate", "seed") if self._semilla_fija is None else self._semilla_fija
        medio, variacion = c.param("decorrelate", "mean_ms"), c.param("decorrelate", "spread_ms")
        largo = decorrelate.largo_necesario(c.param("decorrelate", "length"), medio, variacion, self.sr)
        return largo, semilla, medio, variacion

    def _banco(self) -> decorrelation_bank.Bank:
        """El banco de filtros del decorrelador para la cadena actual, con caché (en
        `decorrelation_bank.bank`): el A/B entre dos presets con otra semilla no lo recalcula
        cada vez, y la descripción de la cadena pide el mismo."""
        return decorrelation_bank.bank(len(self.instalacion.parlantes), *self._parametros_banco(), self.sr)

    def _aviso_de(self, banco: decorrelation_bank.Bank) -> str | None:
        """El aviso con más parlantes que `decorrelate.MAXIMO_FIJOS`. Se calcula al armar el banco
        y no en las métricas: el banco de 3 con que se compara puede tardar en calcularse."""
        return decorrelation_bank.notice_for(len(banco.filters), *self._parametros_banco(), self.sr)

    def _mezclas(self) -> list[tuple[float, float]]:
        """(pan, ambiente que de verdad suena) de cada parlante, para asignar los filtros."""
        mezcla = self._objetivo_mezcla(self._cadena.algorithm("ambience") != "off") if self._extractor else 0.0
        return [(p.pan, p.ambiente * mezcla) for p in self.instalacion.parlantes]

    def _modo_asignacion(self) -> str:
        """`order` (el filtro k al parlante k, lo de siempre) o `mix` (por la mezcla de cada uno)."""
        return self._cadena.param("decorrelate", "assignment")

    def _asignacion(self, banco: decorrelation_bank.Bank) -> list[int]:
        """Qué filtro del banco va a cada parlante: en orden, o los más distintos a los parlantes
        con mezclas más parecidas (experimentos/16 §2.4 y §9)."""
        if self._modo_asignacion() == "order":
            return list(range(len(banco.filters)))
        return decorrelation_bank.assign(banco, self._mezclas())

    def _filtros_en_orden(self, banco: decorrelation_bank.Bank, orden: list[int]) -> dict[str, np.ndarray]:
        return {p.nombre: banco.filters[k] for p, k in zip(self.instalacion.parlantes, orden, strict=True)}

    def _cambiar_banco(self, banco: decorrelation_bank.Bank, orden: list[int], aviso: str | None) -> None:
        self._banco_actual, self._orden, self._orden_pendiente, self._aviso = banco, orden, None, aviso
        self._cambiar_filtros(self._filtros_en_orden(banco, orden))

    def _revisar_asignacion(self) -> None:
        """Si la mejor asignación para las mezclas de ahora es otra, queda pendiente del próximo
        corte: cambiar el filtro de un parlante mientras suena es un salto en la señal, y mover
        un pan no debería producir un corte que nadie pidió."""
        if self._banco_actual is None or self._modo_asignacion() == "order":
            return
        orden = self._asignacion(self._banco_actual)
        self._orden_pendiente = None if orden == self._orden else orden

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

    def _clases_de_corte(self, cambios: set, nuevo: ChainValues) -> set[str]:
        """Qué clases de cambio de `cambios` necesitan un corte. Es pura: la comparten
        `aplicar_cadena` y `pide_corte` para que no se separen."""
        clases = set()
        if {("ambience", p) for p in ("lam", "threshold", "sigma", "min_energy")} & cambios and self._extractor:
            clases.add("ambiente")
        claves_banco = {("decorrelate", k) for k in ("length", "seed", "mean_ms", "spread_ms", "assignment")}
        if self.decorrelar and claves_banco & cambios:
            clases.add("banco")
        if (self.decorrelar and nuevo.algorithm("decorrelate") != "off") != self._decorrelacion_objetivo():
            clases.add("decorrelacion")
        ecualizar = nuevo.algorithm("eq") != "off"
        if self.ecualizar and (
            ecualizar != self.ecualizacion_activa
            or {("eq", "max_boost_db"), ("eq", "budget_db"), ("eq", "treble_cap_db")} & cambios
        ):
            clases.add("eq")
        if any(etapa == "diffuse" and param != "level_db" for etapa, param in cambios):
            clases.add("difusion")
        if any(etapa == "bass" and param != "harmonics_db" for etapa, param in cambios):
            clases.add("graves")
        if ("spatial", None) in cambios:
            clases.add("espacial")
        if ("limiter", None) in cambios or ("limiter", "lookahead_ms") in cambios:
            clases.add("limitador")
        return clases

    def _clases_sin_corte(self, clases: set[str], anterior: ChainValues, nuevo: ChainValues) -> set[str]:
        """De `clases`, las que el fundido cruzado pasa sin corte (spec 2026-10-08 §4, etapa 2): la
        difusión, los graves, la ecualización, el decorrelador (su banco y prenderlo o apagarlo), el
        extractor y el limitador si su latencia no cambia. Con `transition=cut`, ninguna."""
        if nuevo.algorithm("transition") == "cut":
            return set()
        sin_corte = {"difusion", "graves", "eq", "banco", "decorrelacion", "ambiente"}
        if chain_stages.limiter_latency(anterior, self.sr) == chain_stages.limiter_latency(nuevo, self.sr):
            sin_corte.add("limitador")
        return clases & sin_corte

    def pide_corte(self, valores: ChainValues) -> bool:
        """Si `aplicar_cadena(valores)` pediría un corte. Consulta pura, sin efectos."""
        nuevo = valores.copy()
        clases = self._clases_de_corte(set(cadena.changed_params(self._cadena, nuevo)), nuevo)
        return bool(clases - self._clases_sin_corte(clases, self._cadena, nuevo))

    def aplicar_cadena(self, valores: ChainValues, *, en_corte: bool = False) -> str:
        """Lleva el motor a `valores`. Devuelve cómo: `"none"`, `"live"`, `"crossfade"` o `"cut"`.

        Lo `live` se mueve ya, con sus rampas; lo `cut` se pide al corte (o, con
        `en_corte`, se aplica ya: quien llama está en el fondo de uno, como `preset_load`).
        Con `transition=crossfade`, si todo lo que tiene estado es la difusión, los graves, la
        ecualización, el decorrelador, el extractor o un limitador de la misma latencia, va por el
        fundido (`cambiar`): la etapa nueva se calienta a la sombra y se mezcla con la vieja
        (`_cruzar`), y devuelve `"crossfade"`.
        Los algoritmos de `ambience`, `decorrelate` y `eq` se comparan con el estado que el
        motor tiene de verdad, no con la cadena anterior: los atributos de antes
        (`decorrelacion_activa`, …) se pueden haber cambiado a mano.
        """
        anterior, nuevo = self._cadena, valores.copy()
        cambios = set(cadena.changed_params(anterior, nuevo))
        self._cadena = nuevo
        hubo_vivo = self._aplicar_vivo(cambios, nuevo)

        al_corte = []
        al_fundido: list[Callable[[], None]] = []
        clases = self._clases_de_corte(cambios, nuevo)
        # Si una sola clase todavía corta, las demás viajan en ese corte: no se mezclan los dos caminos.
        fundir = not en_corte and bool(clases) and not clases - self._clases_sin_corte(clases, anterior, nuevo)
        if "ambiente" in clases and fundir:
            # Un segundo extractor con los parámetros nuevos y el mismo `n_fft`: la misma latencia,
            # así el camino directo sigue alineado.
            actual = _instancias(self._extractor)[-1]
            extractor = ambience.Extractor(self._parametros_ambiente(), actual.n_fft, actual.salto)
            al_fundido.append(lambda: self._cruzar("_extractor", extractor, extractor.n_fft + 10 * extractor.salto))
        elif "ambiente" in clases:
            parametros = self._parametros_ambiente()
            al_corte.append(lambda: setattr(self._extractor, "p", parametros))
        if "banco" in clases:
            # El banco (y a qué parlante va cada filtro) se calcula ahora y no en el fondo del
            # corte, que corre dentro de `procesar`.
            banco = self._banco()
            orden, aviso = self._asignacion(banco), self._aviso_de(banco)
            # Lo pendiente era del banco de antes: lo reemplaza esta asignación.
            self._orden_pendiente = None
            if fundir:
                al_fundido.append(lambda: self._cruzar_banco(banco, aviso))
            else:
                al_corte.append(lambda: self._cambiar_banco(banco, orden, aviso))
        elif self.decorrelar and ("ambience", None) in cambios:
            # Prender o apagar el extractor cambia las mezclas: la asignación espera al corte.
            self._revisar_asignacion()
        decorrelar = self.decorrelar and nuevo.algorithm("decorrelate") != "off"
        if "decorrelacion" in clases and fundir:
            # Con el cruce de graves, una etapa nueva para la alimentación con el otro atraso.
            graves_nuevos = self._nuevos_graves() if _instancias(self._graves)[-1].to is not None else None
            al_fundido.append(lambda: self._cruzar_decorrelacion(graves_nuevos))
        elif "decorrelacion" in clases:
            al_corte.append(lambda: setattr(self, "decorrelacion_activa", decorrelar))
        ecualizar = nuevo.algorithm("eq") != "off"
        if not self.ecualizar:
            # Sin la etapa en el camino no hay nada que cambie el sonido: no hace falta el corte.
            self.ecualizacion_activa = ecualizar
        elif "eq" in clases and fundir:

            def cruzar_ecualizacion() -> None:
                # De la cadena al correr, no de cuando se pidió: otro pedido antes del inicio que la
                # devolvió a su valor no tiene clase propia (se compara con el estado de ahora).
                self.ecualizacion_activa = self._cadena.algorithm("eq") != "off"
                self._cruzar_taps()

            al_fundido.append(cruzar_ecualizacion)
        elif "eq" in clases:

            def cambiar_ecualizacion() -> None:
                self.ecualizacion_activa = ecualizar
                self._cambiar_taps()

            al_corte.append(cambiar_ecualizacion)
        # Las etapas nuevas (`chain_stages.py`): se arman ahora, fuera del bloque (el fondo del
        # corte y el inicio del fundido corren dentro de `procesar`), y se cambian enteras en el
        # fondo o se cruzan con las viejas en el fundido.
        if "difusion" in clases:
            difusion = self._nueva_difusion()
            if fundir:
                al_fundido.append(lambda: self._cruzar("_difusion", difusion, difusion.memory_samples()))
            else:
                al_corte.append(lambda: setattr(self, "_difusion", difusion))
        if "graves" in clases:
            graves = self._nuevos_graves()
            if fundir:
                al_fundido.append(lambda: self._cruzar_graves(graves))
            else:
                al_corte.append(lambda: setattr(self, "_graves", graves))
        if "espacial" in clases:
            render = nuevo.algorithm("spatial")
            espacial = self._nuevo_espacial(nuevo) if render in SPATIAL_RENDERS else None
            # The stages `direct` does not feed are built here, outside the bottom, and swapped in
            # at it when the render enters or leaves `direct` (review 2026-10-06: kept, the diffuse
            # tail replayed the music from before `direct`).
            fresh = (
                (self._nueva_difusion(), self._nuevos_graves())
                if DIRECT in {self.render, anterior.algorithm("spatial"), render}
                else None
            )
            al_corte.append(lambda: self._switch_render(render, espacial, fresh))
        if "limitador" in clases and fundir:
            limitadores = {p.nombre: self._nuevo_limitador() for p in self.instalacion.parlantes}
            al_fundido.append(lambda: self._cruzar_limitadores(limitadores))
        elif "limitador" in clases:

            def cambiar_limitadores() -> None:
                self._limitadores = {p.nombre: self._nuevo_limitador() for p in self.instalacion.parlantes}

            al_corte.append(cambiar_limitadores)
        # `volume.avrcp` no es del motor: el servicio mueve el volumen digital (`bt_volume.py`).

        if al_fundido:
            self.cambiar(lambda: [accion() for accion in al_fundido])
            return "crossfade"
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
            for lim in (i for cada in self._limitadores.values() for i in _instancias(cada)):
                self._configurar_limitador(lim, nuevo)
            hubo = True
        if self.espacial is not None and any(e == "spatial" and k is not None for e, k in cambios):
            self.espacial.set_params(self._parametros_espaciales(nuevo))
            hubo = True
        if ("diffuse", "level_db") in cambios and nuevo.algorithm("diffuse") == self._cadena_difusion():
            for difusion in _instancias(self._difusion):
                difusion.set_level(nuevo.param("diffuse", "level_db"))
            hubo = True
        if ("bass", "harmonics_db") in cambios and nuevo.algorithm("bass") == self._graves.algorithm:
            for graves in _instancias(self._graves):
                graves.set_harmonics(nuevo.param("bass", "harmonics_db"))
            hubo = True
        return hubo

    def _configurar_limitador(
        self, lim: limiter.PeakLimiter | limiter.TruePeakLimiter, valores: ChainValues | None = None
    ) -> None:
        """Las perillas vivas del limitador (`ceiling_db`, `release_ms`) de `valores` (la cadena)."""
        c = valores or self._cadena
        techo_db, liberacion_ms = c.param("limiter", "ceiling_db"), c.param("limiter", "release_ms")
        if isinstance(lim, limiter.TruePeakLimiter):
            lim.configure(techo_db, liberacion_ms)
        else:
            lim.ceiling, lim.step = 10 ** (techo_db / 20), 1.0 / (liberacion_ms / 1000 * self.sr)

    def _parametros_espaciales(self, cadena: ChainValues | None = None) -> SpatialParams:
        """`cadena`: the chain the renderer is for (a change builds it before the chain is swapped)."""
        c = cadena or self._cadena
        if c.param("spatial", "manual"):
            params = SpatialParams(
                arc_deg=c.param("spatial", "arc_deg"),
                ambience=c.param("spatial", "ambience"),
                ambient_level_db=c.param("spatial", "ambient_level_db"),
                haas_ms=c.param("spatial", "haas_ms"),
            )
        else:
            params = from_character(c.param("spatial", "character"))
        return replace(params, front_intact=c.algorithm("spatial") == "front")

    def _disposicion_espacial(self) -> tuple[dict[str, float], frozenset[str]]:
        """Each principal's angle (from its pan and ambience) and the ambient speakers."""
        ps = self.instalacion.parlantes
        angles = {p.nombre: control.angle_of(p.pan, p.ambiente) for p in ps if p.role_kind != "ambient"}
        return angles, frozenset(p.nombre for p in ps if p.role_kind == "ambient")

    def _clasico(self) -> dict[str, tuple[float, float]]:
        return {p.nombre: (p.pan, p.ambiente) for p in self.instalacion.parlantes}

    def _nuevo_espacial(self, cadena: ChainValues | None = None) -> SpatialUpmix:
        angles, ambient = self._layout_espacial
        names = [p.nombre for p in self.instalacion.parlantes]
        return SpatialUpmix(
            names, angles, set(ambient), self.sr, self._parametros_espaciales(cadena), classic=self._clasico()
        )

    def _actualizar_espacial(self) -> None:
        """A role or a pan changed: the ring changes live (the overlap-add crossfades it; review
        2026-10-04, where a rebuild at a cut clicked and added latency)."""
        disposicion = self._disposicion_espacial()
        if disposicion == self._layout_espacial:
            return
        self._layout_espacial = disposicion
        if self.espacial is not None:
            angles, ambient = disposicion
            self.espacial.set_layout(angles, set(ambient), self._clasico())

    def _cadena_difusion(self) -> str:
        return "noise_tail" if self._difusion.active else "off"

    def _cambiar_filtros(self, filtros: dict[str, np.ndarray]) -> None:
        self._filtros = filtros
        # Filtros nuevos: un cambio de largo (o de banco) arranca desde el silencio, como la cola.
        self._decorreladores = self._nuevos_decorreladores()

    def _nuevos_decorreladores(self) -> dict[str, eq.StreamingFIR | None]:
        """One fresh `StreamingFIR` per speaker (None without a filter), tails empty. It follows the
        engine like every other filter, so the convolution runs in Rust when the engine is Rust."""
        return {n: None if h is None else eq.StreamingFIR(h) for n, h in self._filtros.items()}

    def _cambiar_taps(self) -> None:
        for p in self.instalacion.parlantes:
            if p.nombre in self._ecualizador:
                self._ecualizador[p.nombre].set_taps(self._taps_de(p))

    def _cruzar_taps(self) -> None:
        """La ecualización de cada parlante con sus coeficientes de ahora (la curva, el tope, la
        bandera, el render). En el fondo de un corte (el fundido ya se canceló), los coeficientes
        cambian como siempre (`_cambiar_taps`). Al empezar un fundido, se cruza una sola vez después
        de todas las acciones del inicio (`_cruzar_ecualizador`): con las curvas que dejaron (un preset
        las escribe en otra acción) y sin rehacer los filtros por cada perilla arrastrada del lote."""
        if not self._transicion.busy:
            self._cambiar_taps()
            return
        self._taps_pendientes = True

    def _cruzar_ecualizador(self) -> None:
        """Un `StreamingFIR` nuevo por parlante, cruzado con el que suena, que sigue con sus
        coeficientes hasta que termina el fundido; el nuevo calienta `TAPS - 1`."""
        for p in self.instalacion.parlantes:
            if p.nombre in self._ecualizador:
                nuevo = eq.StreamingFIR(self._taps_de(p))
                self._ecualizador[p.nombre] = self._cruce(self._ecualizador[p.nombre], nuevo)
        if self._ecualizador:
            self._memorias["_ecualizador"] = eq.TAPS - 1

    def _cruzar_banco(self, banco: decorrelation_bank.Bank, aviso: str | None) -> None:
        """Una acción del inicio de un fundido: el banco nuevo. Como la ecualización, se cruza una sola
        vez después de todas las acciones del inicio (`_cruzar_decorreladores`), y la asignación de
        filtros se calcula entonces, con las mezclas que dejaron (un preset escribe los pan y los
        ambientes en otra acción, después de la de la cadena). En el fondo de un corte, el cambio de
        siempre (`_cambiar_banco`)."""
        if not self._transicion.busy:
            self._cambiar_banco(banco, self._asignacion(banco), aviso)
            return
        self._banco_pendiente = (banco, aviso)

    def _cruzar_decorreladores(self, banco: decorrelation_bank.Bank, aviso: str | None) -> None:
        """Un `StreamingFIR` nuevo por parlante con el banco nuevo, cruzado con el que suena (sin filtro,
        la señal tal cual). Las métricas leen el banco nuevo desde ya."""
        orden = self._asignacion(banco)
        self._banco_actual, self._orden, self._orden_pendiente, self._aviso = banco, orden, None, aviso
        self._filtros = self._filtros_en_orden(banco, orden)
        nuevos = self._nuevos_decorreladores()
        self._decorreladores = {
            nombre: self._cruce(
                _PASO if actual is None else actual,
                _PASO if nuevos[nombre] is None else nuevos[nombre],
                self._pesos_decorrelador,
            )
            for nombre, actual in self._decorreladores.items()
        }
        self._memorias["_decorreladores"] = max(
            (len(h) - 1 for h in self._filtros.values() if h is not None), default=0
        )

    def _cruzar_decorrelacion(self, graves: chain_stages.BassStage | None = None) -> None:
        """Una acción del inicio de un fundido: prender o apagar el decorrelador. El motor ya calcula
        las dos señales (el decorrelador convoluciona aunque esté desviado): las mezcla con los pesos
        del bloque (`_decorrelado_o_seco`) y `decorrelacion_activa` cambia al terminar
        (`_resolver_cruces`). El destino sale de la cadena al correr, como el de la ecualización.

        La alimentación de graves (`crossover`) se atrasa con la bandera, y un atraso nuevo empieza
        con su historia vacía (`BassStage._delayed`): cambiarlo sonando era un salto en lo que recibe el
        parlante de graves (MEDIDO: 19-44 veces el paso más grande de la alimentación). `graves`, una
        etapa nueva armada al pedirse, se cruza con la de ahora y se alimenta desde ya con el atraso
        nuevo (`_alimentar_graves`)."""
        destino = self.decorrelar and self._cadena.algorithm("decorrelate") != "off"
        self._decorrelacion_destino = None if destino == self.decorrelacion_activa else destino
        if self._decorrelacion_destino is not None and graves is not None:
            self._cruzar_graves(graves)

    def _decorrelacion_objetivo(self) -> bool:
        """Lo que `decorrelacion_activa` vale, o va a valer al terminar el fundido en curso."""
        destino = self._decorrelacion_destino
        return self.decorrelacion_activa if destino is None else destino

    def _decorrelado_o_seco(self, seco: np.ndarray, decorrelado: np.ndarray) -> np.ndarray:
        """Lo que sale del decorrelador de un parlante: la señal decorrelada o la seca según la
        bandera; mientras un fundido la cambia, las dos mezcladas a igual potencia (`_pesos_decorrelador`)."""
        destino, activa, pesos = self._decorrelacion_destino, self.decorrelacion_activa, self._pesos_decorrelador()
        if destino is None or destino == activa or pesos is None:
            return decorrelado if activa else seco
        viejo, nuevo = (decorrelado, seco) if activa else (seco, decorrelado)
        peso_viejo, peso_nuevo = pesos
        if np.ndim(peso_nuevo) == 0 and peso_nuevo == 0:
            return viejo  # calentando: solo suena la de antes
        return viejo * peso_viejo + nuevo * peso_nuevo

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
        m["decorrelate"]["active"] = self._decorrelacion_objetivo()  # durante un fundido, el nuevo
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
                **self._metricas_separacion(),
            }
        )
        # Durante un fundido, las de la etapa nueva (`Crossfaded.new`).
        m["diffuse"].update(_instancias(self._difusion)[-1].metrics())
        m["eq"].update(
            {
                "active": self.ecualizar and self.ecualizacion_activa and self.render != DIRECT,
                "max_boost_db": {n: round(float(np.max(c)), 2) if c is not None else 0.0 for n, c in curvas.items()},
                "boost_energy_db": {
                    n: round(eq.boost_energy_db(c), 2) if c is not None else 0.0 for n, c in curvas.items()
                },
            }
        )
        m["bass"].update(_instancias(self._graves)[-1].metrics())
        m["spatial"].update({"render": self.render, "makeup_db": round(self._makeup.current_db, 2)})
        m["volume"]["volume_db_now"] = round(self._volumen.current_db, 2)
        m["transition"].update({"mode": self._cadena.algorithm("transition"), "busy": self._transicion.busy})
        m["limiter"].update(
            {
                "kind": "true_peak" if self.latencia_limitador else "peak",
                "latency_ms": round(self.latencia_limitador / self.sr * 1000, 3),
                "reduction_db": {n: round(v, 2) for n, v in self.reduccion_limitador_db().items()},
                "active_pct": self.uso_limitador_pct(),
            }
        )
        return m

    def _metricas_separacion(self) -> dict:
        """Cómo se separan los filtros (experimentos/16 §2 y §9): `worst_above_500` (el peor par
        del banco sobre 500 Hz, con hasta ±1 ms de desfase), `worst_feeds` (lo mismo para lo que
        suena, con las mezclas de cada parlante: el modelo de `decorrelation_bank.assign`),
        `assignment` (qué filtro del banco tiene cada parlante), `assignment_mode` (`order` o
        `mix`), `reassign_pending` (una asignación mejor espera al próximo corte) y `notice`, el
        aviso con más parlantes que `decorrelate.MAXIMO_FIJOS` (None si no hay nada que avisar)."""
        banco = self._banco_actual
        if banco is None:
            return {
                "worst_above_500": None,
                "worst_feeds": None,
                "assignment": {},
                "assignment_mode": None,
                "reassign_pending": False,
                "notice": None,
            }
        return {
            "worst_above_500": banco.worst_above_500,
            "worst_feeds": decorrelation_bank.worst_feeds(banco, self._mezclas(), self._orden),
            "assignment": {p.nombre: k for p, k in zip(self.instalacion.parlantes, self._orden, strict=True)},
            "assignment_mode": self._modo_asignacion(),
            "reassign_pending": self._orden_pendiente is not None,
            "notice": self._aviso,
        }

    def uso_limitador_pct(self) -> dict[str, float]:
        """El porcentaje de los últimos 5 s en que el limitador de cada parlante bajó la ganancia."""
        return {
            n: round(100 * activas / total, 1) if total else 0.0 for n, (total, activas) in self._uso_limitador.items()
        }

    def reiniciar(self) -> None:
        """Vacía el estado. Hay que llamarlo al empezar una reproducción nueva."""
        # Una reproducción nueva no sigue ningún fundido: lo cruzado que no se rearma abajo (el
        # extractor, la bandera del decorrelador) queda en lo nuevo.
        if isinstance(self._extractor, Crossfaded):
            self._extractor = self._extractor.resolve()
        if self._decorrelacion_destino is not None:
            self.decorrelacion_activa, self._decorrelacion_destino = self._decorrelacion_destino, None
        # Cola de la convolución: la parte del bloque anterior que todavía no salió.
        self._decorreladores = self._nuevos_decorreladores()
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
        self._transicion = Transition(self.sr)
        self._pesos_bloque: tuple | None = None
        """Los pesos (viejo, nuevo) del bloque en curso para los `Crossfaded`, calculados una vez al
        empezarlo (`Transition.block_weights`); None fuera de una transición."""
        self._pesos_bloque_limitador: tuple | None = None
        """Los mismos, siempre `equal_gain`: los de los limitadores (`_cruzar_limitadores`)."""
        self._pesos_bloque_decorrelador: tuple | None = None
        """Los mismos, siempre `equal_power`: los del decorrelador (`_pesos_decorrelador`)."""
        self._memorias: dict[str, int] = {}
        """Lo que cada etapa cruzada en este inicio pide calentar; la última cruzada de cada una manda."""
        self._taps_pendientes = False
        """Si una acción del inicio de un fundido pidió cruzar la ecualización (`_cruzar_taps`)."""
        self._banco_pendiente: tuple[decorrelation_bank.Bank, str | None] | None = None
        """El banco (y su aviso) que una acción del inicio de un fundido pidió cruzar (`_cruzar_banco`)."""
        self._ganancia_deslizando: dict[str, tuple[float, int]] = {}
        """(objetivo en dB, muestras que faltan) de la ganancia de un parlante durante un fundido:
        reemplaza el límite de `velocidad_ganancia_db_s` hasta llegar."""
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
        makeup = getattr(self, "_makeup", None)
        self._makeup = DecibelRamp(
            makeup.target_db if makeup is not None else 0.0,
            cadena.default("volume", "volume_speed_db_s"),
            self.sr,
        )
        self._makeup.jump()
        self.comparison_block_db: float | np.ndarray = self._compensacion.target_db
        """The A/B's compensation (dB) the last block was made with (render_match.py takes it out)."""
        self.render_makeup_block_db: float | np.ndarray = self._makeup.target_db
        """The render's makeup (dB) the last block was made with, per sample while it ramps: the
        match takes it back out of what it measures (render_match.py)."""
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
        # Con `cut` es el corte de siempre: en el fondo, `_saltar` resuelve el cruce a la nueva.
        self.cambiar(lambda: self._cruzar_graves(graves))

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
    def render_makeup_db(self) -> float:
        return self._makeup.target_db

    @render_makeup_db.setter
    def render_makeup_db(self, value: float) -> None:
        """Moves as a ramp (30 dB/s); at a cut's bottom it jumps with everything else."""
        self._makeup.target_db = value

    def jump_render_makeup(self, value: float) -> None:
        """Sets the makeup without a ramp: before audio flows, or at a cut's bottom."""
        self._makeup.target_db = value
        self._makeup.jump()

    def _switch_render(
        self,
        render: str,
        espacial: SpatialUpmix | None,
        fresh: tuple[chain_stages.DiffuseStage, chain_stages.BassStage] | None = None,
    ) -> None:
        """At a cut's bottom: the new render, its renderer, and the makeup it starts at."""
        previous, self.render, self.espacial = self.render, render, espacial
        if DIRECT in {previous, render} and previous != render:
            # What `direct` does not feed belongs to the render that is leaving: the decorrelator's
            # tails, the diffuse tail and the bass filters start empty; the EQ plays flat in direct.
            # All of it changes here, with the output at zero.
            self._decorreladores = self._nuevos_decorreladores()
            if fresh is not None:
                self._difusion, self._graves = fresh
            if self.ecualizar:
                self._cambiar_taps()
        if self.on_render_switch is not None:
            self._makeup.target_db = self.on_render_switch(render)

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
        """Si hay un corte o un fundido en curso: la salida no es una configuración quieta. El lazo
        de recalibración, el monitor y el emparejamiento de los renders no miden mientras tanto."""
        return self._corte.busy or self._transicion.busy

    @property
    def cortando(self) -> bool:
        """Si hay un corte en curso (la salida baja a cero o vuelve de él), no un fundido: lo que
        la sesión anota como corte intencional (`session.py`)."""
        return self._corte.busy

    @property
    def corte_pendiente(self) -> bool:
        """Si un corte baja hacia su fondo (no un fundido, ni un corte que ya vuelve): lo que se
        pida ahora debe viajar en él, o su fondo lo pisaría con lo de antes. Mientras sube, lo
        nuevo va por su propia transición: un preset no da vuelta la compuerta para otro hueco."""
        return self._corte.state == FadeGate.OUT

    def cambiar(self, accion: Callable[[], None] | None = None) -> None:
        """Pide un cambio por la transición que elige la etapa `transition`.

        Con `cut`, es exactamente `cortar(accion)`. Con `crossfade`, `accion` corre al empezar el
        próximo bloque y desde ahí todo se desliza en `fade_ms` (`_empezar_transicion`). Lo que se
        pide mientras un fundido suena se junta en **un** fundido pendiente, que empieza cuando
        termina este (`dsp/transition.py`).

        Con un corte bajando hacia su fondo (`corte_pendiente`), `accion` se suma a ese fondo,
        después de lo que el corte ya lleva: un fundido empezaría antes y el fondo lo pisaría con lo
        pedido primero.

        Como todo el motor, tiene un solo escritor: el hilo del motor del servicio (`service.py`,
        "One writer"), entre bloques o dentro de `procesar`. Ni `Motor` ni `Transition` son seguros
        entre hilos.
        """
        if self._cadena.algorithm("transition") == "cut" or self.corte_pendiente:
            self.cortar(accion)
            return
        if self._transicion.request(accion):
            self._transicion.begin(self._largo_transicion())

    def _largo_transicion(self) -> int:
        return max(1, round(self._cadena.param("transition", "fade_ms") * self.sr / 1000))

    def _empezar_transicion(self) -> None:
        """Al empezar un bloque: corre las acciones pedidas. Si alguna cruzó una etapa con estado
        (`_cruzar`), la nueva se calienta primero (WARM) y lo demás espera a que empiece el
        fundido; si no, empieza ya (`_empezar_fundido`)."""
        transicion = self._transicion
        self._memorias.clear()
        for accion in transicion.take_starting():
            accion()
        if self._taps_pendientes:
            self._taps_pendientes = False
            self._cruzar_ecualizador()
        if self._banco_pendiente is not None:
            banco_y_aviso, self._banco_pendiente = self._banco_pendiente, None
            self._cruzar_decorreladores(*banco_y_aviso)
        # El calentamiento se pide por la etapa que quedó puesta, no por cada una que un lote
        # arrastrado armó en el camino (2 s → 0,3 s calienta 0,315 s, no 1 s).
        transicion.need_warm(max(self._memorias.values(), default=0))
        self._memorias.clear()
        if transicion.state != Transition.WARM:
            self._empezar_fundido()

    def _empezar_fundido(self) -> None:
        """Lleva cada parámetro a su objetivo deslizándolo (o fundiendo dos lecturas, el retardo)
        en el largo del fundido, desde el bloque siguiente: todo lo que el cambio toca se mueve
        junto con la mezcla de las etapas cruzadas."""
        largo = self._transicion.length
        for p in self.instalacion.parlantes:
            for suave, objetivo in ((self._pan[p.nombre], p.pan), (self._ambiente[p.nombre], p.ambiente)):
                suave.target = objetivo
                suave.glide(largo)
            self._ganancia_deslizando[p.nombre] = (p.ganancia_db, largo)
        for rampa in (self._mezcla_ambiente, self._compensacion, self._makeup):
            rampa.glide(largo)
        # Las dos lecturas de una línea a retardos distintos no se parecen (música de banda ancha):
        # se funden siempre a igual potencia, diga lo que diga `shape`; con `equal_gain` el medio
        # bajaba ~3 dB (MEDIDO 2026-10-08). `shape` es para los fundidos entre dos instancias de una
        # etapa con estado (etapa 2 de la spec), que sí suenan parecido; los limitadores, siempre a
        # igual ganancia (`_cruzar_limitadores`).
        for nombre, objetivo in self.retardos_efectivos_ms().items():
            linea = self._lineas[nombre]
            if abs(objetivo - linea.actual_ms) > _NADA:
                linea.fundir_a(objetivo, largo, "equal_power")
            else:
                linea.saltar_a(objetivo)

    def _avanzar_transicion(self, n: int) -> None:
        """Después de un bloque: cuenta el fundido. Si terminó, empieza el pendiente (al próximo
        bloque; por un corte si mientras tanto `transition` pasó a `cut`) o, si no hay, devuelve a
        sus rampas los retardos que se pidieron mientras tanto (`objetivo_ms` no se mueve mientras
        una línea funde)."""
        lote = self._transicion.advance(n)
        if lote is Transition.FADE_STARTS:
            # Terminó el calentamiento: las rampas y las líneas empiezan junto con la mezcla.
            self._empezar_fundido()
            return
        if lote is None:
            return
        self._resolver_cruces()
        if lote and self._cadena.algorithm("transition") == "cut":
            self.cortar(lambda: [accion() for accion in lote])
            return
        if lote:
            self._transicion.begin(self._largo_transicion(), lote)
            return
        for nombre, objetivo in self.retardos_efectivos_ms().items():
            self._lineas[nombre].objetivo_ms = objetivo

    def cortar(self, accion: Callable[[], None] | None = None) -> None:
        """Pide un corte: baja a cero, corre `accion` y salta todo con la salida en cero.

        `accion` es lo que hay que cambiar en el fondo (cargar un preset, prender el
        decorrelador). Se ejecuta en el hilo que llama a `procesar`, que es el único que
        escribe el motor y la instalación.
        """
        if accion is not None:
            self._al_saltar.append(accion)
        if self._orden_pendiente is not None:
            # Una asignación de filtros que esperaba un corte viaja en este. Si en el mismo corte
            # se cambia el banco (un preset, `aplicar_cadena`), `_cambiar_banco` ya la descartó:
            # la del banco nuevo se calculó con él.
            def asignar_pendiente() -> None:
                orden = self._orden_pendiente
                if orden is not None:
                    self._cambiar_banco(self._banco_actual, orden, self._aviso)

            self._al_saltar.append(asignar_pendiente)
        self._corte.request()

    def _pesos(self) -> tuple | None:
        """Lo que cada `Crossfaded` pregunta: los pesos de este bloque."""
        return self._pesos_bloque

    def _pesos_limitador(self) -> tuple | None:
        """Los de un limitador cruzado: siempre `equal_gain`. Los dos limitan la misma entrada y
        salen casi iguales; a igual potencia (cos + sin, hasta 1,414) la mezcla pasaba el techo por
        +3 dB (MEDIDO en la revisión de la tarea 2, 2026-10-09)."""
        return self._pesos_bloque_limitador

    def _pesos_decorrelador(self) -> tuple | None:
        """Los del decorrelador (su banco, y prenderlo o apagarlo): siempre `equal_power`, como las
        líneas de retardo. Dos bancos distintos, o la señal seca y la decorrelada, casi no se parecen
        (para eso está): a igual ganancia la mezcla bajaba 2,4-3,0 dB en el medio del fundido sobre
        ruido, a igual potencia queda a 0,5 dB (MEDIDO 2026-10-09, tareas 3-5 del plan de la etapa 2)."""
        return self._pesos_bloque_decorrelador

    def _cruce(self, actual: object, nueva: object, pesos: Callable[[], tuple | None] | None = None) -> Crossfaded:
        """`actual` → `nueva` en un `Crossfaded`. Nunca anida: si `actual` ya es un cruce, es de
        este mismo inicio (otra acción del lote, que todavía no sonó), y se cruza desde su vieja."""
        if isinstance(actual, Crossfaded):
            actual = actual.resolve() if self._transicion.started else actual.old
        return Crossfaded(actual, nueva, pesos or self._pesos)

    def _cruzar(self, atributo: str, nueva: object, memoria: int) -> None:
        """Una acción del inicio de un fundido: pone `nueva` cruzada con la etapa que suena y anota
        que se caliente `memoria` muestras (hasta 1 s) antes de la mezcla (`_empezar_transicion`).

        Las perillas vivas se le vuelven a poner con la cadena de ahora: `nueva` se armó al pedirse,
        y una perilla viva movida mientras esperaba en el lote pendiente solo llegó a las puestas."""
        if atributo == "_difusion":
            nueva.set_level(self._cadena.param("diffuse", "level_db"))
        elif atributo == "_graves":
            nueva.set_harmonics(self._cadena.param("bass", "harmonics_db"))
        setattr(self, atributo, self._cruce(getattr(self, atributo), nueva))
        self._memorias[atributo] = memoria

    def _cruzar_graves(self, graves: chain_stages.BassStage) -> None:
        # Con el atraso con que va a quedar: un fundido que prende o apaga el decorrelador la alimenta
        # desde ya con el nuevo (`_alimentar_graves`).
        self._cruzar(
            "_graves", graves, graves.memory_samples(self._atraso_graves(decorrelando=self._decorrelacion_objetivo()))
        )

    def _cruzar_limitadores(self, limitadores: dict) -> None:
        """Cada limitador nuevo, cruzado con el de su parlante (uno que todavía no existía entra solo),
        con las perillas vivas de ahora y a igual ganancia (`_pesos_limitador`)."""
        for nombre, nuevo in limitadores.items():
            self._configurar_limitador(nuevo)
            actual = self._limitadores.get(nombre)
            self._limitadores[nombre] = nuevo if actual is None else self._cruce(actual, nuevo, self._pesos_limitador)
        self._memorias["_limitadores"] = max((lim.memory_samples for lim in limitadores.values()), default=0)

    def _resolver_cruces(self) -> None:
        """Cada `Crossfaded` queda en su etapa nueva, y la bandera del decorrelador en su destino: al
        terminar el fundido y en el fondo de un corte."""
        for atributo in ETAPAS_CRUZABLES:
            etapa = getattr(self, atributo)
            if isinstance(etapa, Crossfaded):
                setattr(self, atributo, etapa.resolve())
        for atributo in DICCIONARIOS_CRUZABLES:
            etapas = getattr(self, atributo)
            for nombre, etapa in etapas.items():
                if isinstance(etapa, Crossfaded):
                    nueva = etapa.resolve()
                    etapas[nombre] = None if nueva is _PASO else nueva
        if self._decorrelacion_destino is not None:
            self.decorrelacion_activa, self._decorrelacion_destino = self._decorrelacion_destino, None

    def _atraso_graves(self, *, decorrelando: bool | None = None) -> int:
        """Lo que se atrasa la alimentación de graves: el retardo medio del decorrelador si está activo
        (`decorrelando`: si lo estaría; None, la bandera de ahora)."""
        activo = self.decorrelacion_activa if decorrelando is None else decorrelando
        return round(self._cadena.param("decorrelate", "mean_ms") * self.sr / 1000) if activo else 0

    def _alimentar_graves(self, izq: np.ndarray, der: np.ndarray) -> None:
        """La alimentación de graves, una vez por bloque. Mientras un fundido prende o apaga el
        decorrelador, cada etapa cruzada con su atraso: la vieja con el de ahora y la nueva con el que
        va a quedar, así ninguna lo cambia sonando (`_cruzar_decorrelacion`)."""
        graves = self._graves
        if isinstance(graves, Crossfaded) and self._decorrelacion_destino is not None:
            graves.old.feed(izq, der, self._atraso_graves())
            graves.new.feed(izq, der, self._atraso_graves(decorrelando=self._decorrelacion_destino))
        else:
            graves.feed(izq, der, self._atraso_graves())

    def _saltar(self) -> None:
        """Con la salida en cero: aplica lo pendiente y lleva cada parámetro a su objetivo.

        Un fundido en curso termina acá: lo que esperaba su turno corre primero, y el salto deja
        cada rampa y cada línea (`saltar_a` corta su fundido) en su objetivo."""
        acciones, self._al_saltar = [*self._transicion.cancel(), *self._al_saltar], []
        self._ganancia_deslizando.clear()
        # Un cruce en curso queda en la etapa nueva; y lo que se cruce ahora (una acción del fundido
        # cancelado) también: con la salida en cero no hay nada que mezclar.
        self._resolver_cruces()
        for accion in acciones:
            accion()
        self._resolver_cruces()
        for p in self.instalacion.parlantes:
            self._pan[p.nombre].target = p.pan
            self._pan[p.nombre].jump()
            self._ambiente[p.nombre].target = p.ambiente
            self._ambiente[p.nombre].jump()
            self._ganancia[p.nombre] = 10 ** (p.ganancia_db / 20)
        self._mezcla_ambiente.jump()
        self._compensacion.jump()
        self._makeup.jump()
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
        self._actualizar_espacial()

    def _curva_de(self, parlante) -> list[float] | np.ndarray | None:
        """La curva que suena: la guardada, con el tope de `eq.max_boost_db` aplicado al leerla
        (la guardada no se toca: bajar el tope y volver a subirlo la recupera)."""
        curva = parlante.ecualizacion_db
        if curva is None or not self.ecualizacion_activa or self.render == DIRECT:
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
        """Relee la ecualización de cada parlante y la cambia por `cambiar`: en el fondo de un corte
        o, con el fundido, cruzando cada filtro con uno nuevo (`_cruzar_taps`).

        Cambiar los coeficientes de un filtro mientras suena es un salto en la señal.
        """

        self.cambiar(self._cruzar_taps)

    def actualizar_desde_control(self) -> None:
        """Como `actualizar`, pero un retardo que tardaría más de 2 s en llegar va por `cambiar`
        (el fundido, o el corte con `transition=cut`).

        El lazo de recalibración sigue usando `actualizar`: sus correcciones se arrastran sin
        cortar, que es lo que lo hace inaudible. Un control, en cambio, pide un valor nuevo y
        quiere oírlo ya (spec §6.5).
        """
        # Una línea que funde todavía informa el retardo viejo, pero va al nuevo: se mide desde ahí.
        lento = any(
            abs(objetivo - (linea.objetivo_ms if linea.fundiendo else linea.actual_ms)) / self.velocidad_retardo_ms_s
            > MAXIMO_RAMPA_S
            for nombre, objetivo in self.retardos_efectivos_ms().items()
            for linea in (self._lineas[nombre],)
        )
        # Un pan o un ambiente nuevos pueden pedir otra asignación de filtros: espera a un corte.
        self._revisar_asignacion()
        # The spatial ring follows the roles in both branches: before, the slow one (a front and a
        # rear role swapped) never reached it (review 2026-10-04).
        self._actualizar_espacial()
        if lento:
            self.cambiar()
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

        `direct` has no ambience: only the calibration's delay.
        """
        if self.render == DIRECT:
            return {p.nombre: p.retardo_ms for p in self.instalacion.parlantes}
        return {
            p.nombre: p.retardo_ms + p.ambiente * self.instalacion.retardo_traseros_ms
            for p in self.instalacion.parlantes
        }

    def procesar(
        self, izq: np.ndarray, der: np.ndarray, canales: dict[str, np.ndarray] | None = None
    ) -> dict[str, np.ndarray]:
        """Un bloque estéreo de entrada, un bloque por parlante de salida.

        `canales`: a render made elsewhere, one block per speaker (a multichannel source,
        experimentos/17 §1.1). Each channel goes to its speaker as it is: no upmix, no decorrelator,
        no diffusion and no bass crossover; only what belongs to the speaker (delay, EQ, its bass
        stage, gain, volume, limiter). `izq` and `der` are then only its downmix, for the meters."""
        if len(izq) != len(der):
            msg = f"los canales tienen largos distintos: {len(izq)} y {len(der)}"
            raise ValueError(msg)
        n = len(izq)
        if n == 0:
            return {p.nombre: np.zeros(0) for p in self.instalacion.parlantes}
        if self._transicion.busy and not self._transicion.started:
            # Un fundido pedido empieza acá, antes de que nada lea un objetivo de este bloque.
            self._empezar_transicion()
        # Solo cuenta este bloque un fundido que ya corría al empezarlo: uno pedido a mitad del
        # bloque (reentrada: una acción o un callback de `procesar` que llama a `cambiar`) empieza
        # en el próximo, con sus acciones.
        fundiendo = self._transicion.busy
        # Mientras la etapa nueva se calienta (WARM) las rampas no avanzan: empiezan con la mezcla.
        calentando = self._transicion.state == Transition.WARM
        self._pesos_bloque = self._pesos_bloque_limitador = self._pesos_bloque_decorrelador = None
        if fundiendo:
            forma = self._cadena.param("transition", "shape")
            self._pesos_bloque = self._transicion.block_weights(n, forma)
            self._pesos_bloque_limitador = (
                self._pesos_bloque if forma == "equal_gain" else self._transicion.block_weights(n, "equal_gain")
            )
            self._pesos_bloque_decorrelador = (
                self._pesos_bloque if forma == "equal_power" else self._transicion.block_weights(n, "equal_power")
            )

        if self._extractor is not None:
            amb = self._extractor.procesar(izq, der)
            izq_d, der_d = self._directo_retrasado(izq, der)
            mezcla = self._mezcla_ambiente.current if calentando else self._mezcla_ambiente.block(n)
            # Métrica: la parte del ambiente en la entrada, suavizada (no toca el sonido).
            self._ambiente_energia = 0.8 * self._ambiente_energia + 0.2 * float(amb @ amb)
            self._entrada_energia = 0.8 * self._entrada_energia + 0.1 * float(izq_d @ izq_d + der_d @ der_d)
        else:
            amb = np.zeros(n)
            izq_d, der_d = izq, der
            mezcla = 0.0

        # El volumen y el corte se calculan una vez por bloque, igual para todos.
        envolvente, saltar = self._corte.block(n)
        self.volumen_del_bloque_db = self._volumen.block_db(n)
        salida_global = 10 ** (self.volumen_del_bloque_db / 20) * envolvente
        self.comparison_block_db = self._compensacion.current_db if calentando else self._compensacion.block_db(n)
        compensacion = 10 ** (self.comparison_block_db / 20)
        if not (isinstance(compensacion, float) and compensacion == 1.0):
            salida_global = salida_global * compensacion
        self.render_makeup_block_db = self._makeup.current_db if calentando else self._makeup.block_db(n)
        if not (isinstance(self.render_makeup_block_db, float) and self.render_makeup_block_db == 0.0):
            salida_global = salida_global * 10 ** (self.render_makeup_block_db / 20)
        directo_puro = self.render == DIRECT and canales is None
        # El cruce de graves: lo bajo del centro para el parlante de graves, atrasado como su
        # propia señal por el decorrelador (`chain_stages.py`). Cada etapa guarda lo suyo y
        # `before_delay` lo suma: dos etapas cruzadas suman cada una su propia alimentación.
        alimentar = canales is None and not directo_puro
        if alimentar:
            self._alimentar_graves(izq_d, der_d)
        # La difusión sigue sonando si la vieja o la nueva tiene cola (prenderla o apagarla funde).
        difundir = alimentar and any(d.active for d in _instancias(self._difusion))
        sonda = self.sonda if self.sonda is not None and self.sonda.active else None
        if sonda is not None:
            sonda.begin(n)

        espacial = self.espacial.process(izq, der) if self.espacial is not None and canales is None else None

        salida = {}
        for p in self.instalacion.parlantes:
            # Directo: mezcla L/R según el pan. Con pan 0 los dos por igual. Los dos pesos
            # son escalares en reposo y arreglos solo mientras se mueven.
            suave_pan, suave_amb = self._pan[p.nombre], self._ambiente[p.nombre]
            suave_pan.target, suave_amb.target = p.pan, p.ambiente
            pan = suave_pan.current if calentando else suave_pan.block(n)
            ambiente = (suave_amb.current if calentando else suave_amb.block(n)) * mezcla
            if canales is not None:
                given = canales.get(p.nombre)
                x = np.zeros(n) if given is None or len(given) != n else np.asarray(given, dtype=float)
                mezcla_propia = x
            elif directo_puro:
                # Pure aligned stereo: the constant-power pan of L/R and nothing else of the chain.
                angulo = (pan + 1) * (np.pi / 4)
                x = np.cos(angulo) * izq_d + np.sin(angulo) * der_d
                mezcla_propia = x
            elif espacial is not None:
                # Spatial: the direct part placed and left alone; only the ambience is decorrelated
                # (spec 2026-10-04 §3). The decorrelator's tail is the ambience's.
                directo_e, ambiente_e = espacial[p.nombre]
                mezcla_propia = directo_e + ambiente_e
                decorrelado = self._convolucionar(p.nombre, ambiente_e)
                x = directo_e + self._decorrelado_o_seco(ambiente_e, decorrelado)
            else:
                directo = (1 - pan) / 2 * izq_d + (1 + pan) / 2 * der_d
                x = (1 - ambiente) * directo + ambiente * amb
                # El decorrelador convoluciona aunque esté desviado, para que su cola esté lista
                # cuando vuelva.
                mezcla_propia = x
                decorrelado = self._convolucionar(p.nombre, x)
                x = self._decorrelado_o_seco(x, decorrelado)
            if difundir:
                x = self._difusion.process(p.nombre, x, mezcla_propia)
            if alimentar:
                x = self._graves.before_delay(p.nombre, x)
            x = self._lineas[p.nombre].procesar(x)
            if self.ecualizar:
                x = self._ecualizador[p.nombre].process(x)
            if not directo_puro:
                x = self._graves.process(p.nombre, x)
            activo = self._activo[p.nombre]
            activo.target = 0.0 if p.nombre in self.silenciados else 1.0
            ganancia = self._ganancia[p.nombre] if calentando else self._rampa_de_ganancia(p.nombre, p.ganancia_db, n)
            x = x * ganancia * salida_global * activo.block(n)
            # La ecualización solo realza: un pasaje fuerte puede pasar de escala completa, y
            # el limitador baja la ganancia en vez de recortar (`dsp/limiter.py`).
            if sonda is not None:
                # Después del retardo y de la ganancia: sigue el nivel de lo que suena, y su
                # referencia es exactamente lo que se sumó (`session.py`, `probe_measure.py`).
                x = sonda.add(p.nombre, x, envolvente)
            if p.nombre not in self._limitadores:
                self._limitadores[p.nombre] = self._nuevo_limitador()
            lim = self._limitadores[p.nombre]
            vigente = _instancias(lim)[-1]  # durante un cruce, el nuevo cuenta
            previa = vigente.gain
            limitado = lim.process(x)
            if isinstance(vigente, limiter.TruePeakLimiter):
                activas = round(vigente.active_fraction * n)
            elif vigente is lim:
                activas = n if limitado is not x else 0
            else:
                # Cruzado, la salida es siempre otra: el de pico no tocó el bloque si venía sin
                # reducción y nada pasó del techo (su propia prueba en `PeakLimiter.process`).
                activas = 0 if previa >= 1.0 and float(np.max(np.abs(x))) <= vigente.ceiling else n
            self._contar_limitador(p.nombre, n, activas=activas)
            salida[p.nombre] = limitado
        if saltar:
            # El fondo primero: termina el fundido y corre su lote pendiente. Contarlo antes
            # pedía, con `transition` ya en `cut`, un segundo corte al empezar la subida.
            self._saltar()
        elif fundiendo:
            self._avanzar_transicion(n)
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
        deslizando = self._ganancia_deslizando.pop(nombre, None)
        if abs(objetivo - actual) < _NADA:
            return np.full(n, actual)
        if deslizando is not None and deslizando[0] == objetivo_db:
            return self._deslizar_ganancia(nombre, objetivo_db, deslizando[1], n)
        margen_db = self.velocidad_ganancia_db_s * n / self.sr
        actual_db = 20 * np.log10(max(actual, 1e-9))
        alcanzable_db = float(np.clip(objetivo_db, actual_db - margen_db, actual_db + margen_db))
        fin = 10 ** (alcanzable_db / 20)
        rampa = np.linspace(actual, fin, n)
        self._ganancia[nombre] = fin
        return rampa

    def _deslizar_ganancia(self, nombre: str, objetivo_db: float, restantes: int, n: int) -> np.ndarray:
        """La ganancia de un fundido: lineal en dB, llega a `objetivo_db` en exactamente
        `restantes` muestras, sin el límite de velocidad; al llegar queda exacta."""
        actual_db = 20 * np.log10(max(self._ganancia[nombre], 1e-9))
        m = min(n, restantes)
        valores = np.full(n, 10 ** (objetivo_db / 20))
        valores[:m] = 10 ** ((actual_db + (objetivo_db - actual_db) * np.arange(1, m + 1) / restantes) / 20)
        if m < restantes:
            self._ganancia[nombre] = float(valores[m - 1])
            self._ganancia_deslizando[nombre] = (objetivo_db, restantes - m)
        else:
            valores[m - 1] = self._ganancia[nombre] = 10 ** (objetivo_db / 20)
        return valores

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
        """Overlap-add exacto (`eq.StreamingFIR`, en Rust si el motor es Rust): el resultado es
        idéntico a convolucionar todo de una vez."""
        filtro = self._decorreladores[nombre]
        return x if filtro is None else filtro.process(x)


class _Paso:
    """El decorrelador de un parlante sin filtro, dentro de un cruce: la señal tal cual."""

    def process(self, x: np.ndarray) -> np.ndarray:
        return x


_PASO = _Paso()


def _instancias(etapa: object) -> tuple:
    """Las instancias de una etapa: (vieja, nueva) si está cruzada, si no ella sola. La última es
    la que queda."""
    return (etapa.old, etapa.new) if isinstance(etapa, Crossfaded) else (etapa,)


def procesar_completo(motor: Motor, izq: np.ndarray, der: np.ndarray, bloque: int = 4096) -> dict[str, np.ndarray]:
    """Procesa una señal entera en bloques. Existe sobre todo para los tests."""
    partes: dict[str, list[np.ndarray]] = {p.nombre: [] for p in motor.instalacion.parlantes}
    for i in range(0, len(izq), bloque):
        salida = motor.procesar(izq[i : i + bloque], der[i : i + bloque])
        for nombre, trozo in salida.items():
            partes[nombre].append(trozo)
    return {n: np.concatenate(v) if v else np.zeros(0) for n, v in partes.items()}
