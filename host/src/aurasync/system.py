"""What the panel shows about the machine itself: system services, Bluetooth devices and the
applications playing audio. Read on a thread of its own, never on the engine thread.

Reading these takes subprocesses (`systemctl`, `busctl`, `pactl`) that can take tens or
hundreds of milliseconds; on the engine thread that would starve the speakers' buffers. So
an `Observer` refreshes them every few seconds and the snapshot reads its last result.

**Bluetooth is read over D-Bus, never with `bluetoothctl`** (2026-10-02). The observer used
to run `bluetoothctl devices` and one `bluetoothctl info` per device every 3 s: ten clients a
cycle, and each one registers an LE advertisement monitor with `bluetoothd` (the journal
logged ~14 500 "Path / reserved for Adv Monitor app" in 80 minutes). A monitor can make the
controller scan, and a scan takes radio time from the A2DP links (INFERIDO; the suspect of
the cuts in experimentos/10 §9). One read-only `GetManagedObjects` gives the same fields in
~3 ms and registers nothing. `bluetoothctl` stays for what the person asks: connect,
disconnect, pair, search, forget.

The panel **only observes** `bluetoothd`, `pipewire` and `wireplumber`: it never starts or
stops them (minimal footprint, `docs/research/08` §2). Connecting or disconnecting a speaker
is the one action it takes on the system, and it goes through `bluetoothctl` on a worker
thread so that a slow connection never blocks the audio.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import threading
import time
from typing import TYPE_CHECKING, Any

from aurasync import monitor, sonido
from aurasync.sources import list_apps

if TYPE_CHECKING:
    from collections.abc import Callable

log = logging.getLogger("aurasync.svc.bluetooth")
AUDIO_SINK_UUID = "0000110b"
SYSTEM_UNITS = (
    ("bluetoothd", "bluetooth", False),
    ("pipewire", "pipewire", True),
    ("wireplumber", "wireplumber", True),
)


def _run(args: list[str], timeout: float = 5.0) -> str:
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


def parse_info(text: str) -> dict[str, Any]:
    """The fields of `bluetoothctl info` this panel uses."""

    def field(name: str) -> str | None:
        m = re.search(rf"^\s*{name}: (.*)$", text, re.MULTILINE)
        return m.group(1).strip() if m else None

    rssi = field("RSSI")
    rssi_value = None
    if rssi:
        m = re.search(r"-?\d+", rssi.split("(")[-1] if "(" in rssi else rssi)
        rssi_value = int(m.group(0)) if m else None
    return {
        "name": field("Name") or field("Alias"),
        "paired": field("Paired") == "yes",
        "connected": field("Connected") == "yes",
        "modalias": field("Modalias"),
        "audio_sink": AUDIO_SINK_UUID in text.lower(),
        "rssi_dbm": rssi_value,
    }


def parse_bluez_objects(text: str) -> list[dict[str, Any]]:
    """The audio devices in BlueZ's `GetManagedObjects`, as `busctl --json=short` prints it."""
    try:
        objects = json.loads(text)["data"][0]
    except (json.JSONDecodeError, KeyError, IndexError, TypeError):
        return []
    devices = []
    for interfaces in objects.values():
        device = interfaces.get("org.bluez.Device1")
        if not device:
            continue
        value = {k: v.get("data") for k, v in device.items()}
        battery = (interfaces.get("org.bluez.Battery1") or {}).get("Percentage", {}).get("data")
        uuids = [str(u).lower() for u in value.get("UUIDs") or []]
        if not any(u.startswith(AUDIO_SINK_UUID) for u in uuids):
            continue
        devices.append(
            {
                "address": value.get("Address"),
                "name": value.get("Name") or value.get("Alias"),
                "paired": bool(value.get("Paired")),
                "connected": bool(value.get("Connected")),
                "trusted": bool(value.get("Trusted")),
                "modalias": value.get("Modalias"),
                "audio_sink": True,
                "rssi_dbm": value.get("RSSI"),
                "battery_pct": battery,
            }
        )
    return sorted(devices, key=lambda d: (not d["connected"], str(d["name"]).lower()))


def parse_bluez_discovering(text: str) -> bool:
    """Whether an adapter is searching (discovery takes radio time from the A2DP links)."""
    try:
        objects = json.loads(text)["data"][0]
    except (json.JSONDecodeError, KeyError, IndexError, TypeError):
        return False
    return any((i.get("org.bluez.Adapter1") or {}).get("Discovering", {}).get("data") is True for i in objects.values())


def _bluez_objects() -> str:
    return _run(
        [
            "busctl",
            "--system",
            "--json=short",
            "call",
            "org.bluez",
            "/",
            "org.freedesktop.DBus.ObjectManager",
            "GetManagedObjects",
        ],
        3,
    )


def read_bluez_devices() -> list[dict[str, Any]]:
    return parse_bluez_objects(_bluez_objects())


def unit_object_path(unit: str) -> str:
    """The D-Bus path systemd gives a unit: every byte that is not alphanumeric becomes `_xx`.

    A name without a type is a service, as `systemctl` reads it.
    """
    if "." not in unit:
        unit += ".service"
    escaped = "".join(c if c.isascii() and c.isalnum() else f"_{ord(c):02x}" for c in unit)
    return f"/org/freedesktop/systemd1/unit/{escaped}"


def parse_busctl_value(text: str) -> str:
    """`s "active"` → `active`: the value of a `busctl get-property` without its type letter."""
    _, _, value = text.strip().partition(" ")
    return value.strip('"')


def system_unit(unit: str, *, user: bool) -> dict[str, Any]:
    # busctl and not systemctl: `systemctl --user` needs the manager's private socket, which a
    # container does not see, while the session bus does reach it (docs/research/08 §6.2).
    bus = "--user" if user else "--system"
    path = unit_object_path(unit)

    def prop(interface: str, name: str) -> str:
        cmd = [
            "busctl",
            bus,
            "get-property",
            "org.freedesktop.systemd1",
            path,
            f"org.freedesktop.systemd1.{interface}",
            name,
        ]
        return parse_busctl_value(_run(cmd, 2))

    state = prop("Unit", "ActiveState") or "unknown"
    pid = int(prop("Service", "MainPID") or 0) or None
    since_us = int(prop("Unit", "ActiveEnterTimestampMonotonic") or 0)
    uptime = time.clock_gettime(time.CLOCK_MONOTONIC) - since_us / 1e6 if since_us else None
    return {"state": state, "pid": pid, "uptime_s": uptime}


def parse_xruns(top: str, dump: list) -> dict[str, int]:
    """ERR de `pw-top` (cortes por falta de datos), por destino.

    Cuenta tres cosas, cada una con su clave: el stream de cada `pw-play`, por su destino;
    cada salida del sink combinado hacia un parlante, por el sink del parlante; y el propio
    nodo Bluetooth de cada parlante (`bluez_output.*`). Con salida combinada hay un solo
    `pw-play`, así que sin las otras dos no se sabría **qué parlante** cortó.

    `pw-top` lista cada nodo dos veces por vuelta; se queda con la última vuelta y el máximo.
    """
    target = {}
    for o in dump:
        props = (o.get("info") or {}).get("props") or {}
        name = str(props.get("node.name", ""))
        if props.get("application.name") == "pw-play" and props.get("target.object"):
            target[str(o["id"])] = str(props["target.object"])
        elif name.startswith("output.aurasync_salida_"):
            target[str(o["id"])] = "stream:" + name.removeprefix("output.aurasync_salida_")
        elif name.startswith("bluez_output.") and str(o.get("type", "")).endswith("Node"):
            target[str(o["id"])] = "bt:" + name
    lines = top.splitlines()
    start = max((i for i, line in enumerate(lines) if line.lstrip().startswith("S   ID")), default=0)
    found: dict[str, int] = {}
    for line in lines[start:]:
        parts = line.split()
        if len(parts) < 9 or not parts[1].isdigit() or parts[1] not in target:  # noqa: PLR2004
            continue
        try:
            found[target[parts[1]]] = max(int(parts[8]), found.get(target[parts[1]], 0))
        except ValueError:
            continue
    return found


class Observer:
    """Refreshes the system view every `period_s` on its own thread."""

    def __init__(self, period_s: float = 3.0, *, enabled: bool = True) -> None:
        self.period_s = period_s
        self.enabled = enabled
        self.view: dict[str, Any] = {
            "units": {},
            "devices": [],
            "outputs": [],
            "apps": [],
            "microphones": [],
            "scanning": False,
            "at": None,
        }
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._busy: set[str] = set()
        self._xrun_history: list[tuple[float, dict[str, int]]] = []

    def start(self) -> None:
        if not self.enabled or self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="aurasync-observer", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def refresh(self) -> None:
        self._wake.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.view = {**self.view, **self.read()}
            except Exception:
                log.exception("could not read the system state")
            self._wake.wait(self.period_s)
            self._wake.clear()

    def read(self) -> dict[str, Any]:
        units = {name: system_unit(unit, user=user) for name, unit, user in SYSTEM_UNITS}
        bluez = _bluez_objects()
        devices = [{**d, "busy": d["address"] in self._busy} for d in parse_bluez_objects(bluez)]
        outputs = [
            {"sink": o.nodo, "name": o.descripcion, "codec": o.codec, "address": o.direccion}
            for o in sonido.salidas_bluetooth()
        ]
        return {
            "units": units,
            "devices": devices,
            "outputs": outputs,
            "discovering": parse_bluez_discovering(bluez),
            "apps": list_apps(),
            "microphones": [
                {"node": e.nodo, "description": e.descripcion} for e in sonido.entradas_audio() if not e.es_monitor
            ],
            "sinks": monitor.list_sinks(sonido._pw_dump()),  # noqa: SLF001 - the graph's one reader
            "xruns": self._read_xruns(),
            "at": time.time(),
        }

    def _read_xruns(self) -> dict[str, dict]:
        """Cortes de cada stream hacia los parlantes: el total y cuántos por minuto ahora.

        Es la medida de calidad más directa que da el sistema: cada xrun es un hueco o un
        salto en lo que suena. Así se encontró, con ~10 por segundo, la causa del audio
        degradado del 2026-10-01 (experimentos/10 §6).
        """
        counts = parse_xruns(_run(["pw-top", "-b", "-n", "2"], 8), sonido._pw_dump())  # noqa: SLF001
        now = time.monotonic()
        self._xrun_history = [(t, c) for t, c in self._xrun_history if now - t <= 60.0] + [(now, counts)]  # noqa: PLR2004
        oldest_t, oldest = self._xrun_history[0]
        span = now - oldest_t
        result = {}
        for target, total in counts.items():
            before = oldest.get(target)
            rate = (total - before) / span * 60 if before is not None and span >= 5 and total >= before else None  # noqa: PLR2004
            result[target] = {"total": total, "per_min": round(rate, 1) if rate is not None else None}
        return result

    def outputs(self) -> list[sonido.SalidaBluetooth]:
        """The Bluetooth outputs PipeWire has now (read fresh: adding a speaker needs it)."""
        return sonido.salidas_bluetooth()

    # -- actions, on a worker thread ------------------------------------------------

    def _in_background(self, address: str, what: str, job: Callable[[], str]) -> None:
        if address in self._busy:
            msg = f"{address} is already busy"
            raise RuntimeError(msg)
        self._busy.add(address)

        def run() -> None:
            try:
                log.info("%s %s…", what, address)
                result = job()
                log.info("%s %s: %s", what, address, result.strip().splitlines()[-1] if result.strip() else "done")
            except Exception:
                log.exception("%s %s failed", what, address)
            finally:
                self._busy.discard(address)
                self.refresh()

        threading.Thread(target=run, name=f"aurasync-bt-{what}", daemon=True).start()
        self.refresh()

    def connect(self, address: str) -> None:
        def job() -> str:
            known = {d["address"]: d for d in read_bluez_devices()}
            out = ""
            if not known.get(address, {}).get("paired"):
                out += _run(["bluetoothctl", "--timeout", "20", "pair", address], 25)
                out += _run(["bluetoothctl", "trust", address], 5)
            return out + _run(["bluetoothctl", "--timeout", "20", "connect", address], 25)

        self._in_background(address, "connect", job)

    def disconnect(self, address: str) -> None:
        self._in_background(address, "disconnect", lambda: _run(["bluetoothctl", "disconnect", address], 10))

    def forget(self, address: str) -> None:
        """Remove the pairing. To use the device again it has to be paired from scratch."""
        self._in_background(address, "forget", lambda: _run(["bluetoothctl", "remove", address], 10))

    def scan(self, seconds: float = 8.0) -> None:
        """Discovery for `seconds`, so that nearby speakers in pairing mode show up."""
        if self.view.get("scanning"):
            return

        def run() -> None:
            self.view = {**self.view, "scanning": True}
            log.info("searching for speakers for %.0f s…", seconds)
            _run(["bluetoothctl", "--timeout", str(int(seconds)), "scan", "on"], seconds + 5)
            self.view = {**self.view, **self.read(), "scanning": False}
            log.info("search finished: %d audio devices known", len(self.view["devices"]))

        threading.Thread(target=run, name="aurasync-scan", daemon=True).start()
