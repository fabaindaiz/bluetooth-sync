"""A session without speakers: the real motor and the real loop, with PipeWire and the room simulated.

`aurasync service --simular` runs on it, so that the panel can be used and tested in a
browser on any machine. What is real and what is not:

- **real**: the motor, the contract, the service, the recalibration loop and the
  calibration's measurement (`medicion.calibrar`), which run on what the simulated room
  returns;
- **simulated**: the speakers' streams, the virtual sink (it delivers the test signal of
  `sources.py` when a source is chosen), the Bluetooth devices, the system services, and
  the room: each speaker reaches the microphone with a fixed delay and gain plus noise.

The panel shows a SIMULADO badge, and `measurement_save` refuses a simulated calibration:
a simulated number must never end up in `docs/research/experimentos/` as MEDIDO.
"""

from __future__ import annotations

import itertools
import threading
import time
from typing import TYPE_CHECKING, Any, Self

import numpy as np

from aurasync.dsp import eq, response
from aurasync.session import AudioSession
from aurasync.sources import probe_signal

if TYPE_CHECKING:
    from aurasync.config import Instalacion

ROOM_DELAYS_MS = (3.0, 7.5, 12.0, 5.0, 9.0, 1.5)
"""How late each speaker reaches the microphone, in installation order."""
ROOM_GAINS = (1.0, 0.8, 0.6, 0.9, 0.7, 1.0)
ROOM_NOISE = 0.001
ROOM_COLOUR_DB = np.interp(
    np.log10(response.THIRDS),
    np.log10([50, 100, 160, 250, 400, 1000, 4000, 8000, 12500, 20000]),
    [-20, -8, 0, 5, 1, 0, -1, -3, -14, -30],
)
"""How the simulated speakers colour the sound, shaped like the Go 4 measured on 2026-10-01
(experimentos/10 §6): weak bass, a bump at 250 Hz and treble falling past 8 kHz. It gives
the equaliser something real to correct."""
_pids = itertools.count(41000)


class Room:
    """What the microphone hears: every speaker's output, delayed and scaled, plus noise."""

    def __init__(self, names: list[str], rate: int) -> None:
        self.rate = rate
        self.delays = {n: int(ROOM_DELAYS_MS[i % len(ROOM_DELAYS_MS)] * rate / 1000) for i, n in enumerate(names)}
        self.gains = {n: ROOM_GAINS[i % len(ROOM_GAINS)] for i, n in enumerate(names)}
        self._tails = {n: np.zeros(d) for n, d in self.delays.items()}
        self._colour = {n: eq.StreamingFIR(eq.fir(ROOM_COLOUR_DB)) for n in names}
        self._pending: list[np.ndarray] = []
        self._lock = threading.Lock()
        self._rng = np.random.default_rng(7)

    def play(self, blocks: dict[str, np.ndarray]) -> None:
        n = len(next(iter(blocks.values())))
        mix = np.zeros(n)
        for name, x in blocks.items():
            joined = np.concatenate([self._tails[name], self._colour[name].process(x)])
            mix += self.gains[name] * joined[:n]
            self._tails[name] = joined[n:]
        mix += ROOM_NOISE * self._rng.standard_normal(n)
        with self._lock:
            self._pending.append(mix)

    def take(self) -> np.ndarray:
        with self._lock:
            pending, self._pending = self._pending, []
        return np.concatenate(pending) if pending else np.zeros(0)


class SimulatedPlayer:
    def __init__(self, sinks: dict[str, str], room: Room) -> None:
        self._by_sink = {sink: name for name, sink in sinks.items()}
        self._pids = {sink: next(_pids) for sink in sinks.values()}
        self.room = room

    @property
    def vivos(self) -> list[str]:
        return list(self._pids)

    @property
    def pids(self) -> dict[str, int]:
        return dict(self._pids)

    def escribir(self, blocks: dict[str, np.ndarray]) -> None:
        self.room.play({self._by_sink[s]: x for s, x in blocks.items() if s in self._pids})

    def mal_ruteados(self) -> dict:
        return {}

    def reparar_ruteo(self) -> dict:
        return {}

    def soltar(self, sink: str) -> None:
        self._pids.pop(sink, None)


class SimulatedInput:
    """The virtual sink, paced to real time: the test signal while a source plays, else nothing."""

    def __init__(self, rate: int, session: SimulatedSession) -> None:
        self.rate = rate
        self.session = session
        self.pid = next(_pids)
        self._signal = probe_signal(20.0)
        self._pos = 0
        self._next = time.monotonic()

    def leer(self, n: int, espera_s: float = 0.05) -> tuple[np.ndarray, np.ndarray] | None:  # noqa: ARG002
        self._next += n / self.rate
        delay = self._next - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        else:
            self._next = time.monotonic()
        source = self.session.source
        if source is None or source.kind == "system":
            return None
        idx = (self._pos + np.arange(n)) % len(self._signal)
        self._pos = (self._pos + n) % len(self._signal)
        block = self._signal[idx]
        return block[:, 0], block[:, 1]


class SimulatedMicrophone:
    """A `MicrofonoContinuo` that records the simulated room instead of a device."""

    def __init__(self, room: Room, rate: int, seconds: float) -> None:
        self.room, self.rate = room, rate
        self._n = int(rate * seconds)
        self._ring = np.zeros(0)
        self.pid: int | None = None

    def __enter__(self) -> Self:
        self.room.take()  # what was played before it opened is not heard
        self.pid = next(_pids)
        return self

    def __exit__(self, *_) -> None:
        self.cerrar()

    def cerrar(self) -> None:
        self.pid = None

    def bombear(self, espera_s: float = 0.0) -> int:  # noqa: ARG002
        x = self.room.take()
        self._ring = np.concatenate([self._ring, x])[-self._n :]
        return len(x)

    def ultimos(self, seconds: float) -> np.ndarray | None:
        n = int(self.rate * seconds)
        if n <= 0 or len(self._ring) < n:
            return None
        return self._ring[-n:].copy()


class SimulatedSource:
    """The `Source` of a simulated session: it only records what was chosen."""

    APPS = ("Firefox", "Spotify")

    def __init__(self) -> None:
        self.kind, self.name, self.error = "tone", None, None
        self.started_at = time.monotonic()
        self.pid: int | None = next(_pids)

    def set(self, kind: str, name: str | None = None) -> None:
        time.sleep(0.3)
        if kind == "app" and name not in self.APPS:
            msg = f"{name!r} is not playing audio now; playing: {list(self.APPS)}"
            raise ValueError(msg)
        if kind == "file" and not name:
            msg = "no such file"
            raise ValueError(msg)
        self.kind, self.name, self.error = kind, name, None
        self.started_at = time.monotonic()
        self.pid = next(_pids) if kind in {"file", "tone"} else None

    def close(self) -> None:
        self.pid = None

    def describe(self) -> dict:
        return {"kind": self.kind, "name": self.name, "pid": self.pid, "error": self.error}


class SimulatedSession(AudioSession):
    def open(self) -> None:
        o = self.options
        time.sleep(0.3)
        self.room = Room(list(self._sinks), o.rate)
        self._player = SimulatedPlayer(self._sinks, self.room)
        self._input = SimulatedInput(o.rate, self)
        self._stack.callback(self._recal_stack.close)
        self.source = self._new_source()
        self._stack.callback(self.source.close)
        if o.recalibrate:
            self.enable_recalibration(o.microphone)

    def _microphone(self, name: str, seconds: float) -> SimulatedMicrophone:  # noqa: ARG002
        return SimulatedMicrophone(self.room, self.options.rate, seconds)

    def _new_source(self) -> SimulatedSource:  # type: ignore[override]
        return SimulatedSource()


class SimulatedObserver:
    """The system view of a machine with the installation's speakers connected, and one more nearby."""

    def __init__(self, installation: Instalacion | None) -> None:
        speakers = installation.parlantes if installation is not None else []
        self._devices = {
            p.sink.removeprefix("bluez_output.").split(".")[0].replace("_", ":"): {
                "name": p.nombre,
                "paired": True,
                "connected": True,
                "modalias": "bluetooth:v0ECBp2063d0100",
                "audio_sink": True,
                "rssi_dbm": None,
                "busy": False,
                "sink": p.sink,
            }
            for p in speakers
        }
        self._devices["F8:5C:7D:00:11:22"] = {
            "name": "JBL Flip 7",
            "paired": False,
            "connected": False,
            "modalias": None,
            "audio_sink": True,
            "rssi_dbm": -61,
            "busy": False,
            "sink": "bluez_output.F8_5C_7D_00_11_22.1",
        }
        self.view: dict[str, Any] = {}
        self._refresh()

    def _refresh(self, scanning: bool = False) -> None:  # noqa: FBT001, FBT002
        now = time.clock_gettime(time.CLOCK_MONOTONIC)
        self.view = {
            "units": {
                "bluetoothd": {"state": "active", "pid": 812, "uptime_s": now},
                "pipewire": {"state": "active", "pid": 1425, "uptime_s": now},
                "wireplumber": {"state": "active", "pid": 1431, "uptime_s": now},
            },
            "devices": [
                {"address": a, **{k: v for k, v in d.items() if k != "sink"}} for a, d in self._devices.items()
            ],
            "outputs": [
                {"sink": d["sink"], "name": d["name"], "codec": "sbc", "address": a}
                for a, d in self._devices.items()
                if d["connected"]
            ],
            "apps": [{"name": n, "sample_spec": "float32le 2ch 48000Hz"} for n in SimulatedSource.APPS],
            "microphones": [
                {"node": "simulado", "description": "Micrófono simulado"},
                {"node": "simulado-2", "description": "Otro micrófono simulado"},
            ],
            "scanning": scanning,
            "at": time.time(),
        }

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def refresh(self) -> None:
        self._refresh()

    def scan(self) -> None:
        self._refresh(scanning=True)
        threading.Timer(2.0, self._refresh).start()

    def connect(self, address: str) -> None:
        if address not in self._devices:
            msg = f"{address} is not near"
            raise RuntimeError(msg)
        self._devices[address].update(paired=True, connected=True)
        self._refresh()

    def disconnect(self, address: str) -> None:
        if address in self._devices:
            self._devices[address]["connected"] = False
        self._refresh()

    def forget(self, address: str) -> None:
        if address in self._devices:
            self._devices[address].update(paired=False, connected=False)
        self._refresh()

    def outputs(self) -> list:
        from aurasync.sonido import SalidaBluetooth  # noqa: PLC0415

        return [SalidaBluetooth(o["sink"], o["name"], o["codec"]) for o in self.view["outputs"]]
