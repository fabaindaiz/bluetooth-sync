"""El modelo de la instalación: qué parlantes hay, dónde están y cómo se corrigen.

**La decisión de diseño que gobierna este módulo:** un parlante se describe por su
**posición en la pieza**, no por una etiqueta de canal (FL, FR, RL…). El motivo está en
`docs/research/09-efecto-ambiental-y-diseno-de-la-experiencia.md` §4: VBAP y ambisonics
suponen un oyente en el *sweet spot*, y el caso de uso de este proyecto es el contrario
—parlantes en los bordes de una pieza y el oyente caminando—. Con coordenadas se puede
usar **DBAP**, que no supone ni la disposición de los parlantes ni dónde está el oyente.

Las distribuciones clásicas (quad, 3/1) quedan como **presets sobre el modelo de
coordenadas**, no como el modelo en sí.

**Dos correcciones por parlante, y conviene no confundirlas:**

- `retardo_ms` y `ganancia_db` son **la corrección total que se aplica**. Las escribe un
  controlador de sincronía, que según lo que mida el drift puede escribirlas una sola vez
  (desde una calibración) o irlas actualizando. La etapa que las aplica es la misma en los
  dos casos: por eso el resultado de la medición de drift cambia un parámetro y no la
  arquitectura.
- La distancia al oyente da un retardo acústico que se puede **calcular** en vez de
  medir. `retardo_acustico_ms` lo hace, y sirve de punto de partida para la calibración.
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

VELOCIDAD_SONIDO = 343.0
"""m/s a ~20 °C. Cada 34 cm de diferencia de camino es 1 ms."""


@dataclass
class Parlante:
    """Un parlante de la instalación."""

    nombre: str
    """Cómo lo llama la persona: "Go 4 Black". Es lo que se muestra al calibrar."""
    sink: str
    """El nodo de PipeWire, p. ej. `bluez_output.90_F2_60_DA_66_6D.1`."""
    x: float | None = None
    """Metros, eje izquierda-derecha. **Opcional**: la calibración con micrófono no las
    necesita, porque mide el retardo total de cada parlante y eso ya incluye la distancia.
    Solo hacen falta para panear con DBAP, que es un paso posterior."""
    y: float | None = None
    """Metros, eje adelante-atrás. Opcional, igual que `x`."""
    retardo_ms: float = 0.0
    """Corrección de tiempo que se aplica. La escribe el controlador de sincronía."""
    ganancia_db: float = 0.0
    """Corrección de nivel que se aplica."""

    # -- lo único que tiene sentido ajustar a mano ---------------------------------
    # El resto de los campos los escribe la calibración. Estos dos son la decisión
    # artística: qué le toca reproducir a cada parlante.

    pan: float = 0.0
    """De -1 (todo el canal izquierdo) a +1 (todo el derecho). 0 es la mezcla de los dos."""
    ambiente: float = 0.0
    """Cuánto del material **de ambiente** recibe este parlante, de 0 a 1.

    En 0 reproduce la señal directa; en 1, solo el ambiente extraído. Los valores
    intermedios son lo habitual en una instalación en los bordes de una pieza, donde la
    división frontal/trasero es menos nítida que en un cine.

    El ambiente es lo que produce el envolvimiento
    (`docs/research/09-efecto-ambiental-y-diseno-de-la-experiencia.md` §1), así que subirlo
    es la perilla principal del efecto."""
    ecualizacion_db: list[float] | None = None
    """La corrección por tercio de octava (50 Hz a 20 kHz, la grilla de `dsp/response.py`),
    calculada de la respuesta que midió una calibración (`dsp/eq.py`). `None`: sin ecualizar."""
    tipo: str | None = None
    """Qué parlante es (`dsp/profiles.py`: "go4", "charge6", "generic"), lo dice la persona.
    La ecualización confía en la banda del fabricante antes que en la del micrófono.
    `None`: se deduce del nombre si lo dice ("JBL Go 4 …"), y si no, genérico."""

    @property
    def ubicado(self) -> bool:
        """Si tiene coordenadas. Sin ellas se puede calibrar igual, pero no panear."""
        return self.x is not None and self.y is not None

    def distancia_a(self, x: float, y: float) -> float:
        """Metros hasta un punto de la pieza. Requiere coordenadas."""
        if not self.ubicado:
            msg = f"{self.nombre!r} no tiene coordenadas; la distancia no se puede calcular"
            raise ValueError(msg)
        return math.hypot(self.x - x, self.y - y)

    def retardo_acustico_ms(self, x: float = 0.0, y: float = 0.0) -> float:
        """Cuánto tarda el sonido en llegar desde este parlante hasta un punto.

        Se calcula en vez de medirse. Restándolo de lo que mide el micrófono queda el
        desfase **electrónico**, que es el que hay que corregir de verdad.
        """
        return self.distancia_a(x, y) / VELOCIDAD_SONIDO * 1000.0


@dataclass
class Instalacion:
    """Todos los parlantes, más dónde se escucha."""

    parlantes: list[Parlante] = field(default_factory=list)
    oyente_x: float = 0.0
    """Punto de referencia para los cálculos acústicos. Con el oyente moviéndose no hay un
    punto único: se usa el centro de la pieza, o donde se puso el micrófono al calibrar."""
    oyente_y: float = 0.0
    retardo_traseros_ms: float = 12.0
    """Retardo extra para los canales de ambiente. Avendaño y Jot recomiendan **5 a 20 ms**
    para evitar la deslocalización por el efecto de precedencia y simular salas de
    distinto tamaño (`docs/research/09` §11.2). 12 ms es el medio del rango."""

    def __post_init__(self) -> None:
        nombres = [p.nombre for p in self.parlantes]
        if len(set(nombres)) != len(nombres):
            msg = f"hay nombres de parlante repetidos: {nombres}"
            raise ValueError(msg)
        sinks = [p.sink for p in self.parlantes]
        if len(set(sinks)) != len(sinks):
            msg = f"hay sinks repetidos: {sinks}"
            raise ValueError(msg)

    def por_nombre(self, nombre: str) -> Parlante:
        for p in self.parlantes:
            if p.nombre == nombre:
                return p
        msg = f"no hay un parlante llamado {nombre!r}; hay {[p.nombre for p in self.parlantes]}"
        raise KeyError(msg)

    @property
    def todos_ubicados(self) -> bool:
        return all(p.ubicado for p in self.parlantes)

    def retardos_acusticos_ms(self) -> dict[str, float]:
        """El retardo acústico de cada parlante hasta el punto de escucha.

        Solo para instalaciones con coordenadas. **La calibración con micrófono no pasa por
        acá**: mide el retardo total, que ya incluye el vuelo por el aire.
        """
        if not self.todos_ubicados:
            sin = [p.nombre for p in self.parlantes if not p.ubicado]
            msg = f"faltan coordenadas en {sin}; para alinear sin ellas, calibrá con micrófono"
            raise ValueError(msg)
        return {p.nombre: p.retardo_acustico_ms(self.oyente_x, self.oyente_y) for p in self.parlantes}

    def alinear_por_geometria(self) -> None:
        """Iguala los tiempos de llegada usando solo las coordenadas.

        Al parlante más lejano no se le agrega nada; a los demás se les suma la diferencia,
        para que el sonido salga de todos al mismo tiempo **en el punto de escucha**. Es un
        punto de partida razonable antes de medir con micrófono, y en una instalación donde
        el oyente se mueve puede alcanzar.
        """
        acusticos = self.retardos_acusticos_ms()
        if not acusticos:
            return
        mayor = max(acusticos.values())
        for p in self.parlantes:
            p.retardo_ms = mayor - acusticos[p.nombre]

    # -- persistencia -------------------------------------------------------------
    # JSON y no TOML porque la herramienta **escribe** este archivo: la calibración
    # actualiza retardos y ganancias. `tomllib` de la biblioteca estándar solo lee, y
    # escribir TOML pediría otra dependencia para poco beneficio.

    def guardar(self, ruta: Path) -> None:
        ruta.parent.mkdir(parents=True, exist_ok=True)
        ruta.write_text(json.dumps(asdict(self), indent=2, ensure_ascii=False) + "\n")

    @classmethod
    def cargar(cls, ruta: Path) -> Instalacion:
        datos = json.loads(ruta.read_text())
        parlantes = [Parlante(**p) for p in datos.pop("parlantes", [])]
        return cls(parlantes=parlantes, **datos)


def ruta_por_defecto() -> Path:
    """`~/.config/aurasync/instalacion.json`, respetando XDG_CONFIG_HOME."""
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "aurasync" / "instalacion.json"
