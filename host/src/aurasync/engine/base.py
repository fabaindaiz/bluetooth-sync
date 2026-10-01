"""La interfaz de un motor y la validación de órdenes (docs/research/09 §5).

Toda orden pasa por `parse_command()` (forma, tipos y rangos) y por
`check_against_state()` (lo que depende del estado) antes de tocar un motor. Una
orden incompleta se rechaza con un motivo; nunca se completa con un valor por
defecto.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from aurasync.state import (
    BROADCAST_NAME_MAX,
    CONFIG_CHOICES,
    FIXED_CONFIG_KEYS,
    MODES,
    REAR_DELAY_RANGE_MS,
    SOURCE_KINDS,
    Snapshot,
)

MASTER_RANGE_DB = (-60.0, 0.0)
SPEAKER_RANGE_DB = (-60.0, 6.0)
TONE_RANGE_S = (0.5, 10.0)
TRIM_DELAY_RANGE_MS = (0.0, 100.0)
TRIM_GAIN_RANGE_DB = (-12.0, 6.0)
CONFIG_KEYS = (*CONFIG_CHOICES, "rear_delay_ms", "broadcast_name")
ALL_CHANNELS = frozenset(channel for channels in MODES.values() for channel in channels)


class CommandError(ValueError):
    """Una orden que no se ejecuta, con un motivo que el panel muestra tal cual."""


@dataclass(frozen=True)
class Command:
    name: str
    args: Mapping[str, Any]


class Engine(Protocol):
    kind: str

    def snapshot(self) -> Snapshot: ...

    async def apply(self, command: Command) -> None: ...


def _number(args: Mapping[str, Any], key: str, low: float, high: float) -> float:
    value = args.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        msg = f"`{key}` tiene que ser un número"
        raise CommandError(msg)
    if not low <= value <= high:
        msg = f"`{key}` tiene que estar entre {low:g} y {high:g}"
        raise CommandError(msg)
    return float(value)


def _text(args: Mapping[str, Any], key: str) -> str:
    value = args.get(key)
    if not isinstance(value, str) or not value:
        msg = f"falta `{key}`"
        raise CommandError(msg)
    return value


def _bool(args: Mapping[str, Any], key: str) -> bool:
    value = args.get(key)
    if not isinstance(value, bool):
        msg = f"`{key}` tiene que ser true o false"
        raise CommandError(msg)
    return value


def _channel(args: Mapping[str, Any]) -> str:
    channel = _text(args, "channel")
    if channel not in ALL_CHANNELS:
        msg = f"canal desconocido: {channel}"
        raise CommandError(msg)
    return channel


def _no_args(_args: Mapping[str, Any]) -> dict[str, Any]:
    return {}


def _set_mode(args: Mapping[str, Any]) -> dict[str, Any]:
    mode = _text(args, "mode")
    if mode not in MODES:
        msg = f"modo desconocido: {mode} (hay {', '.join(MODES)})"
        raise CommandError(msg)
    return {"mode": mode}


def _set_source(args: Mapping[str, Any]) -> dict[str, Any]:
    kind = _text(args, "kind")
    if kind not in SOURCE_KINDS:
        msg = f"fuente desconocida: {kind}"
        raise CommandError(msg)
    name = _text(args, "name") if kind in {"app", "file"} else None
    return {"kind": kind, "name": name}


def _set_master(args: Mapping[str, Any]) -> dict[str, Any]:
    return {"db": _number(args, "db", *MASTER_RANGE_DB)}


def _set_speaker_volume(args: Mapping[str, Any]) -> dict[str, Any]:
    return {"address": _text(args, "address"), "db": _number(args, "db", *SPEAKER_RANGE_DB)}


def _set_mute(args: Mapping[str, Any]) -> dict[str, Any]:
    return {"address": _text(args, "address"), "muted": _bool(args, "muted")}


def _assign(args: Mapping[str, Any]) -> dict[str, Any]:
    return {"address": _text(args, "address"), "channel": _channel(args)}


def _unassign(args: Mapping[str, Any]) -> dict[str, Any]:
    return {"address": _text(args, "address")}


def _tone(args: Mapping[str, Any]) -> dict[str, Any]:
    return {"channel": _channel(args), "seconds": _number(args, "seconds", *TONE_RANGE_S)}


def _service(args: Mapping[str, Any]) -> dict[str, Any]:
    return {"name": _text(args, "name")}


def _speaker_trim(args: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"address": _text(args, "address")}
    if "delay_ms" in args:
        out["delay_ms"] = _number(args, "delay_ms", *TRIM_DELAY_RANGE_MS)
    if "gain_db" in args:
        out["gain_db"] = _number(args, "gain_db", *TRIM_GAIN_RANGE_DB)
    if len(out) == 1:
        msg = "falta `delay_ms` o `gain_db`"
        raise CommandError(msg)
    return out


def _set_config(args: Mapping[str, Any]) -> dict[str, Any]:
    key = _text(args, "key")
    if key in FIXED_CONFIG_KEYS:
        msg = f"`{key}` es fijo: sin los datos de fabricante de Harman, los JBL ignoran la transmisión"
        raise CommandError(msg)
    if key not in CONFIG_KEYS:
        msg = f"clave de configuración desconocida: {key}"
        raise CommandError(msg)
    if "value" not in args:
        msg = "falta `value`"
        raise CommandError(msg)
    value = args["value"]
    if key == "rear_delay_ms":
        return {"key": key, "value": _number(args, "value", *REAR_DELAY_RANGE_MS)}
    if key == "broadcast_name":
        if not isinstance(value, str) or not value or len(value) > BROADCAST_NAME_MAX or not value.isprintable():
            msg = f"el nombre de la transmisión va de 1 a {BROADCAST_NAME_MAX} caracteres imprimibles"
            raise CommandError(msg)
        return {"key": key, "value": value}
    choices = CONFIG_CHOICES[key]
    if isinstance(value, bool) or value not in choices or type(value) is not type(choices[0]):
        msg = f"`{key}` acepta: {', '.join(str(c) for c in choices)}"
        raise CommandError(msg)
    return {"key": key, "value": value}


_PARSERS = {
    "start": _no_args,
    "stop": _no_args,
    "set_mode": _set_mode,
    "set_source": _set_source,
    "set_master": _set_master,
    "set_speaker_volume": _set_speaker_volume,
    "set_mute": _set_mute,
    "assign": _assign,
    "unassign": _unassign,
    "tone": _tone,
    "calibrate": _no_args,
    "calibrate_cancel": _no_args,
    "calibration_apply": _no_args,
    "save_measurement": _no_args,
    "set_speaker_trim": _speaker_trim,
    "set_config": _set_config,
    "scan": _no_args,
    "service_start": _service,
    "service_stop": _service,
    "service_restart": _service,
    "service_fail": _service,
}

_ALLOWED_KEYS = {
    "start": set(),
    "stop": set(),
    "set_mode": {"mode"},
    "set_source": {"kind", "name"},
    "set_master": {"db"},
    "set_speaker_volume": {"address", "db"},
    "set_mute": {"address", "muted"},
    "assign": {"address", "channel"},
    "unassign": {"address"},
    "tone": {"channel", "seconds"},
    "calibrate": set(),
    "calibrate_cancel": set(),
    "calibration_apply": set(),
    "save_measurement": set(),
    "set_speaker_trim": {"address", "delay_ms", "gain_db"},
    "set_config": {"key", "value"},
    "scan": set(),
    "service_start": {"name"},
    "service_stop": {"name"},
    "service_restart": {"name"},
    "service_fail": {"name"},
}

SERVICE_COMMANDS = frozenset({"service_start", "service_stop", "service_restart", "service_fail"})
LIVE_STATES = frozenset({"running", "starting"})


def parse_command(raw: Mapping[str, Any]) -> Command:
    """Valida la forma de una orden. No mira el estado."""
    name = raw.get("cmd")
    if not isinstance(name, str) or name not in _PARSERS:
        msg = f"orden desconocida: {name!r}"
        raise CommandError(msg)
    args = raw.get("args", {})
    if not isinstance(args, Mapping):
        msg = "`args` tiene que ser un objeto"
        raise CommandError(msg)
    extra = set(args) - _ALLOWED_KEYS[name]
    if extra:
        msg = f"argumentos de más para {name}: {', '.join(sorted(extra))}"
        raise CommandError(msg)
    return Command(name, _PARSERS[name](args))


def check_against_state(command: Command, snapshot: Snapshot) -> None:
    """Valida lo que depende del estado: parlantes que existen y canales del modo."""
    args = command.args
    mode_channels = MODES[snapshot.engine.mode]

    if "address" in args and snapshot.speaker(args["address"]) is None:
        msg = f"no hay ningún parlante con la dirección {args['address']}"
        raise CommandError(msg)

    if "channel" in args and args["channel"] not in mode_channels:
        msg = f"el canal {args['channel']} no existe en el modo {snapshot.engine.mode}"
        raise CommandError(msg)

    if command.name == "assign":
        holder = next((s for s in snapshot.speakers if s.channel == args["channel"]), None)
        if holder is not None and holder.address != args["address"]:
            msg = f"el canal {args['channel']} está ocupado por {holder.name}; libéralo primero"
            raise CommandError(msg)

    if command.name == "tone" and not snapshot.engine.running:
        msg = "el tono sale por el BIG: primero hay que transmitir"
        raise CommandError(msg)

    if command.name == "calibrate" and not snapshot.engine.running:
        msg = "la calibración manda pulsos por el BIG: primero hay que transmitir"
        raise CommandError(msg)

    if command.name == "calibration_apply" and snapshot.calibration.state != "done":
        msg = "no hay una calibración terminada para aplicar"
        raise CommandError(msg)

    if command.name in SERVICE_COMMANDS:
        _check_service(command, snapshot)

    if command.name == "calibrate":
        assigned = {s.channel for s in snapshot.speakers if s.channel}
        missing = [channel for channel in mode_channels if channel not in assigned]
        if missing:
            msg = f"hay canales sin parlante: {', '.join(missing)}"
            raise CommandError(msg)


def _check_service(command: Command, snapshot: Snapshot) -> None:
    name = command.args["name"]
    service = snapshot.service(name)
    if service is None:
        msg = f"no hay ningún servicio llamado {name}"
        raise CommandError(msg)
    if command.name == "service_fail":
        if snapshot.engine.kind != "simulated":
            msg = "simular una falla solo existe en la demo"
            raise CommandError(msg)
        return
    if not service.managed:
        reason = "es del sistema y solo se observa" if service.kind == "sistema" else service.detail
        msg = f"{service.label}: no se puede iniciar ni detener desde el panel ({reason})"
        raise CommandError(msg)
    if command.name in {"service_start", "service_restart"}:
        missing = [
            dep for dep in service.depends_on if (d := snapshot.service(dep)) is None or d.state not in LIVE_STATES
        ]
        if missing:
            msg = f"primero hay que iniciar: {', '.join(missing)}"
            raise CommandError(msg)
