"""The panel's TypeScript types, generated from the contract (d-7c8794-6da524).

The web application (`host/web/`, Vite + TypeScript + Preact) reads the service's replies and
stream events. Their shapes are written **once, here, in Python**, next to the code that
produces them, and `host/web/src/contract.gen.ts` is generated from this module:

- the operations and their arguments come straight from `control.OPS` and the field tables
  (`control.SPEAKER_FIELDS`, `control.GLOBAL_FIELDS`), and the error codes from
  `control.HTTP_STATUS`;
- the closed sets of the chain's descriptors (`kind`, `scope`, `apply`, `store`) are the
  `Literal` aliases of `chain.py` itself;
- the shapes of the replies and events are the `TypedDict`s below. They are not trusted:
  `tests/test_contract_types.py` runs the real producers (`chain.describe`, the simulated
  service's snapshot, its `quality`, `chain` and `radio` events, a `chain_set` reply) and checks
  each against its `TypedDict` with `conforms()`. A key the producer adds or drops fails there
  until it is written here, and then the generated file is stale until it is regenerated.

Nothing stage-specific goes into the generated file: the panel draws the Cadena screen from the
`chain` reply alone, so a stage added in Python reaches the screen without rebuilding the web
application (spec 2026-10-02 §7.1).

Regenerate with `hatch run gen-types` (from `host/`); `hatch run gen-types --check` and
`tests/test_contract_types.py` fail when the file is stale. The module is in the package (and
not a script) because it reads the package's own tables; it has no command in the CLI because
only a developer runs it.
"""

from __future__ import annotations

import json
import sys
import types
import typing
from pathlib import Path
from typing import Any, Literal, NotRequired, TypedDict, Union, get_args, get_origin, get_type_hints

from aurasync import chain, control

GENERATED = Path(__file__).resolve().parents[2] / "web" / "src" / "contract.gen.ts"

# -- shared scalars --------------------------------------------------------------------

ParamValue = float | bool | str
"""A knob's value: `int` and `float` are one JSON number."""
MetricValue = float | bool | str | None | dict[str, float | None]
"""One number of a stage's live metrics: a scalar, or one per speaker."""
Applied = Literal["live", "cut", "restart", "none"]
"""How a chain change was applied now (`none`: no session, or a stage not run yet)."""


# -- the chain (`chain` reply, spec 2026-10-02 §4.3) -------------------------------------


class ChainParam(TypedDict):
    id: str
    title: str
    summary: str
    help: str
    kind: chain.Kind
    default: ParamValue
    low: float | None
    high: float | None
    step: float | None
    unit: str
    choices: list[str]
    scope: chain.Scope
    apply: chain.Apply
    store: chain.Store
    implemented: bool


class ChainAlgorithm(TypedDict):
    id: str
    title: str
    summary: str
    help: str
    cost: str
    latency_ms: float
    implemented: bool
    available: bool
    unavailable_reason: str | None
    notice: str | None
    """What to know about an available algorithm here (`chain.notice`): the decorrelator past
    6 speakers says how far apart its filters are. Additive, 2026-10-02."""
    params: list[ChainParam]


class ChainStageValue(TypedDict):
    algorithm: str
    params: dict[str, ParamValue]
    speakers: dict[str, dict[str, ParamValue]]


class ChainChoice(TypedDict):
    """What the listener chose, sparse."""

    algorithm: NotRequired[str]
    params: NotRequired[dict[str, ParamValue]]
    speakers: NotRequired[dict[str, dict[str, ParamValue]]]


class ChainStage(TypedDict):
    id: str
    title: str
    summary: str
    help: str
    algorithm_apply: chain.Apply
    algorithms: list[ChainAlgorithm]
    default_algorithm: str
    value: ChainStageValue
    chosen: ChainChoice
    pending: bool


class ChainDescription(TypedDict):
    stages: list[ChainStage]
    latency_ms: float


class ChainChangeReply(TypedDict):
    """The result of `chain_set` and `chain_reset`."""

    sequence: int
    stage: str
    value: ChainStageValue
    apply: Applied


class StageMetrics(TypedDict):
    """One stage's live numbers (the stream's `chain` event): `pending` plus whatever the
    stage reports (typed as `MetricValue`, see `EXTRA_ITEMS`)."""

    pending: bool


# -- quality (the stream's `quality` event and `state.quality`, spec §6.3) --------------


class QualityInput(TypedDict):
    m: float | None
    s: float | None
    i: float | None
    tp: float | None
    psr: float | None


class QualityOutput(TypedDict):
    m: float | None
    s: float | None
    tp: float | None
    psr: float | None
    limiter_pct: float | None
    flattening: bool


class QualitySum(TypedDict):
    m: float | None
    s: float | None


class QualityEvent(TypedDict):
    input: QualityInput
    outputs: dict[str, QualityOutput]
    sum: QualitySum
    net_gain_lu: float | None
    chain_gain_lu: float | None
    flattening: bool
    flattening_outputs: list[str]
    tp_max: float | None
    limiter_pct_max: float | None
    cost_ms: float


# -- the radio (the stream's `radio` event and `state.radio`, spec §3) -------------------


class RadioSpeaker(TypedDict):
    identified: bool
    address: str | None
    sinks: list[str]
    bitpool: float | None
    bitpool_max: float | None
    bitpool_median_60s: float | None
    drops_total: int
    drops_60s: int
    drops_per_min: float | None
    last_drop_s: float | None
    write_mtu: float | None


class RadioEvent(TypedDict):
    available: bool
    reason: str | None
    since_s: NotRequired[float]
    lines: NotRequired[int]
    speakers: dict[str, RadioSpeaker]
    drops_seen: int
    simulated: NotRequired[bool]


class RadioLogStatus(TypedDict):
    """`state.radio_log`, and the result of `radio_log` (which has no `available` nor
    `changes_file`: it is only sent where the level can be changed)."""

    available: NotRequired[bool]
    active: bool
    mode: Literal["light", "heavy"] | None
    pending: bool
    error: str | None
    heavy: NotRequired[bool]
    previous: NotRequired[str | None]
    verified: NotRequired[bool | None]
    changes_file: NotRequired[str | None]


# -- what the panel reads of `state` (extra keys are allowed: see `PARTIAL`) --------------


class AbLoudness(TypedDict):
    a: float | None
    b: float | None
    diff: float | None


class AbView(TypedDict):
    a: str
    b: str
    active: NotRequired[bool]
    match_loudness: bool
    loudness_lu: AbLoudness
    compensation_db: dict[str, float]


class SyncView(TypedDict):
    """The residual misalignment the recalibration loop measured last (spec §7.3.4)."""

    residual_ms: float | None
    measured_at: str | None
    age_s: float | None
    speakers: list[str]


class SpeakerView(TypedDict):
    name: str
    # `null` for a virtual speaker (no sink); a wired output has a sink but no Bluetooth fields.
    sink: str | None
    muted: bool
    playing: bool
    # What the session does with it: `null` without a session (`playing` equals `output == "playing"`).
    output: Literal["virtual", "absent", "playing", "lost"] | None
    output_kind: Literal["virtual", "bluetooth", "wired"]
    # Bluetooth only: `null` unless `output_kind == "bluetooth"` (and the link reports it).
    address: str | None
    battery_pct: float | None
    codec: str | None
    rssi_dbm: float | None
    modalias: str | None


class SessionView(TypedDict):
    status: str


class LatencyView(TypedDict):
    known_ms: float
    measured_ms: float | None


class HealthView(TypedDict):
    streams_open: int


class VolumeAvrcp(TypedDict):
    state: Literal["off", "entering", "on", "leaving", "failed"]
    pending: bool
    error: str | None


class StateView(TypedDict):
    sequence: int
    session: SessionView
    speakers: list[SpeakerView]
    latency: LatencyView
    health: HealthView
    chain_summary: dict[str, str]
    chain_latency_ms: float
    chain_pending: list[str]
    quality: QualityEvent | None
    radio: RadioEvent
    radio_log: RadioLogStatus
    volume_avrcp: VolumeAvrcp
    ab: AbView | None
    sync: SyncView


PARTIAL: frozenset[type] = frozenset(
    {AbView, SpeakerView, SessionView, LatencyView, HealthView, VolumeAvrcp, StateView}
)
"""Shapes of which the panel reads only some keys: the producer may carry more."""
EXTRA_ITEMS: dict[type, Any] = {StageMetrics: MetricValue}
"""Shapes with keys of their own beyond the declared ones, all of this type."""

ALIASES: list[tuple[str, Any]] = [
    ("ParamKind", chain.Kind),
    ("ParamScope", chain.Scope),
    ("ApplyKind", chain.Apply),
    ("ParamStore", chain.Store),
    ("Applied", Applied),
    ("ParamValue", ParamValue),
    ("MetricValue", MetricValue),
]
SHAPES: list[type] = [
    ChainParam,
    ChainAlgorithm,
    ChainStageValue,
    ChainChoice,
    ChainStage,
    ChainDescription,
    ChainChangeReply,
    StageMetrics,
    QualityInput,
    QualityOutput,
    QualitySum,
    QualityEvent,
    RadioSpeaker,
    RadioEvent,
    RadioLogStatus,
    AbLoudness,
    AbView,
    SyncView,
    SpeakerView,
    SessionView,
    LatencyView,
    HealthView,
    VolumeAvrcp,
    StateView,
]
EVENTS: dict[str, str] = {
    "state": "StateView",
    "quality": "QualityEvent",
    "chain": "ChainMetricsEvent",
    "radio": "RadioEvent",
}
"""The stream's events the panel's web application reads, and their `data`."""
SENT_OPS: tuple[str, ...] = (
    "chain",
    "chain_set",
    "chain_reset",
    "radio_log",
    "state",
    # The pairing and clients view (d-7c8794-37f9bc): its results are typed in web/src/access.ts.
    "pair_status",
    "pair_start",
    "pair_approve",
    "pair_deny",
    "clients",
    "client_revoke",
    "client_rename",
)
"""The operations the web application sends. Only these go into `OpArgs`: an operation added to
`control.OPS` for another client leaves the generated file (and so the build) as it was."""
RESULTS: dict[str, str] = {
    "chain": "ChainDescription",
    "chain_set": "ChainChangeReply",
    "chain_reset": "ChainChangeReply",
    "radio_log": "RadioLogStatus",
}
"""The results of the operations it sends whose shape it reads; the rest are `unknown`."""


# -- checking a value against a shape ------------------------------------------------------


def _hints(shape: type) -> dict[str, Any]:
    return get_type_hints(shape, include_extras=True)


def _unwrap(tp: Any) -> tuple[Any, bool]:
    """(the type, whether the key may be missing)."""
    if get_origin(tp) is NotRequired:
        return get_args(tp)[0], True
    return tp, False


def _is_union(tp: Any) -> bool:
    return get_origin(tp) in {Union, types.UnionType}


def conforms(value: Any, tp: Any, where: str = "$") -> list[str]:
    """Where `value` departs from `tp`, as readable lines (empty: it conforms)."""
    if tp is Any:
        return []
    if tp is None or tp is type(None):
        return [] if value is None else [f"{where}: expected null, got {value!r}"]
    if _is_union(tp):
        options = get_args(tp)
        failures = [conforms(value, option, where) for option in options]
        if any(not f for f in failures):
            return []
        return [f"{where}: {value!r:.80} is none of {tp}"]
    origin = get_origin(tp)
    if origin is Literal:
        return [] if value in get_args(tp) else [f"{where}: {value!r} is not one of {get_args(tp)}"]
    if origin is list:
        if not isinstance(value, list):
            return [f"{where}: expected a list, got {type(value).__name__}"]
        (item,) = get_args(tp)
        return [line for i, v in enumerate(value) for line in conforms(v, item, f"{where}[{i}]")]
    if origin is dict:
        if not isinstance(value, dict):
            return [f"{where}: expected an object, got {type(value).__name__}"]
        key, item = get_args(tp)
        out = [f"{where}: key {k!r} is not a {key.__name__}" for k in value if not isinstance(k, key)]
        return out + [line for k, v in value.items() for line in conforms(v, item, f"{where}.{k}")]
    if typing.is_typeddict(tp):
        return _conforms_shape(value, tp, where)
    if tp is bool:
        return [] if isinstance(value, bool) else [f"{where}: expected a boolean, got {value!r}"]
    if tp in {int, float}:
        ok = isinstance(value, (int, float)) and not isinstance(value, bool)
        if ok and tp is int and isinstance(value, float) and not value.is_integer():
            ok = False
        return [] if ok else [f"{where}: expected a {tp.__name__}, got {value!r}"]
    if tp is str:
        return [] if isinstance(value, str) else [f"{where}: expected a string, got {value!r}"]
    return [f"{where}: no rule for {tp}"]


def _conforms_shape(value: Any, shape: type, where: str) -> list[str]:
    if not isinstance(value, dict):
        return [f"{where}: expected an object ({shape.__name__}), got {type(value).__name__}"]
    out = []
    hints = _hints(shape)
    for key, raw in hints.items():
        tp, optional = _unwrap(raw)
        if key not in value:
            if not optional:
                out.append(f"{where}.{key}: missing ({shape.__name__})")
            continue
        out += conforms(value[key], tp, f"{where}.{key}")
    extra = [k for k in value if k not in hints]
    if shape in EXTRA_ITEMS:
        for k in extra:
            out += conforms(value[k], EXTRA_ITEMS[shape], f"{where}.{k}")
    elif shape not in PARTIAL and extra:
        out.append(f"{where}: keys not in {shape.__name__}: {sorted(extra)}")
    return out


# -- writing TypeScript ----------------------------------------------------------------------


def _alias_name(tp: Any) -> str | None:
    for name, alias in ALIASES:
        if tp == alias and get_args(tp) == get_args(alias):
            return name
    return None


def ts(tp: Any, *, top: bool = False) -> str:
    """The TypeScript for a Python annotation."""
    if not top and (name := _alias_name(tp)) is not None:
        return name
    if tp is Any:
        return "unknown"
    if tp is None or tp is type(None):
        return "null"
    if _is_union(tp):
        return " | ".join(dict.fromkeys(ts(a) for a in get_args(tp)))
    origin = get_origin(tp)
    if origin is Literal:
        return " | ".join(json.dumps(a) for a in get_args(tp))
    if origin is list:
        inner = ts(get_args(tp)[0])
        return f"({inner})[]" if " " in inner else f"{inner}[]"
    if origin is dict:
        return f"Record<string, {ts(get_args(tp)[1])}>"
    if typing.is_typeddict(tp):
        return tp.__name__
    return {
        bool: "boolean",
        int: "number",
        float: "number",
        str: "string",
        dict: "Record<string, unknown>",
        list: "number[]",
    }[tp]


def _doc(text: str | None, indent: str = "") -> list[str]:
    if not text:
        return []
    lines = [line.strip() for line in text.strip().splitlines()]
    return (
        [f"{indent}/** {lines[0]}"]
        + [f"{indent} * {line}" if line else f"{indent} *" for line in lines[1:]]
        + [f"{indent} */"]
        if len(lines) > 1
        else [f"{indent}/** {lines[0]} */"]
    )


def _interface(shape: type) -> list[str]:
    out = [*_doc(shape.__doc__), f"export interface {shape.__name__} {{"]
    for key, raw in _hints(shape).items():
        tp, optional = _unwrap(raw)
        out.append(f"  {key}{'?' if optional else ''}: {ts(tp)};")
    if shape in EXTRA_ITEMS:
        out.append(f"  [key: string]: {ts(EXTRA_ITEMS[shape])};")
    out.append("}")
    return out


def _field(spec: control.Field) -> str:
    base = " | ".join(json.dumps(c) for c in spec.choices) if spec.choices else ts(spec.kind)
    return f"{base} | null" if spec.nullable else base


def _fields(fields: dict[str, control.Field], *, optional: bool) -> str:
    if not fields:
        return "{}"
    mark = "?" if optional else ""
    return "{ " + " ".join(f"{name}{mark}: {_field(spec)};" for name, spec in fields.items()) + " }"


def _op_args(op: control.Op) -> str:
    parts = []
    parts += [f"{name}: {_field(spec)};" for name, spec in op.required.items()]
    parts += [f"{name}?: {_field(spec)};" for name, spec in op.optional.items()]
    return "{ " + " ".join(parts) + " }" if parts else "Record<string, never>"


def render() -> str:
    """The whole of `contract.gen.ts`."""
    lines = [
        "// GENERATED by `hatch run gen-types` (host/src/aurasync/contract_types.py). Do not edit.",
        "// The shapes of the service's contract that the panel's web application reads",
        "// (d-7c8794-6da524). tests/test_contract_types.py fails when this file is stale.",
        "",
        "export const CONTRACT_VERSION = " + json.dumps(control.VERSION) + ";",
        "",
    ]
    for name, alias in ALIASES:
        lines.append(f"export type {name} = {ts(alias, top=True)};")
    lines += [
        "",
        "/** `code` is one of control.HTTP_STATUS's; the panel shows `message` and never branches on it. */",
        "export interface ContractError {",
        "  code: string;",
        "  message: string;",
        "}",
        "",
        "/** Every reply: `result` when `ok`, `error` when not. */",
        "export type Reply<T> =",
        "  | { v: number; ok: true; result: T; id?: unknown }",
        "  | { v: number; ok: false; error: ContractError; id?: unknown };",
        "",
    ]
    for shape in SHAPES:
        lines += _interface(shape)
        lines.append("")
    lines += [
        "/** The stream's `chain` event: each stage's live numbers, by stage id. */",
        "export type ChainMetricsEvent = Record<string, StageMetrics>;",
        "",
        "/** The stream's events this application reads, and their `data`. */",
        "export interface StreamEvents {",
        *[f"  {event}: {shape};" for event, shape in EVENTS.items()],
        "}",
        "",
        "/** `set` with a speaker: its `changes` (control.SPEAKER_FIELDS). */",
        f"export type SpeakerChanges = {_fields(control.SPEAKER_FIELDS, optional=True)};",
        "/** `set` without a speaker: its `changes` (control.GLOBAL_FIELDS). */",
        f"export type GlobalChanges = {_fields(control.GLOBAL_FIELDS, optional=True)};",
        "",
        "/** The operations this application sends to `POST /v1/command`, and their arguments (control.OPS). */",
        "export interface OpArgs {",
        *[f"  {name}: {_op_args(control.OPS[name])};" for name in SENT_OPS],
        "}",
        "",
        "export type OpName = keyof OpArgs;",
        "",
        "/** The result of the operations whose shape this application reads. */",
        "export interface OpResults {",
        *[f"  {op}: {shape};" for op, shape in RESULTS.items()],
        "}",
        "",
        "export type ResultOf<K extends OpName> = K extends keyof OpResults ? OpResults[K] : unknown;",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    text = render()
    current = GENERATED.read_text(encoding="utf-8") if GENERATED.exists() else None
    if "--check" in args:
        if current != text:
            print(f"{GENERATED} is stale: run `hatch run gen-types` in host/", file=sys.stderr)  # noqa: T201
            return 1
        print(f"{GENERATED.name}: up to date")  # noqa: T201
        return 0
    if current != text:
        GENERATED.write_text(text, encoding="utf-8")
        print(f"wrote {GENERATED}")  # noqa: T201
    else:
        print(f"{GENERATED.name}: up to date")  # noqa: T201
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
