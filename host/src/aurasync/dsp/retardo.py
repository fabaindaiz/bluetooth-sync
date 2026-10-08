"""Retardo por parlante que se puede cambiar mientras suena, sin que se oiga el cambio.

Es la pieza que hace posible la recalibración continua. Sin ella, corregir el retardo de un
parlante exigiría parar y volver a empezar — y parar es exactamente lo que hay que evitar,
porque reiniciar los streams es lo único que sí está medido que puede cambiar la alineación
(`docs/research/experimentos/05-e6-a2dp-un-canal-por-parlante.md`). Si hace falta corregir
algo mientras suena —deriva, o un parlante que se resincronizó— tiene que poder hacerse sin
cortar. Cuánta corrección hace falta de verdad es la pregunta que abre `sincronia.py`.

**Dos cosas que un retardo fijo no puede hacer, y esta línea sí:**

1. **Cambiar de valor gradualmente.** Mover un retardo de golpe corta la forma de onda y se
   oye como un clic. Acá el retardo se mueve hacia su objetivo a una velocidad limitada.
2. **Retardar fracciones de muestra.** A 48 kHz una muestra son 0,0208 ms, y la calibración
   mide con precisión mejor que eso. Redondear a muestras enteras tiraría esa precisión, y
   además haría que cada corrección fuera un salto en vez de un movimiento.

**Por qué la velocidad por defecto no se oye.** Mover el retardo a `v` ms por segundo
equivale a reproducir a una velocidad `1 - v/1000`, o sea un cambio de tono de `v` partes
por mil. Con el valor por defecto de 0,5 ms/s eso es 0,05 %, unos 0,9 centésimos de
semitono: bastante por debajo de lo que se percibe en material musical. Y alcanza de sobra
para lo que hay que corregir: con una deriva de cientos de ms por hora, hacen falta décimas
de ms por segundo.
"""

from __future__ import annotations

import numpy as np

from aurasync.dsp import interpolation
from aurasync.dsp.transition import fade_weights

SR = 48000
VELOCIDAD_POR_DEFECTO_MS_S = 0.5
"""Cuánto puede moverse el retardo por segundo, en ms. Ver el módulo para por qué no se oye."""


class LineaDeRetardo:
    """Un retardo variable, con lectura fraccionaria y cambio limitado en velocidad."""

    def __init__(
        self,
        sr: int = SR,
        retardo_ms: float = 0.0,
        maximo_ms: float = 250.0,
        velocidad_ms_s: float = VELOCIDAD_POR_DEFECTO_MS_S,
        *,
        sinc: bool = False,
    ) -> None:
        """`sinc`: lectura fraccionaria de banda limitada (`dsp/interpolation.py`), que no le
        quita agudos al sonido, a cambio de `interpolation.HALF` muestras de latencia fija.
        La lineal (por defecto) deja exacto el retardo pedido, sin latencia extra: es la que
        usan la sesión para sus referencias y los tests de exactitud."""
        if retardo_ms < 0 or retardo_ms > maximo_ms:
            msg = f"el retardo inicial {retardo_ms} ms está fuera de [0, {maximo_ms}]"
            raise ValueError(msg)
        self.sr = sr
        self.maximo_ms = maximo_ms
        self.velocidad_ms_s = velocidad_ms_s
        self.sinc = sinc
        self.latencia_fija = interpolation.HALF if sinc else 0
        self._maximo_muestras = int(np.ceil(sr * maximo_ms / 1000)) + 2 + 2 * self.latencia_fija
        self._actual = sr * retardo_ms / 1000
        self._objetivo = self._actual
        self._historia = np.zeros(self._maximo_muestras)
        self._fusion: tuple[float, float, int, str] | None = None  # (viejo, nuevo, largo, forma)
        self._fusion_pos = 0

    @property
    def fundiendo(self) -> bool:
        """Si hay un fundido entre dos posiciones de lectura en curso."""
        return self._fusion is not None

    def fundir_a(self, retardo_ms: float, muestras: int, forma: str) -> None:
        """Pasa al retardo nuevo fundiendo dos lecturas fijas, la vieja y la nueva, en `muestras`
        muestras desde el próximo bloque (pesos de `fade_weights`). Sin rampa de velocidad:
        ninguna lectura se mueve, así que el tono no se dobla. Al final queda solo la nueva.

        Una llamada en pleno fundido reinicia desde la posición vieja (la regla de colapso del
        reloj lo evita); las escrituras a `objetivo_ms` se ignoran mientras se funde."""
        fade_weights(forma, 0, 1, 1)  # valida la forma
        nuevo = self.sr * float(np.clip(retardo_ms, 0.0, self.maximo_ms)) / 1000
        if muestras <= 0 or nuevo == self._actual:
            self.saltar_a(retardo_ms)
            return
        self._fusion = (self._actual, nuevo, muestras, forma)
        self._fusion_pos = 0
        self._objetivo = nuevo

    @property
    def actual_ms(self) -> float:
        """Dónde está el retardo ahora. Puede no ser el objetivo si todavía se está moviendo."""
        return self._actual / self.sr * 1000

    @property
    def objetivo_ms(self) -> float:
        return self._objetivo / self.sr * 1000

    @objetivo_ms.setter
    def objetivo_ms(self, valor: float) -> None:
        """Fija el destino. El retardo empieza a moverse hacia él en el próximo bloque."""
        self._objetivo = self.sr * float(np.clip(valor, 0.0, self.maximo_ms)) / 1000

    def saltar_a(self, retardo_ms: float) -> None:
        """Mueve el retardo de golpe, sin rampa.

        **Solo para antes de empezar a reproducir.** En medio de un flujo esto es
        exactamente el clic que la rampa existe para evitar.
        """
        self._objetivo = self.sr * float(np.clip(retardo_ms, 0.0, self.maximo_ms)) / 1000
        self._actual = self._objetivo
        self._fusion = None

    @property
    def en_objetivo(self) -> bool:
        return abs(self._actual - self._objetivo) < 1e-9  # noqa: PLR2004

    def reiniciar(self) -> None:
        self._historia = np.zeros(self._maximo_muestras)
        self._fusion = None

    def procesar(self, x: np.ndarray) -> np.ndarray:
        """Retarda el bloque, moviendo el retardo hacia su objetivo si hace falta."""
        n = len(x)
        if n == 0:
            return np.zeros(0)

        if self._fusion is not None:
            return self._procesar_fundiendo(x)

        # Trayectoria del retardo dentro de este bloque: una rampa que se detiene al llegar.
        por_muestra = self.velocidad_ms_s / 1000  # muestras de retardo por muestra de audio
        restante = self._objetivo - self._actual
        if abs(restante) < 1e-12:  # noqa: PLR2004
            d = np.full(n, self._actual)
        else:
            paso = np.sign(restante) * por_muestra
            d = self._actual + paso * np.arange(1, n + 1)
            lo, hi = min(self._actual, self._objetivo), max(self._actual, self._objetivo)
            d = np.clip(d, lo, hi)
        self._actual = float(d[-1])

        datos = np.concatenate([self._historia, x])
        salida = self._leer(datos, n, d)
        self._historia = datos[-len(self._historia) :]
        return salida

    def _leer(self, datos: np.ndarray, n: int, d: np.ndarray) -> np.ndarray:
        """Lee `n` muestras de `datos` (historia + bloque) con el retardo `d` de cada una."""
        base = len(self._historia)
        posicion = base + np.arange(n) - d - self.latencia_fija
        if self.sinc:
            # La historia alcanza para la mitad del núcleo antes; después hay `latencia_fija`.
            posicion = np.clip(posicion, interpolation.HALF - 1, len(datos) - 1 - interpolation.HALF)
            return interpolation.read(datos, posicion)
        # La historia tiene el largo del retardo máximo más dos, así que la posición nunca
        # cae antes del principio; el `clip` es solo una red por si alguien cambia el máximo.
        posicion = np.clip(posicion, 0, len(datos) - 1)
        i0 = np.floor(posicion).astype(int)
        frac = posicion - i0
        i1 = np.minimum(i0 + 1, len(datos) - 1)
        return datos[i0] * (1 - frac) + datos[i1] * frac

    def _procesar_fundiendo(self, x: np.ndarray) -> np.ndarray:
        """Un bloque durante un fundido: dos lecturas a retardo fijo, mezcladas por los pesos."""
        viejo, nuevo, largo, forma = self._fusion
        n = len(x)
        datos = np.concatenate([self._historia, x])
        w_viejo, w_nuevo = fade_weights(forma, self._fusion_pos, n, largo)
        salida = w_viejo * self._leer(datos, n, np.full(n, viejo))
        salida += w_nuevo * self._leer(datos, n, np.full(n, nuevo))
        self._historia = datos[-len(self._historia) :]
        self._fusion_pos += n
        if self._fusion_pos >= largo:
            self._actual = self._objetivo = nuevo
            self._fusion = None
        return salida
