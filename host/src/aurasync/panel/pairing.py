"""Emparejamiento del teléfono: la URL con el token y su QR (docs/research/09 §6)."""

import io
import socket

import segno

# TEST-NET-1 (RFC 5737): no se le manda ningún paquete. Conectar un socket UDP solo
# le pide al sistema qué interfaz usaría para salir.
_PROBE_ADDRESS = ("192.0.2.1", 9)


def lan_address() -> str | None:
    """La IP del equipo en la red local, o None si no hay una."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(_PROBE_ADDRESS)
            address = sock.getsockname()[0]
    except OSError:
        return None
    return None if address.startswith("127.") else address


def pairing_url(host: str, port: int, token: str) -> str:
    return f"http://{host}:{port}/?t={token}"


def qr_svg(url: str) -> str:
    """Un SVG completo, con namespace: se sirve como archivo y no embebido en el HTML."""
    out = io.BytesIO()
    segno.make(url, error="m").save(out, kind="svg", scale=5, border=2, dark="#111", light="#fff", xmldecl=False)
    return out.getvalue().decode()


def qr_terminal(url: str) -> str:
    out = io.StringIO()
    segno.make(url, error="m").terminal(out=out, compact=True)
    return out.getvalue()
