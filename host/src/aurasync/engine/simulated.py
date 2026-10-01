"""Motor simulado: servicios, parlantes, BIG, reloj y niveles falsos para probar el
panel (d-7c8794-b1eaac, docs/research/09).

No captura audio ni toca Bluetooth. Todo lo que varía en el tiempo se deriva del reloj
inyectado `now()`, nunca de sumar deltas, así que un test puede pedir el estado de
cualquier instante. Las transiciones de los servicios se anotan en el log cuando
`poll()` las observa; `run()` llama a `poll()` en segundo plano.
"""

import asyncio
import contextlib
import logging
import math
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from aurasync.engine.base import Command, CommandError, check_against_state
from aurasync.state import (
    LIVE_CONFIG_KEYS,
    MODES,
    BigState,
    CalibrationResult,
    CalibrationState,
    ClockState,
    ControllerState,
    EngineConfig,
    EngineInfo,
    Latency,
    Meter,
    Service,
    Snapshot,
    Source,
    Speaker,
)

START_SECONDS = 0.6
STOP_SECONDS = 0.3
SETTLE_SECONDS = 2.0  # de "transmitir" a emisor corriendo: cadena de 3 arranques
SYNC_SECONDS = 1.5
CALIBRATION_SECONDS = 8.0
SCAN_SECONDS = 3.0
REFERENCE_MASTER_DB = -20.0
TONE_DB = -12.0
HISTORY_SECONDS = 60
QUEUE_TARGET = 3
CAPTURE_MS = 21.3  # un quantum de 1024 muestras a 48 kHz
CODEC_MS = 12.5  # trama LC3 de 10 ms + 2,5 ms de retardo algorítmico
TRANSPORT_MS = {"low_latency": 20.0, "high_reliability": 65.0}
PIPELINE = ("controller", "capture", "dsp", "emitter")

log = logging.getLogger("aurasync.engine")

# Direcciones administradas localmente (bit 0x02): nunca coinciden con un parlante real.
_SPEAKERS = (
    ("02:00:00:00:00:01", "Go 4 (simulado 1)", "JBL Go 4"),
    ("02:00:00:00:00:02", "Go 4 (simulado 2)", "JBL Go 4"),
    ("02:00:00:00:00:03", "Go 4 (simulado 3)", "JBL Go 4"),
    ("02:00:00:00:00:04", "Charge 6 (simulado)", "JBL Charge 6"),
)
_FOUND_BY_SCAN = ("02:00:00:00:00:05", "Flip 7 (simulado)", "JBL Flip 7")
_BASE_DB = {"FL": -18.0, "FR": -18.0, "FC": -20.0, "RL": -27.0, "RR": -27.0, "RC": -30.0}
_CALIBRATION = {  # canal: (retardo ms, ganancia dB, confianza)
    "FL": (0.0, 0.0, 0.94),
    "FR": (0.3, -0.5, 0.93),
    "FC": (0.6, -4.0, 0.9),
    "RL": (2.1, 1.5, 0.88),
    "RR": (2.4, -3.0, 0.9),
    "RC": (2.2, -3.5, 0.87),
}
_FAILURES = {
    "controller": "el puerto serie se cerró: la SuperMini se desconectó (simulado)",
    "capture": "la captura terminó con código 1 (simulado)",
    "dsp": "excepción en el upmix: el bloque llegó con 2 canales y se esperaban 4 (simulado)",
    "emitter": "HCI LE Create BIG respondió 0x0C, Command Disallowed (simulado)",
}


@dataclass
class _Svc:
    name: str
    label: str
    kind: str
    managed: bool
    depends_on: tuple[str, ...] = ()
    fixed_state: str | None = None
    fixed_detail: str = ""
    target: str = "stopped"
    ready_at: float | None = None
    stop_at: float | None = None
    failed: str | None = None
    restarts: int = 0
    pid: int | None = None
    started_wall: str | None = None
    dependents: list[str] = field(default_factory=list)

    def state(self, t: float) -> str:
        if self.fixed_state:
            return self.fixed_state
        if self.failed:
            return "failed"
        if self.target == "running":
            return "starting" if self.ready_at is None or t < self.ready_at else "running"
        if self.stop_at is not None and t < self.stop_at + STOP_SECONDS:
            return "stopping"
        return "stopped"


def _drift_ppm(t: float) -> float:
    return 18.0 + 3.0 * math.sin(t / 40.0)


def _system_services(platform: str) -> list[_Svc]:
    names = ("coreaudiod",) if platform == "darwin" else ("bluetoothd", "pipewire", "wireplumber")
    return [
        _Svc(name, name, "sistema", managed=False, fixed_state="running", fixed_detail="observado (simulado)")
        for name in names
    ]


class SimulatedEngine:
    kind = "simulated"

    def __init__(
        self,
        *,
        now: Callable[[], float],
        wallclock: Callable[[], datetime] = lambda: datetime.now().astimezone(),
        platform: str = sys.platform,
    ) -> None:
        self._now = now
        self._wallclock = wallclock
        self._seq = 0
        self._mode = "quad"
        self._source = Source("system", None)
        self._master_db = REFERENCE_MASTER_DB
        self._config = EngineConfig()
        self._tone: tuple[str, float] | None = None  # (canal, termina en)
        self._calibration_started_at: float | None = None
        self._calibration_measured_at: str | None = None
        self._scan_until: float | None = None
        self._lost = False  # el emisor falló después de haber transmitido
        self._next_pid = 41_000
        self._last_metrics = 0.0
        self._capture_detail = (
            "process tap de Core Audio (simulado)" if platform == "darwin" else "pw-record --raw, 4 canales (simulado)"
        )
        services = (
            _Svc("controller", "Controlador", "enlace", managed=True),
            _Svc("capture", "Captura", "proceso", managed=True),
            _Svc("dsp", "DSP y reloj", "tarea", managed=True, depends_on=("capture",)),
            _Svc("emitter", "Emisor", "tarea", managed=True, depends_on=("controller", "dsp")),
            _Svc(
                "bass",
                "Asistente BASS",
                "tarea",
                managed=False,
                depends_on=("controller",),
                fixed_state="unavailable",
                fixed_detail="espera E4: no se sabe si los JBL exponen BASS",
            ),
            _Svc(
                "panel",
                "Panel web",
                "tarea",
                managed=False,
                fixed_state="running",
                fixed_detail="este servidor; no se detiene desde sí mismo",
            ),
            *_system_services(platform),
        )
        self._svcs: dict[str, _Svc] = {svc.name: svc for svc in services}
        for svc in self._svcs.values():
            for dep in svc.depends_on:
                self._svcs[dep].dependents.append(svc.name)
        self._speakers = {
            address: Speaker(
                address=address,
                name=name,
                model=model,
                firmware="simulado",
                channel=None,
                bis_index=None,
                state="seen",
                rssi_dbm=None,
                volume_db=0.0,
                muted=False,
                delay_ms=0.0,
                gain_db=0.0,
            )
            for address, name, model in (*_SPEAKERS, _FOUND_BY_SCAN)
        }
        self._hidden = {_FOUND_BY_SCAN[0]}
        self._last_states = {name: svc.state(now()) for name, svc in self._svcs.items()}
        log.info("motor simulado listo: nada de esto viene de un parlante real")

    # -- estado ---------------------------------------------------------------

    def snapshot(self) -> Snapshot:
        self._seq += 1
        t = self._now()
        running = self._svcs["emitter"].state(t) == "running"
        return Snapshot(
            seq=self._seq,
            engine=EngineInfo(
                kind=self.kind,
                running=running,
                mode=self._mode,
                source=Source(self._source.kind, self._source.name),
                master_db=self._master_db,
                scanning=self._scanning(t),
            ),
            controller=self._controller(t),
            clock=self._clock(t),
            speakers=[
                self._speaker_at(s, t, i) for i, s in enumerate(self._speakers.values()) if self._visible(s.address, t)
            ],
            meters=self._meters(t),
            calibration=self._calibration(t),
            services=[self._service_at(svc, t) for svc in self._svcs.values()],
            config=EngineConfig(**vars(self._config)),
            latency=self._latency(),
        )

    def _scanning(self, t: float) -> bool:
        return self._scan_until is not None and t < self._scan_until

    def _visible(self, address: str, t: float) -> bool:
        if address not in self._hidden:
            return True
        return self._scan_until is not None and t >= self._scan_until

    def _service_at(self, svc: _Svc, t: float) -> Service:
        state = svc.state(t)
        uptime = round(t - svc.ready_at, 1) if state == "running" and svc.ready_at is not None else None
        return Service(
            name=svc.name,
            label=svc.label,
            kind=svc.kind,
            managed=svc.managed,
            state=state,
            depends_on=list(svc.depends_on),
            pid=svc.pid if state in {"starting", "running", "stopping"} else None,
            started_at=svc.started_wall if state in {"starting", "running"} else None,
            uptime_s=uptime,
            restarts=svc.restarts,
            detail=self._detail(svc),
            last_error=svc.failed,
        )

    def _detail(self, svc: _Svc) -> str:
        c = self._config
        match svc.name:
            case "controller":
                return "serial:/dev/cu.usbmodem-SIM · hci_uart_iso_timesync (simulado)"
            case "capture":
                return self._capture_detail
            case "dsp":
                return f"upmix {c.upmix} · retardo trasero {c.rear_delay_ms:g} ms"
            case "emitter":
                return (
                    f"1 BIG · {len(MODES[self._mode])} BIS · {c.bitrate_kbps} kbps · "
                    f"PD {c.presentation_delay_us / 1000:g} ms · {c.transport} · «{c.broadcast_name}»"
                )
            case _:
                return svc.fixed_detail

    def _controller(self, t: float) -> ControllerState:
        link = self._svcs["controller"].state(t)
        present = link in {"starting", "running", "stopping"}
        emitter = self._svcs["emitter"].state(t)
        big_state = {"running": "active", "starting": "creating", "failed": "error"}.get(emitter, "idle")
        return ControllerState(
            present=present,
            port="serial:/dev/cu.usbmodem-SIM" if present else None,
            unit="SuperMini (simulada)" if present else None,
            firmware="hci_uart_iso_timesync (simulado)" if present else None,
            iso_broadcaster=True if link == "running" else None,
            big=BigState(
                state=big_state,
                num_bis=len(MODES[self._mode]) if big_state in {"active", "creating"} else 0,
                presentation_delay_us=self._config.presentation_delay_us,
            ),
        )

    def _speaker_at(self, speaker: Speaker, t: float, index: int) -> Speaker:
        emitter = self._svcs["emitter"]
        synced = emitter.state(t) == "running" and emitter.ready_at is not None and t >= emitter.ready_at + SYNC_SECONDS
        if speaker.channel is None:
            state = "seen"
        elif synced:
            state = "synced"
        elif self._lost:
            state = "lost"
        else:
            state = "seen"
        return Speaker(
            address=speaker.address,
            name=speaker.name,
            model=speaker.model,
            firmware=speaker.firmware,
            channel=speaker.channel,
            bis_index=speaker.bis_index,
            state=state,
            rssi_dbm=round(-55 - 5 * index + 2 * math.sin(t / 7 + index)),
            volume_db=speaker.volume_db,
            muted=speaker.muted,
            delay_ms=speaker.delay_ms,
            gain_db=speaker.gain_db,
        )

    def _clock(self, t: float) -> ClockState:
        emitter = self._svcs["emitter"]
        if emitter.state(t) != "running" or emitter.ready_at is None:
            return ClockState(queue_target=QUEUE_TARGET)
        elapsed = t - emitter.ready_at
        samples = min(HISTORY_SECONDS, int(elapsed))
        drift = _drift_ppm(t)
        return ClockState(
            queue_sdus=round(QUEUE_TARGET + 0.4 * math.sin(t / 3), 2),
            queue_target=QUEUE_TARGET,
            ratio_ppm=round(-drift, 2),
            drift_ppm=round(drift, 2),
            underruns=int(elapsed // 600),
            overruns=0,
            history=[round(_drift_ppm(t - (samples - 1 - k)), 2) for k in range(samples)],
        )

    def _audio_flows(self, t: float) -> bool:
        return all(self._svcs[name].state(t) == "running" for name in ("capture", "dsp"))

    def _meters(self, t: float) -> dict[str, Meter]:
        channels = MODES[self._mode]
        if not self._audio_flows(t):
            return {channel: Meter() for channel in channels}
        transmitting = self._svcs["emitter"].state(t) == "running"
        if transmitting and self._tone is not None and t < self._tone[1]:
            return {
                channel: Meter(TONE_DB, TONE_DB + 3) if channel == self._tone[0] else Meter() for channel in channels
            }
        offset = self._master_db - REFERENCE_MASTER_DB
        meters = {}
        for i, channel in enumerate(channels):
            rms = _BASE_DB[channel] + 4 * math.sin(t * (0.9 + 0.37 * i) + i) + offset
            meters[channel] = Meter(rms_db=round(min(rms, 0.0), 1), peak_db=round(min(rms + 6, 0.0), 1))
        return meters

    def _calibration(self, t: float) -> CalibrationState:
        if self._calibration_started_at is None:
            return CalibrationState()
        progress = min(1.0, (t - self._calibration_started_at) / CALIBRATION_SECONDS)
        if progress < 1.0:
            return CalibrationState(state="running", progress=round(progress, 3), simulated=True)
        if self._calibration_measured_at is None:
            self._calibration_measured_at = self._wallclock().isoformat(timespec="seconds")
            log.info("calibración simulada terminada")
        results = [CalibrationResult(channel, *_CALIBRATION[channel]) for channel in MODES[self._mode]]
        return CalibrationState(
            state="done",
            progress=1.0,
            results=results,
            measured_at=self._calibration_measured_at,
            simulated=True,
        )

    def _latency(self) -> Latency:
        transport = TRANSPORT_MS[self._config.transport]
        presentation = self._config.presentation_delay_us / 1000
        total = CAPTURE_MS + CODEC_MS + transport + presentation
        return Latency(
            capture_ms=CAPTURE_MS,
            codec_ms=CODEC_MS,
            transport_ms=transport,
            presentation_ms=presentation,
            total_ms=round(total, 1),
        )

    # -- servicios ------------------------------------------------------------

    def _svc_log(self, name: str) -> logging.Logger:
        return logging.getLogger(f"aurasync.svc.{name}")

    def _start(self, name: str, t: float, *, restart: bool = False) -> None:
        svc = self._svcs[name]
        if svc.target == "running" and not svc.failed and not restart:
            return  # iniciar lo que ya corre no hace nada
        begin = max([t] + [self._svcs[dep].ready_at or t for dep in svc.depends_on])
        svc.target = "running"
        svc.failed = None
        svc.ready_at = begin + START_SECONDS
        svc.stop_at = None
        svc.started_wall = self._wallclock().isoformat(timespec="seconds")
        if restart:
            svc.restarts += 1
        if svc.kind == "proceso":
            self._next_pid += 13
            svc.pid = self._next_pid
        if name == "emitter":
            self._lost = False
        pid = f" (pid {svc.pid})" if svc.kind == "proceso" else ""
        verb = "reiniciando" if restart else "iniciando"
        self._svc_log(name).info("%s%s: %s", verb, pid, self._detail(svc))

    def _stop(self, name: str, t: float, *, reason: str) -> None:
        svc = self._svcs[name]
        for dependent in svc.dependents:
            other = self._svcs[dependent]
            if other.managed and other.state(t) != "stopped":
                self._stop(dependent, t, reason=f"se detuvo {name}, del que depende")
        if svc.target == "stopped" and not svc.failed:
            return
        svc.target = "stopped"
        svc.failed = None
        svc.stop_at = t
        if name == "emitter":
            self._tone = None
            self._calibration_started_at = None
        self._svc_log(name).info("deteniendo: %s", reason)

    def _fail(self, name: str, t: float) -> None:
        emitter_was_running = self._svcs["emitter"].state(t) == "running"
        svc = self._svcs[name]
        for dependent in svc.dependents:
            if self._svcs[dependent].managed:
                self._stop(dependent, t, reason=f"falló {name}, del que depende")
        svc.target = "stopped"
        svc.failed = _FAILURES.get(name, "falla simulada")
        svc.stop_at = t
        if emitter_was_running and self._svcs["emitter"].state(t) != "running":
            self._lost = True
        if name == "emitter":
            self._tone = None
            self._calibration_started_at = None
        self._svc_log(name).error("%s", svc.failed)

    def poll(self) -> None:
        """Anota en el log las transiciones de los servicios y, cada 2 s, el reloj."""
        t = self._now()
        for name, svc in self._svcs.items():
            state = svc.state(t)
            previous = self._last_states.get(name)
            if state != previous:
                level = logging.ERROR if state == "failed" else logging.INFO
                self._svc_log(name).log(level, "%s → %s", previous, state)
                self._last_states[name] = state
        if self._svcs["emitter"].state(t) == "running" and t - self._last_metrics >= 2:  # noqa: PLR2004
            self._last_metrics = t
            c = self._clock(t)
            self._svc_log("dsp").debug(
                "cola %.1f SDU (objetivo %d) · drift %.1f ppm · corrección %.1f ppm",
                c.queue_sdus,
                c.queue_target,
                c.drift_ppm,
                c.ratio_ppm,
            )

    async def run(self) -> None:
        while True:
            with contextlib.suppress(Exception):
                self.poll()
            await asyncio.sleep(0.25)

    # -- órdenes --------------------------------------------------------------

    async def apply(self, command: Command) -> None:
        check_against_state(command, self.snapshot())
        args = command.args
        t = self._now()
        match command.name:
            case "start":
                for name in PIPELINE:
                    self._start(name, t)
                log.info("transmitir: 1 BIG con %d BIS", len(MODES[self._mode]))
            case "stop":
                for name in reversed(PIPELINE):
                    self._stop(name, t, reason="se pidió detener la transmisión")
                self._tone = None
                self._calibration_started_at = None
                log.info("transmisión detenida")
            case "service_start":
                self._start(args["name"], t)
            case "service_stop":
                self._stop(args["name"], t, reason="se pidió desde el panel")
            case "service_restart":
                self._start(args["name"], t, restart=True)
            case "service_fail":
                self._fail(args["name"], t)
            case "set_mode":
                self._mode = args["mode"]
                for speaker in self._speakers.values():
                    speaker.channel = None
                    speaker.bis_index = None
                self._calibration_started_at = None
                log.warning("modo %s: se borraron las asignaciones de canal", self._mode)
            case "set_source":
                self._source = Source(args["kind"], args["name"])
                log.info("fuente: %s%s", args["kind"], f" ({args['name']})" if args["name"] else "")
            case "set_master":
                self._master_db = args["db"]
            case "set_speaker_volume":
                self._speakers[args["address"]].volume_db = args["db"]
            case "set_mute":
                self._speakers[args["address"]].muted = args["muted"]
            case "set_speaker_trim":
                speaker = self._speakers[args["address"]]
                if "delay_ms" in args:
                    speaker.delay_ms = args["delay_ms"]
                if "gain_db" in args:
                    speaker.gain_db = args["gain_db"]
                log.info("%s: retardo %g ms, ganancia %g dB", speaker.name, speaker.delay_ms, speaker.gain_db)
            case "assign":
                speaker = self._speakers[args["address"]]
                speaker.channel = args["channel"]
                speaker.bis_index = MODES[self._mode].index(args["channel"]) + 1
                log.info("%s → %s (BIS %d)", speaker.name, args["channel"], speaker.bis_index)
            case "unassign":
                speaker = self._speakers[args["address"]]
                speaker.channel = None
                speaker.bis_index = None
                log.info("%s sin canal", speaker.name)
            case "tone":
                self._tone = (args["channel"], t + args["seconds"])
                log.info("tono de %g s en %s", args["seconds"], args["channel"])
            case "scan":
                if not self._scanning(t):
                    self._scan_until = t + SCAN_SECONDS
                    log.info("buscando parlantes durante %g s", SCAN_SECONDS)
            case "set_config":
                self._set_config(args["key"], args["value"], t)
            case "calibrate":
                self._calibration_started_at = t
                self._calibration_measured_at = None
                log.info("calibración simulada en curso")
            case "calibrate_cancel":
                self._calibration_started_at = None
                log.info("calibración cancelada")
            case "calibration_apply":
                results = {r.channel: r for r in self._calibration(t).results}
                for speaker in self._speakers.values():
                    if speaker.channel in results:
                        speaker.delay_ms = results[speaker.channel].delay_ms
                        speaker.gain_db = results[speaker.channel].gain_db
                log.info("ajustes de la calibración aplicados a %d parlantes", len(results))
            case "save_measurement":
                msg = "motor simulado: una medición simulada no se guarda como MEDIDO"
                raise CommandError(msg)

    def _set_config(self, key: str, value: object, t: float) -> None:
        setattr(self._config, key, value)
        log.info("configuración: %s = %s", key, value)
        emitter = self._svcs["emitter"]
        if key not in LIVE_CONFIG_KEYS and emitter.target == "running" and not emitter.failed:
            self._start("emitter", t, restart=True)
