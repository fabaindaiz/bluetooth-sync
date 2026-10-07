"""The processing chain as data: every stage, every algorithm, every knob.

The design is `docs/superpowers/specs/2026-10-02-microcuts-chain-and-quality-design.md` §4.
The engine (`motor.py`) is a fixed sequence of stages; this module describes them so that
the contract can list and set any knob, the panel can draw a screen from the description
alone, and the choices can be stored. Identifiers are English; titles, summaries and help
are Spanish, because they are what the listener reads.

**Defaults reproduce the sound the engine had before the chain existed** (bit-exact,
`tests/test_chain_golden.py`). Several defaults are read from the DSP modules' own
constants, so there is one source for each number.

**Only choices are stored** (card *persist-inputs-derive-verdicts*): `ChainValues.choices`
is sparse — `{stage: {"algorithm"?: id, "params"?: {id: value}, "speakers"?: {name: {id:
value}}}}` — and the effective value of a knob is derived at read time (the choice, or the
default of the algorithm in use). A changed default reaches whoever did not choose.

**Where each knob lives** (`Param.store`):

- `chain`: in `ChainValues` (persisted by the service in `<config>/chain.json`);
- `installation`: in `instalacion.json`, as before (`pan`, `ambience`, `gain_db`,
  `rear_delay_ms`): the chain only shows and sets them, so there is one copy;
- `session`: in the service's memory, never on disk (`volume_db`, `muted`), as before.

Stages and algorithms marked `implemented=False` are declared (the panel can show them, the
contract can choose them and they are stored) but the engine does not run them yet: it
plays as if the default were chosen and the stage's metrics say `"pending": true`.
"""

from __future__ import annotations

import copy
import json
import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from aurasync.dsp import ambience, decorrelate, decorrelation_bank, eq, interpolation, limiter, profiles

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

SR = 48000
Kind = Literal["float", "int", "bool", "choice"]
Scope = Literal["global", "speaker"]
Apply = Literal["live", "cut", "restart"]
Store = Literal["chain", "installation", "session"]

BASS_CAPABLE_HZ = 60.0
"""A kind of speaker whose maker's low end is at or below this can take the bass of the
others (`dsp/profiles.py`: the Charge 6, 56 Hz; not the Go 4, 90 Hz)."""
AUTO = "auto"
"""The `to` of the crossover when the listener does not name a speaker: the first one of a
bass-capable kind."""
PRESET_EXCLUDED_STAGES = ("volume",)
"""A preset holds what the listener chooses as sound, not the volume: a louder preset wins
an A/B comparison for being louder (spec 2026-09-29 §5.6)."""


UNKNOWN_FIELD, OUT_OF_RANGE, WRONG_TYPE = "unknown_field", "out_of_range", "type"
BAD_REQUEST, UNAVAILABLE, NOT_FOUND = "bad_request", "unavailable", "not_found"
"""The contract's error codes this module raises (`control.HTTP_STATUS`)."""


class ChainError(ValueError):
    """A bad chain change. `code` is one of the contract's error codes (`control.py`)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Param:
    id: str
    title: str
    summary: str
    help: str
    kind: Kind
    default: Any
    low: float | None = None
    high: float | None = None
    step: float | None = None
    unit: str = ""
    choices: tuple[str, ...] = ()
    scope: Scope = "global"
    apply: Apply = "live"
    store: Store = "chain"
    implemented: bool = True
    choices_from: str = ""
    """`bass_speakers`: the choices are `auto` plus the installation's bass-capable speakers."""


@dataclass(frozen=True)
class Algorithm:
    id: str
    title: str
    summary: str
    help: str
    params: tuple[Param, ...] = ()
    cost: str = ""
    latency_ms: float = 0.0
    """Identical on every speaker, by construction."""
    implemented: bool = True
    latency_param: str = ""
    """When set, the algorithm's latency is this param's value in ms (it is a knob), not
    `latency_ms` (which is then the latency at the param's default)."""
    requires: str = ""
    """`bass_speaker`: unavailable without a speaker of a bass-capable kind."""
    caution: str = ""
    """`many_speakers`: available, with a `notice`, beyond the speakers fixed filters fully
    decorrelate (`decorrelate.MAXIMO_FIJOS`; experiment 16 §2). Until 2026-10-02 it was a
    requirement (`few_speakers`) and the algorithm was unavailable there."""


@dataclass(frozen=True)
class Stage:
    id: str
    title: str
    summary: str
    help: str
    algorithms: tuple[Algorithm, ...]
    default_algorithm: str
    algorithm_apply: Apply = "cut"
    """How a change of algorithm is applied."""

    def algorithm(self, algorithm_id: str) -> Algorithm:
        for a in self.algorithms:
            if a.id == algorithm_id:
                return a
        msg = f"stage {self.id!r} has no algorithm {algorithm_id!r}; it has {[a.id for a in self.algorithms]}"
        raise ChainError(OUT_OF_RANGE, msg)

    def find_param(self, param_id: str, algorithm_id: str | None = None) -> Param | None:
        """The param with this id: the given algorithm's version first, then any other's."""
        ordered = sorted(self.algorithms, key=lambda a: a.id != algorithm_id)
        for a in ordered:
            for p in a.params:
                if p.id == param_id:
                    return p
        return None

    def param_ids(self) -> list[str]:
        seen: list[str] = []
        for a in self.algorithms:
            seen.extend(p.id for p in a.params if p.id not in seen)
        return seen


# -- the stages -------------------------------------------------------------------------

_AMB = ambience.Parametros()
_MS = 1000 / SR

_PAN = Param(
    "pan",
    "Pan",
    "Qué parte del estéreo toca este parlante: -1 todo el izquierdo, +1 todo el derecho.",
    "Con A2DP cada parlante recibe una mezcla, no un canal. En 0 recibe los dos canales por igual.",
    "float",
    0.0,
    -1.0,
    1.0,
    0.05,
    scope="speaker",
    store="installation",
)
_AMBIENCE = Param(
    "ambience",
    "Ambiente",
    "Cuánto del ambiente extraído recibe este parlante (0: solo el sonido directo).",
    "Es la perilla principal del envolvimiento. También lo aleja un poco: el retardo de los "
    "traseros se aplica en proporción a este valor.",
    "float",
    0.0,
    0.0,
    1.0,
    0.05,
    scope="speaker",
    store="installation",
)
_MOVE_SPEED = Param(
    "move_speed",
    "Velocidad de los cambios",
    "Qué tan rápido llegan el pan, el ambiente y la mezcla a su valor nuevo.",
    "En unidades por segundo: 2 lleva de 0 a 1 en medio segundo. Más rápido se puede oír como un salto.",
    "float",
    2.0,
    0.5,
    10.0,
    0.5,
    unit="/s",
)

_AMBIENCE_STAGE = Stage(
    "ambience",
    "Ambiente",
    "Separa del estéreo lo que no está igual en los dos canales —la sala, la reverberación— "
    "para mandarlo a los parlantes que envuelven.",
    "Avendaño y Jot (2002): donde los dos canales se parecen poco y tienen energía parecida, "
    "hay ambiente. Apagarlo no cambia la latencia: el extractor sigue corriendo y su mezcla "
    "baja a cero.",
    (
        Algorithm(
            "avendano_jot",
            "Coherencia entre canales",
            "Extrae el ambiente por coherencia, banda por banda.",
            "Avendaño y Jot, AES 22 (2002), ecuaciones 11 y 12. docs/research/09 §11.2.",
            (
                Param(
                    "mix",
                    "Cantidad de ambiente",
                    "Cuánto del ambiente extraído entra en la mezcla de todos los parlantes.",
                    "Multiplica el ambiente de cada parlante. En 0 es como apagar la extracción.",
                    "float",
                    1.0,
                    0.0,
                    1.0,
                    0.05,
                ),
                Param(
                    "lam",
                    "Memoria",
                    "Cuánto recuerda el detector: más alto es más estable y más lento.",
                    "El factor de olvido de las correlaciones (λ del paper), por trama de 10,7 ms.",
                    "float",
                    _AMB.lam,
                    0.5,
                    0.99,
                    0.01,
                    apply="cut",
                ),
                Param(
                    "threshold",
                    "Umbral",
                    "Desde qué diferencia entre canales algo cuenta como ambiente.",
                    "El Φ₀ del paper: dónde está el codo de la curva que decide.",
                    "float",
                    _AMB.umbral,
                    0.0,
                    1.0,
                    0.05,
                    apply="cut",
                ),
                Param(
                    "sigma",
                    "Pendiente",
                    "Qué tan brusca es la decisión entre directo y ambiente.",
                    "La sigma del paper (su figura 2 usa 2 y 8). Muy alta mete artefactos.",
                    "float",
                    _AMB.sigma,
                    0.5,
                    10.0,
                    0.5,
                    apply="cut",
                ),
                Param(
                    "min_energy",
                    "Energía mínima",
                    "Evita confundir un instrumento paneado a un lado con ambiente.",
                    "La razón mínima entre la energía del canal flojo y la del fuerte para aceptar "
                    "una banda como ambiente (el criterio adicional del paper).",
                    "float",
                    _AMB.energia_minima,
                    0.0,
                    1.0,
                    0.05,
                    apply="cut",
                ),
                _MOVE_SPEED,
                _PAN,
                _AMBIENCE,
            ),
            cost="~1 ms por bloque",
            latency_ms=ambience.N_FFT * _MS,
        ),
        Algorithm(
            "off",
            "Sin extraer",
            "Todos los parlantes reciben solo el sonido directo, según su pan.",
            "El extractor sigue corriendo para que la latencia no cambie.",
            (_MOVE_SPEED, _PAN, _AMBIENCE),
            latency_ms=ambience.N_FFT * _MS,
        ),
    ),
    "avendano_jot",
    algorithm_apply="live",
)

_SPATIAL_PARAMS = (
    Param(
        "character",
        "Carácter",
        "De 0 (ubicación: cada instrumento en su lugar) a 1 (envolvimiento: el ambiente llena la pieza).",
        "Mueve juntas el arco, el ambiente, su nivel y el retardo de Haas, salvo con «Manual».",
        "float",
        0.5,
        0.0,
        1.0,
        0.05,
    ),
    Param(
        "manual",
        "Manual",
        "Con esto prendido mandan las cuatro perillas de abajo; apagado, las fija el carácter.",
        "",
        "bool",
        False,
    ),
    Param(
        "arc_deg",
        "Arco",
        "Cuánto se abre el estéreo en el anillo: un instrumento a la izquierda del todo va a -arco.",
        "Con 60° el estéreo queda adelante; con 150° lo de los costados se va atrás.",
        "float",
        105.0,
        30.0,
        180.0,
        5.0,
        "°",
    ),
    Param(
        "ambience",
        "Ambiente",
        "Cuánto de lo que parece ambiente se saca del sonido directo.",
        "",
        "float",
        0.5,
        0.0,
        1.0,
        0.05,
    ),
    Param(
        "ambient_level_db",
        "Nivel del ambiente",
        "El ambiente frente al directo. La energía total se conserva: es un balance, no volumen.",
        "",
        "float",
        3.0,
        -6.0,
        10.0,
        0.5,
        "dB",
    ),
    Param(
        "haas_ms",
        "Retardo de Haas",
        "Cuánto llega tarde el ambiente: entre 10 y 25 ms suma espacio sin robar la ubicación.",
        "research/09 §3.",
        "float",
        14.0,
        0.0,
        30.0,
        1.0,
        "ms",
    ),
)
"""The knobs of the spatial renders (`spatial` and `front` share them: dsp/spatial.py)."""

_SPATIAL_STAGE = Stage(
    "spatial",
    "Modo espacial",
    "Cómo se reparte el estéreo entre los parlantes.",
    "Spec 2026-10-04 (d-7c8794-be2477), dsp/spatial.py. Por cada banda de frecuencia, de qué lado "
    "del estéreo viene y cuánto es ambiente. El clásico sigue de fábrica hasta que un modo gane el "
    "A/B ciego 8 de 10.",
    (
        Algorithm(
            "classic",
            "Clásico",
            "Una mezcla de L y R por parlante, como hasta ahora.",
            "Un instrumento suena por todos los parlantes a la vez.",
        ),
        Algorithm(
            "spatial",
            "Espacial",
            "Cada instrumento sale de su lado; el ambiente, a los ambientales.",
            "Upmix por índice de paneo y de ambiente en tiempo-frecuencia (STFT de 2048, la misma "
            "latencia que el extractor). Conserva la energía de la entrada.",
            _SPATIAL_PARAMS,
            cost="~3 ms por bloque con 3 parlantes",
        ),
        Algorithm(
            "front",
            "Frente intacto",
            "Adelante, el estéreo tal cual; el resto, solo el ambiente.",
            "El principio de Logic7 (research/14 §4): nunca peor que el estéreo adelante, y la sala "
            "alrededor. El ambiente se suma, no se saca del frente. Sin par de adelante (nadie a un "
            "lado), suena como el espacial. El arco no se usa.",
            _SPATIAL_PARAMS,
            cost="~3 ms por bloque con 3 parlantes",
        ),
        Algorithm(
            "direct",
            "Directo",
            "Estéreo puro alineado: sin efectos, mismo volumen.",
            "Cada parlante toca su lado de L/R según su pan, a potencia constante. Sin extraer ambiente, "
            "sin decorrelar, sin ecualización, sin graves, sin cola difusa, sin Haas y sin upmix; quedan "
            "la alineación, la ganancia de cada parlante, el silencio, el volumen y el limitador. Suena "
            "a la misma sonoridad que el clásico (una ganancia de igualación, spec 2026-10-05 §9): sirve "
            "para comparar «antes y después».",
        ),
    ),
    "classic",
)

_DECORRELATE_STAGE = Stage(
    "decorrelate",
    "Decorrelación",
    "Hace que cada parlante suene apenas distinto, para que el oído no los funda en uno solo.",
    "Un filtro todo-paso distinto por parlante: no cambia el timbre, cambia la fase. Cambiarlo "
    "mueve a los parlantes en el tiempo unos milisegundos, por eso pasa por un corte breve.",
    (
        Algorithm(
            "group_delay",
            "Fase aleatoria suave",
            "Un retardo de grupo al azar, distinto por parlante, alrededor de 2,5 ms.",
            "docs/research/experimentos/10 §6: plano a ±0,1 dB, correlación con ruido rosa ~0,53.",
            (
                Param(
                    "mean_ms",
                    "Retardo medio",
                    "El retardo de grupo medio, igual en todos los parlantes.",
                    "Todos los filtros comparten este retardo, así ninguno se corre respecto de otro.",
                    "float",
                    decorrelate.RETARDO_MEDIO_MS,
                    1.0,
                    4.0,
                    0.1,
                    unit="ms",
                    apply="cut",
                ),
                Param(
                    "spread_ms",
                    "Variación",
                    "Cuánto se aparta el retardo de una frecuencia a otra.",
                    "Más variación decorrelaciona más, y a partir de cierto punto se oye como un leve chorus.",
                    "float",
                    decorrelate.VARIACION_MS,
                    0.0,
                    2.0,
                    0.1,
                    unit="ms",
                    apply="cut",
                ),
                Param(
                    "length",
                    "Largo del filtro",
                    "Cuántas muestras tiene cada filtro.",
                    "Tiene que alcanzar para el retardo medio más la variación.",
                    "int",
                    decorrelate.LARGO_POR_DEFECTO,
                    256,
                    1024,
                    128,
                    unit="muestras",
                    apply="cut",
                ),
                Param(
                    "seed",
                    "Semilla",
                    "Otro número da otro juego de filtros.",
                    "Sirve para probar si un resultado depende de un filtro en particular.",
                    "int",
                    0,
                    0,
                    9999,
                    1,
                    apply="cut",
                ),
                Param(
                    "assignment",
                    "Qué filtro a qué parlante",
                    "order: el filtro k al parlante k, como siempre. mix: los más distintos a los parlantes con mezclas más parecidas.",
                    "mix baja la correlación que predice el modelo, pero en simulación lo que suena no la "
                    "siguió: sobre 500 Hz salió peor en 20 de 32 casos (docs/research/experimentos/16 §9). "
                    "Queda para comparar de oído en el A/B ciego. Mover un pan o un ambiente no corta el sonido: la asignación nueva espera "
                    "al próximo corte.",
                    "choice",
                    "order",
                    choices=decorrelation_bank.ASSIGNMENTS,
                    apply="cut",
                ),
            ),
            cost="~0,2 ms por parlante",
            latency_ms=decorrelate.RETARDO_MEDIO_MS,
            latency_param="mean_ms",
            caution="many_speakers",
        ),
        Algorithm(
            "off",
            "Sin decorrelar",
            "Los parlantes que reciben la misma mezcla suenan idénticos.",
            "La calibración se mide con la decorrelación prendida.",
        ),
    ),
    "group_delay",
)

_DIFFUSE_STAGE = Stage(
    "diffuse",
    "Difusión",
    "Agrega una cola de sala distinta en cada parlante, para que el ambiente se sienta más amplio.",
    "Una respuesta al impulso de ruido que decae, con otra semilla por parlante. Alimentada con "
    "el ambiente extraído y un poco del directo.",
    (
        Algorithm("off", "Sin difusión", "Nada se agrega.", ""),
        Algorithm(
            "noise_tail",
            "Cola de ruido",
            "Una reverberación corta y distinta por parlante.",
            "docs/superpowers/specs/2026-10-02-… §5 (dsp/diffuse.py).",
            (
                Param(
                    "level_db",
                    "Nivel",
                    "Cuánto de la cola entra en la mezcla.",
                    "La energía de la cola respecto de lo que la alimenta. Con -16 dB la coherencia "
                    "entre parlantes baja solo 0,07; hacen falta ~-11 dB para bajarla 0,15 (MEDIDO, "
                    "tests/test_diffuse.py). Más alto se oye como sala.",
                    "float",
                    -12.0,
                    -30.0,
                    -6.0,
                    1.0,
                    unit="dB",
                ),
                Param(
                    "rt60_s",
                    "Largo",
                    "Cuánto tarda la cola en apagarse.",
                    "",
                    "float",
                    0.6,
                    0.1,
                    2.0,
                    0.1,
                    "s",
                    apply="cut",
                ),
                Param(
                    "predelay_ms",
                    "Pre-retardo",
                    "Cuánto espera la cola antes de empezar.",
                    "",
                    "float",
                    15.0,
                    0.0,
                    50.0,
                    1.0,
                    "ms",
                    apply="cut",
                ),
                Param(
                    "damping_hz",
                    "Amortiguación",
                    "Por encima de esta frecuencia la cola se apaga más rápido, como en una sala real.",
                    "",
                    "float",
                    6000.0,
                    1000.0,
                    16000.0,
                    500.0,
                    "Hz",
                    apply="cut",
                ),
                Param("seed", "Semilla", "Otro número da otras colas.", "", "int", 0, 0, 9999, 1, apply="cut"),
            ),
            cost="~0,2 ms por parlante",
        ),
    ),
    "off",
)

_ALIGN_STAGE = Stage(
    "align",
    "Alineación",
    "Atrasa a cada parlante lo justo para que todos lleguen juntos, más el retardo de los traseros.",
    "El retardo de cada parlante lo mide la calibración y lo corrige el lazo. Los cambios se "
    "arrastran despacio, sin clics; uno muy grande pasa por un corte breve.",
    (
        Algorithm(
            "sinc",
            "Retardo de banda limitada",
            "Lee el retardo entre muestras sin perder agudos.",
            "dsp/interpolation.py: la interpolación lineal quitaba hasta 3,5 dB a 12,7 kHz.",
            (
                Param(
                    "rear_delay_ms",
                    "Retardo de los traseros",
                    "Cuánto se alejan los parlantes que llevan ambiente.",
                    "Efecto de precedencia: Avendaño y Jot recomiendan 5 a 20 ms. Se aplica en "
                    "proporción al ambiente de cada parlante.",
                    "float",
                    12.0,
                    0.0,
                    50.0,
                    0.5,
                    "ms",
                    store="installation",
                ),
                Param(
                    "delay_speed_ms_s",
                    "Velocidad de las correcciones",
                    "Qué tan rápido se mueve un retardo hacia su valor nuevo.",
                    "0,5 ms/s equivale a un cambio de tono de 0,05 %: inaudible. Más rápido se "
                    "puede oír en notas largas.",
                    "float",
                    0.5,
                    0.1,
                    5.0,
                    0.1,
                    "ms/s",
                ),
            ),
            cost="~0,1 ms por parlante",
            latency_ms=interpolation.HALF * _MS,
        ),
    ),
    "sinc",
)

_EQ_STAGE = Stage(
    "eq",
    "Ecualización",
    "Levanta lo que a cada parlante le falta, según lo que midió la calibración.",
    "Solo realza, nunca recorta, y solo dentro de la banda que el parlante puede reproducir. "
    "El filtro está siempre en el camino (aunque sea plano), así prenderlo no corre a nadie "
    "en el tiempo.",
    (
        Algorithm(
            "boost_only",
            "Solo realce",
            "Corrige los huecos de la respuesta medida, hasta un máximo.",
            "dsp/eq.py: FIR de fase lineal de 2048 coeficientes, tercio de octava.",
            (
                Param(
                    "max_boost_db",
                    "Realce máximo",
                    "Lo más que se levanta una banda.",
                    "Más realce gasta margen: el limitador baja todo cuando un pasaje se pasa.",
                    "float",
                    eq.MAX_BOOST_DB,
                    0.0,
                    eq.MAX_BOOST_DB,
                    0.5,
                    "dB",
                    apply="cut",
                ),
                Param(
                    "dead_band_db",
                    "Banda muerta",
                    "Correcciones más chicas que esto se ignoran (son ruido de medición).",
                    "Se usa al aplicar la ecualización de una calibración nueva: no cambia la curva que ya suena.",
                    "float",
                    eq.DEAD_BAND_DB,
                    0.0,
                    3.0,
                    0.5,
                    "dB",
                    apply="cut",
                ),
                Param(
                    "budget_db",
                    "Presupuesto de realce",
                    "Un tope para el realce total de cada parlante (0: sin tope).",
                    "Evita que la suma de realces se coma el margen del limitador. Se mide como la "
                    "subida de energía de ruido rosa; una curva que se pasa se baja entera, sin cambiar "
                    "su forma. Actúa sobre lo que suena, sin tocar la curva guardada.",
                    "float",
                    0.0,
                    0.0,
                    12.0,
                    0.5,
                    "dB",
                    apply="cut",
                ),
                Param(
                    "treble_cap_db",
                    "Tope en agudos",
                    "Lo más que se levantan las bandas por encima de 8 kHz (6: sin tope extra).",
                    "El micrófono barato lee bajo en agudos: confiar menos ahí. Actúa sobre lo que "
                    "suena, sin tocar la curva guardada.",
                    "float",
                    eq.MAX_BOOST_DB,
                    0.0,
                    eq.MAX_BOOST_DB,
                    0.5,
                    "dB",
                    apply="cut",
                ),
            ),
            cost="~0,3 ms por parlante",
            latency_ms=eq.LATENCY_SAMPLES * _MS,
        ),
        Algorithm(
            "off",
            "Sin ecualizar",
            "Cada parlante suena como suena.",
            "El filtro sigue en el camino, plano, para que la latencia no cambie.",
            latency_ms=eq.LATENCY_SAMPLES * _MS,
        ),
    ),
    "boost_only",
)

_BASS_STAGE = Stage(
    "bass",
    "Graves",
    "Protege a los parlantes chicos de los graves que no pueden dar, o se los pasa a uno grande.",
    "El Go 4 baja los graves por su cuenta pasado el 50 % de volumen (REPORTADO). Quitarle lo "
    "que no puede reproducir le deja margen para el resto. El modo espacial «Directo» no pasa por "
    "esta etapa; el limitador lo sigue acotando.",
    (
        Algorithm("off", "Sin tocar", "Todos reciben todos los graves.", ""),
        Algorithm(
            "protect",
            "Proteger",
            "Un pasa-altos en los parlantes chicos; opcionalmente, armónicos que sugieren el grave.",
            "Linkwitz-Riley de 4.º orden. Los armónicos son bajo psicoacústico (NLD): el oído "
            "reconstruye la fundamental que falta.",
            (
                Param(
                    "cutoff_hz",
                    "Corte",
                    "Por debajo de esto el parlante chico no recibe nada.",
                    "El Go 4 reproduce desde ~90 Hz (fabricante). Con orden 4 a 90 Hz, la energía de "
                    "ruido rosa bajo 60 Hz baja ~25 dB (MEDIDO, tests/test_motor_bass.py).",
                    "float",
                    90.0,
                    60.0,
                    150.0,
                    5.0,
                    "Hz",
                    apply="cut",
                ),
                Param(
                    "order",
                    "Pendiente",
                    "Qué tan brusco es el corte: 4 (24 dB por octava) u 8 (48 dB por octava).",
                    "Linkwitz-Riley de orden 4 u 8. El 8 deja menos grave justo bajo el corte.",
                    "int",
                    4,
                    4,
                    8,
                    4,
                    apply="cut",
                ),
                Param(
                    "harmonics_db",
                    "Armónicos",
                    "Cuánto del grave se insinúa con armónicos (en el mínimo, nada).",
                    "A algunas personas no les gusta: por eso es una perilla. -24 dB es apagado; 0 dB "
                    "agrega tanta energía de armónicos como la del grave que quita el pasa-altos.",
                    "float",
                    -24.0,
                    -24.0,
                    6.0,
                    1.0,
                    "dB",
                ),
            ),
            cost="~0,1 ms por parlante; ~0,4 con armónicos",
        ),
        Algorithm(
            "crossover",
            "Cruce a un parlante grande",
            "Los graves de todos van al parlante que puede darlos.",
            "Salidas en fase, sin latencia extra. Hace falta un parlante de un tipo apto para "
            "graves (p. ej. JBL Charge 6).",
            (
                Param(
                    "cutoff_hz",
                    "Corte",
                    "Por debajo de esto los graves van al parlante grande.",
                    "",
                    "float",
                    100.0,
                    40.0,
                    200.0,
                    5.0,
                    "Hz",
                    apply="cut",
                ),
                Param(
                    "to",
                    "Parlante de graves",
                    "Cuál recibe los graves (auto: el primero apto).",
                    "",
                    "choice",
                    AUTO,
                    choices=(AUTO,),
                    apply="cut",
                    choices_from="bass_speakers",
                ),
            ),
            cost="~0,1 ms por parlante",
            requires="bass_speaker",
        ),
    ),
    "off",
)

_VOLUME_PARAMS = (
    Param(
        "volume_db",
        "Volumen",
        "El volumen de todo.",
        "Arranca bajo (-20 dB) cada vez que arranca el servicio: las pruebas son en una pieza con gente.",
        "float",
        -20.0,
        -60.0,
        0.0,
        1.0,
        "dB",
        store="session",
    ),
    Param(
        "volume_speed_db_s",
        "Velocidad del volumen",
        "Qué tan rápido sigue el volumen a la perilla.",
        "",
        "float",
        30.0,
        5.0,
        120.0,
        5.0,
        "dB/s",
    ),
    Param(
        "gain_db",
        "Ganancia",
        "El nivel de este parlante respecto de los demás.",
        "La escribe la calibración; se puede retocar a mano.",
        "float",
        0.0,
        -40.0,
        6.0,
        0.5,
        "dB",
        scope="speaker",
        store="installation",
    ),
    Param(
        "gain_speed_db_s",
        "Velocidad de la ganancia",
        "Qué tan rápido se mueve la ganancia de un parlante cuando la corrige el lazo.",
        "",
        "float",
        6.0,
        1.0,
        30.0,
        1.0,
        "dB/s",
    ),
    Param(
        "muted",
        "Silencio",
        "Calla a este parlante sin sacarlo de la sesión.",
        "No se guarda: al reiniciar todos suenan.",
        "bool",
        default=False,
        scope="speaker",
        store="session",
    ),
    Param(
        "mute_fade_ms",
        "Fundido del silencio",
        "Cuánto tarda un parlante en callarse o volver.",
        "",
        "float",
        50.0,
        10.0,
        500.0,
        10.0,
        "ms",
    ),
)

_VOLUME_STAGE = Stage(
    "volume",
    "Volumen",
    "El volumen general y el nivel de cada parlante.",
    "Hoy el volumen es digital. El códec Bluetooth recibe 16 bits sin dither: mucha atenuación "
    "digital tira resolución, por eso existe la opción de mover el volumen del parlante.",
    (
        Algorithm(
            "digital",
            "Digital",
            "El volumen se aplica en el programa, antes del Bluetooth.",
            "",
            _VOLUME_PARAMS,
        ),
        Algorithm(
            "avrcp",
            "En el parlante",
            "El volumen se pide a cada parlante por Bluetooth; la señal digital queda cerca de 0 dB.",
            "El volumen es el de PipeWire en el sink Bluetooth de cada parlante (curva cúbica: 80 % "
            "son -5,8 dB), y cada parlante conserva su diferencia con el más alto. Cada cambio se "
            "lee de vuelta: si un parlante no lo tomó, el panel lo avisa y el volumen digital no se "
            "toca. Cambiar de modo pasa por un corte breve y conserva el nivel que se oye. La curva "
            "real del Go 4 no es la de PipeWire (-6 dB pedidos dieron -4,1, experimentos/10 §5.4).",
            _VOLUME_PARAMS,
            cost="un pactl por parlante y cambio, fuera del hilo del audio",
        ),
    ),
    "digital",
)

_LIMITER_SHARED = (
    Param(
        "ceiling_db",
        "Techo",
        "Lo más alto que sale cada parlante.",
        "Un poco debajo de 0 dB: el remuestreo y el códec pueden sumar algo encima.",
        "float",
        -1.0,
        -6.0,
        0.0,
        0.5,
        "dBFS",
    ),
    Param(
        "release_ms",
        "Recuperación",
        "Qué tan rápido vuelve el nivel después de un pico.",
        "Más corto se puede oír como bombeo; más largo deja el pasaje siguiente más bajo.",
        "float",
        limiter.RELEASE_S * 1000,
        20.0,
        2000.0,
        10.0,
        "ms",
    ),
)

_LIMITER_STAGE = Stage(
    "limiter",
    "Limitador",
    "Baja el nivel en vez de recortar cuando un pasaje se pasa de escala.",
    "La ecualización solo realza: un pasaje fuerte puede pasarse, y recortar es la distorsión más dura que hay.",
    (
        Algorithm(
            "peak",
            "De pico",
            "Ataque instantáneo, sin latencia.",
            "Baja la ganancia muestra a muestra; puede modular los graves (INFERIDO).",
            _LIMITER_SHARED,
        ),
        Algorithm(
            "true_peak",
            "De pico real",
            "Mira 3 ms adelante y detecta los picos entre muestras.",
            "Detección sobremuestreada 4 veces y ataque coseno: menos distorsión en graves.",
            (
                *_LIMITER_SHARED,
                Param(
                    "lookahead_ms",
                    "Anticipación",
                    "Cuánto mira adelante (es latencia, igual en todos).",
                    "",
                    "float",
                    3.0,
                    1.0,
                    10.0,
                    0.5,
                    "ms",
                    apply="cut",
                ),
            ),
            cost="0,08 ms por parlante (MEDIDO en el Mac)",
            latency_ms=3.0,
            latency_param="lookahead_ms",
        ),
    ),
    "peak",
)

CHAIN: tuple[Stage, ...] = (
    _AMBIENCE_STAGE,
    _SPATIAL_STAGE,
    _DECORRELATE_STAGE,
    _DIFFUSE_STAGE,
    _ALIGN_STAGE,
    _EQ_STAGE,
    _BASS_STAGE,
    _VOLUME_STAGE,
    _LIMITER_STAGE,
)
"""In the real processing order."""

STAGES: dict[str, Stage] = {s.id: s for s in CHAIN}

ON_OFF_ALIASES: dict[str, str] = {"extract_ambience": "ambience", "decorrelate": "decorrelate", "eq_active": "eq"}
"""Old boolean fields of the contract that are now a stage's algorithm (`off` or not)."""
GLOBAL_ALIASES: dict[str, tuple[str, str]] = {
    "rear_delay_ms": ("align", "rear_delay_ms"),
    "volume_db": ("volume", "volume_db"),
}
SPEAKER_ALIASES: dict[str, tuple[str, str]] = {
    "pan": ("ambience", "pan"),
    "ambience": ("ambience", "ambience"),
    "gain_db": ("volume", "gain_db"),
    "muted": ("volume", "muted"),
}


def stage(stage_id: str) -> Stage:
    s = STAGES.get(stage_id)
    if s is None:
        msg = f"unknown stage {stage_id!r}; known: {list(STAGES)}"
        raise ChainError(UNKNOWN_FIELD, msg)
    return s


def default(stage_id: str, param_id: str) -> Any:
    """The default of a param, in the stage's default algorithm (or the first that has it)."""
    s = stage(stage_id)
    p = s.find_param(param_id, s.default_algorithm)
    if p is None:
        msg = f"stage {stage_id!r} has no param {param_id!r}"
        raise ChainError(UNKNOWN_FIELD, msg)
    return p.default


def on_algorithm(stage_id: str) -> str:
    """The algorithm an old `true` means: the first that is not `off`."""
    return next(a.id for a in stage(stage_id).algorithms if a.id != "off")


# -- the installation the chain runs on ---------------------------------------------------


@dataclass(frozen=True)
class ChainContext:
    """What availability and dynamic choices depend on: the installation's speakers."""

    speakers: tuple[tuple[str, str | None], ...] = ()
    """(name, kind) per speaker; kind as `dsp/profiles.py`, guessed from the name if unset."""

    @classmethod
    def of(cls, installation: Any) -> ChainContext:
        if installation is None:
            return cls()
        return cls(tuple((p.nombre, p.tipo or profiles.guess(p.nombre)) for p in installation.parlantes))

    @property
    def names(self) -> list[str]:
        return [n for n, _ in self.speakers]

    @property
    def bass_speakers(self) -> list[str]:
        return [n for n, kind in self.speakers if bass_capable(kind)]


def bass_capable(kind: str | None) -> bool:
    profile = profiles.PROFILES.get(kind or "")
    return profile is not None and profile.low_hz is not None and profile.low_hz <= BASS_CAPABLE_HZ


def unavailable_reason(algorithm: Algorithm, context: ChainContext | None) -> str | None:
    """Why an algorithm cannot be chosen in this installation, or None."""
    if context is None:
        return None
    if algorithm.requires == "bass_speaker" and not context.bass_speakers:
        kinds = [p.label for p in profiles.PROFILES.values() if bass_capable(p.key)]
        return f"hace falta un parlante apto para graves ({', '.join(kinds)}); di qué tipo es cada parlante"
    return None


def notice(algorithm: Algorithm, context: ChainContext | None, values: ChainValues | None = None) -> str | None:
    """What the listener should know about an available algorithm in this installation, or None.

    `many_speakers`: with more than `decorrelate.MAXIMO_FIJOS` speakers the bank is built anyway
    and this says how far apart its filters are (the worst pair above 500 Hz of the bank the
    engine uses, `decorrelation_bank.bank`, cached for both)."""
    if context is None or algorithm.caution != "many_speakers" or len(context.speakers) <= decorrelate.MAXIMO_FIJOS:
        return None
    values = values or ChainValues()
    stage_id = "decorrelate"
    mean, spread = values.param(stage_id, "mean_ms"), values.param(stage_id, "spread_ms")
    length = decorrelate.largo_necesario(values.param(stage_id, "length"), mean, spread, SR)
    return decorrelation_bank.notice_for(
        len(context.speakers), length, values.param(stage_id, "seed"), mean, spread, SR
    )


def choices_of(param: Param, context: ChainContext | None) -> tuple[str, ...]:
    if param.choices_from == "bass_speakers" and context is not None:
        return (AUTO, *context.bass_speakers)
    return param.choices


# -- checking a value ----------------------------------------------------------------------


def check_param(param: Param, value: Any, context: ChainContext | None = None) -> Any:
    """The value, normalised, or `ChainError` (`type` or `out_of_range`). Never clamps."""
    name = param.id
    if param.kind == "bool":
        if not isinstance(value, bool):
            msg = f"{name} must be true or false; got {json.dumps(value)}"
            raise ChainError(WRONG_TYPE, msg)
        return value
    if param.kind == "choice":
        if not isinstance(value, str):
            msg = f"{name} must be a string; got {json.dumps(value)}"
            raise ChainError(WRONG_TYPE, msg)
        allowed = choices_of(param, context)
        # Without the installation, the speaker names cannot be checked yet: the service checks
        # them again with it.
        if (context is not None or not param.choices_from) and value not in allowed:
            msg = f"{name} must be one of {list(allowed)}; got {value!r}"
            raise ChainError(OUT_OF_RANGE, msg)
        return value
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        msg = f"{name} must be a number; got {json.dumps(value)}"
        raise ChainError(WRONG_TYPE, msg)
    number = float(value)
    if not math.isfinite(number):
        msg = f"{name} must be a finite number"
        raise ChainError(WRONG_TYPE, msg)
    if param.kind == "int" and not number.is_integer():
        msg = f"{name} must be a whole number; got {value}"
        raise ChainError(WRONG_TYPE, msg)
    if (param.low is not None and number < param.low) or (param.high is not None and number > param.high):
        msg = f"{name} goes from {param.low:g} to {param.high:g}; got {number:g}"
        raise ChainError(OUT_OF_RANGE, msg)
    if param.kind == "int" and param.step and param.low is not None and (number - param.low) % param.step:
        msg = f"{name} goes in steps of {param.step:g} from {param.low:g}; got {number:g}"
        raise ChainError(OUT_OF_RANGE, msg)
    return int(number) if param.kind == "int" else number


@dataclass(frozen=True)
class ChainChange:
    """A validated `chain_set`: nothing in it can fail to apply for a reason of form."""

    stage: str
    algorithm: str | None
    params: dict[str, Any]
    speaker: str | None

    def split(self) -> tuple[dict[str, Any], dict[str, Any]]:
        """(params kept by the chain, params kept elsewhere: installation or session)."""
        s = stage(self.stage)
        mine, elsewhere = {}, {}
        for pid, value in self.params.items():
            p = s.find_param(pid, self.algorithm)
            (mine if p.store == "chain" else elsewhere)[pid] = value
        return mine, elsewhere


def validate_set(
    stage_id: Any,
    algorithm: Any = None,
    params: Any = None,
    speaker: Any = None,
    *,
    values: ChainValues | None = None,
    context: ChainContext | None = None,
) -> ChainChange:
    """Check a `chain_set` completely. With `context` (the installation) also availability,
    the speaker's name and dynamic choices; without it, only what the descriptors say."""
    if not isinstance(stage_id, str):
        msg = "stage must be a string"
        raise ChainError(WRONG_TYPE, msg)
    s = stage(stage_id)
    if algorithm is None and not params:
        msg = "chain_set needs an algorithm, params, or both"
        raise ChainError(BAD_REQUEST, msg)
    if algorithm is not None:
        if not isinstance(algorithm, str):
            msg = "algorithm must be a string"
            raise ChainError(WRONG_TYPE, msg)
        reason = unavailable_reason(s.algorithm(algorithm), context)
        if reason is not None:
            msg = f"{stage_id}.{algorithm} is not available here: {reason}"
            raise ChainError(UNAVAILABLE, msg)
    if params is not None and not isinstance(params, dict):
        msg = "params must be an object"
        raise ChainError(WRONG_TYPE, msg)
    target = algorithm or (values.algorithm(stage_id) if values is not None else s.default_algorithm)
    checked: dict[str, Any] = {}
    scopes = set()
    for pid, value in (params or {}).items():
        p = s.find_param(pid, target)
        if p is None:
            msg = f"stage {stage_id!r} has no param {pid!r}; it has {s.param_ids()}"
            raise ChainError(UNKNOWN_FIELD, msg)
        scopes.add(p.scope)
        checked[pid] = check_param(p, value, context)
    if len(scopes) > 1:
        msg = "speaker params and global params go in separate chain_set messages"
        raise ChainError(BAD_REQUEST, msg)
    speaker_scoped = scopes == {"speaker"}
    if speaker_scoped and speaker is None:
        msg = f"{sorted(checked)} are per speaker: name the speaker"
        raise ChainError(BAD_REQUEST, msg)
    if speaker is not None and not speaker_scoped:
        msg = "speaker goes only with per-speaker params"
        raise ChainError(BAD_REQUEST, msg)
    if speaker is not None:
        if not isinstance(speaker, str):
            msg = "speaker must be a string"
            raise ChainError(WRONG_TYPE, msg)
        if context is not None and speaker not in context.names:
            msg = f"no speaker named {speaker!r}; there are {context.names}"
            raise ChainError(NOT_FOUND, msg)
        if algorithm is not None:
            msg = "an algorithm is chosen for every speaker at once: send it without speaker"
            raise ChainError(BAD_REQUEST, msg)
    return ChainChange(stage_id, algorithm, checked, speaker)


def validate_reset(stage_id: Any, param: Any = None, speaker: Any = None) -> Param | None:
    """Check a `chain_reset`. Returns the param, or None for the whole stage."""
    s = stage(stage_id)
    if param is None:
        if speaker is not None:
            msg = "chain_reset with speaker needs the param"
            raise ChainError(BAD_REQUEST, msg)
        return None
    p = s.find_param(param)
    if p is None:
        msg = f"stage {stage_id!r} has no param {param!r}; it has {s.param_ids()}"
        raise ChainError(UNKNOWN_FIELD, msg)
    if (p.scope == "speaker") != (speaker is not None):
        msg = f"{param} is {'per speaker: name the speaker' if p.scope == 'speaker' else 'global: no speaker'}"
        raise ChainError(BAD_REQUEST, msg)
    return p


# -- the values ----------------------------------------------------------------------------


@dataclass
class ChainValues:
    """The listener's choices, sparse. Everything else is a default derived at read time."""

    choices: dict[str, dict] = field(default_factory=dict)

    def copy(self) -> ChainValues:
        return ChainValues(copy.deepcopy(self.choices))

    def algorithm(self, stage_id: str) -> str:
        return self.choices.get(stage_id, {}).get("algorithm") or stage(stage_id).default_algorithm

    def chosen(self, stage_id: str) -> dict:
        return copy.deepcopy(self.choices.get(stage_id, {}))

    def param(self, stage_id: str, param_id: str, speaker: str | None = None) -> Any:
        """The effective value of a chain-kept param: the choice, or the default of the
        algorithm in use (or of the first algorithm that has it)."""
        s = stage(stage_id)
        chosen = self.choices.get(stage_id, {})
        if speaker is not None:
            value = chosen.get("speakers", {}).get(speaker, {}).get(param_id)
        else:
            value = chosen.get("params", {}).get(param_id)
        if value is not None:
            return value
        p = s.find_param(param_id, self.algorithm(stage_id))
        if p is None:
            msg = f"stage {stage_id!r} has no param {param_id!r}"
            raise ChainError(UNKNOWN_FIELD, msg)
        return p.default

    def params(self, stage_id: str) -> dict[str, Any]:
        """The effective global chain-kept params of the algorithm in use."""
        algo = stage(stage_id).algorithm(self.algorithm(stage_id))
        return {p.id: self.param(stage_id, p.id) for p in algo.params if p.scope == "global" and p.store == "chain"}

    def with_change(self, change: ChainChange) -> ChainValues:
        """A copy with a validated change applied (only the chain-kept part of it)."""
        new = self.copy()
        mine, _ = change.split()
        if change.algorithm is None and not mine:
            return new
        entry = new.choices.setdefault(change.stage, {})
        if change.algorithm is not None:
            entry["algorithm"] = change.algorithm
        if mine:
            if change.speaker is None:
                entry.setdefault("params", {}).update(mine)
            else:
                entry.setdefault("speakers", {}).setdefault(change.speaker, {}).update(mine)
        return new

    def with_algorithm(self, stage_id: str, algorithm: str) -> ChainValues:
        return self.with_change(ChainChange(stage_id, algorithm, {}, None))

    def with_reset(self, stage_id: str, param: str | None = None, speaker: str | None = None) -> ChainValues:
        new = self.copy()
        entry = new.choices.get(stage_id)
        if entry is None:
            return new
        if param is None:
            del new.choices[stage_id]
            return new
        if speaker is None:
            entry.get("params", {}).pop(param, None)
        else:
            entry.get("speakers", {}).get(speaker, {}).pop(param, None)
            if not entry.get("speakers", {}).get(speaker, True):
                del entry["speakers"][speaker]
        for key in ("params", "speakers"):
            if key in entry and not entry[key]:
                del entry[key]
        if not entry:
            del new.choices[stage_id]
        return new

    def without_speaker(self, name: str) -> ChainValues:
        new = self.copy()
        for entry in new.choices.values():
            entry.get("speakers", {}).pop(name, None)
        return new

    # -- presets ----------------------------------------------------------------------

    def preset_part(self) -> dict:
        """What a preset keeps of the chain: every choice but the excluded stages'."""
        return {k: copy.deepcopy(v) for k, v in self.choices.items() if k not in PRESET_EXCLUDED_STAGES}

    def with_preset(self, part: dict) -> ChainValues:
        """A preset's chain replaces the choices of every stage a preset holds."""
        kept = {k: v for k, v in self.choices.items() if k in PRESET_EXCLUDED_STAGES}
        return ChainValues(copy.deepcopy({**kept, **{k: v for k, v in part.items() if k not in kept}}))

    # -- disk ---------------------------------------------------------------------------

    def to_json(self) -> dict:
        return copy.deepcopy(self.choices)

    @classmethod
    def from_json(cls, data: Any, log: Callable[[str], None] = lambda _: None) -> ChainValues:
        """Read stored choices. Never raises: an invalid entry is dropped and logged, so a
        file written by a newer version (or by hand) never stops the service."""
        return cls(clean_choices(data, log))


def clean_choices(data: Any, log: Callable[[str], None]) -> dict:
    if not isinstance(data, dict):
        log(f"chain: expected an object of stages, got {type(data).__name__}; ignored")
        return {}
    clean: dict[str, dict] = {}
    for stage_id, entry in data.items():
        s = STAGES.get(stage_id)
        if s is None:
            log(f"chain: unknown stage {stage_id!r} dropped")
            continue
        if not isinstance(entry, dict):
            log(f"chain: {stage_id} is not an object; dropped")
            continue
        out: dict[str, Any] = {}
        for key in set(entry) - {"algorithm", "params", "speakers"}:
            log(f"chain: {stage_id}.{key} is not a known key; dropped")
        algorithm = entry.get("algorithm")
        if algorithm is not None:
            if any(a.id == algorithm for a in s.algorithms):
                out["algorithm"] = algorithm
            else:
                log(f"chain: {stage_id} has no algorithm {algorithm!r}; dropped")
        params = _clean_params(s, entry.get("params", {}), "global", f"{stage_id}", log)
        if params:
            out["params"] = params
        speakers = entry.get("speakers", {})
        if isinstance(speakers, dict):
            cleaned = {}
            for name, values in speakers.items():
                kept = _clean_params(s, values, "speaker", f"{stage_id}.speakers.{name}", log)
                if kept:
                    cleaned[str(name)] = kept
            if cleaned:
                out["speakers"] = cleaned
        if out:
            clean[stage_id] = out
    return clean


def _clean_params(s: Stage, params: Any, scope: str, where: str, log: Callable[[str], None]) -> dict:
    if not isinstance(params, dict):
        log(f"chain: {where} params are not an object; dropped")
        return {}
    kept = {}
    for pid, value in params.items():
        p = s.find_param(pid)
        if p is None or p.scope != scope or p.store != "chain":
            log(f"chain: {where}.{pid} is not a stored {scope} param; dropped")
            continue
        try:
            kept[pid] = check_param(p, value)
        except ChainError as exc:
            log(f"chain: {where}.{pid}: {exc.message}; dropped")
    return kept


# -- what the engine runs, and what the contract shows -----------------------------------


def pending(values: ChainValues) -> dict[str, bool]:
    """Per stage: whether what is chosen is declared but not yet run by the engine."""
    out = {}
    for s in CHAIN:
        algo = s.algorithm(values.algorithm(s.id))
        waiting = not algo.implemented
        for p in algo.params:
            if not p.implemented and p.store == "chain" and values.param(s.id, p.id) != p.default:
                waiting = True
        out[s.id] = waiting
    return out


def algorithm_latency_ms(values: ChainValues, stage_id: str) -> float:
    """The latency the stage's algorithm in use adds, with its knobs (equal on every speaker)."""
    if stage_id == "decorrelate" and values.algorithm("spatial") == "direct":
        # `direct` does not go through the decorrelator: its group delay is not there.
        return 0.0
    algo = stage(stage_id).algorithm(values.algorithm(stage_id))
    if algo.latency_param:
        return float(values.param(stage_id, algo.latency_param))
    return algo.latency_ms


def latency_ms(values: ChainValues) -> float:
    return round(sum(algorithm_latency_ms(values, s.id) for s in CHAIN), 3)


def summary(values: ChainValues) -> dict[str, str]:
    return {s.id: values.algorithm(s.id) for s in CHAIN}


def param_dict(p: Param, context: ChainContext | None) -> dict:
    return {
        "id": p.id,
        "title": p.title,
        "summary": p.summary,
        "help": p.help,
        "kind": p.kind,
        "default": p.default,
        "low": p.low,
        "high": p.high,
        "step": p.step,
        "unit": p.unit,
        "choices": list(choices_of(p, context)),
        "scope": p.scope,
        "apply": p.apply,
        "store": p.store,
        "implemented": p.implemented,
    }


def describe(
    values: ChainValues,
    context: ChainContext | None = None,
    backed: Callable[[str, str, str | None], Any] | None = None,
) -> dict:
    """The `chain` reply. `backed(stage, param, speaker)` reads the knobs the chain does not
    keep (installation and session ones); without it they show their defaults."""
    waiting = pending(values)
    names = context.names if context is not None else []
    stages = []
    for s in CHAIN:
        current = values.algorithm(s.id)
        algo = s.algorithm(current)

        def read(p: Param, speaker: str | None = None, stage_id: str = s.id) -> Any:
            if p.store != "chain" and backed is not None:
                return backed(stage_id, p.id, speaker)
            return values.param(stage_id, p.id, speaker)

        speakers = {
            name: {p.id: read(p, name) for p in algo.params if p.scope == "speaker"}
            for name in names
            if any(p.scope == "speaker" for p in algo.params)
        }
        stages.append(
            {
                "id": s.id,
                "title": s.title,
                "summary": s.summary,
                "help": s.help,
                "algorithm_apply": s.algorithm_apply,
                "algorithms": [
                    {
                        "id": a.id,
                        "title": a.title,
                        "summary": a.summary,
                        "help": a.help,
                        "cost": a.cost,
                        "latency_ms": round(algorithm_latency_ms(values, s.id) if a.id == current else a.latency_ms, 3),
                        "implemented": a.implemented,
                        "available": unavailable_reason(a, context) is None,
                        "unavailable_reason": unavailable_reason(a, context),
                        "notice": notice(a, context, values),
                        "params": [param_dict(p, context) for p in a.params],
                    }
                    for a in s.algorithms
                ],
                "default_algorithm": s.default_algorithm,
                "value": {
                    "algorithm": current,
                    "params": {p.id: read(p) for p in algo.params if p.scope == "global"},
                    "speakers": speakers,
                },
                "chosen": values.chosen(s.id),
                "pending": waiting[s.id],
            }
        )
    return {"stages": stages, "latency_ms": latency_ms(values)}


def stage_value(described: dict, stage_id: str) -> dict:
    return next(s["value"] for s in described["stages"] if s["id"] == stage_id)


def changed_params(before: ChainValues, after: ChainValues) -> Iterable[tuple[str, str | None]]:
    """(stage, param) for each chain-kept knob whose effective value differs; param None is
    the algorithm."""
    for s in CHAIN:
        if before.algorithm(s.id) != after.algorithm(s.id):
            yield s.id, None
        for pid in s.param_ids():
            p = s.find_param(pid)
            if p.store != "chain" or p.scope != "global":
                continue
            if before.param(s.id, pid) != after.param(s.id, pid):
                yield s.id, pid


def apply_kind(stage_id: str, param_id: str | None, algorithm: str | None = None) -> Apply:
    s = stage(stage_id)
    if param_id is None:
        return s.algorithm_apply
    p = s.find_param(param_id, algorithm)
    return p.apply if p is not None else "cut"
