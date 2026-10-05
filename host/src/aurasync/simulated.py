"""A session without speakers: the real motor and the real loop, with PipeWire and the room simulated.

`aurasync service --simular` runs on it, so that the panel can be used and tested in a
browser on any machine. What is real and what is not:

- **real**: the motor, the contract, the service, the recalibration loop and the
  calibration's measurement (`medicion.calibrar`), which run on what the simulated room
  returns;
- **simulated**: the speakers' streams, the virtual sink (it delivers the test signal of
  `sources.py` when a source is chosen), the Bluetooth devices, the system services, and
  the room: each speaker reaches the microphone with a fixed delay and gain plus noise,
  times its Bluetooth volume (`SimulatedVolumes`, what `volume.avrcp` moves);
- **simulated, and marked so**: the radio (`SimulatedRadio` writes journal lines like
  WirePlumber's, with a dropped packet now and then, only while the radio log is "on";
  `state.radio.simulated` is true) and the log level (`simulated_log_level`, which runs no
  `wpctl` and writes its changes file in the simulation's folder).

The panel shows a SIMULADO badge, and `measurement_save` refuses a simulated calibration:
a simulated number must never end up in `docs/research/experimentos/` as MEDIDO.
"""

from __future__ import annotations

import itertools
import re
import subprocess
import threading
import time
from collections import deque
from typing import TYPE_CHECKING, Any, Self

import numpy as np

from aurasync.dsp import eq, response
from aurasync.radio import MONITOR_TOPIC, SINK_TOPIC, LogLevel, RadioMonitor
from aurasync.session import AudioSession
from aurasync.sources import probe_signal

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from aurasync.config import Instalacion

ROOM_DELAYS_MS = (3.0, 7.5, 12.0, 5.0, 9.0, 1.5, 10.5, 6.0)
"""How late each speaker reaches the microphone, in installation order. Eight distinct values,
so an installation of up to 8 (the goal of d-7c8794-3b7793) has no two speakers alike; a ninth
repeats the first."""
ROOM_GAINS = (1.0, 0.8, 0.6, 0.9, 0.7, 0.95, 0.75, 0.85)
"""Each speaker's level at the microphone, distinct for up to 8 (the sixth was 1.0, like the
first, until 2026-10-02)."""
ROOM_NOISE = 0.001
ROOM_BUFFER_S = 30.0
"""The most the room keeps for a microphone that is not reading, like a capture device that
drops what nobody takes. The simulated microphone's ring is ~14 s. Until 2026-10-03 the room
kept every block: ~690 MB in 30 min of `--simular` without the loop."""
ROOM_COLOUR_DB = np.interp(
    np.log10(response.THIRDS),
    np.log10([50, 100, 160, 250, 400, 1000, 4000, 8000, 12500, 20000]),
    [-20, -8, 0, 5, 1, 0, -1, -3, -14, -30],
)
"""How the simulated speakers colour the sound, shaped like the Go 4 measured on 2026-10-01
(experimentos/10 §6): weak bass, a bump at 250 Hz and treble falling past 8 kHz. It gives
the equaliser something real to correct."""
_pids = itertools.count(41000)


class SimulatedVolumes:
    """The speakers' Bluetooth volume (PipeWire percent per sink), for `bt_volume.BluetoothVolume`.

    A speaker takes a volume in AVRCP steps (127), as a Go 4 does; `deaf` sinks ignore it."""

    def __init__(self, start_pct: float = 100.0) -> None:
        self.start_pct = start_pct
        self.percents: dict[str, float] = {}
        self.deaf: set[str] = set()

    def unavailable(self) -> str | None:
        return None

    def set_percent(self, sink: str, percent: float) -> bool:
        if sink not in self.deaf:
            self.percents[sink] = round(max(0.0, min(percent, 100.0)) / 100 * 127) / 127 * 100
        return True

    def get_percent(self, sink: str) -> float | None:
        return self.percents.get(sink, self.start_pct)

    def gain(self, sink: str) -> float:
        """Linear gain of the cubic curve: 80 % is -5.81 dB."""
        return (self.get_percent(sink) / 100) ** 3


class Room:
    """What the microphone hears: every speaker's output, delayed and scaled, plus noise."""

    def __init__(self, names: list[str], rate: int, volume: Callable[[str], float] | None = None) -> None:
        self.rate = rate
        self.volume = volume or (lambda _: 1.0)
        self.delays = {n: int(ROOM_DELAYS_MS[i % len(ROOM_DELAYS_MS)] * rate / 1000) for i, n in enumerate(names)}
        self.gains = {n: ROOM_GAINS[i % len(ROOM_GAINS)] for i, n in enumerate(names)}
        self._tails = {n: np.zeros(d) for n, d in self.delays.items()}
        self._colour = {n: eq.StreamingFIR(eq.fir(ROOM_COLOUR_DB)) for n in names}
        self._pending: deque[np.ndarray] = deque()
        self._pending_n = 0
        self._lock = threading.Lock()
        self._rng = np.random.default_rng(7)

    def play(self, blocks: dict[str, np.ndarray]) -> None:
        n = len(next(iter(blocks.values())))
        mix = np.zeros(n)
        for name, x in blocks.items():
            joined = np.concatenate([self._tails[name], self._colour[name].process(x)])
            mix += self.gains[name] * self.volume(name) * joined[:n]
            self._tails[name] = joined[n:]
        mix += ROOM_NOISE * self._rng.standard_normal(n)
        with self._lock:
            self._pending.append(mix)
            self._pending_n += n
            while self._pending_n > ROOM_BUFFER_S * self.rate:
                self._pending_n -= len(self._pending.popleft())

    def take(self) -> np.ndarray:
        with self._lock:
            pending, self._pending, self._pending_n = list(self._pending), deque(), 0
        return np.concatenate(pending) if pending else np.zeros(0)


class SimulatedPlayer:
    """The real part of a simulated session: only the playing speakers' sinks, so the room
    never hears a virtual or absent one (spec 2026-10-05-virtual-speakers-and-hot-join §4)."""

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

    def cerrar(self) -> None:
        pass


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
    bt_volumes: SimulatedVolumes | None = None
    """Set by the service: the speakers' Bluetooth volume the room applies."""

    def open(self) -> None:
        o = self.options
        time.sleep(0.3)
        volumes = self.bt_volumes if isinstance(self.bt_volumes, SimulatedVolumes) else None
        # Every real speaker is connected in the simulation; a virtual one has no sink and is
        # only computed: neither the room nor the player knows it.
        playing = {n: sink for n, sink in self._sinks.items() if sink is not None}
        self.room = Room(
            list(playing), o.rate, (lambda name: volumes.gain(self._sinks[name])) if volumes is not None else None
        )
        self._stack.callback(self.outputs.close)
        self.outputs.attach(SimulatedPlayer(playing, self.room) if playing else None, set(playing))
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


SIMULATED_BATTERY_PCT = (90, 75, 60, 45)


class SimulatedMonitor:
    """The headphone monitor without PipeWire (monitor.MonitorOutput's shape): it counts what
    it gets and says it reached its target."""

    def __init__(self, settings, names, angles, rate, sink) -> None:  # noqa: ARG002 - the factory's signature
        self.settings = settings
        self.pushed = 0
        self.writer = None

    def open(self) -> None:
        time.sleep(0.05)

    def where(self) -> str | None:
        return self.settings.target

    def push(self, pair, blocks) -> None:  # noqa: ARG002
        self.pushed += 1

    def close(self) -> None:
        pass


class SimulatedObserver:
    """The system view of a machine with the installation's speakers connected, and one more nearby."""

    def __init__(self, installation: Instalacion | None) -> None:
        # A virtual speaker has no device: nothing to pair, connect or list.
        speakers = [p for p in (installation.parlantes if installation is not None else []) if p.sink is not None]
        self._devices = {
            p.sink.removeprefix("bluez_output.").split(".")[0].replace("_", ":"): {
                "name": p.nombre,
                "paired": True,
                "connected": True,
                "modalias": "bluetooth:v0ECBp2063d0100",
                "audio_sink": True,
                "rssi_dbm": None,
                # A battery each, as BlueZ reports a Go 4's (`org.bluez.Battery1`): the panel
                # shows it and warns at 20 % (spec 2026-10-02 §7.3.3).
                "battery_pct": SIMULATED_BATTERY_PCT[i % len(SIMULATED_BATTERY_PCT)],
                "busy": False,
                "sink": p.sink,
            }
            for i, p in enumerate(speakers)
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
            # Every output PipeWire would list: the speakers, the PC's own and headphones (the
            # monitor's candidates, monitor.py).
            "sinks": [
                {"node": "simulated_pc_output", "description": "Salida del PC (simulada)"},
                {"node": "simulated_headphones", "description": "Audífonos (simulados)"},
                *({"node": d["sink"], "description": d["name"]} for d in self._devices.values() if d["connected"]),
            ],
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


# -- the radio and its log level ------------------------------------------------------------

_LEVEL = re.compile(r"\S+")


def simulated_log_level(changes_file: Path) -> LogLevel:
    """A `LogLevel` whose `wpctl` and `pw-metadata` are an in-memory WirePlumber.

    It writes its changes file like the real one (with its reversal), so the panel's flow and
    the kill switch can be tried without a Linux machine; it changes nothing in the system."""
    level: dict[str, str | None] = {"value": None}

    def run(args: list[str], _timeout: float) -> subprocess.CompletedProcess[str]:
        name = args[0].rsplit("/", 1)[-1]
        if name == "wpctl" and args[1:2] == ["set-log-level"]:
            level["value"] = None if args[2] == "-" else args[2]
            return subprocess.CompletedProcess(args, 0, "", "")
        if name == "pw-metadata":
            lines = "update: id:0 key:'log.level' value:'2' type:''\n"
            if level["value"] is not None:
                lines += f"update: id:57 key:'log.level' value:'{level['value']}' type:''\n"
            return subprocess.CompletedProcess(args, 0, lines, "")
        return subprocess.CompletedProcess(args, 1, "", "unknown command")

    log_level = LogLevel(changes_file, run=run, which=lambda name: f"/usr/bin/{name}")
    log_level.simulated = True  # type: ignore[attr-defined]
    return log_level


class SimulatedRadio(RadioMonitor):
    """A `RadioMonitor` fed with journal lines like WirePlumber's instead of `journalctl`.

    While `logging()` is true it writes, every second and per speaker, the healthy
    `increase bitpool`, and now and then (`drop_every_s` on average, per speaker) a
    `reduce bitpool`: a dropped packet. The mapping lines (transport → address, sink →
    transport) come first, as when a speaker starts playing with the log raised.
    """

    simulated = True

    def __init__(
        self,
        sinks: Callable[[], list[str]],
        logging: Callable[[], bool],
        *,
        drop_every_s: float = 45.0,
        seed: int | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._sinks_now = sinks
        self._logging = logging
        self.drop_every_s = drop_every_s
        self._rng = np.random.default_rng(seed)
        self._bitpool: dict[str, int] = {}
        self._pointers: dict[str, tuple[str, str]] = {}
        self._sim_thread: threading.Thread | None = None

    def start(self) -> None:
        if self._sim_thread is not None:
            return
        self._started_at = self.clock()
        self._stopping.clear()
        self._sim_thread = threading.Thread(target=self._simulate, name="aurasync-radio-sim", daemon=True)
        self._sim_thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stopping.set()
        if self._sim_thread is not None:
            self._sim_thread.join(timeout)
            self._sim_thread = None

    def _entry(self, topic: str, message: str) -> dict[str, Any]:
        return {"__REALTIME_TIMESTAMP": str(int(self.clock() * 1e6)), "TOPIC": topic, "MESSAGE": message}

    def tick(self) -> None:
        """One second of journal (the thread calls it; tests can too)."""
        if not self._logging():
            self._pointers.clear()
            return
        for k, sink in enumerate(self._sinks_now()):
            if sink not in self._pointers:
                address = sink.removeprefix("bluez_output.").split(".")[0]
                node, transport = f"0x5a1{k:04x}0", f"0x7f2{k:04x}0"
                self._pointers[sink] = (node, transport)
                path = f"/org/bluez/hci0/dev_{address}/sep1/fd{k + 3}"
                self.feed(self._entry(MONITOR_TOPIC, f"transport {transport}: Acquired {path}, fd 4{k} MTU 895:895"))
                self.feed(self._entry(SINK_TOPIC, f"{node}: transport {transport} state 1->2"))
            node, _ = self._pointers[sink]
            bitpool = self._bitpool.get(sink, 40)
            if self._rng.random() < 1.0 / self.drop_every_s:
                bitpool = max(2, bitpool - 2)
                self.feed(self._entry(SINK_TOPIC, f"{node}: reduce bitpool: {bitpool}"))
            else:
                bitpool = min(40, bitpool + 1)
                self.feed(self._entry(SINK_TOPIC, f"{node}: increase bitpool: {bitpool}"))
            self._bitpool[sink] = bitpool

    def _simulate(self) -> None:
        while not self._stopping.wait(1.0):
            try:
                self.tick()
            except Exception:  # noqa: BLE001 - a simulation must not take the service down
                time.sleep(1.0)
