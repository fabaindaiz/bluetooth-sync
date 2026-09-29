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
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

from aurasync.dsp import ambience, decorrelate

if TYPE_CHECKING:
    from aurasync.config import Instalacion

SR = 48000


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
    ) -> None:
        if not instalacion.parlantes:
            msg = "la instalación no tiene parlantes"
            raise ValueError(msg)
        self.instalacion = instalacion
        self.sr = sr
        self.extraer_ambiente = extraer_ambiente
        self.decorrelar = decorrelar

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
        self.reiniciar()

    def reiniciar(self) -> None:
        """Vacía el estado. Hay que llamarlo al empezar una reproducción nueva."""
        nombres = [p.nombre for p in self.instalacion.parlantes]
        # Cola de la convolución: la parte del bloque anterior que todavía no salió.
        self._cola_filtro = {n: np.zeros(len(h) - 1 if h is not None else 0) for n, h in self._filtros.items()}
        # Línea de retardo por parlante, dimensionada al retardo máximo posible.
        self._linea = {n: np.zeros(self._muestras_de_retardo(n)) for n in nombres}
        if self._extractor is not None:
            self._extractor.reiniciar()
        # Compensación de la latencia del extractor sobre el camino directo.
        self._cola_directo_izq = np.zeros(self.latencia)
        self._cola_directo_der = np.zeros(self.latencia)

    def _muestras_de_retardo(self, nombre: str) -> int:
        p = self.instalacion.por_nombre(nombre)
        total = p.retardo_ms + p.ambiente * self.instalacion.retardo_traseros_ms
        return max(0, round(self.sr * total / 1000))

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
        else:
            amb = np.zeros(n)
            izq_d, der_d = izq, der

        salida = {}
        for p in self.instalacion.parlantes:
            # Directo: mezcla L/R según el pan. Con pan 0 los dos por igual.
            peso_izq, peso_der = (1 - p.pan) / 2, (1 + p.pan) / 2
            directo = peso_izq * izq_d + peso_der * der_d
            x = (1 - p.ambiente) * directo + p.ambiente * amb
            x = self._convolucionar(p.nombre, x)
            x = self._retrasar(p.nombre, x)
            salida[p.nombre] = x * (10 ** (p.ganancia_db / 20))
        return salida

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

    def _retrasar(self, nombre: str, x: np.ndarray) -> np.ndarray:
        """Línea de retardo por parlante, exacta a nivel de muestra."""
        linea = self._linea[nombre]
        if len(linea) == 0:
            return x
        juntos = np.concatenate([linea, x])
        self._linea[nombre] = juntos[len(x) :] if len(x) < len(juntos) else juntos[-len(linea) :]
        return juntos[: len(x)]


def procesar_completo(motor: Motor, izq: np.ndarray, der: np.ndarray, bloque: int = 4096) -> dict[str, np.ndarray]:
    """Procesa una señal entera en bloques. Existe sobre todo para los tests."""
    partes: dict[str, list[np.ndarray]] = {p.nombre: [] for p in motor.instalacion.parlantes}
    for i in range(0, len(izq), bloque):
        salida = motor.procesar(izq[i : i + bloque], der[i : i + bloque])
        for nombre, trozo in salida.items():
            partes[nombre].append(trozo)
    return {n: np.concatenate(v) if v else np.zeros(0) for n, v in partes.items()}
