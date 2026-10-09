"""Which microphones the service must never open.

A Bluetooth headset's microphone lives in the headset profile (HFP): opening it makes the
device leave A2DP, and PipeWire rebuilds its sink under the same name with a new id. On
2026-10-09 (HP-O16) the panel's microphone check opened the WH-CH520's own microphone while
the headphone monitor played on them: the headphones went to hands-free and the monitor fell
silent (experimentos/23 §7). So the microphone of a Bluetooth device that is an output in use
(the monitor's target or a speaker of the installation) is never opened, by anyone: the check,
a calibration or the recalibration loop. The device is matched by its Bluetooth address, which
PipeWire spells two ways (`bluez_input.14:06:A7:6B:E3:F0`, `bluez_output.14_06_A7_6B_E3_F0.1`).

The reasons are shown in the panel as they are (panel copy is Spanish).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable

_PAIRS = r"[0-9A-Fa-f]{2}(?:[:_][0-9A-Fa-f]{2}){5}"
_ADDRESS = re.compile(rf"^bluez_(?:input|output|source|sink)\.({_PAIRS})(?:\.|$)")


def bluetooth_address(node: str | None, prop: str | None = None) -> str | None:
    """The device's address (`14:06:A7:6B:E3:F0`) of a BlueZ node of PipeWire, from its name, or
    else from its `api.bluez5.address` property (`prop`: names that do not carry it, such as the
    older `bluez_source.*` or a renamed node); None for any other node."""
    m = _ADDRESS.match(node or "")
    if m:
        return m.group(1).replace("_", ":").upper()
    if prop and re.fullmatch(_PAIRS, prop):
        return prop.replace("_", ":").upper()
    return None


@dataclass(frozen=True)
class OutputInUse:
    sink: str
    role: str
    """`monitor` (the headphone monitor's target) or `speaker` (one of the installation)."""
    name: str
    """What the listener calls the device: its Bluetooth name, or the speaker's name."""
    address: str | None = None
    """The sink's `api.bluez5.address`, when PipeWire gives it."""


def blocked_reason(microphone: str | None, outputs: Iterable[OutputInUse], address: str | None = None) -> str | None:
    """Why `microphone` must not be opened, or None when it may. `address`: its
    `api.bluez5.address`, when PipeWire gives it."""
    address = bluetooth_address(microphone, address)
    if address is None:
        return None
    for out in outputs:
        if bluetooth_address(out.sink, out.address) != address:
            continue
        if out.role == "monitor":
            return (
                f"es el micrófono de {out.name}, que es la salida del monitor: abrirlo pasa sus audífonos "
                "a manos libres y corta el sonido; elegí otro micrófono"
            )
        return (
            f"es el micrófono de {out.name}, un parlante de la instalación: abrirlo lo pasa a manos libres "
            "y corta su sonido; elegí otro micrófono"
        )
    return None


def mark(listed: list[dict[str, Any]], outputs: Iterable[OutputInUse]) -> list[dict[str, Any]]:
    """The observer's microphones, each with `blocked_reason` (None: it may be opened)."""
    used = list(outputs)
    return [{**m, "blocked_reason": blocked_reason(m.get("node"), used, m.get("address"))} for m in listed]
