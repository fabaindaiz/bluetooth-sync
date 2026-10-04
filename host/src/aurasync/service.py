"""The control service: a long-lived program that owns one audio session at a time.

`aurasync service` is started by hand and stays up until it is told to shut down
(Ctrl-C, `SIGTERM` or the `shutdown` operation). A failing audio session does not stop
it: the session closes in order, `state` says why, and the program waits for another
`start`. The design is `docs/superpowers/specs/2026-09-29-control-service-design.md`;
the panel's operations are its §15.

**One writer.** Requests arrive on HTTP threads; they are validated there (`control.py`)
and queued. The engine thread — the one that calls `run` — is the only one that touches
the motor, the installation and the presets: between audio blocks while a session plays,
on a 50 ms tick when none does. HTTP threads only read the latest published snapshot and
the log buffer. Anything slow (Bluetooth, switching the source, measuring a calibration)
runs on worker threads that never touch the motor.

**The chain** (`chain.py`, spec 2026-10-02 §4). The listener's chain choices live in
`Settings.chain` and are written to `<config>/chain.json` on every change (only choices,
sparse); a preset's chain goes to `<config>/presets-chain.json`. `extract_ambience`,
`decorrelate` and `eq_active` are no longer fields of their own: they read and write the
algorithm of their stage, so the old `set` and the new `chain_set` cannot disagree.

**The radio** (spec 2026-10-02 §3). The `RadioMonitor` (`radio.py`) lives as long as the
service, not a session: the lines that tie a sink to its speaker are printed when the speaker
starts playing, which can be before a session opens, and the drops of other applications'
audio matter too. Each drop goes to the playing session's cut log. The `radio_log` operation
raises WirePlumber's log level (`radio.LogLevel`, written down with its reversal in
`<config>/cambios-de-sistema.txt` before it runs); the service puts it back on `shutdown`,
SIGTERM, Ctrl-C (all three leave `run` through its `finally`) and when a session fails, and
reverts at start whatever a killed run left behind (card *kill-switch-reaches-every-path*).

**Quality, the chain's metrics and the speaker volume.** The engine thread computes the
quality summary (`quality.py`) at 2 Hz and the stages' metrics at 5 Hz into fields the stream
threads read; `volume.avrcp` is `bt_volume.py`, whose worker never blocks this thread. A blind
A/B measures the loudness of A and B and can match them with a compensating gain.
"""

from __future__ import annotations

import json
import logging
import math
import os
import platform
import queue
import secrets
import signal
import socket
import stat
import subprocess
import sys
import threading
import time
import traceback
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from aurasync import __version__, control, remote
from aurasync import chain as chain_model
from aurasync import radio as radio_module
from aurasync.access import ACCESS_OPS
from aurasync.bt_volume import BluetoothVolume
from aurasync.chain import ChainValues
from aurasync.config import Instalacion, Parlante, ruta_por_defecto
from aurasync.control import ContractError
from aurasync.cut_report import CutReporter
from aurasync.dsp import eq, profiles
from aurasync.logbuffer import LogBuffer
from aurasync.motor import Motor
from aurasync.presets import PresetChainStore, PresetStore, read_lenient, write_atomic
from aurasync.session import AudioSession, SessionError, SessionOptions
from aurasync.snapshot import build_snapshot
from aurasync.system import Observer

if TYPE_CHECKING:
    from collections.abc import Callable

    from aurasync.access import Access
    from aurasync.radio import Drop, LogLevel, RadioMonitor

DEFAULT_PORT = 8731
TICK_S = 0.05
REPLY_TIMEOUT_S = 30.0
"""`start` blocks the engine for about two seconds (the routing check); this is far above."""
CONFIG_KEYS = {"bind", "port", "token", "installation", "microphone", "measurements", *remote.REMOTE_KEYS}
PARTS = {
    "ruteo": ("routing", logging.WARNING),
    "parlante perdido": ("session", logging.WARNING),
    "lazo": ("recalibration", logging.INFO),
    "ajuste": ("recalibration", logging.INFO),
    "descartado": ("recalibration", logging.INFO),
    "sin señal": ("recalibration", logging.DEBUG),
    "error": ("recalibration", logging.ERROR),
    "calibración": ("calibration", logging.INFO),
}


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "aurasync"


def data_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "aurasync"


class ConfigError(Exception):
    """`service.json` is unusable. The message says how to fix it."""


@dataclass(frozen=True)
class ServiceConfig:
    bind: str = "0.0.0.0"  # noqa: S104 - the phone on the LAN is the point; every request carries the token
    port: int = DEFAULT_PORT
    token: str = ""
    installation: str = field(default_factory=lambda: str(ruta_por_defecto()))
    """The same file `aurasync init` writes, respecting XDG_CONFIG_HOME."""
    microphone: str | None = None
    measurements: str = field(default_factory=lambda: str(data_dir() / "mediciones"))
    """Where `measurement_save` writes. Point it at `docs/research/experimentos/datos/` while
    running experiments from the repository."""
    # Reaching the service from other devices (`remote.py`, d-7c8794-37f9bc):
    tls: bool = False
    """False in a file written before HTTPS existed; a new file gets true."""
    https_port: int = remote.DEFAULT_HTTPS_PORT
    panel_origins: list[str] = field(default_factory=remote.default_origins)
    pair_window_s: float = 600
    mdns: bool = False

    @property
    def installation_path(self) -> Path:
        return Path(self.installation).expanduser()

    @property
    def measurements_path(self) -> Path:
        return Path(self.measurements).expanduser()


def load_config(path: Path) -> ServiceConfig:
    """Read `service.json`, creating it with a new token if it is missing.

    Fails closed, as `ssh` does with an exposed key: a file readable by others, invalid JSON
    or an unknown key stop the program before it opens a port.
    """
    if not path.exists():
        config = ServiceConfig(token=secrets.token_urlsafe(32), tls=True)
        write_atomic(path, json.dumps(asdict(config), indent=2) + "\n", mode=0o600)
        return config
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        msg = f"{path} has mode {mode:o}: it holds the token, so only you may read it. Fix: chmod 600 {path}"
        raise ConfigError(msg)
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        msg = f"{path} is not valid JSON ({exc}). Fix it, or delete it to get a new one with a new token"
        raise ConfigError(msg) from exc
    if not isinstance(data, dict):
        msg = f"{path} must hold a JSON object"
        raise ConfigError(msg)
    unknown = set(data) - CONFIG_KEYS
    if unknown:
        msg = f"{path} has unknown keys {sorted(unknown)}; known: {sorted(CONFIG_KEYS)}"
        raise ConfigError(msg)
    config = ServiceConfig(**data)
    if not isinstance(config.token, str) or len(config.token) < 32:  # noqa: PLR2004
        msg = f"{path}: the token is missing or too short. Delete the key to get a new one"
        raise ConfigError(msg)
    if not isinstance(config.port, int) or not 0 < config.port < 65536:  # noqa: PLR2004
        msg = f"{path}: port must be a number from 1 to 65535"
        raise ConfigError(msg)
    if config.microphone is not None and not isinstance(config.microphone, str):
        msg = f"{path}: microphone must be a PipeWire node name or null"
        raise ConfigError(msg)
    problems = remote.check_config(config, path)
    if problems:
        raise ConfigError("; ".join(problems))
    return config


def microphone_from_config(path: Path | None = None) -> str | None:
    """The `microphone` of `service.json`, for the commands that are not the service.

    Lenient on purpose: `calibrate` must not fail because the service's file is broken.
    """
    path = path or config_dir() / "service.json"
    try:
        value = json.loads(path.read_text()).get("microphone")
    except (OSError, ValueError, AttributeError):
        return None
    return value if isinstance(value, str) and value else None


CHAIN_METRICS_S = 0.2
"""The stages' metrics for the stream's `chain` event, at 5 Hz."""
QUALITY_S = 0.5
"""The quality summary, at 2 Hz."""
AB_SETTLE_S = 3.3
"""Audio after a switch in the A/B before its loudness counts: the cut (0.16 s) plus the 3 s
short-term window, plus a little."""
AB_RECOMPENSATE_LU = 0.3
"""The A/B's compensation moves only when the measured difference moved by more than this."""
SLOW_ORDER_S = 0.03
"""An order that keeps the engine thread longer than this is noted as a possible cause."""


CHAIN_VERSION = 1


@dataclass
class Settings:
    """The global fields that are not in the installation file."""

    volume_db: float = -20.0
    """Starts low: tests happen in a room with people in it (amplitude 0.1)."""
    chain: ChainValues = field(default_factory=ChainValues)
    """The listener's chain choices (`chain.py`). `extract_ambience`, `decorrelate` and
    `eq_active` below are views of it."""
    layout: str = "quad"
    block_size: int = 4096
    player_latency_ms: int = 50
    sink_description: str = "aurasync (envolvente)"
    recalibrate_every_s: float = 20.0
    recalibrate_measure_s: float = 10.0
    output_mode: str = "combinado"
    recalibrate: bool = True
    """The recalibration loop is part of the protocol: on by default whenever there is a
    microphone. Switching it off is an advanced option, and it takes effect live."""
    probe: bool = False
    """The masked probe the loop measures against (dsp/probe.py). Off until the blind A/B
    says it cannot be heard (i-7c8794-e3e40d, step 4)."""
    probe_margin_db: float = -20.0
    muted: set[str] = field(default_factory=set)

    @property
    def extract_ambience(self) -> bool:
        return self.chain.algorithm("ambience") != "off"

    @property
    def decorrelate(self) -> bool:
        return self.chain.algorithm("decorrelate") != "off"

    @property
    def eq_active(self) -> bool:
        return self.chain.algorithm("eq") != "off"

    def aliases(self) -> dict[str, bool]:
        return {name: getattr(self, name) for name in chain_model.ON_OFF_ALIASES}


@dataclass
class SessionStatus:
    status: str = "stopped"
    since: str | None = None
    reason: str | None = None
    recalibrate: bool = False
    started_at: float | None = None
    starts: int = 0

    def move(self, status: str, reason: str | None = None) -> None:
        self.status = status
        self.reason = reason
        self.since = datetime.now().astimezone().isoformat(timespec="seconds")
        if status == "playing":
            self.started_at = time.monotonic()
            self.starts += 1
        elif status != "starting":
            self.started_at = None


@dataclass
class ABTest:
    """A blind comparison of two presets: X is one of them, drawn again after every answer."""

    a: str
    b: str
    x: str
    playing: str | None = None
    trials: list[dict] = field(default_factory=list)
    match: bool = False
    raw: dict[str, float | None] = field(default_factory=lambda: {"a": None, "b": None})
    """Short-term loudness of the sum of the outputs with each preset, without compensation."""
    compensation: dict[str, float] = field(default_factory=lambda: {"a": 0.0, "b": 0.0})
    applied: float = 0.0
    measure_from: int | None = None
    """`QualityMeter.samples` from which the current preset's loudness counts."""
    taken: dict[str, int | None] = field(default_factory=lambda: {"a": None, "b": None})
    """`QualityMeter.samples` when each side's loudness was last taken."""

    def heard(self, which: str) -> float | None:
        raw = self.raw[which]
        return None if raw is None else raw + self.compensation[which]

    def loudness(self) -> dict[str, float | None]:
        a, b = self.heard("a"), self.heard("b")
        return {
            "a": round(a, 2) if a is not None else None,
            "b": round(b, 2) if b is not None else None,
            "diff": round(b - a, 2) if a is not None and b is not None else None,
        }

    def draw(self) -> None:
        self.x = secrets.choice((self.a, self.b))


@dataclass
class _Pending:
    command: control.Command
    future: Future = field(default_factory=Future)
    actor: str | None = None
    """Who sent it (a client's name, or `master`), for the log. None from inside the program."""


class Service:
    """Implements `control.Controllable`. Every method runs on the engine thread."""

    def __init__(
        self,
        installation_path: Path,
        presets_path: Path,
        *,
        options: SessionOptions | None = None,
        session_factory: Callable[..., AudioSession] = AudioSession,
        motor_factory: Callable[..., Motor] | None = None,
        log: Callable[[str], None] | None = None,
        observer: Observer | None = None,
        measurements_path: Path | None = None,
        simulated: bool = False,
        logs: LogBuffer | None = None,
        config_path: Path | None = None,
        chain_path: Path | None = None,
        radio: RadioMonitor | None = None,
        log_level: LogLevel | None = None,
        bt_volume: BluetoothVolume | None = None,
    ) -> None:
        self.installation_path = installation_path
        self.config_path = config_path
        """`service.json`, where a microphone chosen in the panel is kept. None: not kept."""
        self.preset_store = PresetStore(presets_path)
        self.chain_path = chain_path or presets_path.parent / "chain.json"
        """The listener's chain choices. Next to the presets, so a simulation (which works on
        a copy of them) never writes the real one."""
        self.options = options or SessionOptions()
        self.session_factory = session_factory
        self.motor_factory = motor_factory or _default_motor
        self.settings = Settings(block_size=self.options.block, player_latency_ms=self.options.player_latency_ms)
        self.status = SessionStatus()
        self.sequence = 0
        self.preset: str | None = None
        self.installation: Instalacion | None = None
        self.session: AudioSession | None = None
        self._cut_reporter: CutReporter | None = None
        """The open session's cuts, into the log (`cut_report.py`)."""
        self.motor: Motor | None = None
        self.dirty = False
        """The installation in memory differs from the file: `save` writes it."""
        self.last_calibration: dict | None = None
        self.ab: ABTest | None = None
        self.ab_last: dict | None = None
        self.observer = observer or Observer(enabled=False)
        self.measurements_path = measurements_path or data_dir() / "mediciones"
        self.simulated = simulated
        self.pairing: dict = {"urls": []}
        self.access: Access | None = None
        """Clients, pairing and the master token (`access.py`); `serve` and `make_server` set it."""
        self.logs = logs or LogBuffer()
        self.restarts: dict[str, int] = {}
        self.errors: dict[str, str] = {}
        self._print = log or (lambda line: print(line, flush=True))  # noqa: T201 - the service's log is its stdout
        self._queue: queue.Queue[_Pending] = queue.Queue()
        self._started = time.monotonic()
        self._stopping = threading.Event()
        self._slow_order_until = 0.0
        self._xruns_seen_at: float | None = None
        self._xrun_totals: dict[str, int | None] = {}
        self._snapshot: dict = {}
        self.radio = radio
        """Follows the journal for the radio's dropped packets; None: not watched (tests, the Mac)."""
        self.log_level = log_level
        """Raises and restores the bluez5 log level (`radio_log`); None: the op is unavailable."""
        self.bt_volume = bt_volume or BluetoothVolume()
        self.radio_drops = 0
        """Drops seen since the service started: the stream sends a `radio` event when it moves."""
        self.streams: dict[str, int] = {"open": 0}
        """Server-Sent Event streams open now (the REST adapter keeps it)."""
        self._engine_calls: queue.Queue[Callable[[], None]] = queue.Queue()
        self.chain_metrics: dict | None = None
        self.quality: dict | None = None
        self._chain_metrics_at = 0.0
        self._quality_at = 0.0
        if radio is not None:
            radio.on_drop = self._on_radio_drop
            radio.speaker_name = self._speaker_by_address
        self._logger = logging.getLogger("aurasync.svc")
        if self.logs not in self._logger.handlers:
            self._logger.addHandler(self.logs)
        self._logger.setLevel(logging.DEBUG)
        self._logger.propagate = False
        self.presets_chain = PresetChainStore(
            presets_path.parent / "presets-chain.json", lambda line: self.log(line, level=logging.WARNING, part="chain")
        )
        self.settings.chain = self._load_chain()
        self.reload_installation()
        self._publish()

    def close(self) -> None:
        """Detach the log buffer (tests create many services in one process)."""
        self._logger.removeHandler(self.logs)

    # -- transport side (any thread) -------------------------------------------------

    def handle(self, message: Any, actor: str | None = None) -> dict:
        """One decoded message in, one reply out. Safe to call from any thread.

        `actor` is who sent it (the transport knows: a client's name, or `master`); it only goes
        to the log, so that with several phones paired each line says who did it."""
        cid = control.message_id(message)
        try:
            command = control.parse(message)
        except ContractError as exc:
            self.log(f"rejected: {exc.code}: {exc.message}", level=logging.WARNING)
            return control.error(cid, exc.code, exc.message)
        # Read-only operations answer from this thread: they never touch the engine.
        if command.op == "state":
            return control.ok(cid, self._snapshot)
        if command.op == "logs":
            return control.ok(cid, self.logs.since(command.args.get("since", 0), command.args.get("limit", 500)))
        if command.op in ACCESS_OPS:
            # Clients and pairing (`access.py`): they never touch the engine either.
            if self.access is None:
                return control.error(cid, "unavailable", "this service has no client store")
            return self.access.handle(command)
        pending = _Pending(command, actor=actor)
        self._queue.put(pending)
        try:
            return pending.future.result(timeout=REPLY_TIMEOUT_S)
        except FutureTimeout:
            return control.error(cid, "internal", "the engine did not answer in time")

    @property
    def snapshot(self) -> dict:
        return self._snapshot

    @property
    def stopping(self) -> bool:
        return self._stopping.is_set()

    # -- engine side ----------------------------------------------------------------

    def run(self) -> None:
        """The engine loop. Returns after `shutdown`; the caller closes the transport.

        Its `finally` is the one exit of the program's every path (`shutdown`, and SIGTERM and
        Ctrl-C, which `serve` turns into a KeyboardInterrupt raised here): the radio log level
        goes back there."""
        self._recover_radio_log()
        if self.radio is not None:
            self.radio.start()
        self.observer.start()
        if self.settings.chain.algorithm("volume") == "avrcp":
            self._enter_avrcp()
        try:
            while not self._stopping.is_set():
                if self.session is not None:
                    self._watch_system_cuts()
                    self._step()
                    self._report_cuts()
                    self._drain(block=False)
                else:
                    self._drain(block=True)
                self._refresh_views()
                self._publish()
        finally:
            try:
                self._close_session("stopped", None)
            finally:
                self._restore_radio_log("the service is stopping")
                if self.radio is not None:
                    self.radio.stop()
                self.observer.stop()
                self._publish()

    def _step(self) -> None:
        try:
            self.session.step()
        except SessionError as exc:
            self.log(f"session failed: {exc.message}", level=logging.ERROR, part="session")
            self.errors["session"] = exc.message
            self._close_session("error", exc.message)
        except Exception as exc:  # noqa: BLE001 - a failing session must not stop the program
            self.log(f"session failed: {exc!r}\n{traceback.format_exc()}", level=logging.ERROR, part="session")
            self.errors["session"] = repr(exc)
            self._close_session("error", f"internal error: {exc!r}")

    def _report_cuts(self) -> None:
        reporter = self._cut_reporter
        if reporter is None or self.session is None:
            return
        line = reporter.tick(getattr(self.session, "block_ms", None))
        if line is not None:
            self.log(line, level=logging.WARNING, part="session")

    def _drain(self, *, block: bool) -> None:
        self._run_engine_calls()
        try:
            pending = self._queue.get(timeout=TICK_S) if block else self._queue.get_nowait()
        except queue.Empty:
            return
        while True:
            self._execute(pending)
            try:
                pending = self._queue.get_nowait()
            except queue.Empty:
                return

    def _execute(self, pending: _Pending) -> None:
        command = pending.command
        started = time.monotonic()
        try:
            result = control.dispatch(command, self)
        except ContractError as exc:
            self.log(
                f"{command.op}: rejected: {exc.code}: {exc.message}{_by(pending.actor)}",
                level=logging.WARNING,
                part="panel",
            )
            pending.future.set_result(control.error(command.id, exc.code, exc.message))
            return
        except Exception as exc:  # noqa: BLE001 - logged; the program keeps running
            self.log(
                f"{command.op}: internal error: {exc!r}\n{traceback.format_exc()}", level=logging.ERROR, part="panel"
            )
            pending.future.set_result(control.error(command.id, "internal", repr(exc)))
            return
        self.log(f"{command.op}: ok {_brief(command.args)}{_by(pending.actor)}", part="panel")
        self._note_order(command.op, time.monotonic() - started)
        self._publish()
        pending.future.set_result(control.ok(command.id, result))

    def on_engine(self, call: Callable[[], None]) -> None:
        """Run `call` on the engine thread, between blocks (from a worker's callback)."""
        self._engine_calls.put(call)

    def _run_engine_calls(self) -> None:
        while True:
            try:
                call = self._engine_calls.get_nowait()
            except queue.Empty:
                return
            try:
                call()
            except Exception as exc:  # noqa: BLE001 - logged; the program keeps running
                self.log(f"engine call failed: {exc!r}\n{traceback.format_exc()}", level=logging.ERROR)
            self._publish()

    def _refresh_views(self) -> None:
        """What the stream sends besides the snapshot, computed here so that no stream thread
        reads the motor while this thread writes it."""
        now = time.monotonic()
        motor = self.motor
        if motor is None:
            self.chain_metrics = self.quality = None
            return
        if now - self._chain_metrics_at >= CHAIN_METRICS_S and hasattr(motor, "metricas_cadena"):
            self._chain_metrics_at = now
            metrics = motor.metricas_cadena()
            metrics["volume"]["mode"] = self.bt_volume.state
            self.chain_metrics = metrics
        meter = getattr(self.session, "quality", None)
        if meter is not None and now - self._quality_at >= QUALITY_S:
            self._quality_at = now
            pct = motor.uso_limitador_pct() if hasattr(motor, "uso_limitador_pct") else {}
            digital = getattr(motor, "volumen_db", None)
            self.quality = meter.summary(pct, digital)
            self._ab_measure(meter)

    def _note_order(self, op: str, seconds: float) -> None:
        """An order runs on the engine thread: a slow one delays the next block (cuts.py)."""
        cuts = getattr(self.session, "cuts", None)
        if cuts is None:
            return
        cuts.context["last_order"] = op
        # Starting or stopping a session is slow by nature and is not a cut of the music.
        if seconds > SLOW_ORDER_S and op not in {"start", "stop", "service_restart", "shutdown"}:
            cuts.context["slow_order"] = f"{op}, {seconds * 1000:.0f} ms"
            self._slow_order_until = time.monotonic() + 2.0

    def _watch_system_cuts(self) -> None:
        """New xruns from PipeWire, and whether Bluetooth is searching, into the cut log."""
        cuts = getattr(self.session, "cuts", None)
        if cuts is None:
            return
        if self._slow_order_until and time.monotonic() > self._slow_order_until:
            cuts.context.pop("slow_order", None)
            self._slow_order_until = 0.0
        view = self.observer.view
        cuts.context["bt_discovering"] = bool(view.get("discovering"))
        if view.get("at") == self._xruns_seen_at:
            return
        self._xruns_seen_at = view.get("at")
        names = {p.sink: p.nombre for p in (self.installation.parlantes if self.installation else [])}
        for key, entry in (view.get("xruns") or {}).items():
            total = entry.get("total") if isinstance(entry, dict) else None
            before = self._xrun_totals.get(key)
            self._xrun_totals[key] = total
            if total is None or before is None or total <= before:
                continue
            sink = key.split(":", 1)[-1]
            where = names.get(sink, "salida combinada" if sink == "aurasync_salida" else sink)
            layer = {"bt": "nodo Bluetooth", "stream": "stream del sink combinado"}.get(key.split(":", 1)[0], "pw-play")
            cuts.add("xrun", where, f"{total - before} en el {layer}", count=total - before)

    def log(self, line: str, *, level: int = logging.INFO, part: str = "service") -> None:
        logging.getLogger(f"aurasync.svc.{part}").log(level, line)
        stamp = datetime.now().astimezone().strftime("%H:%M:%S")
        self._print(f"[{stamp}] {part}: {line}")

    # -- the installation -----------------------------------------------------------

    def reload_installation(self) -> None:
        if self.installation_path.exists():
            self.installation = Instalacion.cargar(self.installation_path)
        else:
            self.installation = None
        self.dirty = False

    def _need_installation(self) -> Instalacion:
        if self.installation is None:
            raise ContractError(
                "not_found", f"no installation at {self.installation_path}; create it with aurasync init"
            )
        return self.installation

    def _speaker(self, name: str) -> Parlante:
        try:
            return self._need_installation().por_nombre(name)
        except KeyError as exc:
            raise ContractError("not_found", str(exc.args[0])) from exc

    def _need_session(self) -> AudioSession:
        if self.session is None:
            raise ContractError("conflict", "no session is playing: start it first")
        return self.session

    def _need_no_session(self, what: str) -> None:
        if self.session is not None:
            raise ContractError("conflict", f"stop the session before {what}: it changes the speakers it plays to")

    def _changed(self, *, dirty: bool = True) -> dict:
        self.sequence += 1
        self.dirty = self.dirty or dirty
        return {"sequence": self.sequence}

    # -- operations (control.Controllable) ------------------------------------------

    def state(self) -> dict:
        return self._snapshot

    def start(self, *, recalibrate: bool | None = None) -> dict:
        if self.session is not None:
            raise ContractError("conflict", "a session is already playing; stop it first")
        installation = self._need_installation()
        if not installation.parlantes:
            raise ContractError("conflict", "the installation has no speakers: add one first")
        s = self.settings
        if recalibrate is None:
            recalibrate = s.recalibrate
        if recalibrate and not self.options.microphone:
            # Without a microphone the loop cannot run; playing still can.
            self.log("no microphone: playing without the recalibration loop", level=logging.WARNING, part="session")
            recalibrate = False
        options = replace(
            self.options,
            block=s.block_size,
            player_latency_ms=s.player_latency_ms,
            sink_description=s.sink_description,
            every_s=s.recalibrate_every_s,
            measure_s=s.recalibrate_measure_s,
            output=s.output_mode,
            recalibrate=recalibrate,
            microphone=self.options.microphone if recalibrate else None,
            probe=s.probe,
            probe_margin_db=s.probe_margin_db,
        )
        try:
            motor = self.motor_factory(installation, options.rate, s)
        except ValueError as exc:
            raise ContractError("conflict", str(exc)) from exc
        motor.silenciados = s.muted
        if self.bt_volume.active and hasattr(motor, "saltar_volumen"):
            # The speakers carry the volume: the digital one starts at 0 dB.
            motor.saltar_volumen(0.0)
        session = self.session_factory(installation, motor, options, self._session_log)
        if hasattr(session, "bt_volumes"):
            # A simulated room applies the speakers' volume (`simulated.py`).
            session.bt_volumes = self.bt_volume.backend
        self.status.move("starting")
        self.status.recalibrate = recalibrate
        self._publish()
        try:
            session.open()
        except SessionError as exc:
            session.close()
            self.status.move("error", exc.message)
            self.errors["session"] = exc.message
            self._restore_radio_log("the session failed to start")
            raise ContractError(exc.code, exc.message) from exc
        except Exception as exc:
            session.close()
            self.status.move("error", repr(exc))
            self.errors["session"] = repr(exc)
            self._restore_radio_log("the session failed to start")
            raise
        self.session, self.motor = session, motor
        self.session_options = options
        cuts = getattr(session, "cuts", None)
        self._cut_reporter = CutReporter(cuts) if cuts is not None else None
        self.status.move("playing")
        self.log(
            f"session open: {len(installation.parlantes)} speakers, block {options.block}, "
            f"pw-play buffer {options.player_latency_ms} ms, recalibration {'on' if recalibrate else 'off'}",
            part="session",
        )
        self.errors.pop("session", None)
        if s.chain.algorithm("volume") == "avrcp" and self.bt_volume.state in {"off", "failed"}:
            # Now the speakers are connected: a mode that could not enter before tries again.
            self._enter_avrcp()
        return {}

    def stop(self) -> dict:
        self._close_session("stopped", None)
        return {}

    def set_speaker(self, speaker: str, changes: dict) -> dict:
        self._apply_speaker(speaker, changes)
        return self._changed(dirty=bool(set(changes) - {"muted"}))

    def _apply_speaker(self, speaker: str, changes: dict) -> None:
        target = self._speaker(speaker)
        if "delay_ms" in changes and self.session is not None and self.session.loop is not None:
            raise ContractError(
                "conflict", "delay_ms belongs to the recalibration loop while it runs; switch it off first"
            )
        if "muted" in changes:
            if changes["muted"]:
                self.settings.muted.add(speaker)
            else:
                self.settings.muted.discard(speaker)
        for name, value in changes.items():
            attr = control.SPEAKER_FIELDS[name].attr
            if attr is not None:
                setattr(target, attr, value)
        if "kind" in changes and self.motor is not None and hasattr(self.motor, "actualizar_tipos"):
            # Which speakers are small decides the bass stage.
            self.motor.actualizar_tipos()
        if self.motor is not None:
            # `ambience` and `delay_ms` move the speaker's delay; a large move goes through the fade.
            self.motor.actualizar_desde_control()
        artistic = set(changes) & set(control.ARTISTIC_SPEAKER_FIELDS)
        if artistic:
            self.preset = None

    def set_global(self, changes: dict) -> dict:
        self._apply_global(changes)
        return self._changed(dirty="rear_delay_ms" in changes)

    def _apply_global(self, changes: dict) -> None:
        if "rear_delay_ms" in changes:
            self._need_installation()
        if changes.get("extract_ambience") and self.motor is not None and not self.motor.tiene_extractor:
            raise ContractError("conflict", "this session's motor was built without the ambience extractor")
        values = self.settings.chain
        for name, value in changes.items():
            stage = chain_model.ON_OFF_ALIASES.get(name)
            # An alias of the stage's algorithm. Only a change is a choice: sending the value
            # it already has does not pin the default.
            if stage is not None and (values.algorithm(stage) != "off") != value:
                values = values.with_algorithm(stage, chain_model.on_algorithm(stage) if value else "off")
        # The chain first: it is the only part that writes a file, and a failed write must
        # leave everything as it was. Switching the decorrelator or the EQ goes through the
        # fade; the extractor moves its mix live (`motor.aplicar_cadena`).
        self._set_chain(values)
        for name, value in changes.items():
            if name in chain_model.ON_OFF_ALIASES:
                continue
            if name == "rear_delay_ms":
                self.installation.retardo_traseros_ms = value
            else:
                setattr(self.settings, name, value)
        if "volume_db" in changes:
            self._volume_changed(changes["volume_db"])
        if self.motor is not None:
            self._apply_settings_live(changes)
        if set(changes) & set(control.ARTISTIC_GLOBAL_FIELDS):
            self.preset = None

    def _apply_settings_live(self, changes: dict) -> None:
        motor = self.motor
        if "rear_delay_ms" in changes:
            motor.actualizar_desde_control()
        if "recalibrate" in changes and self.session is not None and changes["recalibrate"] != self.status.recalibrate:
            self.recalibrate(changes["recalibrate"])
        if {"probe", "probe_margin_db"} & set(changes) and hasattr(self.session, "set_probe"):
            margin = changes.get("probe_margin_db")
            try:
                self.session.set_probe(self.settings.probe, margin)
            except SessionError as exc:
                raise ContractError(exc.code, exc.message) from exc

    # -- the chain (spec 2026-10-02 §4) -------------------------------------------------

    def _chain_context(self) -> chain_model.ChainContext:
        return chain_model.ChainContext.of(self.installation)

    def _load_chain(self) -> ChainValues:
        """The stored choices. Never fails: what cannot be used is logged and dropped."""
        if not self.chain_path.exists():
            return ChainValues()

        def warn(line: str) -> None:
            self.log(line, level=logging.WARNING, part="chain")

        data = read_lenient(self.chain_path, warn)
        if data is None:
            return ChainValues()
        if not isinstance(data, dict) or data.get("v") != CHAIN_VERSION:
            warn(f"{self.chain_path}: expected {{'v': {CHAIN_VERSION}, 'chain': {{...}}}}; using the defaults")
            return ChainValues()
        return ChainValues.from_json(data.get("chain", {}), warn)

    def _write_chain(self, values: ChainValues) -> None:
        text = json.dumps({"v": CHAIN_VERSION, "chain": values.to_json()}, indent=2, ensure_ascii=False) + "\n"
        write_atomic(self.chain_path, text)

    def _set_chain(self, values: ChainValues, *, at_bottom: bool = False) -> str:
        """Make `values` the chain: on disk first (a failed write changes nothing), then in
        memory, then in the motor. Returns how the motor applied it."""
        old = self.settings.chain
        if values.choices == old.choices:
            return "none"
        self._write_chain(values)
        self.settings.chain = values
        changed = {s for s in chain_model.STAGES if old.choices.get(s) != values.choices.get(s)}
        if changed - set(chain_model.PRESET_EXCLUDED_STAGES):
            self.preset = None
        kind = "none" if self.motor is None else self.motor.aplicar_cadena(values, en_corte=at_bottom)
        if old.algorithm("volume") != values.algorithm("volume"):
            moved = self._volume_mode(values.algorithm("volume"))
            if moved and self.motor is not None:
                kind = "cut"
        return kind

    # -- the volume in the speakers (`bt_volume.py`) --------------------------------------

    def _sinks(self) -> dict[str, str]:
        return {p.nombre: p.sink for p in (self.installation.parlantes if self.installation else [])}

    def _volume_changed(self, value: float) -> None:
        """The panel's volume: the speakers' while `avrcp` is in effect, else the digital one."""
        bv = self.bt_volume
        if bv.state == "on":
            bv.apply(self._sinks(), value)
            return
        if self.motor is not None and not bv.active:
            self.motor.volumen_db = value
        if bv.state == "failed" and self.settings.chain.algorithm("volume") == "avrcp":
            self._enter_avrcp()

    def _volume_mode(self, algorithm: str) -> bool:
        """Switch between the digital volume and the speakers'. True if the sound will move."""
        bv = self.bt_volume
        if algorithm == "avrcp":
            return self._enter_avrcp()
        if bv.state == "on":
            volume = self.settings.volume_db
            restore, digital = bv.leave_levels(volume)
            self.log(f"volume back to digital: {digital:.1f} dB, the speakers back to {restore:.1f} dB", part="volume")
            self.settings.volume_db = round(digital, 2)
            motor = self.motor
            if motor is not None:
                # Quieter first: the digital volume goes down at the bottom of the cut, and the
                # speakers come back up only when that audio is the one heard (`bt_volume.py`).
                motor.cortar(lambda: motor.saltar_volumen(digital))
            bv.leave(self._sinks(), volume, done=lambda missed: self.on_engine(lambda: self._volume_left(missed)))
            return True
        bv.forget()
        return False

    def _enter_avrcp(self) -> bool:
        bv = self.bt_volume
        if self.installation is None or not self.installation.parlantes or bv.state in {"entering", "on", "leaving"}:
            return False
        digital = self.settings.volume_db
        bv.enter(self._sinks(), digital, done=lambda volume: self.on_engine(lambda: self._avrcp_entered(volume)))
        return True

    def _avrcp_entered(self, volume: float | None) -> None:
        bv = self.bt_volume
        if volume is None:
            self.log(
                f"the speakers did not take the volume: {bv.status()['error']}", level=logging.WARNING, part="volume"
            )
            self._changed(dirty=False)
            return
        if self.settings.chain.algorithm("volume") != "avrcp":
            # Chosen `digital` again while it was entering: the speakers go back, the digital
            # volume never moved.
            bv.leave(self._sinks(), volume, done=lambda missed: self.on_engine(lambda: self._volume_left(missed)))
            return
        self.settings.volume_db = round(volume, 2)
        motor = self.motor
        if motor is not None:
            motor.cortar(lambda: motor.saltar_volumen(0.0))
        self.log(f"volume in the speakers: {volume:.1f} dB, digital at 0 dB", part="volume")
        self._changed(dirty=False)

    def _volume_left(self, missed: list[str]) -> None:
        if missed:
            self.log(
                f"speakers that did not go back to their volume: {', '.join(missed)}",
                level=logging.WARNING,
                part="volume",
            )
        self._changed(dirty=False)

    def _backed(self, stage: str, param: str, speaker: str | None) -> object:
        """The knobs of the chain that live in the installation or the session."""
        if speaker is not None:
            name = _alias_of(chain_model.SPEAKER_ALIASES, stage, param)
            if name == "muted":
                return speaker in self.settings.muted
            return getattr(self._speaker(speaker), control.SPEAKER_FIELDS[name].attr)
        name = _alias_of(chain_model.GLOBAL_ALIASES, stage, param)
        if name == "rear_delay_ms":
            if self.installation is None:
                return chain_model.default(stage, param)
            return self.installation.retardo_traseros_ms
        return getattr(self.settings, name)

    def _stage_reply(self, stage: str, kind: str) -> dict:
        described = chain_model.describe(self.settings.chain, self._chain_context(), self._backed)
        return {"stage": stage, "value": chain_model.stage_value(described, stage), "apply": kind}

    def chain(self) -> dict:
        return chain_model.describe(self.settings.chain, self._chain_context(), self._backed)

    def chain_set(
        self, stage: str, algorithm: str | None = None, params: dict | None = None, speaker: str | None = None
    ) -> dict:
        """Validated completely (again, now with the installation) before anything changes."""
        try:
            change = chain_model.validate_set(
                stage, algorithm, params, speaker, values=self.settings.chain, context=self._chain_context()
            )
        except chain_model.ChainError as exc:
            raise ContractError(exc.code, exc.message) from exc
        _, elsewhere = change.split()
        legacy = {
            _alias_of(chain_model.SPEAKER_ALIASES if speaker else chain_model.GLOBAL_ALIASES, stage, pid): value
            for pid, value in elsewhere.items()
        }
        if legacy and speaker is None and "rear_delay_ms" in legacy:
            self._need_installation()
        if (
            stage == "ambience"
            and algorithm not in {None, "off"}
            and self.motor is not None
            and not self.motor.tiene_extractor
        ):
            raise ContractError("conflict", "this session's motor was built without the ambience extractor")
        kind = "none"
        if legacy:
            if speaker is not None:
                self._apply_speaker(speaker, legacy)
            else:
                self._apply_global(legacy)
            kind = "live"
        applied = self._set_chain(self.settings.chain.with_change(change))
        kind = applied if applied != "none" else kind
        dirty = bool(set(legacy) - {"muted", "volume_db"})
        return {**self._changed(dirty=dirty), **self._stage_reply(stage, kind)}

    def chain_reset(self, stage: str, param: str | None = None, speaker: str | None = None) -> dict:
        """Back to the default. A whole stage clears the chain's own choices; the knobs that
        live in the installation (pan, ambience, gain, rear delay) are reset one by one."""
        try:
            spec = chain_model.validate_reset(stage, param, speaker)
        except chain_model.ChainError as exc:
            raise ContractError(exc.code, exc.message) from exc
        if spec is not None and spec.store != "chain":
            return self.chain_set(stage, params={param: spec.default}, speaker=speaker)
        if speaker is not None:
            self._speaker(speaker)
        kind = self._set_chain(self.settings.chain.with_reset(stage, param, speaker))
        return {**self._changed(dirty=False), **self._stage_reply(stage, kind)}

    def eq_apply(self) -> dict:
        """Each speaker's EQ from the last calibration: the previous curve plus what the
        residual says is missing, lifting only, inside the band its kind can reproduce
        (`dsp/eq.py`, `dsp/profiles.py`). Doubtful or silent speakers keep theirs."""
        session = self._need_session()
        cal = session.calibration
        if cal is None or cal.state != "done":
            raise ContractError("conflict", "there is no finished calibration to equalise from")
        changed, skipped = [], []
        for r in cal.results:
            if r["silent"] or r["doubtful"] or not r.get("response_db"):
                skipped.append(r["speaker"])
                continue
            p = self._speaker(r["speaker"])
            band = profiles.boost_band(p.tipo or profiles.guess(p.nombre), tuple(r.get("band_hz") or (None, None)))
            curve = eq.correction(
                r["response_db"],
                r.get("applied_eq_db"),
                band,
                dead_band_db=self.settings.chain.param("eq", "dead_band_db"),
            )
            p.ecualizacion_db = [float(v) for v in curve]
            changed.append(r["speaker"])
        if not changed:
            raise ContractError("conflict", "no speaker was measured reliably; calibrate again")
        if self.motor is not None:
            self.motor.actualizar_ecualizacion()
        self.log(
            f"EQ applied to {', '.join(changed)}" + (f"; kept: {', '.join(skipped)}" if skipped else ""),
            part="calibration",
        )
        return {**self._changed(), "skipped": skipped}

    def eq_reset(self) -> dict:
        for p in self._need_installation().parlantes:
            p.ecualizacion_db = None
        if self.motor is not None:
            self.motor.actualizar_ecualizacion()
        return self._changed()

    def assign(self, speaker: str, role: str) -> dict:
        layout = self.settings.layout
        if role not in control.ROLES[layout]:
            raise ContractError(
                "out_of_range", f"{role} is not a role of the {layout} layout: {list(control.ROLES[layout])}"
            )
        target = self._speaker(speaker)
        for p in self.installation.parlantes:
            if p is not target and control.role_of(p.pan, p.ambiente, layout) == role:
                raise ContractError("conflict", f"{role} is taken by {p.nombre}; give it another role first")
        pan, ambience = control.ROLES[layout][role]
        return self.set_speaker(speaker, {"pan": pan, "ambience": ambience})

    def presets(self) -> dict:
        return {"presets": self.preset_store.presets}

    def preset_save(self, name: str) -> dict:
        installation = self._need_installation()
        preset = {
            "global": {
                "rear_delay_ms": installation.retardo_traseros_ms,
                "extract_ambience": self.settings.extract_ambience,
                "decorrelate": self.settings.decorrelate,
            },
            "speakers": {
                p.nombre: {"pan": p.pan, "ambience": p.ambiente, "gain_db": p.ganancia_db}
                for p in installation.parlantes
            },
        }
        # The chain part first: if the preset itself then fails to save, it is put back.
        before = self.presets_chain.get(name)
        self.presets_chain.save(name, self.settings.chain.preset_part())
        try:
            self.preset_store.save(name, preset)
        except BaseException:
            self.presets_chain.restore(name, before)
            raise
        self.preset = name
        return {}

    def preset_load(self, name: str) -> dict:
        preset = self.preset_store.get(name)
        installation = self._need_installation()
        known = {p.nombre for p in installation.parlantes}
        unknown = sorted(set(preset["speakers"]) - known)
        if unknown:
            raise ContractError("not_found", f"preset {name!r} names speakers this installation lacks: {unknown}")

        # The chain is decided (and written) now, outside the audio thread's fade; the motor
        # takes it at the bottom of the fade, with everything else.
        part = self.presets_chain.get(name)
        values = self.settings.chain if part is None else self.settings.chain.with_preset(part)
        for key, value in preset["global"].items():
            stage = chain_model.ON_OFF_ALIASES.get(key)
            if stage is not None and (values.algorithm(stage) != "off") != value:
                values = values.with_algorithm(stage, chain_model.on_algorithm(stage) if value else "off")
        if values.choices != self.settings.chain.choices:
            self._write_chain(values)
            self.settings.chain = values

        def apply() -> None:
            for speaker, fields in preset["speakers"].items():
                target = installation.por_nombre(speaker)
                for key, value in fields.items():
                    setattr(target, control.SPEAKER_FIELDS[key].attr, value)
            if "rear_delay_ms" in preset["global"]:
                installation.retardo_traseros_ms = preset["global"]["rear_delay_ms"]
            if self.motor is not None:
                self.motor.aplicar_cadena(values, en_corte=True)

        if self.motor is not None:
            # Always through the fade, even when nothing changes: in a blind A/B the
            # presence or absence of the dip would give the answer away.
            self.motor.cortar(apply)
        else:
            apply()
        self.preset = name
        return self._changed()

    def preset_delete(self, name: str) -> dict:
        if self.ab is not None and name in {self.ab.a, self.ab.b}:
            raise ContractError("conflict", f"{name!r} is in the blind A/B test; stop it first")
        self.preset_store.delete(name)
        self.presets_chain.delete(name)
        if self.preset == name:
            self.preset = None
        return {}

    def save(self) -> dict:
        installation = self._need_installation()
        text = json.dumps(asdict(installation), indent=2, ensure_ascii=False) + "\n"
        write_atomic(self.installation_path, text)
        self.dirty = False
        return {"path": str(self.installation_path)}

    def shutdown(self) -> dict:
        self._stopping.set()
        return {}

    # -- the panel's operations (spec §15) --------------------------------------------

    def source(self, kind: str, name: str | None = None) -> dict:
        session = self._need_session()
        if kind in {"app", "file"} and not name:
            raise ContractError("bad_request", f"the {kind} source needs a name")

        def done(error: str | None) -> None:
            if error:
                self.errors["source"] = error
                self.log(f"source {kind}: {error}", level=logging.ERROR, part="source")
            else:
                self.errors.pop("source", None)

        try:
            session.set_source(kind, name, done)
        except SessionError as exc:
            raise ContractError(exc.code, exc.message) from exc
        return {}

    def tone(self, speaker: str, seconds: float = 2.0) -> dict:
        self._speaker(speaker)
        try:
            self._need_session().tone(speaker, seconds)
        except SessionError as exc:
            raise ContractError(exc.code, exc.message) from exc
        return {}

    def recalibrate(self, active: bool) -> dict:  # noqa: FBT001 - the contract's field
        session = self._need_session()
        try:
            if active:
                session.enable_recalibration(self.options.microphone)
            else:
                session.disable_recalibration()
        except SessionError as exc:
            raise ContractError(exc.code, exc.message) from exc
        self.status.recalibrate = active
        self.settings.recalibrate = active
        return {}

    def calibrate(self, seconds: float = 10.0, amplitude: float = 0.1) -> dict:
        session = self._need_session()
        try:
            session.start_calibration(seconds, amplitude, self.options.microphone)
        except SessionError as exc:
            raise ContractError(exc.code, exc.message) from exc
        return {}

    def calibrate_cancel(self) -> dict:
        self._need_session().cancel_calibration()
        return {}

    def calibration_apply(self) -> dict:
        session = self._need_session()
        cal = session.calibration
        if cal is None or cal.state != "done":
            raise ContractError("conflict", "there is no finished calibration to apply")
        # The loop owns the delays while it runs: it restarts from the applied values, so its
        # history (measured against the old ones) does not pull them back.
        restart_loop = session.loop is not None
        if restart_loop:
            session.disable_recalibration()
        # A doubtful speaker (windows that disagree) keeps what it has: applying a number
        # the measurement itself does not trust can misalign it (2026-10-01: Blue came out
        # with 16.8 ms of disagreement and was applied anyway).
        values = {r["speaker"]: r for r in cal.results if not r["silent"] and not r["doubtful"]}
        skipped = [r["speaker"] for r in cal.results if r["silent"] or r["doubtful"]]
        if not values:
            raise ContractError("conflict", "no speaker was measured reliably; calibrate again")

        def apply() -> None:
            # The calibration measured the residual through the corrections it found in
            # place: the new correction is the one it used plus the residual. The reliable
            # speakers are aligned among themselves (their smallest residual is the base);
            # skipped ones keep exactly what they had, since their measurement is not
            # trusted. Then all are shifted so that the smallest delay is 0 (no latency added
            # for nothing) and the largest gain is 0 dB (never boost, it could clip).
            base = min(r["delay_ms"] for r in values.values())
            delays = {r["speaker"]: r["applied_delay_ms"] for r in cal.results}
            gains = {r["speaker"]: r["applied_gain_db"] for r in cal.results}
            for name, r in values.items():
                delays[name] = r["applied_delay_ms"] + r["delay_ms"] - base
                gains[name] = r["applied_gain_db"] + r["gain_db"]
            low, high = min(delays.values()), max(gains.values())
            for name, delay in delays.items():
                p = self.installation.por_nombre(name)
                p.retardo_ms = round(delay - low, 3)
                p.ganancia_db = round(max(-40.0, gains[name] - high), 2)

        # At the bottom of a fade: a calibration moves delays by several ms at once.
        self.motor.cortar(apply)
        if restart_loop:
            session.enable_recalibration(self.options.microphone)
        self.log(
            f"calibration applied to {', '.join(values)}"
            + (f"; kept as they were: {', '.join(skipped)}" if skipped else ""),
            part="calibration",
        )
        return {**self._changed(), "skipped": skipped}

    def measurement_save(self, note: str = "") -> dict:
        cal = getattr(self.session, "calibration", None)
        described = cal.describe() if cal is not None else self.last_calibration
        if described is None or described["state"] != "done":
            raise ContractError("conflict", "there is no finished calibration to save")
        if self.simulated:
            raise ContractError("conflict", "a simulated calibration is not a measurement and is not saved")
        record = {
            "kind": "calibration",
            "mark": "MEDIDO",
            "saved_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "note": note,
            "environment": environment(),
            "speakers": self._snapshot.get("speakers", []),
            "settings": {
                **{k: v for k, v in asdict(self.settings).items() if k not in {"muted", "chain"}},
                **self.settings.aliases(),
            },
            "chain": self.settings.chain.to_json(),
            "installation": asdict(self.installation) if self.installation is not None else None,
            "calibration": described,
            "recalibration_history": self.session.recalibration_history if self.session is not None else [],
        }
        self.measurements_path.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
        path = self.measurements_path / f"calibracion-{stamp}.json"
        self._save_raw(cal, self.measurements_path / f"calibracion-{stamp}.npz")
        write_atomic(path, json.dumps(record, indent=2, ensure_ascii=False) + "\n")
        self.log(f"measurement saved to {path}", part="calibration")
        return {"path": str(path)}

    def calibration_dump(self) -> dict:
        """The last calibration's raw data (recording and references), for offline analysis.

        Also for a failed one: that is when it is most needed.
        """
        cal = getattr(self.session, "calibration", None)
        if cal is None or getattr(cal, "recording", None) is None:
            raise ContractError("conflict", "there is no calibration recording to dump")
        self.measurements_path.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
        path = self.measurements_path / f"calibracion-{stamp}-{cal.state}.npz"
        self._save_raw(cal, path)
        return {"path": str(path)}

    @staticmethod
    def _save_raw(cal: Any, path: Path) -> None:
        import numpy as np  # noqa: PLC0415

        if getattr(cal, "recording", None) is None:
            return
        np.savez_compressed(
            path,
            recording=cal.recording,
            names=np.array(list(cal.references)),
            references=np.stack(list(cal.references.values())),
            applied=np.array([cal.applied[n] for n in cal.references]),
            rate=cal.rate,
            before=cal.before,
        )

    def scan(self) -> dict:
        self.observer.scan()
        return {}

    def microphone_set(self, node: str | None) -> dict:
        """Change the microphone the calibration and the loop listen with, live.

        It has to be one the system lists now (not a monitor: a monitor hears the signal
        before the air, and would measure nothing). A running loop restarts with it; a
        calibration in progress refuses. The choice is kept in `service.json`.
        """
        available = [m["node"] for m in self.observer.view.get("microphones", [])]
        if node is not None and available and node not in available:
            raise ContractError("out_of_range", f"{node!r} is not a microphone the system lists: {available}")
        session = self.session
        if (
            session is not None
            and session.calibration is not None
            and session.calibration.state
            in {
                "running",
                "measuring",
            }
        ):
            raise ContractError("conflict", "a calibration is listening; cancel it or wait for it to finish")
        self.options = replace(self.options, microphone=node)
        if session is not None and session.loop is not None:
            session.disable_recalibration()
            if node is not None:
                session.enable_recalibration(node)
            else:
                self.status.recalibrate = False
        if self.config_path is not None and self.config_path.exists():
            data = json.loads(self.config_path.read_text())
            data["microphone"] = node
            write_atomic(self.config_path, json.dumps(data, indent=2) + "\n", mode=0o600)
        self.log(f"microphone: {node or 'none'}", part="calibration")
        return {}

    def connect(self, address: str) -> dict:
        try:
            self.observer.connect(address)
        except RuntimeError as exc:
            raise ContractError("conflict", str(exc)) from exc
        return {}

    def disconnect(self, address: str) -> dict:
        try:
            self.observer.disconnect(address)
        except RuntimeError as exc:
            raise ContractError("conflict", str(exc)) from exc
        return {}

    def forget(self, address: str) -> dict:
        """Forget a pairing. Refused for a speaker of the installation: remove it first."""
        sinks = {
            p.sink.removeprefix("bluez_output.").split(".")[0].replace("_", ":"): p.nombre
            for p in (self.installation.parlantes if self.installation else [])
        }
        if address in sinks:
            raise ContractError("conflict", f"{sinks[address]} is in the installation; remove it from there first")
        try:
            self.observer.forget(address)
        except RuntimeError as exc:
            raise ContractError("conflict", str(exc)) from exc
        return {}

    def speaker_add(self, address: str) -> dict:
        self._need_no_session("adding a speaker")
        outputs = {s.direccion: s for s in self.observer.outputs()}
        output = outputs.get(address)
        if output is None:
            raise ContractError("unavailable", f"{address} is not connected as an audio output; connect it first")
        if self.installation is None:
            self.installation = Instalacion(parlantes=[])
        if any(p.sink == output.nodo for p in self.installation.parlantes):
            raise ContractError("conflict", f"{output.descripcion} is already in the installation")
        if any(p.nombre == output.descripcion for p in self.installation.parlantes):
            raise ContractError("conflict", f"there is already a speaker named {output.descripcion!r}")
        layout = self.settings.layout
        taken = {control.role_of(p.pan, p.ambiente, layout) for p in self.installation.parlantes}
        free = next((r for r in control.ROLES[layout] if r not in taken), None)
        pan, ambience = control.ROLES[layout][free] if free else (0.0, 0.3)
        self.installation.parlantes.append(Parlante(output.descripcion, output.nodo, pan=pan, ambiente=ambience))
        self.log(f"added {output.descripcion} as {free or 'custom'}", part="session")
        return self._changed()

    def speaker_remove(self, speaker: str) -> dict:
        self._need_no_session("removing a speaker")
        target = self._speaker(speaker)
        self.installation.parlantes.remove(target)
        self.settings.muted.discard(speaker)
        self._set_chain(self.settings.chain.without_speaker(speaker))
        return self._changed()

    def service_start(self, name: str) -> dict:
        return self._service_action(name, "start")

    def service_stop(self, name: str) -> dict:
        return self._service_action(name, "stop")

    def service_restart(self, name: str) -> dict:
        return self._service_action(name, "restart")

    def _service_action(self, name: str, action: str) -> dict:
        if name == "session":
            if action in {"stop", "restart"}:
                recalibrate = self.status.recalibrate
                self._close_session("stopped", None)
            else:
                recalibrate = False
            if action in {"start", "restart"}:
                if action == "restart":
                    self.restarts["session"] = self.restarts.get("session", 0) + 1
                if self.session is None:
                    self.start(recalibrate=recalibrate)
            return {}
        if name == "recalibration":
            if action in {"stop", "restart"} and self.session is not None:
                self.recalibrate(active=False)
            if action in {"start", "restart"}:
                if action == "restart":
                    self.restarts["recalibration"] = self.restarts.get("recalibration", 0) + 1
                self.recalibrate(active=True)
            return {}
        if name == "source":
            session = self._need_session()
            current = session.source.kind if session.source else "system"
            current_name = session.source.name if session.source else None
            if action == "stop":
                return self.source("system")
            if action == "restart":
                self.restarts["source"] = self.restarts.get("source", 0) + 1
            return self.source(current if current != "system" or action == "restart" else "tone", current_name)
        known = {s["name"] for s in self._snapshot.get("services", [])}
        if name not in known:
            raise ContractError("not_found", f"no service {name!r}")
        raise ContractError("conflict", f"{name} is only observed: the panel does not start or stop it")

    def ab_start(self, a: str, b: str, match_loudness: bool = False) -> dict:  # noqa: FBT001, FBT002
        self._need_session()
        if a == b:
            raise ContractError("conflict", "an A/B test needs two different presets")
        for name in (a, b):
            self.preset_store.get(name)
        self.ab = ABTest(a=a, b=b, x=a, match=match_loudness)
        self.ab.draw()
        self.log(f"blind A/B started: {a} against {b}{' (loudness matched)' if match_loudness else ''}", part="ab")
        return self.ab_play("a")

    def ab_play(self, which: str) -> dict:
        if self.ab is None:
            raise ContractError("conflict", "no A/B test is running")
        ab = self.ab
        preset = {"a": ab.a, "b": ab.b, "x": ab.x}[which]
        self.preset_load(preset)
        ab.playing = which
        side = which if which != "x" else ("a" if ab.x == ab.a else "b")
        ab.applied = ab.compensation[side]
        motor = self.motor
        if motor is not None and hasattr(motor, "ganancia_comparacion_db"):
            # The same fade as the preset: the compensation jumps with it.
            motor.cortar(lambda: setattr(motor, "ganancia_comparacion_db", ab.applied))
        self._ab_settle()
        # The panel must not learn which preset X is from the state.
        self.preset = None if which == "x" else preset
        return {}

    def _ab_settle(self) -> None:
        meter = getattr(self.session, "quality", None)
        rate = self.options.rate
        self.ab.measure_from = meter.samples + round(AB_SETTLE_S * rate) if meter is not None else None

    def _ab_measure(self, meter: Any) -> None:
        """While A or B plays (never X: its loudness would say which it is), take the
        short-term loudness of the sum of the outputs; with `match`, compensate the louder."""
        ab = self.ab
        if ab is None or ab.playing not in {"a", "b"} or ab.measure_from is None or meter.samples < ab.measure_from:
            return
        heard = meter.outputs_short_term
        if not math.isfinite(heard):
            return
        ab.raw[ab.playing] = heard - ab.applied
        ab.taken[ab.playing] = meter.samples
        if not ab.match or ab.raw["a"] is None or ab.raw["b"] is None:
            return
        diff = ab.raw["b"] - ab.raw["a"]
        wanted = {"a": min(0.0, diff), "b": min(0.0, -diff)}
        if all(abs(wanted[k] - ab.compensation[k]) <= AB_RECOMPENSATE_LU for k in wanted):
            return
        ab.compensation = {k: round(v, 2) for k, v in wanted.items()}
        ab.applied = ab.compensation[ab.playing]
        if self.motor is not None and hasattr(self.motor, "ganancia_comparacion_db"):
            self.motor.ganancia_comparacion_db = ab.applied
        self._ab_settle()

    def ab_answer(self, x_is: str) -> dict:
        if self.ab is None:
            raise ContractError("conflict", "no A/B test is running")
        truth = "a" if self.ab.x == self.ab.a else "b"
        correct = x_is == truth
        self.ab.trials.append({"answer": x_is, "truth": truth, "correct": correct})
        self.ab.draw()
        self.ab.playing = None
        self.log(f"A/B answer {len(self.ab.trials)}: {'right' if correct else 'wrong'}", part="ab")
        return {"correct": correct, "truth": truth}

    def ab_stop(self) -> dict:
        if self.ab is None:
            raise ContractError("conflict", "no A/B test is running")
        right = sum(t["correct"] for t in self.ab.trials)
        self.ab_last = {
            "a": self.ab.a,
            "b": self.ab.b,
            "trials": self.ab.trials,
            "correct": right,
            "match_loudness": self.ab.match,
            "loudness_lu": self.ab.loudness(),
            "compensation_db": dict(self.ab.compensation),
        }
        if self.motor is not None and hasattr(self.motor, "ganancia_comparacion_db"):
            self.motor.ganancia_comparacion_db = 0.0
        self.log(f"blind A/B finished: {right} of {len(self.ab.trials)} right", part="ab")
        self.ab = None
        return self.ab_last

    # -- internals ------------------------------------------------------------------

    def _close_session(self, status: str, reason: str | None) -> None:
        if self.session is not None:
            reporter, self._cut_reporter = self._cut_reporter, None
            if reporter is not None:
                self.log(reporter.session_summary(), part="session")
            try:
                calibration = getattr(self.session, "calibration", None)
                if calibration is not None:
                    self.last_calibration = calibration.describe()
                self.session.close()
            finally:
                self.session, self.motor = None, None
                self.ab = None
                self.status.move(status, reason)
                self.log(
                    f"session closed{f': {reason}' if reason else ''}",
                    level=logging.WARNING if status == "error" else logging.INFO,
                    part="session",
                )
                if status == "error":
                    self._restore_radio_log("the session failed")
        elif status == "stopped" and self.status.status == "error":
            self.status.move("stopped")

    # -- the radio (spec 2026-10-02 §3) -----------------------------------------------------

    def radio_log(self, active: bool, mode: str | None = None) -> dict:  # noqa: FBT001 - the contract's field
        """Raise or restore the bluez5 log level, on a worker thread (a few `wpctl` calls)."""
        if self.log_level is None:
            raise ContractError("unavailable", "this service does not manage the radio log level")
        if self.log_level.pending:
            raise ContractError("conflict", "the radio log level is still changing")

        def done(status: dict) -> None:
            self.on_engine(lambda: self._radio_log_done(active, status))

        if active:
            self.log_level.enable_in_background(mode or "light", done)
        else:
            self.log_level.restore_in_background(done)
        return {**self.log_level.status(), "pending": True}

    def _radio_log_done(self, active: bool, status: dict) -> None:  # noqa: FBT001
        if status.get("error"):
            self.log(f"radio log: {status['error']}", level=logging.WARNING, part="radio")
        else:
            what = f"on ({status['mode']})" if active else "back to how it was"
            self.log(f"radio log {what}; the change is written in {self.log_level.changes_file}", part="radio")
        self._changed(dirty=False)

    def _restore_radio_log(self, why: str) -> None:
        """The kill switch: every way out of a session or the program reaches this."""
        level = self.log_level
        if level is None or level.mode is None:
            return
        try:
            status = level.restore()
        except Exception as exc:  # noqa: BLE001 - logged; a failed restore must not hide the original exit
            self.log(f"radio log: could not restore ({why}): {exc!r}", level=logging.ERROR, part="radio")
            return
        if status.get("error"):
            self.log(f"radio log: {status['error']} ({why})", level=logging.ERROR, part="radio")
        else:
            self.log(f"radio log restored: {why}", part="radio")

    def _recover_radio_log(self) -> None:
        if self.log_level is None:
            return
        try:
            if self.log_level.recover():
                self.log("radio log: reverted a change a previous run left behind", level=logging.WARNING, part="radio")
        except Exception as exc:  # noqa: BLE001
            self.log(f"radio log: could not check for a change left behind: {exc!r}", level=logging.ERROR, part="radio")

    def _on_radio_drop(self, drop: Drop) -> None:
        """On the radio monitor's thread: count it, and put it in the playing session's cut log."""
        self.radio_drops += 1
        cuts = getattr(self.session, "cuts", None)
        if cuts is not None:
            radio_module.to_cutlog(cuts)(drop)

    def _speaker_by_address(self, address: str) -> str | None:
        """`AA:BB:…` → the installation's speaker name (any thread: it only reads)."""
        installation = self.installation
        for p in installation.parlantes if installation is not None else []:
            if p.sink.removeprefix("bluez_output.").split(".")[0].replace("_", ":").upper() == address.upper():
                return p.nombre
        return None

    def radio_view(self) -> dict:
        """The `radio` event and `state.radio` (spec §6.3), from any thread."""
        if self.radio is None:
            view = {"available": False, "reason": "este servicio no sigue el registro de radio", "speakers": {}}
        else:
            view = self.radio.snapshot()
            if getattr(self.radio, "simulated", False):
                view["simulated"] = True
        view["drops_seen"] = self.radio_drops
        return view

    def _session_log(self, kind: str, **fields: Any) -> None:
        part, level = PARTS.get(kind, ("session", logging.INFO))
        self.log(f"{kind}: {fields.get('motivo', '')}", level=level, part=part)

    def _publish(self) -> None:
        """Build a fresh snapshot. Only the engine thread calls it."""
        self._snapshot = build_snapshot(self)


def environment() -> dict:
    """What a measurement records about the machine (CLAUDE.md: each measurement notes it)."""

    def run(*args: str) -> str:
        try:
            return subprocess.run(args, capture_output=True, text=True, timeout=3, check=False).stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            return ""

    return {
        "host": platform.node(),
        "kernel": platform.release(),
        "pipewire": run("pipewire", "--version").splitlines()[-1:] or None,
        "wireplumber": run("wireplumber", "--version").splitlines()[-1:] or None,
        "bluez": run("bluetoothctl", "--version") or None,
        "aurasync": __version__,
    }


def _default_motor(installation: Instalacion, rate: int, settings: Settings) -> Motor:
    # The chain decides which stages are on from the first sample: nothing ramps from a
    # default the listener did not choose.
    return Motor(
        installation,
        rate,
        extraer_ambiente=True,
        decorrelar=True,
        volumen_db=settings.volume_db,
        ecualizar=True,
        chain=settings.chain,
        bloque=settings.block_size,
    )


def _alias_of(aliases: dict[str, tuple[str, str]], stage: str, param: str) -> str:
    """The old contract field that is this chain knob."""
    return next(name for name, target in aliases.items() if target == (stage, param))


def _by(actor: str | None) -> str:
    """The log's "who": ` · by <name>`, or nothing for an order from inside the program."""
    return f" · by {actor}" if actor else ""


def _brief(args: dict) -> str:
    return json.dumps(args, ensure_ascii=False) if args else ""


def serve(
    service: Service,
    config: ServiceConfig,
    *,
    bind: str | None = None,
    port: int | None = None,
    announce: Callable[[str], None] = print,
    show_token: bool | None = None,
    directory: Path | None = None,
) -> int:
    """Open the port, run the engine on this thread, close in order. Returns the exit code.

    `directory` holds `clients.json` and `tls/` (`remote.py`); without one the clients live in
    memory and HTTPS cannot be on.

    The link with the token (and its QR) is printed only to a terminal. When the output goes
    to a file, the token would stay in it: on 2026-10-01 it ended up in a log inside the
    repository (`servicio.sh` redirects there). Then only the addresses are printed, and the
    token stays in `service.json`.
    """
    if show_token is None:
        show_token = sys.stdout.isatty()
    if config.tls and directory is None:
        announce("tls is on but there is no directory for the certificates")
        return 1

    def show_code(code: str, seconds: float) -> None:
        # The terminal only: the log buffer reaches every `read` client.
        if show_token:
            announce(f"  pairing code: {code} (valid {seconds:.0f} s)")

    try:
        listeners = remote.open_listeners(
            service,
            config,
            bind=bind or config.bind,
            port=port if port is not None else config.port,
            https_port=None,
            config_dir=directory,
            log=service.log,
            show_code=show_code,
        )
    except OSError as exc:
        announce(f"cannot listen on {bind or config.bind}:{port or config.port} (or https {config.https_port}): {exc}")
        return 1
    server = listeners.http
    listeners.start()
    host, real_port = server.server_address[:2]
    urls = lan_urls(host, real_port)
    service.pairing = {"urls": urls}
    announce(f"aurasync service {__version__} listening on http://{host}:{real_port}/")
    if not show_token:
        announce("  panel: " + " · ".join(urls) + "  (the token is in service.json; not printed to a file)")
    else:
        announce("  panel (the link carries the token; keep it private):")
        for url in urls:
            announce(f"    {url}/?t={config.token}")
    lan = [u for u in urls if "127.0.0.1" not in u]
    if lan and show_token:
        from aurasync import rest  # noqa: PLC0415

        # With HTTPS the phone gets the PWA's pairing link (no token: it pairs and is approved).
        # The token QR made any phone that scanned it admin forever (d-7c8794-37f9bc).
        link = rest.pairing_link(urls, listeners.access.tls if listeners.access else None)
        if link:
            qr = rest.qr_terminal(link)
            heading = "  phone, same network (the panel app; it pairs, no token in the QR):"
        else:
            qr = rest.qr_terminal(f"{lan[0]}/?t={config.token}")
            heading = "  phone, same network (the link carries the token; keep it private):"
        if qr:
            announce(heading)
            announce(qr)
    remote.announce_remote(listeners, urls, announce, show_token=show_token)

    def as_ctrl_c(*_) -> None:
        raise KeyboardInterrupt

    previous = signal.signal(signal.SIGTERM, as_ctrl_c)
    try:
        service.run()
    except KeyboardInterrupt:
        # A second Ctrl-C exits at once: P1 measured that the virtual sink disappears
        # even after kill -9 (docs/research/experimentos/07-…).
        signal.signal(signal.SIGINT, lambda *_: os._exit(130))
        service.log("closing")
        service._close_session("stopped", None)  # noqa: SLF001
    finally:
        signal.signal(signal.SIGTERM, previous)
        # Let the reply to `shutdown` leave before the port closes.
        time.sleep(0.2)
        listeners.close()
    announce("aurasync service stopped")
    return 0


def lan_urls(host: str, port: int) -> list[str]:
    """Every address the panel can be reached at, the most likely home network first.

    The interface of the default route is not always the home network: with a VPN up it was
    the WireGuard address (2026-10-01), and the phone on the Wi-Fi could not use it.
    """
    if host not in {"0.0.0.0", ""}:  # noqa: S104
        return [f"http://{host}:{port}"]
    addresses = sorted(local_addresses(), key=_home_first)
    return [f"http://{a}:{port}" for a in [*addresses, "127.0.0.1"]]


def local_addresses() -> list[str]:
    """The machine's IPv4 addresses, without loopback (`ip -4 -o addr`)."""
    try:
        out = subprocess.run(
            ["ip", "-4", "-o", "addr", "show"], capture_output=True, text=True, timeout=3, check=False
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        out = ""
    found = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 4 and parts[2] == "inet" and parts[1] != "lo":  # noqa: PLR2004
            found.append(parts[3].split("/")[0])
    lan = lan_address()
    if lan and lan not in found:
        found.append(lan)
    return found


def _home_first(address: str) -> tuple[int, str]:
    # 192.168/16 is where home routers put phones; 10/8 is often a VPN or a container.
    return (0 if address.startswith("192.168.") else 1 if address.startswith("172.") else 2, address)


def lan_address() -> str | None:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        try:
            # No packet is sent: connecting a UDP socket only picks the outgoing interface.
            probe.connect(("192.0.2.1", 9))
            address = probe.getsockname()[0]
        except OSError:
            return None
    return None if address.startswith("127.") else address
