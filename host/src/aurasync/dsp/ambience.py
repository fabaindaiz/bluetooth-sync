"""Extracción de ambiente de una señal estéreo, por coherencia entre canales.

Es de dónde salen los "canales ligeramente distintos" que alimentan a los parlantes
traseros. La alternativa barata —mandar L-R a los traseros— tiene dos problemas: solo
extrae el ambiente de las fuentes paneadas al centro, y deja **los dos traseros
correlacionados entre sí**, que es lo contrario de lo que produce envolvimiento.

**Implementa Avendaño y Jot**, *Frequency Domain Techniques for Stereo to Multichannel
Upmix* (AES 22nd International Conference, 2002), ecuaciones (11) y (12). El detalle está
en `docs/research/09-efecto-ambiental-y-diseno-de-la-experiencia.md` §11.2.

El algoritmo:

1. STFT de los dos canales.
2. Correlaciones cruzadas de corto plazo, suavizadas con un **factor de olvido** `lam`,
   para seguir la no-estacionariedad de la música y que el sistema sea causal.
3. **Coherencia** `phi = |P12| / sqrt(P11 * P22)`, en [0, 1]: cerca de 1 es componente
   directo, cerca de 0 es ambiente.
4. **Índice de ambiente** `A = 1 - phi`.
5. **Criterio adicional del paper**, y sin él el resultado es malo: los dos canales tienen
   que tener **energías comparables**. Una fuente paneada del todo a un lado también da
   coherencia baja, y sin este filtro se confundiría con ambiente.
6. Se pesan las transformadas con una **tangente hiperbólica** del índice y se vuelve al
   tiempo por overlap-add.

La cadena completa del surround, según la figura 9 del paper, es
**ambiente → todo-paso decorrelador → retardo de 5 a 20 ms**. Este módulo hace el primer
paso; los otros dos están en `decorrelate` y en el retardo por parlante.

**Engine** (spec rust-engine §5, `dsp/backend.py`): with `engine=rust` the streaming `Extractor`
hands its work to `aurasync_engine.AmbienceExtractor` (engine/crates/aurasync-dsp/src/ambience.rs),
within 1e-9 of this code, which stays the oracle (tests/test_ambience_rust.py). The extractor owns
the Rust object and moves its whole state into it, or back, when the engine switches at a cut's
bottom (`on_engine_switch`): the move is exact, so the sound goes on as if nothing had switched.
After a Rust failure the extractor gives silence until the cut's bottom, then starts afresh in
numpy (the Rust state may be torn), as `reiniciar` leaves it. `extraer` and `indice_ambiente`
(whole signals, for the tests and the probes) stay numpy only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from aurasync.dsp import backend

N_FFT = 2048
"""~43 ms a 48 kHz: suficiente resolución en frecuencia para separar fuentes, y corto
como para seguir los transitorios."""

SALTO = N_FFT // 4
"""75 % de solape. Con ventana raíz de Hann en análisis y síntesis, la suma de los
cuadrados de las ventanas es constante y la reconstrucción es exacta."""

_PISO_NORMA = 1e-8
"""Por debajo de esto, la suma de ventanas no alcanza para dividir sin amplificar ruido
numérico. Pasa solo en los bordes de la señal."""


@dataclass(frozen=True)
class Parametros:
    """Los parámetros de la ecuación (12) del paper.

    `mu1` es el techo de la salida y va en 1, porque —dice el paper— no se quiere
    *realzar* las regiones no coherentes, solo dejarlas pasar. `mu0` es el piso: cuánto se
    atenúa lo que se considera directo, y es la perilla principal. `sigma` es la pendiente
    (la figura 2 del paper usa 2 y 8), y `umbral` es dónde está el codo de la curva.
    """

    lam: float = 0.9
    """Factor de olvido del suavizado. Más cerca de 1, más memoria y más estabilidad, pero
    más lento para seguir un cambio."""
    umbral: float = 0.5
    """El `Phi_0` del paper."""
    mu0: float = 0.0
    """Piso de la salida. En 0, lo perfectamente coherente se elimina."""
    mu1: float = 1.0
    """Techo de la salida."""
    sigma: float = 2.0
    """Pendiente."""
    energia_minima: float = 0.25
    """Razón mínima entre la energía del canal flojo y la del fuerte para aceptar una
    región como ambiente. Es el criterio adicional del paper, que evita confundir una
    fuente paneada a un lado con ambiente."""


def mapeo(indice: np.ndarray, p: Parametros) -> np.ndarray:
    """La función `Gamma` de la ecuación (12).

    Suave a propósito: un umbral duro sobre el índice mete artefactos audibles.
    """
    return ((p.mu1 - p.mu0) / 2) * np.tanh(p.sigma * np.pi * (indice - p.umbral)) + ((p.mu1 + p.mu0) / 2)


def _ventana(n: int) -> np.ndarray:
    # Raíz de Hann: se aplica en análisis y en síntesis, y el producto da Hann.
    return np.sqrt(np.hanning(n + 1)[:n])


def _stft(x: np.ndarray, n_fft: int, salto: int) -> np.ndarray:
    w = _ventana(n_fft)
    n_tramas = 1 + max(0, (len(x) - n_fft) // salto)
    return np.stack([np.fft.rfft(x[i * salto : i * salto + n_fft] * w) for i in range(n_tramas)])


def _istft(espectro: np.ndarray, n_fft: int, salto: int, largo: int) -> np.ndarray:
    w = _ventana(n_fft)
    y = np.zeros(largo + n_fft)
    norma = np.zeros(largo + n_fft)
    for i, trama in enumerate(espectro):
        ini = i * salto
        y[ini : ini + n_fft] += np.fft.irfft(trama, n=n_fft) * w
        norma[ini : ini + n_fft] += w**2
    # Donde la suma de ventanas es casi cero (los bordes) no se divide, para no amplificar
    # ruido numérico.
    util = norma > _PISO_NORMA
    y[util] /= norma[util]
    return y[:largo]


def indice_ambiente(
    izq: np.ndarray, der: np.ndarray, p: Parametros | None = None, n_fft: int = N_FFT, salto: int = SALTO
) -> np.ndarray:
    """El índice de ambiente por trama y frecuencia, en [0, 1]. 1 es ambiente puro."""
    p = p or Parametros()
    espectro_izq, espectro_der = _stft(izq, n_fft, salto), _stft(der, n_fft, salto)

    # Suavizado exponencial de las correlaciones: es el factor de olvido de la ecuación (3).
    p12 = np.zeros_like(espectro_izq)
    p11 = np.zeros(espectro_izq.shape)
    p22 = np.zeros(espectro_izq.shape)
    acc12 = np.zeros(espectro_izq.shape[1], dtype=complex)
    acc11 = np.zeros(espectro_izq.shape[1])
    acc22 = np.zeros(espectro_izq.shape[1])
    for i in range(espectro_izq.shape[0]):
        acc12 = p.lam * acc12 + (1 - p.lam) * espectro_izq[i] * np.conj(espectro_der[i])
        acc11 = p.lam * acc11 + (1 - p.lam) * np.abs(espectro_izq[i]) ** 2
        acc22 = p.lam * acc22 + (1 - p.lam) * np.abs(espectro_der[i]) ** 2
        p12[i], p11[i], p22[i] = acc12, acc11, acc22

    coherencia = np.abs(p12) / np.sqrt(p11 * p22 + 1e-20)
    indice = np.clip(1.0 - coherencia, 0.0, 1.0)

    # El criterio adicional: sin energía comparable en los dos canales, no es ambiente
    # sino una fuente paneada a un lado.
    flojo = np.minimum(p11, p22)
    fuerte = np.maximum(p11, p22) + 1e-20
    indice[flojo / fuerte < p.energia_minima] = 0.0
    return indice


def extraer(
    izq: np.ndarray,
    der: np.ndarray,
    p: Parametros | None = None,
    n_fft: int = N_FFT,
    salto: int = SALTO,
) -> tuple[np.ndarray, np.ndarray]:
    """Devuelve el par de señales de ambiente, del mismo largo que la entrada.

    Es la ecuación (11): cada canal se pesa con la función del índice de ambiente y se
    vuelve al tiempo.
    """
    if len(izq) != len(der):
        msg = f"los canales tienen largos distintos: {len(izq)} y {len(der)}"
        raise ValueError(msg)
    p = p or Parametros()
    indice = indice_ambiente(izq, der, p, n_fft, salto)
    ganancia = mapeo(indice, p)
    amb_izq = _istft(_stft(izq, n_fft, salto) * ganancia, n_fft, salto, len(izq))
    amb_der = _istft(_stft(der, n_fft, salto) * ganancia, n_fft, salto, len(der))
    return amb_izq, amb_der


class Extractor:
    """Extracción de ambiente sobre un flujo, con estado entre bloques.

    `extraer` procesa una señal entera; esta clase hace lo mismo sobre un flujo que llega
    de a pedazos, que es lo que necesita el motor de reproducción.

    **No alcanza con llamar a `extraer` por bloque.** Una STFT necesita tramas completas y
    una suma de ventanas que se cierre; partirla en bloques chicos deja los bordes sin
    reconstruir. Medido: con bloques de 1024 muestras, la salida difería **642 %** de la
    correcta, lo que en audio significa un clic por bloque.

    El precio es una **latencia de `n_fft` muestras** (43 ms a 48 kHz), inevitable: hasta no
    tener la trama entera no se puede transformar. Quien la use tiene que retrasar lo mismo
    los caminos paralelos, o el ambiente llegaría corrido respecto del directo.
    """

    def __init__(self, p: Parametros | None = None, n_fft: int = N_FFT, salto: int = SALTO) -> None:
        self._rust: Any = None
        """The Rust extractor while the engine is Rust; the numpy state below is then stale until
        the switch back moves Rust's state into it."""
        self._rust_broken = False
        """A Rust call failed: its state may be torn, so the next switch restarts in numpy."""
        self._p = p or Parametros()
        self.n_fft = n_fft
        self.salto = salto
        self.latencia = n_fft
        self.reiniciar()
        backend.register(self)
        self._follow(rust=backend.rust_active())

    @property
    def p(self) -> Parametros:
        """The params; the motor replaces them at a cut's bottom, and they apply from the next
        frame, in either engine."""
        return self._p

    @p.setter
    def p(self, p: Parametros) -> None:
        self._p = p
        if self._rust is not None:
            self._rust_call(lambda rust: rust.set_params(*self._rust_params()))

    def reiniciar(self) -> None:
        self._reiniciar_numpy()
        if self._rust is not None:
            self._rust_call(lambda rust: rust.reset())

    def _reiniciar_numpy(self) -> None:
        self._pendiente_izq = np.zeros(0)
        self._pendiente_der = np.zeros(0)
        self._ola = np.zeros(self.n_fft)
        self._norma = np.zeros(self.n_fft)
        self._listo = np.zeros(self.latencia)
        # El suavizado de las correlaciones también es estado: reiniciarlo en cada bloque
        # haría que el índice de ambiente arrancara de cero una y otra vez.
        self._acc12 = np.zeros(self.n_fft // 2 + 1, dtype=complex)
        self._acc11 = np.zeros(self.n_fft // 2 + 1)
        self._acc22 = np.zeros(self.n_fft // 2 + 1)

    # -- the engine (dsp/backend.py) ---------------------------------------------------------

    def on_engine_switch(self, _active: str) -> None:
        """`backend.use` at a cut's bottom: move the state to the engine that runs now."""
        self._follow(rust=backend.rust_active())

    def _follow(self, *, rust: bool) -> None:
        """Run on Rust (`rust`) or numpy from now on, moving the state across; after a failure,
        restart in numpy first."""
        if self._rust_broken:
            self._rust, self._rust_broken = None, False
            self._reiniciar_numpy()
        if rust and self._rust is None:
            self._rust = backend.guarded(self._build_rust, lambda: None)
        elif not rust and self._rust is not None:
            state = backend.guarded(self._rust.state, lambda: None)
            self._rust = None
            if state is None:
                self._reiniciar_numpy()
            else:
                self._load_state(state)

    def _build_rust(self) -> Any:
        rust = backend.module().AmbienceExtractor(self.n_fft, self.salto)
        rust.set_params(*self._rust_params())
        rust.set_state(self._numpy_state())
        return rust

    def _rust_call(self, call: Any) -> Any:
        rust = self._rust
        return backend.guarded(lambda: call(rust), self._broke)

    def _broke(self) -> None:
        self._rust_broken = True

    def _rust_params(self) -> tuple[float, ...]:
        p = self._p
        return (p.lam, p.umbral, p.mu0, p.mu1, p.sigma, p.energia_minima)

    def _numpy_state(self) -> dict[str, np.ndarray]:
        """The numpy extractor's state in the Rust extractor's terms."""

        def vector(x: np.ndarray) -> np.ndarray:
            return np.ascontiguousarray(x, dtype=np.float64)

        return {
            "acc12_re": vector(self._acc12.real),
            "acc12_im": vector(self._acc12.imag),
            "acc11": vector(self._acc11),
            "acc22": vector(self._acc22),
            "pending_left": vector(self._pendiente_izq),
            "pending_right": vector(self._pendiente_der),
            "ola": vector(self._ola),
            "norm": vector(self._norma),
            "ready": vector(self._listo),
        }

    def _load_state(self, state: dict[str, Any]) -> None:
        """The Rust extractor's state (`state()`) as this extractor's own."""
        self._acc12 = np.empty(len(state["acc12_re"]), dtype=complex)
        self._acc12.real = state["acc12_re"]
        self._acc12.imag = state["acc12_im"]
        self._acc11 = np.array(state["acc11"])
        self._acc22 = np.array(state["acc22"])
        self._pendiente_izq = np.array(state["pending_left"])
        self._pendiente_der = np.array(state["pending_right"])
        self._ola = np.array(state["ola"])
        self._norma = np.array(state["norm"])
        self._listo = np.array(state["ready"])

    # -- el bloque ---------------------------------------------------------------------------

    def procesar(self, izq: np.ndarray, der: np.ndarray) -> np.ndarray:
        """Devuelve el ambiente en mono, tantas muestras como entraron."""
        if len(izq) != len(der):
            msg = f"los canales tienen largos distintos: {len(izq)} y {len(der)}"
            raise ValueError(msg)
        if backend.silent() is None:
            self._follow(rust=backend.rust_active())
        if backend.silent() is not None:
            # A Rust failure, until the cut's bottom: silence.
            return np.zeros(len(izq))
        if self._rust is not None:
            return self._procesar_rust(izq, der)
        return self._procesar_numpy(izq, der)

    def _procesar_rust(self, izq: np.ndarray, der: np.ndarray) -> np.ndarray:
        n = len(izq)
        # Rust takes float64 C-contiguous only: converted here (no copy when it already is).
        izq = np.ascontiguousarray(izq, dtype=np.float64)
        der = np.ascontiguousarray(der, dtype=np.float64)

        def silence() -> np.ndarray:
            self._broke()
            return np.zeros(n)

        rust = self._rust
        return backend.guarded(lambda: rust.process(izq, der), silence)

    def _procesar_numpy(self, izq: np.ndarray, der: np.ndarray) -> np.ndarray:
        self._pendiente_izq = np.concatenate([self._pendiente_izq, izq])
        self._pendiente_der = np.concatenate([self._pendiente_der, der])

        w = _ventana(self.n_fft)
        while len(self._pendiente_izq) >= self.n_fft:
            trama_izq = np.fft.rfft(self._pendiente_izq[: self.n_fft] * w)
            trama_der = np.fft.rfft(self._pendiente_der[: self.n_fft] * w)

            lam = self.p.lam
            self._acc12 = lam * self._acc12 + (1 - lam) * trama_izq * np.conj(trama_der)
            self._acc11 = lam * self._acc11 + (1 - lam) * np.abs(trama_izq) ** 2
            self._acc22 = lam * self._acc22 + (1 - lam) * np.abs(trama_der) ** 2

            coherencia = np.abs(self._acc12) / np.sqrt(self._acc11 * self._acc22 + 1e-20)
            indice = np.clip(1.0 - coherencia, 0.0, 1.0)
            flojo, fuerte = np.minimum(self._acc11, self._acc22), np.maximum(self._acc11, self._acc22) + 1e-20
            indice[flojo / fuerte < self.p.energia_minima] = 0.0
            ganancia = mapeo(indice, self.p)

            mono = (trama_izq + trama_der) / 2
            self._ola += np.fft.irfft(mono * ganancia, n=self.n_fft) * w
            self._norma += w**2

            listas = self._ola[: self.salto].copy()
            normas = self._norma[: self.salto]
            util = normas > _PISO_NORMA
            listas[util] /= normas[util]
            self._listo = np.concatenate([self._listo, listas])

            self._ola = np.concatenate([self._ola[self.salto :], np.zeros(self.salto)])
            self._norma = np.concatenate([self._norma[self.salto :], np.zeros(self.salto)])
            self._pendiente_izq = self._pendiente_izq[self.salto :]
            self._pendiente_der = self._pendiente_der[self.salto :]

        n = len(izq)
        if len(self._listo) < n:
            self._listo = np.concatenate([self._listo, np.zeros(n - len(self._listo))])
        salida, self._listo = self._listo[:n], self._listo[n:]
        return salida


def reconstruir(x: np.ndarray, n_fft: int = N_FFT, salto: int = SALTO) -> np.ndarray:
    """STFT e inversa sin modificar nada. Existe para que los tests puedan comprobar que
    la reconstrucción es exacta: si esto no da la señal original, cualquier cosa que se
    mida encima está contaminada por el andamiaje."""
    return _istft(_stft(x, n_fft, salto), n_fft, salto, len(x))
