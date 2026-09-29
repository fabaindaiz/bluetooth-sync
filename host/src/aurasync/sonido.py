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


@dataclass(frozen=True)
class EntradaAudio:
    """Una fuente de audio del sistema: un micrófono, o el monitor de una salida."""

    nodo: str
    descripcion: str

    @property
    def es_monitor(self) -> bool:
        """Los monitores no sirven para calibrar: devuelven la señal sin pasar por el aire."""
        return self.nodo.endswith(".monitor") or "monitor" in self.nodo.lower()


def leer_entradas(objetos: list) -> list[EntradaAudio]:
    """Extrae las fuentes de audio de una salida de `pw-dump` ya parseada."""
    entradas = []
    for objeto in objetos:
        props = (objeto.get("info") or {}).get("props") or {}
        if props.get("media.class") != "Audio/Source":
            continue
        nodo = str(props.get("node.name", ""))
        if not nodo:
            continue
        entradas.append(EntradaAudio(nodo=nodo, descripcion=str(props.get("node.description", nodo))))
    return sorted(entradas, key=lambda e: e.nodo)


def entradas_audio() -> list[EntradaAudio]:
    """Los micrófonos y monitores disponibles ahora mismo."""
    return leer_entradas(_pw_dump())


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

    def destinos_reales(self) -> dict[str, str | None]:
        """A qué nodo llegó de verdad cada stream. La clave es el nodo que se pidió.

        **Existe porque `pw-play --target` puede fallar en silencio.** Si el nombre no se
        resuelve en ese instante, `pw-play` no aborta: manda el audio al **sink por
        defecto**. Medido con parlantes el 2026-09-29: el stream de un Go 4 terminó entrando
        al propio sink virtual de `aurasync`, cerrando un lazo de realimentación, y el
        síntoma fue "ese parlante no suena" sin un solo mensaje de error.

        Devuelve `None` para un stream que no se encontró enlazado a ninguna salida.
        """
        objetos = _pw_dump()

        def props_de(objeto: dict) -> dict:
            return (objeto.get("info") or {}).get("props") or {}

        # **El pid está en el objeto Client, no en el Node.** Buscarlo en el Node fue el
        # primer intento y devolvía "ningún destino" para todos, o sea una falsa alarma en la
        # comprobación que existe justamente para detectar falsas ausencias. El Node se
        # relaciona con su Client por `client.id`.
        cliente_de_pid: dict[int, int] = {}
        nodos: dict[int, dict] = {}
        enlaces: dict[int, int] = {}
        for objeto in objetos:
            tipo, ident, props = str(objeto.get("type")), objeto.get("id"), props_de(objeto)
            if tipo.endswith("Client"):
                pid = props.get("application.process.id")
                if pid is not None and ident is not None:
                    cliente_de_pid[int(pid)] = int(ident)
            elif tipo.endswith("Node") and ident is not None:
                nodos[int(ident)] = props
            elif tipo.endswith("Link"):
                salida, entrada = props.get("link.output.node"), props.get("link.input.node")
                if salida is not None and entrada is not None:
                    enlaces[int(salida)] = int(entrada)

        resultado: dict[str, str | None] = {}
        for nodo, proceso in self._procesos.items():
            cliente = cliente_de_pid.get(proceso.pid)
            stream = next(
                (
                    ident
                    for ident, props in nodos.items()
                    if props.get("client.id") == cliente
                    and str(props.get("media.class", "")).startswith("Stream/Output")
                ),
                None,
            )
            sumidero = enlaces.get(stream) if stream is not None else None
            nombre = (nodos.get(sumidero) or {}).get("node.name") if sumidero is not None else None
            resultado[nodo] = str(nombre) if nombre else None
        return resultado

    def mal_ruteados(self) -> dict[str, str | None]:
        """Los streams que no llegaron a donde se les pidió."""
        return {pedido: real for pedido, real in self.destinos_reales().items() if real != pedido}

    def _indices_de_pactl(self) -> dict[int, int]:
        """De pid de `pw-play` al índice de su stream según pactl. Para poder moverlo.

        Se lee de `pactl` y no de `pw-dump` porque `pactl move-sink-input` quiere ese índice.
        """
        salida = subprocess.run(["pactl", "list", "sink-inputs"], capture_output=True, text=True, check=False).stdout
        indices: dict[int, int] = {}
        actual: int | None = None
        for linea in salida.splitlines():
            despojada = linea.strip()
            if despojada.startswith("Sink Input #"):
                actual = int(despojada.removeprefix("Sink Input #"))
            elif "application.process.id" in despojada and actual is not None:
                pid = despojada.split("=", 1)[1].strip().strip('"')
                if pid.isdigit():
                    indices[int(pid)] = actual
        return indices

    def reparar_ruteo(self) -> dict[str, str | None]:
        """Devuelve a su parlante los streams que fueron movidos. Informa qué reparó.

        **Por qué hace falta y no alcanza con pedir el destino.** Medido el 2026-09-29: al
        aparecer el sink virtual de `aurasync`, WirePlumber lo toma como salida por defecto
        —porque el usuario lo eligió alguna vez y queda guardado en
        `~/.local/state/wireplumber/default-nodes`— y **mueve** el stream que apuntaba al
        default anterior, aunque ese stream tenga su `target.object` puesto. El síntoma es
        que un parlante deja de sonar y su audio entra al propio sink de entrada, cerrando un
        lazo de realimentación, sin un solo mensaje de error.
        """
        perdidos = self.mal_ruteados()
        if not perdidos:
            return {}
        indices = self._indices_de_pactl()
        for nodo in perdidos:
            proceso = self._procesos.get(nodo)
            indice = indices.get(proceso.pid) if proceso is not None else None
            if indice is None:
                continue
            subprocess.run(["pactl", "move-sink-input", str(indice), nodo], capture_output=True, check=False)
        return perdidos

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


class MicrofonoContinuo:
    """Graba el micrófono en un anillo en memoria, para poder mirar el pasado reciente.

    `grabar` escribe a un archivo y sirve para una medición con principio y fin. El lazo de
    recalibración necesita lo otro: una grabación que **no termina**, de la que cada tanto se
    toman los últimos segundos. Un anillo en memoria no crece con el tiempo.

    **Hay que llamar a `bombear` seguido.** Lo que `pw-record` escribe va a una tubería, y
    una tubería llena hace que el proceso se trabe y pierda muestras. Si eso pasa, el anillo
    queda con un salto y la medición sale mal; los filtros de `sincronia.Controlador` la
    descartan, pero es mejor no llegar ahí. El lazo de `aurasync run` bombea una vez por
    bloque, o sea unas 12 veces por segundo.
    """

    def __init__(self, microfono: str, sr: int = SR, segundos: float = 14.0) -> None:
        self.microfono = microfono
        self.sr = sr
        self.segundos = segundos
        self._n = max(1, int(sr * segundos))
        self._anillo = np.zeros(self._n)
        self._escritos = 0
        self._pos = 0
        self._resto = b""
        self._proceso: subprocess.Popen | None = None

    def __enter__(self) -> Self:
        self._proceso = subprocess.Popen(
            [
                "pw-record",
                "--target",
                self.microfono,
                "--rate",
                str(self.sr),
                "--channels",
                "1",
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

    def bombear(self, espera_s: float = 0.0) -> int:
        """Pasa al anillo todo lo que haya llegado. Devuelve cuántas muestras entraron."""
        if self._proceso is None or self._proceso.stdout is None:
            return 0
        trozos: list[bytes] = []
        while True:
            listos, _, _ = select.select([self._proceso.stdout], [], [], espera_s)
            if not listos:
                break
            leido = self._proceso.stdout.read1(1 << 16)
            if not leido:
                break
            trozos.append(leido)
            espera_s = 0.0  # la primera espera puede ser larga; las siguientes, no
        if not trozos:
            return 0
        crudo = self._resto + b"".join(trozos)
        # Una lectura puede cortar una muestra por la mitad: el byte suelto espera al próximo.
        sobra = len(crudo) % 2
        self._resto = crudo[len(crudo) - sobra :] if sobra else b""
        muestras = np.frombuffer(crudo[: len(crudo) - sobra], dtype="<i2").astype(float) / 32768
        self.agregar(muestras)
        return len(muestras)

    def agregar(self, x: np.ndarray) -> None:
        """Mete muestras en el anillo. Lo usa `bombear`, y los tests para entrar sin PipeWire."""
        if len(x) == 0:
            return
        if len(x) >= self._n:
            self._anillo[:] = x[-self._n :]
            self._pos = 0
        else:
            fin = self._pos + len(x)
            if fin <= self._n:
                self._anillo[self._pos : fin] = x
                self._pos = fin % self._n
            else:
                corte = self._n - self._pos
                self._anillo[self._pos :] = x[:corte]
                self._anillo[: len(x) - corte] = x[corte:]
                self._pos = len(x) - corte
        self._escritos += len(x)

    @property
    def lleno(self) -> bool:
        """Si el anillo ya dio una vuelta completa. Antes de eso tiene ceros al principio."""
        return self._escritos >= self._n

    def ultimos(self, segundos: float) -> np.ndarray | None:
        """Los últimos `segundos` grabados, en orden, o `None` si todavía no hay tantos."""
        n = int(self.sr * segundos)
        if n <= 0 or n > self._n or self._escritos < n:
            return None
        ordenado = np.concatenate([self._anillo[self._pos :], self._anillo[: self._pos]])
        return ordenado[-n:].copy()


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
