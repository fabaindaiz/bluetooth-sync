#!/usr/bin/env python3
"""Decodifica los bits de LE Features de un controlador Bluetooth.

Lee por stdin la salida de `/sys/kernel/debug/bluetooth/hci0/features` (busca la
línea que empieza con `LE:`) o, con -b, ocho bytes en hexadecimal.

Los bits son los de Core Spec 5.4, Vol 6, Part B, §4.6. Los cuatro que deciden E1
son 28 a 31: sin el bit 30 (Isochronous Broadcaster) este controlador no puede
crear un BIG, y el proyecto necesita otro emisor (i-7c8794-3f730a).
"""

from __future__ import annotations

import argparse
import re
import sys

# Bit -> (nombre, ¿decide algo en este proyecto?)
FEATURES: dict[int, tuple[str, str | None]] = {
    0: ("LE Encryption", None),
    1: ("Connection Parameters Request Procedure", None),
    2: ("Extended Reject Indication", None),
    3: ("Peripheral-initiated Features Exchange", None),
    4: ("LE Ping", None),
    5: ("LE Data Packet Length Extension", None),
    6: ("LL Privacy", None),
    7: ("Extended Scanning Filter Policies", None),
    8: ("LE 2M PHY", "ancho de banda para varios BIS (02 §4)"),
    9: ("Stable Modulation Index - Transmitter", None),
    10: ("Stable Modulation Index - Receiver", None),
    11: ("LE Coded PHY", None),
    12: ("LE Extended Advertising", "requisito de radio para Auracast"),
    13: ("LE Periodic Advertising", "hace falta para leer la BASE (E2)"),
    14: ("Channel Selection Algorithm #2", None),
    15: ("LE Power Class 1", None),
    16: ("Minimum Number of Used Channels Procedure", None),
    17: ("Connection CTE Request", None),
    18: ("Connection CTE Response", None),
    19: ("Connectionless CTE Transmitter", None),
    20: ("Connectionless CTE Receiver", None),
    21: ("Antenna Switching During CTE Transmission (AoD)", None),
    22: ("Antenna Switching During CTE Reception (AoA)", None),
    23: ("Receiving Constant Tone Extensions", None),
    24: ("Periodic Advertising Sync Transfer - Sender", None),
    25: ("Periodic Advertising Sync Transfer - Recipient", None),
    26: ("Sleep Clock Accuracy Updates", None),
    27: ("Remote Public Key Validation", None),
    28: ("Connected Isochronous Stream - Central", "unicast LE Audio (Tune 770NC)"),
    29: ("Connected Isochronous Stream - Peripheral", None),
    30: ("Isochronous Broadcaster", "EL BIT QUE DECIDE E1: crear un BIG"),
    31: ("Synchronized Receiver", "recibir un BIS (leer el BIGInfo en E2)"),
    32: ("Connected Isochronous Stream (Host Support)", None),
    33: ("LE Power Control Request", None),
    34: ("LE Power Change Indication", None),
    35: ("LE Path Loss Monitoring", None),
    36: ("Periodic Advertising ADI support", None),
    37: ("Connection Subrating", None),
    38: ("Connection Subrating (Host Support)", None),
    39: ("Channel Classification", None),
}

DECISIVOS = (28, 29, 30, 31, 13, 12)


def parse_bytes(texto: str) -> list[int]:
    for linea in texto.splitlines():
        if linea.strip().startswith("LE:"):
            texto = linea.split(":", 1)[1]
            break
    octetos = re.findall(r"(?:0x)?([0-9a-fA-F]{2})\b", texto)
    if len(octetos) < 8:
        msg = f"esperaba 8 bytes de LE Features, encontré {len(octetos)}: {texto!r}"
        raise SystemExit(msg)
    return [int(o, 16) for o in octetos[:8]]


def activo(octetos: list[int], bit: int) -> bool:
    return bool(octetos[bit // 8] & (1 << (bit % 8)))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("-b", "--bytes", help="los 8 bytes en hex, en vez de leer stdin")
    args = ap.parse_args()

    octetos = parse_bytes(args.bytes if args.bytes else sys.stdin.read())
    print("LE Features: " + " ".join(f"0x{o:02x}" for o in octetos))
    print()

    print("== Los que deciden algo en este proyecto ==")
    for bit in DECISIVOS:
        nombre, porque = FEATURES[bit]
        marca = "SI " if activo(octetos, bit) else "NO "
        print(f"  [{marca}] bit {bit:2d}  {nombre}")
        if porque:
            print(f"           → {porque}")
    print()

    print("== Todos los soportados ==")
    for bit in sorted(FEATURES):
        if activo(octetos, bit):
            print(f"  bit {bit:2d}  {FEATURES[bit][0]}")

    desconocidos = [b for b in range(64) if activo(octetos, b) and b not in FEATURES]
    if desconocidos:
        print("\n  bits activos que este script no conoce: " + str(desconocidos))

    print()
    if activo(octetos, 30):
        print("VEREDICTO: el controlador declara Isochronous Broadcaster. E1 puede")
        print("           seguir con este chip, sin comprar nada.")
    else:
        print("VEREDICTO: el controlador NO declara Isochronous Broadcaster (bit 30).")
        print("           No puede crear un BIG. E1 pasa a las SuperMini nRF52840")
        print("           (d-7c8794-b82ee9) o a una tarjeta MT7921/BE200.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
