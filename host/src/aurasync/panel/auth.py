"""Token del panel y chequeos de Host y Origin (docs/research/09 §6).

El token es obligatorio incluso en 127.0.0.1: una página web abierta en el mismo
navegador puede mandar pedidos al puerto local (CSRF o DNS rebinding). El token vive
lo que vive el proceso.
"""

import hmac
import secrets

COOKIE = "aurasync_token"


def new_token() -> str:
    return secrets.token_urlsafe(16)


def token_matches(expected: str, given: str | None) -> bool:
    if not given:
        return False
    return hmac.compare_digest(expected.encode(), given.encode())


def hostname(host_header: str | None) -> str | None:
    """El nombre sin el puerto. Solo IPv4 y nombres: el panel no escucha en IPv6."""
    if not host_header:
        return None
    return host_header.rsplit(":", 1)[0] if ":" in host_header else host_header


def host_allowed(host_header: str | None, allowed: frozenset[str]) -> bool:
    return hostname(host_header) in allowed


def origin_allowed(origin: str | None, host_header: str | None) -> bool:
    return bool(origin) and bool(host_header) and origin == f"http://{host_header}"
