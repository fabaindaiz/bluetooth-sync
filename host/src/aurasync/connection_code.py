"""The connection code: one short code a person types in the PWA to add and pair this device.

The PWA lives on GitHub Pages and cannot find a device on the local network by itself (no mDNS from a
web page), so the code carries the address. It packs, in Crockford base32 (no I, L, O or U, so a
digit and a letter are never confused) grouped by four:

- the IPv4 address, shorter for the private ranges (192.168.x.y in 16 bits, 10.x.y.z in 24,
  172.16-31.y.z in 20; anything else in 32);
- the port, only when it is not 8443;
- the 6-digit pairing code (`pairing.PairingDesk.start_code`);
- the first 20 bits of the root's SHA-256, so the PWA can tell it reached this device and not
  another one at that address;
- one check symbol (weighted sum mod 31), which catches a mistyped symbol and two swapped ones.

On a home network on the default port it is 13 symbols, as `341C-2J9K-XRCG-B`. The same encoding is in
`web/src/connect/code.ts`; `tests/test_connection_code.py` and `web/test/code.test.ts` share vectors.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass

ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
"""Crockford's base32."""
DEFAULT_PORT = 8443
FP_BITS = 20
FP_HEX = FP_BITS // 4
CODE_BITS = 20
"""A 6-digit code is below 2**20."""
_CLASSES = (
    (ipaddress.ip_network("192.168.0.0/16"), 16),
    (ipaddress.ip_network("10.0.0.0/8"), 24),
    (ipaddress.ip_network("172.16.0.0/12"), 20),
)
_ANY_BITS = 32
_TYPED_AS = {"O": "0", "I": "1", "L": "1", "U": "V"}


class CodeError(ValueError):
    """Not a connection code, or one with a typo."""


@dataclass(frozen=True)
class Decoded:
    host: str
    port: int
    pairing: str
    fp_prefix: str
    """The first `FP_HEX` hex digits of the root's SHA-256, upper case."""


def _check(symbols: list[int]) -> int:
    return sum((i + 1) * s for i, s in enumerate(symbols)) % 31


def _group(text: str) -> str:
    return "-".join(text[i : i + 4] for i in range(0, len(text), 4))


def encode(host: str, port: int, pairing: str, root_sha256: str) -> str:
    try:
        ip = ipaddress.IPv4Address(host)
    except ValueError as exc:
        msg = f"a connection code carries an IPv4 address; got {host!r}"
        raise ValueError(msg) from exc
    if len(pairing) != 6 or not pairing.isdigit():  # noqa: PLR2004
        msg = "the pairing code has 6 digits"
        raise ValueError(msg)
    fp = root_sha256.replace(":", "").upper()[:FP_HEX]
    bits = ""
    for kind, (net, width) in enumerate(_CLASSES):
        if ip in net:
            bits += f"{kind:02b}" + f"{int(ip) - int(net.network_address):0{width}b}"
            break
    else:
        bits += "11" + f"{int(ip):032b}"
    bits += "0" if port == DEFAULT_PORT else "1" + f"{port:016b}"
    bits += f"{int(pairing):0{CODE_BITS}b}" + f"{int(fp, 16):0{FP_BITS}b}"
    bits += "0" * (-len(bits) % 5)
    symbols = [int(bits[i : i + 5], 2) for i in range(0, len(bits), 5)]
    return _group("".join(ALPHABET[s] for s in [*symbols, _check(symbols)]))


def decode(text: str) -> Decoded:
    plain = "".join(_TYPED_AS.get(c, c) for c in text.upper() if c not in " -")
    if not plain or any(c not in ALPHABET for c in plain):
        msg = "not a connection code"
        raise CodeError(msg)
    values = [ALPHABET.index(c) for c in plain]
    symbols, check = values[:-1], values[-1]
    if not symbols or _check(symbols) != check:
        msg = "the code has a typo (or is not a connection code)"
        raise CodeError(msg)
    bits = "".join(f"{s:05b}" for s in symbols)
    pos = 0

    def take(n: int) -> int:
        nonlocal pos
        if pos + n > len(bits):
            msg = "the code is too short"
            raise CodeError(msg)
        value = int(bits[pos : pos + n], 2)
        pos += n
        return value

    kind = take(2)
    if kind < len(_CLASSES):
        net, width = _CLASSES[kind]
        host = str(ipaddress.IPv4Address(int(net.network_address) + take(width)))
    else:
        host = str(ipaddress.IPv4Address(take(_ANY_BITS)))
    port = take(16) if take(1) else DEFAULT_PORT
    pairing = f"{take(CODE_BITS):06d}"
    fp = f"{take(FP_BITS):0{FP_HEX}X}"
    if len(bits) - pos >= 5 or int(bits[pos:] or "0", 2) != 0 or int(pairing) >= 10**6:  # noqa: PLR2004
        msg = "not a connection code"
        raise CodeError(msg)
    return Decoded(host, port, pairing, fp)
