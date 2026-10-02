"""Lo que las pruebas con parlantes le piden al sistema, y cómo comprueban que pasó.

Lo usan `probes/16-calidad/` y `probes/15-graves-y-volumen/` (que lo importa desde acá: se
borran juntas). Reglas del repositorio que esto aplica (CLAUDE.md):

- **Lo que se le pide a PipeWire se verifica.** Cada `pw-play` y cada `pw-record` se busca
  después en `pw-dump` por el PID del proceso, y se comprueba a qué nodo quedó enlazado. Si no
  es el pedido, se detiene y se avisa (experimentos/09: un `--target` que termina en otro lado
  no da error).
- **Los cambios en el sistema se anotan antes, con cómo revertirlos.** `Restaurador` escribe la
  línea en `cambios-de-sistema.txt` antes del cambio, y deshace todo al terminar, también con
  Ctrl-C o SIGTERM.
- **Falta de sistema es un error claro al principio**, no a mitad de camino: `requerir()`.

Solo biblioteca estándar y numpy, más `aurasync` (el entorno de hatch) para el volumen con
lectura de vuelta (`aurasync.bt_volume.PactlVolume`).
"""

from __future__ import annotations

import contextlib
import json
import os
import platform
import shutil
import signal
import struct
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

SR = 48000
RAIZ = Path(__file__).resolve().parents[2]
DATOS = Path(os.environ.get("DATOS_PRUEBAS") or RAIZ / "docs/research/experimentos/datos")
"""Dónde van los datos. `DATOS_PRUEBAS` lo cambia (los ensayos en simulación no ensucian docs/)."""
AMPLITUD_MAXIMA = 0.2
"""El tope de las señales de prueba (memoria del proyecto: 0,1 por defecto, nunca más de 0,2)."""


class FaltaSistema(RuntimeError):
    """Falta un programa o un nodo que la prueba necesita. Se lanza antes de cambiar nada."""


class RuteoIncorrecto(RuntimeError):
    """PipeWire no puso el stream donde se pidió."""


# -- antes de empezar -------------------------------------------------------------------------


def requerir(*programas: str) -> None:
    faltan = [p for p in programas if shutil.which(p) is None]
    if faltan:
        msg = (
            f"faltan {', '.join(faltan)} en este equipo. Esta prueba corre en PC-Ryzen5 (Linux con "
            "PipeWire, los Go 4 y el micrófono); en el Mac solo se prueban el análisis "
            "(los tests) y, donde existe, la parte del servicio con --ensayo."
        )
        raise FaltaSistema(msg)


def limitar_amplitud(amplitud: float) -> float:
    if not 0 < amplitud <= AMPLITUD_MAXIMA:
        msg = f"amplitud {amplitud}: tiene que estar entre 0 y {AMPLITUD_MAXIMA} (regla de las pruebas)"
        raise SystemExit(msg)
    return amplitud


def _version(*comando: str) -> str | None:
    if shutil.which(comando[0]) is None:
        return None
    try:
        r = subprocess.run(list(comando), capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    texto = (r.stdout or r.stderr).strip()
    return texto.splitlines()[-1] if texto else None


def entorno() -> dict:
    """Equipo, versiones y hora: lo que cada medición anota (CLAUDE.md)."""
    try:
        import aurasync  # noqa: PLC0415

        version = aurasync.__version__
    except ImportError:
        version = None
    return {
        "hora": datetime.now().astimezone().isoformat(timespec="seconds"),
        "equipo": platform.node(),
        "sistema": platform.system(),
        "kernel": platform.release(),
        "pipewire": _version("pipewire", "--version"),
        "wireplumber": _version("wireplumber", "--version"),
        "bluez": _version("bluetoothctl", "--version"),
        "python": platform.python_version(),
        "numpy": np.__version__,
        "aurasync": version,
    }


def pares(texto: str | None) -> dict[str, str]:
    """`"Red=80,Blue=75"` → `{"Red": "80", "Blue": "75"}`."""
    out = {}
    for parte in (texto or "").split(","):
        if "=" in parte:
            k, v = parte.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def agregar_anotaciones(parser: Any) -> None:
    """Lo que se anota a mano en cada medición (CLAUDE.md: el entorno va con el número)."""
    parser.add_argument("--sesion", default="1", help="1, 2…: la repetición independiente a la que pertenece")
    parser.add_argument("--nota", default="", help="qué es esta corrida, en palabras")
    parser.add_argument("--microfono-en", default="", help="dónde está el micrófono (altura, distancia, apuntando a…)")
    parser.add_argument("--posiciones", help='dónde está cada parlante: "Red=frente-izq,Blue=atras"')
    parser.add_argument("--bateria", help='la batería que muestra cada parlante: "Red=80,Blue=75"')
    parser.add_argument("--firmware", help='la versión de firmware de cada JBL, si se leyó en la app: "Red=…"')


def anotaciones(args: Any) -> dict:
    return {
        "sesion": args.sesion,
        "nota": args.nota,
        "microfono_en": args.microfono_en,
        "posiciones": pares(args.posiciones),
        "bateria": pares(args.bateria),
        "firmware": pares(args.firmware) or "sin leer",
    }


def ruta_datos(experimento: str, nombre: str, ahora: datetime | None = None) -> Path:
    """`datos/<experimento>/<fecha>T<hora>-<nombre>` (sin extensión)."""
    ahora = ahora or datetime.now().astimezone()
    carpeta = DATOS / experimento
    carpeta.mkdir(parents=True, exist_ok=True)
    return carpeta / f"{ahora.strftime('%Y-%m-%dT%H%M%S')}-{nombre}"


def guardar_json(ruta: Path, datos: dict) -> Path:
    ruta.write_text(json.dumps(datos, indent=2, ensure_ascii=False, default=_a_json) + "\n")
    return ruta


def _a_json(x: Any) -> Any:
    if isinstance(x, np.ndarray):
        return [None if not np.isfinite(v) else round(float(v), 3) for v in x.ravel()]
    if isinstance(x, np.floating | np.integer):
        return x.item()
    if isinstance(x, Path):
        return str(x)
    msg = f"no sé guardar {type(x).__name__}"
    raise TypeError(msg)


# -- señales y WAV ------------------------------------------------------------------------------


def ruido_rosa(n: int, semilla: int = 0) -> np.ndarray:
    """Ruido rosa (−3 dB por octava desde 20 Hz) con pico 1."""
    rng = np.random.default_rng(semilla)
    f = np.fft.rfftfreq(n, 1 / SR)
    x = np.fft.irfft(np.fft.rfft(rng.standard_normal(n)) / np.sqrt(np.maximum(f, 20.0)), n)
    return x / np.max(np.abs(x))


def fundido(x: np.ndarray, ms: float = 20.0) -> np.ndarray:
    """Rampa de entrada y salida, para que la propia señal no empiece con un clic."""
    n = min(len(x) // 2, int(SR * ms / 1000))
    if n == 0:
        return x
    r = np.ones(len(x))
    r[:n] = np.linspace(0, 1, n)
    r[-n:] = np.linspace(1, 0, n)
    return x * (r if x.ndim == 1 else r[:, None])


def escribir_wav(ruta: Path, datos: np.ndarray, sr: int = SR) -> Path:
    """WAV de coma flotante de 32 bits (formato 3): lo que graba el micrófono, sin perder nada."""
    x = np.asarray(datos, dtype="<f4")
    if x.ndim == 1:
        x = x[:, None]
    canales = x.shape[1]
    cuerpo = x.tobytes()
    cabecera = b"RIFF" + struct.pack("<I", 36 + len(cuerpo)) + b"WAVE"
    cabecera += b"fmt " + struct.pack("<IHHIIHH", 16, 3, canales, sr, sr * canales * 4, canales * 4, 32)
    cabecera += b"data" + struct.pack("<I", len(cuerpo))
    ruta.write_bytes(cabecera + cuerpo)
    return ruta


def leer_wav(ruta: Path) -> tuple[np.ndarray, int]:
    """(muestras (n, canales) en float64 entre −1 y 1, frecuencia). PCM 16/24/32 y float 32/64,
    también con cabecera WAVE_FORMAT_EXTENSIBLE (lo que escriben pw-record y muchos editores)."""
    datos = Path(ruta).read_bytes()
    if datos[:4] != b"RIFF" or datos[8:12] != b"WAVE":
        msg = f"{ruta} no es un WAV"
        raise ValueError(msg)
    pos, formato, canales, sr, bits, cuerpo = 12, None, None, None, None, None
    while pos + 8 <= len(datos):
        nombre, largo = datos[pos : pos + 4], struct.unpack("<I", datos[pos + 4 : pos + 8])[0]
        trozo = datos[pos + 8 : pos + 8 + largo]
        if nombre == b"fmt ":
            formato, canales, sr = struct.unpack("<HHI", trozo[:8])
            bits = struct.unpack("<H", trozo[14:16])[0]
            if formato == 0xFFFE and len(trozo) >= 26:  # noqa: PLR2004 - el subformato está en 24..26
                formato = struct.unpack("<H", trozo[24:26])[0]
        elif nombre == b"data":
            cuerpo = trozo
        pos += 8 + largo + (largo & 1)
    if formato is None or cuerpo is None:
        msg = f"{ruta}: falta fmt o data"
        raise ValueError(msg)
    if formato == 3 and bits in {32, 64}:  # noqa: PLR2004
        x = np.frombuffer(cuerpo[: len(cuerpo) // (bits // 8) * (bits // 8)], dtype="<f4" if bits == 32 else "<f8")  # noqa: PLR2004
    elif formato == 1 and bits == 16:  # noqa: PLR2004
        x = np.frombuffer(cuerpo[: len(cuerpo) // 2 * 2], dtype="<i2") / 32768.0
    elif formato == 1 and bits == 32:  # noqa: PLR2004
        x = np.frombuffer(cuerpo[: len(cuerpo) // 4 * 4], dtype="<i4") / 2147483648.0
    elif formato == 1 and bits == 24:  # noqa: PLR2004
        b = np.frombuffer(cuerpo[: len(cuerpo) // 3 * 3], dtype=np.uint8).reshape(-1, 3).astype(np.int32)
        v = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
        x = np.where(v >= 1 << 23, v - (1 << 24), v) / 8388608.0
    else:
        msg = f"{ruta}: formato {formato} de {bits} bits no soportado"
        raise ValueError(msg)
    x = np.asarray(x, dtype=float)
    return x[: len(x) // canales * canales].reshape(-1, canales), int(sr)


# -- pw-dump: dónde quedó cada stream ------------------------------------------------------------


def pw_dump() -> list:
    r = subprocess.run(["pw-dump"], capture_output=True, text=True, timeout=10, check=True)
    return json.loads(r.stdout or "[]")


def _props(o: dict) -> dict:
    return (o.get("info") or {}).get("props") or {}


def _grafo(objetos: list) -> tuple[dict[int, dict], list[tuple[int, int]], dict[int, str]]:
    """(nodos {id: props}, enlaces [(salida, entrada)], pid de cada cliente {id: pid})."""
    nodos, enlaces, clientes = {}, [], {}
    for o in objetos:
        tipo, p = str(o.get("type")), _props(o)
        if tipo.endswith("Node"):
            nodos[o["id"]] = p
        elif tipo.endswith("Link") and "link.output.node" in p:
            enlaces.append((int(p["link.output.node"]), int(p["link.input.node"])))
        elif tipo.endswith("Client"):
            clientes[o["id"]] = str(p.get("application.process.id"))
    return nodos, enlaces, clientes


def nodos_de_pid(objetos: list, pid: int) -> list[int]:
    """Los nodos de stream que creó el proceso `pid` (por su cliente)."""
    nodos, _, clientes = _grafo(objetos)
    propios = {c for c, p in clientes.items() if p == str(pid)}
    return sorted(
        i for i, p in nodos.items() if p.get("client.id") in propios or str(p.get("application.process.id")) == str(pid)
    )


def destinos_de(objetos: list, pid: int) -> list[str]:
    """A qué nodos (por `node.name`) quedó enlazada la salida del proceso `pid`."""
    nodos, enlaces, _ = _grafo(objetos)
    propios = set(nodos_de_pid(objetos, pid))
    return sorted({str(nodos.get(b, {}).get("node.name", b)) for a, b in enlaces if a in propios})


def origenes_de(objetos: list, pid: int) -> list[str]:
    """De qué nodos (por `node.name`) graba el proceso `pid`."""
    nodos, enlaces, _ = _grafo(objetos)
    propios = set(nodos_de_pid(objetos, pid))
    return sorted({str(nodos.get(a, {}).get("node.name", a)) for a, b in enlaces if b in propios})


def quienes_alimentan(objetos: list, sink: str, salvo_pid: int | None = None) -> list[str]:
    """Los streams enlazados hacia el nodo `sink`, salvo los del proceso `salvo_pid`: cada uno
    como `"<aplicación> (pid N)"`."""
    nodos, enlaces, clientes = _grafo(objetos)
    ids = {i for i, p in nodos.items() if p.get("node.name") == sink}
    propios = set(nodos_de_pid(objetos, salvo_pid)) if salvo_pid is not None else set()
    out = []
    for a, b in enlaces:
        if b in ids and a not in propios:
            p = nodos.get(a, {})
            app = p.get("application.name") or p.get("node.name") or str(a)
            pid = clientes.get(p.get("client.id")) or p.get("application.process.id")
            out.append(f"{app} (pid {pid})")
    return sorted(set(out))


def nodo_existe(objetos: list, nombre: str) -> bool:
    return any(p.get("node.name") == nombre for p in _grafo(objetos)[0].values())


def sinks_bluetooth(objetos: list) -> dict[str, str]:
    """`{node.name: descripción}` de los sinks `bluez_output.*`."""
    return {
        str(p["node.name"]): str(p.get("node.description") or p["node.name"])
        for p in _grafo(objetos)[0].values()
        if str(p.get("node.name", "")).startswith("bluez_output.")
    }


def coincidencias(pedido: str, candidatos: list[tuple[str, str]]) -> list[str]:
    """Los `(nodo, nombre)` que corresponden a `pedido`: el nodo exacto, la MAC (con `:` o `_`)
    dentro del nodo, o una parte del nombre ("Red"). Una parte del nombre del nodo no cuenta:
    "blue" está en todos los `bluez_output…`."""
    exactos = [n for n, _ in candidatos if n == pedido]
    if exactos:
        return exactos
    mac = pedido.replace(":", "_").upper()
    if len(mac) == 17 and mac.count("_") == 5:  # noqa: PLR2004 - AA_BB_CC_DD_EE_FF
        return [n for n, _ in candidatos if mac in n.upper()]
    return [n for n, d in candidatos if pedido.lower() in d.lower()]


def elegir_sinks(objetos: list, pedidos: list[str] | None) -> dict[str, str]:
    """Los sinks Bluetooth pedidos por nombre de nodo, MAC o parte de la descripción."""
    todos = sinks_bluetooth(objetos)
    if not todos:
        msg = "no hay ningún parlante Bluetooth conectado (ningún sink bluez_output.* en pw-dump)"
        raise FaltaSistema(msg)
    if not pedidos:
        return todos
    elegidos = {}
    for pedido in pedidos:
        hallados = coincidencias(pedido, [(n, d) for n, d in todos.items()])
        if len(hallados) != 1:
            msg = f"{pedido!r} coincide con {hallados or 'ningún'} sink; hay {todos}"
            raise FaltaSistema(msg)
        elegidos[hallados[0]] = todos[hallados[0]]
    return elegidos


def _esperar_enlace(pid: int, leer: Callable[[list, int], list[str]], limite_s: float = 3.0) -> list[str]:
    fin = time.monotonic() + limite_s
    vistos: list[str] = []
    while time.monotonic() < fin:
        vistos = leer(pw_dump(), pid)
        if vistos:
            return vistos
        time.sleep(0.2)
    return vistos


# -- reproducir y grabar, verificado ----------------------------------------------------------


class Reproduccion:
    """Un `pw-play --raw` hacia `destino` con los datos dados, comprobado en `pw-dump`.

    `canales` es el mapa de canales (`FL,FR`, o `AUX0,AUX1,AUX2` para el sink combinado de
    aurasync): con datos crudos, el mapa es el que se pide, no el que adivine libsndfile.
    """

    def __init__(self, destino: str, datos: np.ndarray, canales: str = "FL,FR", sr: int = SR) -> None:
        x = np.asarray(datos, dtype="<f4")
        self.datos = x[:, None] if x.ndim == 1 else x
        if self.datos.shape[1] != len(canales.split(",")):
            msg = f"{self.datos.shape[1]} canales de datos y mapa {canales}"
            raise ValueError(msg)
        if np.max(np.abs(self.datos)) > 1.0:
            msg = "los datos pasan de 1,0: recortarían"
            raise ValueError(msg)
        self.destino, self.canales, self.sr = destino, canales, sr
        self.proceso: subprocess.Popen | None = None
        self.inicio: float | None = None
        self.verificado: dict | None = None
        self._hilo: threading.Thread | None = None

    def iniciar(self, verificar: bool = True) -> Reproduccion:  # noqa: FBT001, FBT002
        self.proceso = subprocess.Popen(
            [
                "pw-play",
                "--target",
                self.destino,
                "--raw",
                "--rate",
                str(self.sr),
                "--channels",
                str(self.datos.shape[1]),
                "--channel-map",
                self.canales,
                "--format",
                "f32",
                "-P",
                "{ node.dont-move = true node.dont-reconnect = true node.dont-fallback = true }",
                "-",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.inicio = time.time()

        def escribir() -> None:
            with contextlib.suppress(BrokenPipeError, ValueError, OSError):
                assert self.proceso is not None
                assert self.proceso.stdin is not None
                self.proceso.stdin.write(self.datos.tobytes())
                self.proceso.stdin.close()

        self._hilo = threading.Thread(target=escribir, daemon=True)
        self._hilo.start()
        if verificar:
            self.verificar()
        return self

    def verificar(self) -> dict:
        """Dónde quedó de verdad: tiene que ser exactamente `destino`."""
        assert self.proceso is not None
        destinos = _esperar_enlace(self.proceso.pid, destinos_de)
        objetos = pw_dump()
        self.verificado = {
            "pedido": self.destino,
            "destinos": destinos,
            "otros_streams_en_destino": quienes_alimentan(objetos, self.destino, self.proceso.pid),
            "t": time.time(),
        }
        if destinos != [self.destino]:
            self.detener()
            msg = f"pw-play pidió {self.destino!r} y quedó en {destinos or 'ningún nodo'}: detenido"
            raise RuteoIncorrecto(msg)
        return self.verificado

    def esperar(self, margen_s: float = 2.0) -> None:
        assert self.proceso is not None
        duracion = len(self.datos) / self.sr
        try:
            self.proceso.wait(timeout=duracion + margen_s + 5)
        except subprocess.TimeoutExpired:
            self.detener()

    def detener(self) -> None:
        if self.proceso is not None and self.proceso.poll() is None:
            self.proceso.terminate()
            with contextlib.suppress(subprocess.TimeoutExpired):
                self.proceso.wait(timeout=2)


class Grabacion:
    """`pw-record --raw` del micrófono a memoria, comprobado en `pw-dump`; se guarda como WAV.

    Anota la ganancia del micrófono al empezar y al terminar (`pactl get-source-volume`): si
    cambió, la medición no es comparable (research/11 §1.5: ganancia fija, sin AGC).
    """

    def __init__(self, microfono: str, sr: int = SR) -> None:
        if microfono.endswith(".monitor"):
            msg = f"{microfono} es el monitor de una salida, no un micrófono: no escucha el aire"
            raise FaltaSistema(msg)
        self.microfono, self.sr = microfono, sr
        self.proceso: subprocess.Popen | None = None
        self.inicio: float | None = None
        self._trozos: list[bytes] = []
        self._hilo: threading.Thread | None = None
        self.verificado: dict | None = None
        self.volumen_inicio = volumen_fuente(microfono)
        self.volumen_fin: str | None = None

    def iniciar(self) -> Grabacion:
        self.proceso = subprocess.Popen(
            [
                "pw-record",
                "--target",
                self.microfono,
                "--raw",
                "--rate",
                str(self.sr),
                "--channels",
                "1",
                "--format",
                "f32",
                "-P",
                "{ node.dont-move = true node.dont-reconnect = true }",
                "-",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        self.inicio = time.time()

        def leer() -> None:
            assert self.proceso is not None
            assert self.proceso.stdout is not None
            while True:
                trozo = self.proceso.stdout.read(65536)
                if not trozo:
                    return
                self._trozos.append(trozo)

        self._hilo = threading.Thread(target=leer, daemon=True)
        self._hilo.start()
        origenes = _esperar_enlace(self.proceso.pid, origenes_de)
        self.verificado = {"pedido": self.microfono, "origenes": origenes, "t": time.time()}
        if origenes != [self.microfono]:
            self.detener()
            msg = f"pw-record pidió {self.microfono!r} y graba de {origenes or 'ningún nodo'}: detenido"
            raise RuteoIncorrecto(msg)
        return self

    def segundos(self) -> float:
        return sum(len(t) for t in self._trozos) / 4 / self.sr

    def detener(self) -> np.ndarray:
        if self.proceso is not None and self.proceso.poll() is None:
            self.proceso.send_signal(signal.SIGINT)
            try:
                self.proceso.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.proceso.kill()
        if self._hilo is not None:
            self._hilo.join(timeout=3)
        self.volumen_fin = volumen_fuente(self.microfono)
        datos = b"".join(self._trozos)
        return np.frombuffer(datos[: len(datos) // 4 * 4], dtype="<f4").astype(float)

    def describir(self) -> dict:
        return {
            "microfono": self.microfono,
            "inicio": self.inicio,
            "verificado": self.verificado,
            "volumen_microfono_inicio": self.volumen_inicio,
            "volumen_microfono_fin": self.volumen_fin,
            "ganancia_cambio": self.volumen_fin is not None and self.volumen_fin != self.volumen_inicio,
        }


def volumen_fuente(nodo: str) -> str | None:
    if shutil.which("pactl") is None:
        return None
    r = subprocess.run(["pactl", "get-source-volume", nodo], capture_output=True, text=True, timeout=3, check=False)
    return r.stdout.strip() if r.returncode == 0 else None


def resolver_microfono(pedido: str | None) -> str:
    """`--microfono`, si no el de `service.json`, si no la fuente por defecto (como `aurasync`)."""
    from argparse import Namespace  # noqa: PLC0415

    from aurasync.cli import resolver_microfono as resolver  # noqa: PLC0415

    nodo = resolver(Namespace(microfono=pedido))
    if not nodo:
        msg = "no hay micrófono: pasalo con --microfono (pactl list short sources)"
        raise FaltaSistema(msg)
    return nodo


# -- el volumen Bluetooth de un parlante, con lectura de vuelta ---------------------------------


class VolumenParlante:
    """`pactl set-sink-volume` y `get-sink-volume` sobre un sink `bluez_output…`, como
    `aurasync.bt_volume`: después de pedir, se lee de vuelta hasta 5 veces en ~0,5 s, y si no
    quedó dentro de un paso AVRCP (`TOLERANCE_PCT`), es un error."""

    def __init__(self, backend: Any = None) -> None:
        from aurasync.bt_volume import READBACK_TRIES, READBACK_WAIT_S, TOLERANCE_PCT, PactlVolume  # noqa: PLC0415

        self.backend = backend or PactlVolume()
        self.tolerancia, self.intentos, self.espera = TOLERANCE_PCT, READBACK_TRIES, READBACK_WAIT_S

    def leer(self, sink: str) -> float:
        valor = self.backend.get_percent(sink)
        if valor is None:
            msg = f"no pude leer el volumen de {sink}"
            raise FaltaSistema(msg)
        return valor

    def poner(self, sink: str, porcentaje: float) -> float:
        if not self.backend.set_percent(sink, porcentaje):
            msg = f"pactl no aceptó {porcentaje:.1f} % en {sink}"
            raise RuteoIncorrecto(msg)
        leido = None
        for _ in range(self.intentos):
            time.sleep(self.espera)
            leido = self.backend.get_percent(sink)
            if leido is not None and abs(leido - porcentaje) <= self.tolerancia:
                return leido
        msg = f"pedí {porcentaje:.1f} % en {sink} y quedó en {leido} %"
        raise RuteoIncorrecto(msg)


# -- deshacer todo al terminar ------------------------------------------------------------------


class Restaurador:
    """Anota cada cambio **antes** de hacerlo (con su reversión) y lo deshace al final.

    Uso: `with Restaurador(archivo) as r: r.cambio("qué", "cómo revertir", deshacer)`. Las
    reversiones corren en orden inverso al salir, también por Ctrl-C (KeyboardInterrupt) o
    SIGTERM/SIGHUP (que se convierten en SystemExit mientras dura el bloque).
    """

    def __init__(self, archivo: Path | None) -> None:
        self.archivo = archivo
        self._pendientes: list[tuple[str, Callable[[], Any]]] = []
        self.informe: list[dict] = []
        self._previas: dict[int, Any] = {}

    def __enter__(self) -> Restaurador:
        for s in (signal.SIGTERM, signal.SIGHUP):
            with contextlib.suppress(ValueError, OSError):
                self._previas[s] = signal.signal(s, self._senal)
        return self

    @staticmethod
    def _senal(numero: int, _frame: Any) -> None:
        raise SystemExit(f"señal {numero}: restauro y salgo")

    def anotar(self, texto: str) -> None:
        if self.archivo is None:
            return
        self.archivo.parent.mkdir(parents=True, exist_ok=True)
        with self.archivo.open("a", encoding="utf-8") as f:
            f.write(f"{datetime.now().astimezone().isoformat(timespec='seconds')} {texto}\n")

    def cambio(self, que: str, revertir: str, deshacer: Callable[[], Any], *, sistema: bool = True) -> None:
        """Registrar un cambio antes de hacerlo. `sistema`: si va a `cambios-de-sistema.txt`
        (el volumen de un sink lo es; un ajuste de la sesión de aurasync, no)."""
        if sistema:
            self.anotar(f"CAMBIO: {que}. REVERTIR: {revertir}")
        self._pendientes.append((que, deshacer))

    def restaurar(self) -> list[dict]:
        while self._pendientes:
            que, deshacer = self._pendientes.pop()
            try:
                resultado = deshacer()
                self.informe.append({"que": que, "ok": True, "resultado": resultado})
                print(f"  ↺ restaurado: {que}")
            except Exception as e:  # noqa: BLE001 - se informa y se sigue con el resto
                self.informe.append({"que": que, "ok": False, "error": str(e)})
                print(f"  ✗ NO se pudo restaurar: {que}: {e}", file=sys.stderr)
        return self.informe

    def __exit__(self, *_exc: object) -> None:
        try:
            self.restaurar()
            if any(not r["ok"] for r in self.informe):
                self.anotar("ATENCIÓN: alguna reversión falló; ver la salida del script")
            elif self.informe:
                self.anotar("revertido todo lo anterior de esta corrida")
        finally:
            for s, previa in self._previas.items():
                with contextlib.suppress(ValueError, OSError, TypeError):
                    signal.signal(s, previa)
