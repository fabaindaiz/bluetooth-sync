"""La capa que habla con PipeWire: descubrir parlantes, reproducir y grabar.

Todo pasa por las herramientas de línea de comandos de PipeWire (`pw-dump`, `pw-play`,
`pw-record`) en vez de por una biblioteca. Dos razones:

- **no agrega dependencias nativas.** El extra `auracast` de Bumble trae `sounddevice`,
  que en Linux exige PortAudio del sistema, y el proyecto lo evita a propósito
  (`host/pyproject.toml`);
- **lo que se cree no sobrevive al proceso.** Si el programa muere, sus streams mueren con
  él y no queda nada que limpiar. Es la propiedad que pide P1 en el roadmap.

**Un parlante por proceso `pw-play`.** Podría hacerse con un solo sink combinado de
PipeWire, y de hecho los probes lo hicieron así, pero entonces el reparto de canales lo
decide `libpipewire-module-combine-stream` y no este programa: el retardo y la ganancia por
parlante quedarían fuera de nuestro alcance. Con un proceso por parlante, el motor manda
exactamente lo que cada uno tiene que reproducir. Los N streams siguen el mismo reloj del
grafo de PipeWire, así que no se desalinean entre sí por este motivo.
"""

from __future__ import annotations

import contextlib
import json
import select
import subprocess
import wave
from dataclasses import dataclass
from typing import TYPE_CHECKING, Self

import numpy as np

if TYPE_CHECKING:
    from pathlib import Path

SR = 48000


@dataclass(frozen=True)
class SalidaBluetooth:
    """Un parlante Bluetooth tal como lo ve PipeWire."""

    nodo: str
    descripcion: str
    codec: str

    @property
    def direccion(self) -> str:
        """La dirección Bluetooth deducida del nombre del nodo, con dos puntos."""
        crudo = self.nodo.removeprefix("bluez_output.").split(".")[0]
        return crudo.replace("_", ":")


def _pw_dump() -> list:
    salida = subprocess.run(["pw-dump"], capture_output=True, text=True, check=False).stdout
    try:
        return json.loads(salida)
    except json.JSONDecodeError:
        return []


def leer_salidas(objetos: list) -> list[SalidaBluetooth]:
    """Extrae los parlantes Bluetooth de una salida de `pw-dump` ya parseada.

    Está separado de la llamada al comando para poder probarlo sin PipeWire.

    Se descartan los nodos marcados `api.bluez5.internal`: PipeWire crea uno por cada
    stream isócrono de un conjunto coordinado —unos auriculares LE Audio aparecen con
    tres— y solo el que no es interno representa al dispositivo
    (`docs/research/experimentos/04-e8-unicast-le-audio-tune-770nc.md`).
    """
    salidas = []
    for objeto in objetos:
        props = (objeto.get("info") or {}).get("props") or {}
        nodo = str(props.get("node.name", ""))
        if not nodo.startswith("bluez_output.") or props.get("api.bluez5.internal"):
            continue
        salidas.append(
            SalidaBluetooth(
                nodo=nodo,
                descripcion=str(props.get("node.description", nodo)),
                codec=str(props.get("api.bluez5.codec", "?")),
            )
        )
    return sorted(salidas, key=lambda s: s.nodo)


def salidas_bluetooth() -> list[SalidaBluetooth]:
    """Los parlantes Bluetooth conectados ahora mismo."""
    return leer_salidas(_pw_dump())


def codecs_mezclados(salidas: list[SalidaBluetooth]) -> bool:
    """Si hay más de un códec en uso, que es lo que arruina la alineación.

    Con códecs distintos el desfase entre parlantes salta a 45 a 150 ms, que ya es zona de
    eco audible en música
    (`docs/research/experimentos/05-e6-a2dp-un-canal-por-parlante.md`).
    """
    return len({s.codec for s in salidas}) > 1


class Reproductor:
    """Manda audio a varios parlantes a la vez, uno por proceso."""

    def __init__(self, nodos: list[str], sr: int = SR, latencia_ms: int = 200) -> None:
        if not nodos:
            msg = "no se indicó ningún parlante"
            raise ValueError(msg)
        self.nodos = nodos
        self.sr = sr
        self.latencia_ms = latencia_ms
        self._procesos: dict[str, subprocess.Popen] = {}

    def __enter__(self) -> Self:
        for nodo in self.nodos:
            self._procesos[nodo] = subprocess.Popen(
                [
                    "pw-play",
                    "--target",
                    nodo,
                    "--rate",
                    str(self.sr),
                    "--channels",
                    "1",
                    "--format",
                    "s16",
                    "--latency",
                    f"{self.latencia_ms}ms",
                    "--raw",
                    "-",
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        return self

    def __exit__(self, *_) -> None:
        self.cerrar()

    def escribir(self, bloques: dict[str, np.ndarray]) -> None:
        """Un bloque mono por parlante. Las claves son nombres de nodo."""
        for nodo, x in bloques.items():
            proceso = self._procesos.get(nodo)
            if proceso is None or proceso.stdin is None:
                continue
            pcm = (np.clip(x, -1.0, 1.0) * 32767).astype("<i2")
            try:
                proceso.stdin.write(pcm.tobytes())
            except BrokenPipeError:
                # El parlante se desconectó. No se corta la reproducción de los demás: en
                # una instalación es preferible seguir sonando con los que quedan.
                self._procesos.pop(nodo, None)

    def cerrar(self) -> None:
        for proceso in self._procesos.values():
            if proceso.stdin is not None:
                with contextlib.suppress(BrokenPipeError):
                    proceso.stdin.close()
        for proceso in self._procesos.values():
            try:
                proceso.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proceso.kill()
        self._procesos.clear()

    @property
    def vivos(self) -> list[str]:
        return [n for n, p in self._procesos.items() if p.poll() is None]


class SinkVirtual:
    """Un dispositivo de salida que el sistema ve, y cuyo audio llega a este proceso.

    Lo crea `pw-record` con `media.class=Audio/Sink`: PipeWire lo muestra en la lista de
    salidas como cualquier placa, y todo lo que se rutee ahí sale por su stdout. Con eso,
    cualquier reproductor —un navegador, Spotify, lo que sea— puede pasar por el
    procesamiento sin instalarle nada ni configurarlo.

    **No deja huella.** El nodo vive en el proceso: si el programa muere, el dispositivo
    desaparece y la configuración del sistema queda como estaba. Es el criterio que pide P1
    en el roadmap, y está comprobado: al terminar, `pactl list sinks` no lo muestra más.

    **Cuando no hay nada reproduciéndose, el nodo queda suspendido y no emite datos.** Por
    eso `leer` puede devolver `None`: quien lo use tiene que seguir alimentando a los
    parlantes con silencio en ese caso, o sus streams A2DP se suspenden también y al volver
    traen un desfase distinto, que es justamente lo que la calibración acaba de corregir
    (`docs/research/experimentos/05-e6-a2dp-un-canal-por-parlante.md`).
    """

    def __init__(
        self,
        nombre: str = "aurasync",
        descripcion: str = "aurasync (envolvente)",
        sr: int = SR,
    ) -> None:
        self.nombre = nombre
        self.descripcion = descripcion
        self.sr = sr
        self._proceso: subprocess.Popen | None = None

    def __enter__(self) -> Self:
        propiedades = (
            f"{{ media.class=Audio/Sink node.name={self.nombre} "
            f'node.description="{self.descripcion}" audio.position=[FL FR] }}'
        )
        self._proceso = subprocess.Popen(
            [
                "pw-record",
                "-P",
                propiedades,
                "--rate",
                str(self.sr),
                "--channels",
                "2",
                "--format",
                "s16",
                "--raw",
                "-",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        return self

    def __exit__(self, *_) -> None:
        self.cerrar()

    def cerrar(self) -> None:
        if self._proceso is None:
            return
        self._proceso.terminate()
        try:
            self._proceso.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self._proceso.kill()
        self._proceso = None

    def leer(self, muestras: int, espera_s: float = 0.05) -> tuple[np.ndarray, np.ndarray] | None:
        """Un bloque estéreo, o `None` si no hay nada reproduciéndose todavía.

        Devuelve `None` en vez de bloquear, para que el llamador pueda mandar silencio a los
        parlantes y mantener sus streams despiertos.
        """
        if self._proceso is None or self._proceso.stdout is None:
            return None
        faltan = muestras * 2 * 2  # dos canales, dos bytes
        trozos: list[bytes] = []
        while faltan > 0:
            listos, _, _ = select.select([self._proceso.stdout], [], [], espera_s)
            if not listos:
                break
            leido = self._proceso.stdout.read1(faltan)
            if not leido:
                break
            trozos.append(leido)
            faltan -= len(leido)
        crudo = b"".join(trozos)
        if not crudo:
            return None
        marco = np.frombuffer(crudo, dtype="<i2").astype(float) / 32768
        # Un bloque puede llegar cortado; se usa lo que haya y el resto llega después.
        marco = marco[: (len(marco) // 2) * 2].reshape(-1, 2)
        return marco[:, 0], marco[:, 1]


def grabar(destino: Path, microfono: str, sr: int = SR) -> subprocess.Popen:
    """Arranca una grabación en segundo plano y la deja corriendo.

    No recibe una duración a propósito: quien llama decide cuándo cortar, con
    `terminar_grabacion`, y así la grabación siempre cubre toda la reproducción aunque esta
    tarde más de lo previsto.
    """
    destino.parent.mkdir(parents=True, exist_ok=True)
    return subprocess.Popen(
        ["pw-record", "--target", microfono, "--rate", str(sr), "--channels", "1", "--format", "s16", str(destino)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def terminar_grabacion(proceso: subprocess.Popen) -> None:
    """Corta con SIGINT, que es lo que hace a `pw-record` cerrar bien el WAV.

    Con SIGTERM el archivo queda sin la cabecera actualizada y no se puede leer.
    """
    proceso.send_signal(2)
    try:
        proceso.wait(timeout=20)
    except subprocess.TimeoutExpired:
        proceso.kill()


def leer_wav_mono(ruta: Path) -> np.ndarray:
    """Lee un WAV como flotantes en [-1, 1], mezclando a mono si hace falta."""
    with wave.open(str(ruta)) as w:
        datos = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(float) / 32768
        if w.getnchannels() > 1:
            datos = datos.reshape(-1, w.getnchannels()).mean(axis=1)
    return datos


def leer_wav_estereo(ruta: Path) -> tuple[np.ndarray, np.ndarray, int]:
    """Lee un WAV y devuelve (izquierdo, derecho, frecuencia).

    Un archivo mono se duplica a los dos canales: el motor espera estéreo, y con una fuente
    mono el ambiente simplemente sale vacío, que es correcto.
    """
    with wave.open(str(ruta)) as w:
        if w.getsampwidth() != 2:  # noqa: PLR2004
            msg = f"solo se leen WAV de 16 bits; este es de {w.getsampwidth() * 8}"
            raise ValueError(msg)
        crudo = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(float) / 32768
        canales, sr = w.getnchannels(), w.getframerate()
    if canales == 1:
        return crudo, crudo.copy(), sr
    marco = crudo.reshape(-1, canales)
    return marco[:, 0], marco[:, 1], sr
