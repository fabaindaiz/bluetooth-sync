"""What the Bluetooth radio does to the sound: the packets PipeWire's A2DP sink throws away.

The cuts the engine can see (`cuts.py`) all happen *before* PipeWire. The most likely cause
of the microcuts happens after it: when a speaker's link cannot take more data, the socket
returns `EAGAIN` and PipeWire's bluez5 sink **skips the packet** ("There will be a sound
glitch in any case") and lowers the SBC bitpool by 2, logging `reduce bitpool` at most once
every 0.5 s. A link with no error for a second raises it by 1 and logs `increase bitpool`:
that is the healthy heartbeat, not congestion. Source: PipeWire 1.6.9,
`spa/plugins/bluez5/media-sink.c` `flush_data()`, lines 1082-1099 and 1159-1166 (spec
2026-10-02 §2; `probes/14-microcortes/README.md` has every line this module reads).

Those lines are `debug` level, so they only exist while the log level of the bluez5 topics
is raised (`LogLevel`), and they live in the journal of the process that hosts the bluez5
nodes: **WirePlumber** (it creates them with `LocalNode("adapter", …)`).

`%p` in those lines is the sink's own object, which no `pw-dump` property shows (the native
protocol blanks every `pointer:` value). It is tied to a speaker through two other debug
lines: the sink's `"%p: transport %p state %d->%d"` (sink → transport) and the bluez5
monitor's `"transport %p: %s state changed …"` / `"transport %p: Acquired %s, …"` (transport
→ D-Bus path `/org/bluez/hciN/dev_AA_BB_CC_DD_EE_FF/…`). Both are printed when a transport
becomes active, so the mapping exists only if the log was raised **before** the speaker
started playing. Without it the drops are kept per pointer ("enlace sin identificar").

Unavailable is a state, not an error: without `journalctl` (the Mac), with the log level
not raised, or with nothing playing, `available` is false and `reason` says why.
"""

from __future__ import annotations

import contextlib
import json
import logging
import re
import shutil
import statistics
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from aurasync.config import ruta_por_defecto

if TYPE_CHECKING:
    from collections.abc import Callable

    from aurasync.cuts import CutLog

log = logging.getLogger("aurasync.radio")

SINK_TOPIC = "spa.bluez5.sink.media"
"""`media-sink.c` line 44. The bitpool lines and the sink → transport line."""
MONITOR_TOPIC = "spa.bluez5"
"""`bluez5-dbus.c` line 47. The transport → D-Bus path lines."""
WINDOW_S = 60.0
SILENCE_S = 15.0
"""With audio flowing, each sink logs `increase bitpool` about once a second."""
MIN_RATE_SPAN_S = 10.0
"""Below this, a per-minute rate would be extrapolated from too little."""

_PTR = r"(0x[0-9a-fA-F]+|\(nil\))"
# media-sink.c:1090  spa_log_debug(this->log, "%p: reduce bitpool: %i", this, res);
_REDUCE = re.compile(_PTR + r": reduce bitpool: (-?\d+)")
# media-sink.c:1164  spa_log_debug(this->log, "%p: increase bitpool: %i", this, res);
_INCREASE = re.compile(_PTR + r": increase bitpool: (-?\d+)")
# media-sink.c:2462  spa_log_debug(this->log, "%p: transport %p state %d->%d", this, this->transport, old, state);
_SINK_TRANSPORT = re.compile(_PTR + r": transport " + _PTR + r" state (-?\d+)->(-?\d+)")
# bluez5-dbus.c:3309  "transport %p: %s state changed %d -> %d", transport, transport->path, old, state
_TRANSPORT_STATE = re.compile(r"transport " + _PTR + r": (/\S+) state changed (-?\d+) -> (-?\d+)")
# bluez5-dbus.c:4181  "transport %p: Acquired %s, fd %d MTU %d:%d", transport, path, fd, read_mtu, write_mtu
_ACQUIRED = re.compile(r"transport " + _PTR + r": (?:linked )?Acquired (/[^,\s]+), fd -?\d+ MTU (\d+):(\d+)")
# media-sink.c:1332  spa_log_warn(this->log, "%p: connection (%s) terminated unexpectedly", this, path);
_TERMINATED = re.compile(_PTR + r": connection \(([^)]*)\) terminated unexpectedly")
_ADDRESS = re.compile(r"/dev_([0-9A-Fa-f]{2}(?:_[0-9A-Fa-f]{2}){5})")
# WirePlumber's journal MESSAGE for a debug-enabled topic (wireplumber lib/wp/log.c,
# wp_log_fields_write_to_journal): "D spa.bluez5.sink.media[media-sink.c:1090:flush_data]: …";
# otherwise "topic: …". The TOPIC field carries the same name and is preferred.
_TOPIC_IN_MESSAGE = re.compile(r"^(?:[FEWNIDT] )?([a-z][\w.\-]*)(?:\[[^\]]*\])?: ")


@dataclass(frozen=True)
class RadioEvent:
    """One journal line that matters to the radio.

    `kind`: `reduce` (a dropped packet), `increase` (the heartbeat), `sink_transport`
    (sink → transport), `transport_path` (transport → D-Bus path), `terminated` (a link
    closed by the speaker; carries the sink and the path together).
    """

    kind: str
    t: float
    """Wall-clock seconds, from the journal's `__REALTIME_TIMESTAMP`."""
    sink: str | None = None
    transport: str | None = None
    path: str | None = None
    address: str | None = None
    bitpool: int | None = None
    write_mtu: int | None = None
    topic: str | None = None
    message: str = ""


def _message(entry: dict[str, Any]) -> str:
    raw = entry.get("MESSAGE")
    if isinstance(raw, list):  # journalctl -o json prints non-UTF-8 messages as a byte array
        return bytes(b & 0xFF for b in raw if isinstance(b, int)).decode("utf-8", "replace")
    return raw if isinstance(raw, str) else ""


def _time(entry: dict[str, Any]) -> float | None:
    try:
        return int(entry["__REALTIME_TIMESTAMP"]) / 1e6
    except (KeyError, TypeError, ValueError):
        return None


def _topic(entry: dict[str, Any], message: str) -> str | None:
    topic = entry.get("TOPIC")
    if isinstance(topic, str) and topic:
        return topic
    m = _TOPIC_IN_MESSAGE.match(message)
    return m.group(1) if m else None


def address_of(path: str | None) -> str | None:
    """`/org/bluez/hci0/dev_88_92_CC_68_91_C0/sep1/fd3` → `88:92:CC:68:91:C0`."""
    m = _ADDRESS.search(path or "")
    return m.group(1).replace("_", ":").upper() if m else None


def _pointer(p: str) -> str | None:
    return None if p == "(nil)" else p.lower()


def parse(entry: dict[str, Any], *, now: float | None = None) -> RadioEvent | None:
    """One `journalctl -o json` entry → a `RadioEvent`, or None if it is not about the radio.

    The sink lines are only taken from the sink's topic when the topic is known: the A2DP
    *source* (`media-source.c:2047`) prints the same `transport %p state` line.
    """
    message = _message(entry)
    if "bitpool" not in message and "transport" not in message and "terminated" not in message:
        return None
    t = _time(entry)
    t = t if t is not None else (now if now is not None else time.time())
    topic = _topic(entry, message)
    sink_ok = topic is None or topic == SINK_TOPIC
    base = {"t": t, "topic": topic, "message": message}
    if sink_ok and (m := _REDUCE.search(message)):
        value = int(m.group(2))
        # A codec without reduce_bitpool (AAC…) logs 0: the packet was dropped all the same.
        return RadioEvent("reduce", sink=_pointer(m.group(1)), bitpool=value if value > 0 else None, **base)
    if sink_ok and (m := _INCREASE.search(message)):
        value = int(m.group(2))
        return RadioEvent("increase", sink=_pointer(m.group(1)), bitpool=value if value > 0 else None, **base)
    if sink_ok and (m := _SINK_TRANSPORT.search(message)):
        return RadioEvent("sink_transport", sink=_pointer(m.group(1)), transport=_pointer(m.group(2)), **base)
    if sink_ok and (m := _TERMINATED.search(message)):
        path = m.group(2) or None
        return RadioEvent("terminated", sink=_pointer(m.group(1)), path=path, address=address_of(path), **base)
    monitor_ok = topic is None or topic == MONITOR_TOPIC
    if monitor_ok and (m := _ACQUIRED.search(message)):
        path = m.group(2)
        return RadioEvent(
            "transport_path",
            transport=_pointer(m.group(1)),
            path=path,
            address=address_of(path),
            write_mtu=int(m.group(4)),
            **base,
        )
    if monitor_ok and (m := _TRANSPORT_STATE.search(message)):
        path = m.group(2)
        return RadioEvent("transport_path", transport=_pointer(m.group(1)), path=path, address=address_of(path), **base)
    return None


@dataclass(frozen=True)
class Drop:
    """A packet the radio threw away: one audible cut of ~24-40 ms (INFERIDO, spec §2)."""

    speaker: str
    """The speaker's name, its address if it has no name, or the sink pointer."""
    identified: bool
    address: str | None
    sink: str | None
    bitpool: int | None
    t: float
    """Wall-clock seconds."""


@dataclass
class _Link:
    key: str
    identified: bool
    address: str | None = None
    sinks: set[str] = field(default_factory=set)
    bitpool: int | None = None
    bitpool_max: int | None = None
    samples: deque[tuple[float, int]] = field(default_factory=deque)
    drops: deque[float] = field(default_factory=deque)
    drops_total: int = 0
    last_drop_t: float | None = None
    write_mtu: int | None = None

    def merge(self, other: _Link) -> None:
        self.sinks |= other.sinks
        self.drops_total += other.drops_total
        self.drops = deque(sorted([*self.drops, *other.drops]))
        self.samples = deque(sorted([*self.samples, *other.samples]))
        self.last_drop_t = max(filter(None, (self.last_drop_t, other.last_drop_t)), default=None)
        if other.bitpool is not None:
            self.bitpool = other.bitpool
        maxes = [b for b in (self.bitpool_max, other.bitpool_max) if b is not None]
        self.bitpool_max = max(maxes) if maxes else None
        self.write_mtu = self.write_mtu or other.write_mtu


def to_cutlog(cuts: CutLog) -> Callable[[Drop], None]:
    """The `on_drop` that writes each drop to the cut log as kind `radio` (spec §3.1)."""

    def add(drop: Drop) -> None:
        where = drop.speaker if drop.identified else f"enlace sin identificar {drop.speaker}"
        detail = f"bitpool {drop.bitpool}" if drop.bitpool is not None else "paquete descartado"
        cuts.add("radio", where, detail, identified=drop.identified, address=drop.address, bitpool=drop.bitpool)

    return add


class RadioMonitor:
    """Follows the journal on a thread of its own and keeps the state of each link.

    - `on_drop(Drop)` is called on the monitor's thread for every `reduce bitpool`;
      `to_cutlog(cuts)` makes one that feeds `CutLog`. An exception in it is logged and
      the monitor goes on.
    - `speaker_name(address) -> name | None` names a link (`AA:BB:…`); without it, or when
      it returns None, the link is called by its address.
    - `clock` is wall-clock time (`time.time`): the journal's timestamps are wall-clock.
    - Lines older than `start()` (the last `backfill_lines`) only teach the mapping: the
      pointer → speaker lines are printed when playback starts, maybe long before.
    """

    def __init__(
        self,
        *,
        on_drop: Callable[[Drop], None] | None = None,
        speaker_name: Callable[[str], str | None] | None = None,
        clock: Callable[[], float] = time.time,
        units: tuple[str, ...] = ("wireplumber", "pipewire"),
        backfill_lines: int = 2000,
        silence_s: float = SILENCE_S,
        which: Callable[[str], str | None] = shutil.which,
        popen: Callable[..., Any] = subprocess.Popen,
    ) -> None:
        self.on_drop = on_drop
        self.speaker_name = speaker_name
        self.clock = clock
        self.units = units
        self.backfill_lines = backfill_lines
        self.silence_s = silence_s
        self._which = which
        self._popen = popen
        self._lock = threading.Lock()
        self._links: dict[str, _Link] = {}
        self._sink_transport: dict[str, str] = {}
        self._sink_address: dict[str, str] = {}
        self._transport_address: dict[str, str] = {}
        self._transport_mtu: dict[str, int] = {}
        self._started_at: float | None = None
        self._last_line_t: float | None = None
        self._lines = 0
        self._failure: str | None = None
        self._process: Any = None
        self._thread: threading.Thread | None = None
        self._stopping = threading.Event()

    # -- life cycle ---------------------------------------------------------------

    def command(self, journalctl: str = "journalctl") -> list[str]:
        units = [arg for unit in self.units for arg in ("-u", unit)]
        return [journalctl, "--user", "-f", "-o", "json", "-n", str(self.backfill_lines), *units]

    def start(self) -> None:
        """Starts following. Never blocks: the subprocess is launched on the thread."""
        if self._thread is not None:
            return
        self._started_at = self.clock()
        journalctl = self._which("journalctl")
        if journalctl is None:
            self._failure = "este equipo no tiene journalctl: el registro de radio solo existe en Linux con systemd"
            return
        self._stopping.clear()
        self._thread = threading.Thread(target=self._follow, args=(journalctl,), name="aurasync-radio", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        """Stops following; safe to call more than once, and before `start()`."""
        self._stopping.set()
        process = self._process
        if process is not None:
            try:
                process.terminate()
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                process.kill()
            except OSError:
                pass
        if self._thread is not None:
            self._thread.join(timeout)
            self._thread = None

    def _follow(self, journalctl: str) -> None:
        try:
            self._process = self._popen(
                self.command(journalctl),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
        except OSError as error:
            self._failure = f"no se pudo leer el journal: {error}"
            return
        for line in self._process.stdout:
            if self._stopping.is_set():
                break
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(entry, dict):
                self.feed(entry)
        if not self._stopping.is_set():
            error = ""
            with contextlib.suppress(OSError, ValueError, AttributeError):
                error = (self._process.stderr.read() or "").strip()[-300:]
            self._failure = f"journalctl terminó: {error or 'sin mensaje'}"

    # -- state --------------------------------------------------------------------

    def feed(self, entry: dict[str, Any]) -> RadioEvent | None:
        """Takes one journal entry (the thread calls this; tests too)."""
        event = parse(entry, now=self.clock())
        if event is None:
            return None
        drop = None
        with self._lock:
            live = self._started_at is None or event.t >= self._started_at - 1.0
            if live:
                self._last_line_t = max(event.t, self._last_line_t or event.t)
                self._lines += 1
            drop = self._apply(event, live=live)
        if drop is not None and self.on_drop is not None:
            try:
                self.on_drop(drop)
            except Exception:
                log.exception("on_drop failed")
        return event

    def _apply(self, event: RadioEvent, *, live: bool) -> Drop | None:
        if event.kind == "transport_path" and event.transport and event.address:
            self._transport_address[event.transport] = event.address
            if event.write_mtu:
                self._transport_mtu[event.transport] = event.write_mtu
            self._rekey()
        elif event.kind == "sink_transport" and event.sink and event.transport:
            self._sink_transport[event.sink] = event.transport
            self._rekey()
        elif event.kind == "terminated" and event.sink and event.address:
            self._sink_address[event.sink] = event.address
            self._rekey()
        if event.kind not in {"reduce", "increase"} or not event.sink or not live:
            return None
        link = self._link_for(event.sink)
        if event.bitpool is not None:
            link.bitpool = event.bitpool
            link.bitpool_max = max(event.bitpool, link.bitpool_max or 0)
            link.samples.append((event.t, event.bitpool))
        if event.kind == "increase":
            return None
        link.drops.append(event.t)
        link.drops_total += 1
        link.last_drop_t = event.t
        return Drop(link.key, link.identified, link.address, event.sink, event.bitpool, event.t)

    def _address(self, sink: str) -> str | None:
        transport = self._sink_transport.get(sink)
        if transport and transport in self._transport_address:
            return self._transport_address[transport]
        return self._sink_address.get(sink)

    def _name(self, address: str) -> str:
        name = None
        if self.speaker_name is not None:
            try:
                name = self.speaker_name(address)
            except Exception:
                log.exception("speaker_name failed")
        return name or address

    def _link_for(self, sink: str) -> _Link:
        address = self._address(sink)
        key = self._name(address) if address else sink
        link = self._links.get(key)
        if link is None:
            link = self._links[key] = _Link(key=key, identified=address is not None, address=address)
        link.sinks.add(sink)
        transport = self._sink_transport.get(sink)
        if transport in self._transport_mtu:
            link.write_mtu = self._transport_mtu[transport]
        return link

    def _rekey(self) -> None:
        """Moves what was counted per pointer to its speaker once the mapping appears."""
        for key, link in list(self._links.items()):
            if link.identified:
                continue
            sink = next((s for s in sorted(link.sinks) if self._address(s) is not None), None)
            if sink is None:
                continue
            del self._links[key]
            target = self._link_for(sink)
            target.merge(link)

    @property
    def available(self) -> bool:
        return self.snapshot()["available"]

    @property
    def reason(self) -> str | None:
        return self.snapshot()["reason"]

    def snapshot(self) -> dict[str, Any]:
        """The `radio` event of spec §6.3, plus a few fields for the Cortes card."""
        now = self.clock()
        with self._lock:
            available, reason = self._availability(now)
            span = now - self._started_at if self._started_at is not None else 0.0
            speakers = {key: self._link_view(link, now, span) for key, link in sorted(self._links.items())}
            lines = self._lines
        return {
            "available": available,
            "reason": reason,
            "since_s": round(span, 1),
            "lines": lines,
            "speakers": speakers,
        }

    def _availability(self, now: float) -> tuple[bool, str | None]:
        if self._failure:
            return False, self._failure
        if self._started_at is None:
            return False, "el monitor de radio no está corriendo"
        if self._last_line_t is None:
            if now - self._started_at < self.silence_s:
                return False, "esperando las primeras líneas de bluez5 en el journal"
            return False, (
                f"no llegó ninguna línea de bluez5 en {now - self._started_at:.0f} s: "
                "el registro de radio está apagado o no suena nada"
            )
        if now - self._last_line_t > self.silence_s:
            return False, (
                f"sin líneas de bluez5 hace {now - self._last_line_t:.0f} s: "
                "se apagó el registro de radio o dejó de sonar"
            )
        return True, None

    def _link_view(self, link: _Link, now: float, span: float) -> dict[str, Any]:
        while link.drops and now - link.drops[0] > WINDOW_S:
            link.drops.popleft()
        while link.samples and now - link.samples[0][0] > WINDOW_S:
            link.samples.popleft()
        window = min(WINDOW_S, span)
        rate = len(link.drops) / window * 60.0 if window >= MIN_RATE_SPAN_S else None
        median = statistics.median(b for _, b in link.samples) if link.samples else None
        return {
            "identified": link.identified,
            "address": link.address,
            "sinks": sorted(link.sinks),
            "bitpool": link.bitpool,
            "bitpool_max": link.bitpool_max,
            "bitpool_median_60s": median,
            "drops_total": link.drops_total,
            "drops_60s": len(link.drops),
            "drops_per_min": round(rate, 2) if rate is not None else None,
            "last_drop_s": round(now - link.last_drop_t, 1) if link.last_drop_t is not None else None,
            "write_mtu": link.write_mtu,
        }


# -- the log level: a system change, written down before it is made --------------------

LIGHT = "spa.bluez5.sink.media:D,spa.bluez5:D"
"""Debug only for the two topics the monitor reads. Our patterns go **first**: WirePlumber
(g_pattern) and PipeWire (fnmatch) both take the first pattern that matches a topic."""
HEAVY = "4"
"""Everything at debug: journald's rate limit may then drop lines (flagged `heavy`)."""
_METADATA_LINE = re.compile(r"update: id:(\d+) key:'log\.level' value:'([^']*)'")
_TAG = "[aurasync radio_log]"
_CHANGE_LINE = re.compile(re.escape(_TAG) + r" cambio temporal: .*?\(revertir: (.+)\)\s*$")
_REVERTED_LINE = re.compile(re.escape(_TAG) + r" revertido")


def default_changes_file() -> Path:
    """`<config>/cambios-de-sistema.txt`, next to `instalacion.json`."""
    return ruta_por_defecto().parent / "cambios-de-sistema.txt"


def _run(args: list[str], timeout: float) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None


class LogLevel:
    """Raises WirePlumber's log level for the bluez5 topics, and puts it back.

    How (VERIFICADO in the code, `probes/14-microcortes/README.md` §2): `wpctl
    set-log-level "<patterns>"` writes `log.level` in the `settings` metadata for each
    WirePlumber client; WirePlumber's `module-log-settings` applies it with
    `wp_log_set_level()`, which accepts `[<glob>:]<level>,…` and passes the patterns on to
    PipeWire's `pw_log_set_level_string()`, which re-levels the bluez5 plugin's topics.
    `wpctl set-log-level -` deletes the key, and WirePlumber goes back to level "2".

    **Before** running anything it appends the change and how to revert it to
    `changes_file` (CLAUDE.md). `enable()` is idempotent; `restore()` is safe to call any
    number of times (shutdown, SIGTERM, a failed session). `recover()` reverts a change a
    previous run left behind (a SIGKILL cannot be caught). Each call runs at most three
    short subprocesses with a timeout; from the engine thread use the `*_in_background`
    versions.
    """

    def __init__(
        self,
        changes_file: Path | None = None,
        *,
        run: Callable[[list[str], float], subprocess.CompletedProcess[str] | None] = _run,
        which: Callable[[str], str | None] = shutil.which,
        now: Callable[[], datetime] = lambda: datetime.now().astimezone(),
        timeout: float = 3.0,
    ) -> None:
        self.changes_file = changes_file
        self._run = run
        self._which = which
        self._now = now
        self.timeout = timeout
        self._lock = threading.Lock()
        self.mode: str | None = None
        """None (not raised by us), "light" or "heavy"."""
        self.previous: str | None = None
        self.revert_command: list[str] | None = None
        self.verified: bool | None = None
        self.error: str | None = None
        self.pending = False

    # -- what the system has ---------------------------------------------------------

    def _tool(self, name: str) -> str | None:
        return self._which(name)

    def read_current(self) -> str | None:
        """The `log.level` WirePlumber's clients have in the `settings` metadata (None: unset)."""
        tool = self._tool("pw-metadata")
        if tool is None:
            return None
        result = self._run([tool, "-n", "settings"], self.timeout)
        if result is None:
            return None
        for subject, value in _METADATA_LINE.findall(result.stdout or ""):
            if subject != "0":  # 0 is the PipeWire server; the bluez5 nodes live in WirePlumber
                return value
        return None

    # -- changes ------------------------------------------------------------------------

    def _write_change(self, text: str) -> None:
        path = self.changes_file or default_changes_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        stamp = self._now().isoformat(timespec="seconds")
        with path.open("a", encoding="utf-8") as f:
            f.write(f"{stamp} · {_TAG} {text}\n")
            f.flush()

    def enable(self, mode: str = "light") -> dict[str, Any]:
        if mode not in {"light", "heavy"}:
            msg = f"unknown mode {mode!r}"
            raise ValueError(msg)
        with self._lock:
            if self.mode == mode:
                return self.status()
            wpctl = self._tool("wpctl")
            if wpctl is None:
                self.error = "este equipo no tiene wpctl (WirePlumber)"
                return self.status()
            if self.mode is None:
                self.previous = self.read_current()
                self.revert_command = [wpctl, "set-log-level", self.previous if self.previous else "-"]
            base = self.previous if self.previous and mode == "light" else "2"
            level = f"{LIGHT},{base}" if mode == "light" else HEAVY
            command = [wpctl, "set-log-level", level]
            revert = " ".join(self._shown(self.revert_command or []))
            self._write_change(f"cambio temporal: {' '.join(self._shown(command))} (revertir: {revert})")
            result = self._run(command, self.timeout)
            if result is None or result.returncode != 0:
                self.error = f"wpctl falló: {(result.stderr or '').strip() if result else 'sin respuesta'}"
                self._write_change(f"revertido (no se aplicó): {self.error}")
                self.mode = None
                return self.status()
            self.mode = mode
            self.error = None
            self.verified = self.read_current() == level
            return self.status()

    def restore(self) -> dict[str, Any]:
        with self._lock:
            if self.mode is None or self.revert_command is None:
                return self.status()
            result = self._run(self.revert_command, self.timeout)
            ok = result is not None and result.returncode == 0
            shown = " ".join(self._shown(self.revert_command))
            self._write_change(f"revertido: {shown}" if ok else f"revertir falló: {shown}")
            if ok:
                self.mode = None
                self.verified = None
                self.error = None
            else:
                self.error = f"no se pudo revertir: {shown}"
            return self.status()

    def recover(self) -> bool:
        """Reverts a change a previous run left in `changes_file` without its `revertido`."""
        path = self.changes_file or default_changes_file()
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return False
        pending: str | None = None
        for line in lines:
            if m := _CHANGE_LINE.search(line):
                pending = m.group(1)
            elif _REVERTED_LINE.search(line):
                pending = None
        if pending is None or self.mode is not None:
            return False
        args = pending.split(" ")
        tool = self._tool(Path(args[0]).name)
        if tool is None:
            return False
        result = self._run([tool, *args[1:]], self.timeout)
        ok = result is not None and result.returncode == 0
        self._write_change(f"revertido (al arrancar): {pending}" if ok else f"revertir al arrancar falló: {pending}")
        return ok

    @staticmethod
    def _shown(command: list[str]) -> list[str]:
        if not command:
            return []
        return [Path(command[0]).name, *command[1:]]

    def status(self) -> dict[str, Any]:
        return {
            "active": self.mode is not None,
            "mode": self.mode,
            "heavy": self.mode == "heavy",
            "previous": self.previous,
            "verified": self.verified,
            "error": self.error,
            "pending": self.pending,
        }

    # -- for callers that must not wait (the engine thread) ----------------------------

    def _background(self, job: Callable[[], dict[str, Any]], done: Callable[[dict[str, Any]], None] | None) -> None:
        self.pending = True

        def run() -> None:
            try:
                status = job()
            except Exception:
                log.exception("radio log level change failed")
                status = self.status()
            finally:
                self.pending = False
            if done is not None:
                done({**status, "pending": False})

        threading.Thread(target=run, name="aurasync-radio-log", daemon=True).start()

    def enable_in_background(self, mode: str = "light", done: Callable[[dict[str, Any]], None] | None = None) -> None:
        self._background(lambda: self.enable(mode), done)

    def restore_in_background(self, done: Callable[[dict[str, Any]], None] | None = None) -> None:
        self._background(self.restore, done)
