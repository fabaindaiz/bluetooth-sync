"""The control contract: one JSON message in, one JSON reply out, whatever the transport.

REST (`rest.py`) is the first transport; a serial line to the Phase 3 hardware is the next
(i-7c8794-a9f161), and the panel's WebSocket would be another. All of them call `handle`,
so they cannot disagree. The design is
`docs/superpowers/specs/2026-09-29-control-service-design.md` §5.

This module does no I/O. It validates a message **completely** before anything happens
(types, ranges, unknown fields), so a `set` with one bad field applies none; then it calls
one method of a `Controllable`, which raises `ContractError` for what depends on state
(an unknown speaker, a session already playing).

It also owns the only translation between the contract's English field names and the
Spanish ones of `config.py`, until i-7c8794-f30928 renames those.

**The chain** (`chain.py`, spec 2026-10-02 §4.3): `chain`, `chain_set` and `chain_reset`
are additive. `chain_set` is checked here against the descriptors (stage, algorithm, each
param's type, range and choices, and whether it needs a speaker); what depends on the
installation (availability, the speaker's name, the bass speakers) the service checks again.
The old fields stay, as aliases of the chain (`chain.ON_OFF_ALIASES`, `GLOBAL_ALIASES`,
`SPEAKER_ALIASES`).
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from aurasync import chain
from aurasync.dsp.profiles import PROFILES

VERSION = 1
MAX_BYTES = 64 * 1024

HTTP_STATUS = {
    "bad_request": 400,
    "version": 400,
    "unknown_op": 400,
    "unknown_field": 400,
    "type": 400,
    "out_of_range": 400,
    "read_only": 400,
    "not_found": 404,
    "conflict": 409,
    "unavailable": 409,
    "unauthorized": 401,
    "forbidden": 403,
    "rate_limited": 429,
    "busy": 503,
    "internal": 500,
}


class ContractError(Exception):
    """An error with a stable code, so firmware in C can branch on it without parsing text."""

    def __init__(self, code: str, message: str) -> None:
        if code not in HTTP_STATUS:
            msg = f"unknown error code {code!r}"
            raise ValueError(msg)
        super().__init__(message)
        self.code = code
        self.message = message


# -- fields -------------------------------------------------------------------------


@dataclass(frozen=True)
class Field:
    kind: type
    low: float | None = None
    high: float | None = None
    attr: str | None = None
    """The name in `config.py`, for the fields that map to one."""
    choices: tuple | None = None
    """A closed set of values. Anything else is `out_of_range`."""
    nullable: bool = False
    pattern: str | None = None


MAC = r"^[0-9A-F]{2}(:[0-9A-F]{2}){5}$"

SPEAKER_FIELDS: dict[str, Field] = {
    "pan": Field(float, -1.0, 1.0, attr="pan"),
    "ambience": Field(float, 0.0, 1.0, attr="ambiente"),
    "gain_db": Field(float, -40.0, 6.0, attr="ganancia_db"),
    "delay_ms": Field(float, 0.0, 100.0, attr="retardo_ms"),
    "muted": Field(bool),
    "kind": Field(str, attr="tipo", choices=tuple(PROFILES)),
}
"""`delay_ms` is normally the recalibration loop's; by hand only while the loop is off."""

GLOBAL_FIELDS: dict[str, Field] = {
    "rear_delay_ms": Field(float, 0.0, 50.0, attr="retardo_traseros_ms"),
    "volume_db": Field(float, -60.0, 0.0),
    "extract_ambience": Field(bool),
    "decorrelate": Field(bool),
    "layout": Field(str, choices=("quad", "lcrs")),
    # Applied when the next session starts:
    "block_size": Field(int, choices=(1024, 2048, 4096, 8192)),
    "player_latency_ms": Field(int, 50, 500),
    "sink_description": Field(str, 1, 64),
    "recalibrate_every_s": Field(float, 5.0, 300.0),
    "recalibrate_measure_s": Field(float, 10.0, 30.0),
    "output_mode": Field(str, choices=("combinado", "separado")),
    "eq_active": Field(bool),
    "recalibrate": Field(bool),
}
RESTART_FIELDS = (
    "block_size",
    "player_latency_ms",
    "sink_description",
    "recalibrate_every_s",
    "recalibrate_measure_s",
    "output_mode",
)
"""Fields a playing session cannot change: they take effect on the next `start`."""

ARTISTIC_SPEAKER_FIELDS = ("pan", "ambience", "gain_db")
ARTISTIC_GLOBAL_FIELDS = ("rear_delay_ms", "extract_ambience", "decorrelate")
"""What a preset holds. Not `delay_ms` (the loop's) nor `volume_db` (the listener's, which
would bias an A/B comparison)."""

ROLES: dict[str, dict[str, tuple[float, float]]] = {
    "quad": {"FL": (-0.7, 0.15), "FR": (0.7, 0.15), "RL": (-0.7, 0.55), "RR": (0.7, 0.55)},
    "lcrs": {"FL": (-0.7, 0.15), "FC": (0.0, 0.1), "FR": (0.7, 0.15), "RC": (0.0, 0.55)},
}
"""A role is a shortcut for a `(pan, ambience)` pair: with A2DP a speaker gets a mix, not a
channel. The values are the ones `aurasync init` gives and the first listening kept
(`experimentos/09`). A speaker whose values match no role of the layout is "custom"."""


def role_of(pan: float, ambience: float, layout: str) -> str | None:
    for role, (p, a) in ROLES[layout].items():
        if abs(pan - p) < 1e-9 and abs(ambience - a) < 1e-9:  # noqa: PLR2004
            return role
    return None


def check_value(name: str, spec: Field, value: Any) -> Any:
    if value is None and spec.nullable:
        return None
    if spec.kind is bool:
        if not isinstance(value, bool):
            raise ContractError("type", f"{name} must be true or false; got {json.dumps(value)}")
        return value
    if spec.kind is str:
        if not isinstance(value, str):
            raise ContractError("type", f"{name} must be a string; got {json.dumps(value)}")
        if spec.choices is not None and value not in spec.choices:
            raise ContractError("out_of_range", f"{name} must be one of {list(spec.choices)}; got {value!r}")
        if spec.low is not None and not spec.low <= len(value.strip()) <= spec.high:
            raise ContractError("out_of_range", f"{name} must have {spec.low:g} to {spec.high:g} characters")
        if spec.pattern is not None and not re.fullmatch(spec.pattern, value):
            raise ContractError("out_of_range", f"{name} has the wrong form: {value!r}")
        if not value.isprintable():
            raise ContractError("out_of_range", f"{name} must be printable text")
        return value.strip()
    # `True` is an int in Python, and must not pass as 1.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ContractError("type", f"{name} must be a number; got {json.dumps(value)}")
    if spec.kind is int and not float(value).is_integer():
        raise ContractError("type", f"{name} must be a whole number; got {value}")
    number = float(value)
    if not math.isfinite(number):
        raise ContractError("type", f"{name} must be a finite number")
    if spec.choices is not None and number not in spec.choices:
        raise ContractError("out_of_range", f"{name} must be one of {list(spec.choices)}; got {value:g}")
    if (spec.low is not None and number < spec.low) or (spec.high is not None and number > spec.high):
        raise ContractError("out_of_range", f"{name} goes from {spec.low:g} to {spec.high:g}; got {number:g}")
    return int(number) if spec.kind is int else number


def check_changes(changes: Any, *, speaker: bool) -> dict[str, Any]:
    """Validate every field of a `set`. Raises on the first bad one; returns them all."""
    if not isinstance(changes, dict) or not changes:
        raise ContractError("bad_request", "changes must be a non-empty object")
    fields = SPEAKER_FIELDS if speaker else GLOBAL_FIELDS
    checked: dict[str, Any] = {}
    for name, value in changes.items():
        spec = fields.get(name)
        if spec is None:
            where = "speaker" if speaker else "global"
            raise ContractError("unknown_field", f"unknown {where} field {name!r}; known: {sorted(fields)}")
        checked[name] = check_value(name, spec, value)
    return checked


# -- operations ---------------------------------------------------------------------

NAME = Field(str, 1, 64)
SPEAKER = Field(str, 1, 64)
ADDRESS = Field(str, pattern=MAC)
SERVICE = Field(str, 1, 64)
SCOPE = Field(str, choices=("read", "control", "admin"))
CLIENT_ID = Field(str, pattern=r"^[0-9a-f]{8}$")
REQUEST_ID = Field(str, pattern=r"^[A-Za-z0-9_-]{8,64}$")


@dataclass(frozen=True)
class Op:
    required: dict[str, Field] = field(default_factory=dict)
    optional: dict[str, Field] = field(default_factory=dict)


OPS: dict[str, Op] = {
    "state": Op(),
    "start": Op(optional={"recalibrate": Field(bool)}),
    "stop": Op(),
    "set": Op(required={"changes": Field(dict)}, optional={"speaker": SPEAKER}),
    "assign": Op(required={"speaker": SPEAKER, "role": Field(str, choices=("FL", "FR", "RL", "RR", "FC", "RC"))}),
    "presets": Op(),
    "preset_save": Op(required={"name": NAME}),
    "preset_load": Op(required={"name": NAME}),
    "preset_delete": Op(required={"name": NAME}),
    "save": Op(),
    "shutdown": Op(),
    # The panel (spec §15):
    "source": Op(
        required={"kind": Field(str, choices=("system", "app", "file", "tone"))}, optional={"name": Field(str, 1, 512)}
    ),
    "tone": Op(required={"speaker": SPEAKER}, optional={"seconds": Field(float, 0.5, 10.0)}),
    "recalibrate": Op(required={"active": Field(bool)}),
    "calibrate": Op(optional={"seconds": Field(float, 5.0, 20.0), "amplitude": Field(float, 0.02, 0.2)}),
    "calibrate_cancel": Op(),
    "calibration_apply": Op(),
    "measurement_save": Op(optional={"note": Field(str, 0, 500)}),
    "calibration_dump": Op(),
    "eq_apply": Op(),
    "eq_reset": Op(),
    "scan": Op(),
    "microphone_set": Op(required={"node": Field(str, 1, 256, nullable=True)}),
    "connect": Op(required={"address": ADDRESS}),
    "disconnect": Op(required={"address": ADDRESS}),
    "forget": Op(required={"address": ADDRESS}),
    "speaker_add": Op(required={"address": ADDRESS}),
    "speaker_remove": Op(required={"speaker": SPEAKER}),
    "logs": Op(optional={"since": Field(int, 0, 2**62), "limit": Field(int, 1, 2000)}),
    "service_start": Op(required={"name": SERVICE}),
    "service_stop": Op(required={"name": SERVICE}),
    "service_restart": Op(required={"name": SERVICE}),
    "ab_start": Op(required={"a": NAME, "b": NAME}, optional={"match_loudness": Field(bool)}),
    "ab_play": Op(required={"which": Field(str, choices=("a", "b", "x"))}),
    "ab_answer": Op(required={"x_is": Field(str, choices=("a", "b"))}),
    "ab_stop": Op(),
    # The chain (spec 2026-10-02 §4.3):
    "chain": Op(),
    "chain_set": Op(
        required={"stage": Field(str, 1, 64)},
        optional={"algorithm": Field(str, 1, 64), "params": Field(dict), "speaker": SPEAKER},
    ),
    "chain_reset": Op(required={"stage": Field(str, 1, 64)}, optional={"param": Field(str, 1, 64), "speaker": SPEAKER}),
    # The radio (spec 2026-10-02 §3.2): raise the bluez5 log level so the radio monitor sees drops.
    "radio_log": Op(required={"active": Field(bool)}, optional={"mode": Field(str, choices=("light", "heavy"))}),
    # Clients and pairing (d-7c8794-37f9bc, `access.py`): they never touch the engine.
    "pair_start": Op(optional={"seconds": Field(float, 30.0, 600.0)}),
    "pair_status": Op(),
    "pair_approve": Op(required={"request": REQUEST_ID}, optional={"scope": SCOPE}),
    "pair_deny": Op(required={"request": REQUEST_ID}),
    "clients": Op(),
    "client_revoke": Op(required={"client": CLIENT_ID}),
    "client_rename": Op(required={"client": CLIENT_ID, "name": NAME}),
}
ENVELOPE = {"v", "id", "op"}


@dataclass(frozen=True)
class Command:
    op: str
    args: dict[str, Any]
    id: Any = None


def parse(message: Any) -> Command:
    """Validate a decoded message. Nothing about state: that is the `Controllable`'s job."""
    if not isinstance(message, dict):
        raise ContractError("bad_request", "a message is a JSON object")
    if message.get("v") != VERSION:
        raise ContractError("version", f"this service speaks version {VERSION}; got {message.get('v')!r}")
    op_name = message.get("op")
    if not isinstance(op_name, str):
        raise ContractError("bad_request", "missing op")
    op = OPS.get(op_name)
    if op is None:
        raise ContractError("unknown_op", f"unknown op {op_name!r}; known: {sorted(OPS)}")
    args = {k: v for k, v in message.items() if k not in ENVELOPE}
    for key in args:
        if key not in op.required and key not in op.optional:
            raise ContractError("unknown_field", f"{op_name} takes no field {key!r}")
    for key in op.required:
        if key not in args:
            raise ContractError("bad_request", f"{op_name} needs {key!r}")
    for key, value in list(args.items()):
        spec = op.required.get(key) or op.optional[key]
        if spec.kind is dict:
            if not isinstance(value, dict):
                raise ContractError("type", f"{key} must be an object")
            continue
        args[key] = check_value(key, spec, value)
    if op_name == "set":
        args["changes"] = check_changes(args["changes"], speaker="speaker" in args)
    try:
        if op_name == "chain_set":
            change = chain.validate_set(args["stage"], args.get("algorithm"), args.get("params"), args.get("speaker"))
            if "params" in args:
                args["params"] = change.params
        elif op_name == "chain_reset":
            chain.validate_reset(args["stage"], args.get("param"), args.get("speaker"))
    except chain.ChainError as exc:
        raise ContractError(exc.code, exc.message) from exc
    return Command(op_name, args, message.get("id"))


def decode(raw: bytes) -> Any:
    if len(raw) > MAX_BYTES:
        raise ContractError("bad_request", f"the body is over {MAX_BYTES // 1024} KiB")
    try:
        return json.loads(raw, parse_constant=_reject_constant)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ContractError("bad_request", f"not valid JSON: {exc}") from exc


def _reject_constant(name: str) -> float:
    # NaN and Infinity are not JSON; Python's parser accepts them unless told otherwise.
    raise ContractError("type", f"{name} is not a number this contract accepts")


# -- dispatch -----------------------------------------------------------------------


class Controllable(Protocol):
    """What the service implements: one method per op, named like it. Each may raise
    `ContractError`. `set` goes to `set_speaker` or `set_global`."""

    def state(self) -> dict: ...
    def set_speaker(self, speaker: str, changes: dict) -> dict: ...
    def set_global(self, changes: dict) -> dict: ...


def dispatch(command: Command, target: Controllable) -> dict:
    a = command.args
    if command.op == "set":
        if "speaker" in a:
            return target.set_speaker(a["speaker"], a["changes"])
        return target.set_global(a["changes"])
    return getattr(target, command.op)(**a)


def ok(command_id: Any, result: dict) -> dict:
    reply: dict[str, Any] = {"v": VERSION, "ok": True, "result": result}
    if command_id is not None:
        reply["id"] = command_id
    return reply


def error(command_id: Any, code: str, message: str) -> dict:
    reply: dict[str, Any] = {"v": VERSION, "ok": False, "error": {"code": code, "message": message}}
    if command_id is not None:
        reply["id"] = command_id
    return reply


def message_id(message: Any) -> Any:
    return message.get("id") if isinstance(message, dict) else None
