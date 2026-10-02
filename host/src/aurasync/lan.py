"""The names this machine answers to on the local network, kept current.

Two readers need the same list: the `Host` check of `rest.py` (DNS rebinding: a page on
another name that resolves here is refused) and the certificate of `tls.py` (its SANs). Both
used to be computed once at start; a Raspberry Pi that changes its address by DHCP would then
lock the panel out until the service restarted (research H §0). `LocalNames` recomputes the
addresses at most every `refresh_s`, and at once (rate-limited) when a request arrives with a
name it does not know, so a new address is accepted within a request or two.
"""

from __future__ import annotations

import ipaddress
import socket
import threading
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

MDNS_NAME = "aurasync.local"
"""The fixed name the PWA tries first. A second device on the same network is renamed by avahi
(`aurasync-2.local`); the QR and the remembered address are the fallback."""
REFRESH_S = 30.0
MISS_REFRESH_S = 2.0
"""A request with an unknown `Host` triggers a refresh at most this often: an attacker
sending foreign names cannot make every request run `ip addr`."""


def _default_addresses() -> list[str]:
    from aurasync.service import local_addresses  # noqa: PLC0415 - service imports rest, which imports this

    return local_addresses()


class LocalNames:
    """Every name and address a browser may use for this machine."""

    def __init__(
        self,
        bind: str = "0.0.0.0",  # noqa: S104 - the same default as the service
        *,
        addresses: Callable[[], list[str]] | None = None,
        hostname: Callable[[], str] = socket.gethostname,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.bind = bind
        self._addresses = addresses or _default_addresses
        self._hostname = hostname
        self._clock = clock
        self._lock = threading.Lock()
        self._ips: tuple[str, ...] = ()
        self._names: frozenset[str] = frozenset()
        self._at = -1e18
        self._miss_at = -1e18
        self.refresh()

    @property
    def wildcard(self) -> bool:
        return self.bind in {"0.0.0.0", ""}  # noqa: S104

    def refresh(self) -> bool:
        """Recompute now. True when the set of addresses changed."""
        if self.wildcard:
            ips = tuple(sorted(set(self._addresses())))
            host = self._hostname()
        else:
            ips, host = (self.bind,), ""
        names = {"127.0.0.1", "localhost", *ips}
        if self.wildcard:
            names.update({host, f"{host}.local", MDNS_NAME} if host else {MDNS_NAME})
        with self._lock:
            changed = ips != self._ips
            self._ips, self._names, self._at = ips, frozenset(n.lower() for n in names), self._clock()
        return changed

    def _maybe_refresh(self) -> None:
        if self._clock() - self._at >= REFRESH_S:
            self.refresh()

    @property
    def ips(self) -> tuple[str, ...]:
        """The machine's LAN addresses (IPv4, without loopback)."""
        self._maybe_refresh()
        return self._ips

    @property
    def hostname(self) -> str:
        return self._hostname() if self.wildcard else ""

    def names(self) -> frozenset[str]:
        self._maybe_refresh()
        return self._names

    def allows(self, host: str | None) -> bool:
        """Whether a request's `Host` (without the port) names this machine."""
        if not host:
            return False
        host = host.lower()
        if host in self.names():
            return True
        now = self._clock()
        if now - self._miss_at < MISS_REFRESH_S:
            return False
        self._miss_at = now
        self.refresh()
        return host in self._names

    def dns_names(self) -> list[str]:
        """The DNS names a certificate should carry."""
        out = ["localhost"]
        if self.wildcard:
            host = self.hostname.lower()
            out += [MDNS_NAME]
            if host:
                out += [host, f"{host}.local"] if not host.endswith(".local") else [host]
        elif not _is_ip(self.bind):
            out.append(self.bind.lower())
        return sorted(set(out))

    def ip_addresses(self) -> list[str]:
        return sorted({"127.0.0.1", *(ip for ip in self.ips if _is_ip(ip))})


def _is_ip(text: str) -> bool:
    try:
        ipaddress.ip_address(text)
    except ValueError:
        return False
    return True


def host_name(header: str | None) -> str | None:
    """The `Host` header without its port; IPv6 literals keep their address."""
    if not header:
        return None
    if header.startswith("["):
        return header[1 : header.find("]")] if "]" in header else None
    return header.rsplit(":", 1)[0] if ":" in header else header
