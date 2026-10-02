"""Announcing the service on the local network by mDNS (optional, off by default).

`service.json` `"mdns": true` makes the service run avahi's own publishers as child processes
for as long as it lives:

- `avahi-publish-service <name> _aurasync._tcp <port> v=1 id=… tls=… https=…` (DNS-SD: a native
  app can browse for it; a browser cannot, research H §3);
- `avahi-publish-address -R aurasync.local <ip>`, so the PWA can try `https://aurasync.local:8443`
  (the name the certificate carries). A second device on the network gets a collision: that
  publisher exits, it is logged, and the device stays reachable by its host name and address.

Why child processes and not the alternatives:

- a service file in `/etc/avahi/services/` is a change to the system that outlives the program
  (CLAUDE.md: written down with its reversal first), and needs root;
- `python-zeroconf` would be a new dependency (LGPL-2.1-or-later and `ifaddr`, per PyPI for 0.151.5 on 2026-10-02), and it would run a
  second mDNS responder next to avahi-daemon on port 5353;
- `avahi-publish-*` (package `avahi-utils`) registers through the running avahi-daemon and the
  record disappears when the process does: nothing to revert. Its absence (the Mac, a Pi without
  `avahi-utils`) only logs a warning: the announcement is a convenience, the QR and the address
  still work.

Every exit of the service (`shutdown`, Ctrl-C, SIGTERM) goes through `serve`'s `finally`, which
calls `stop` (card *kill-switch-reaches-every-path*). A SIGKILL skips that `finally`; on Linux the
children are started with `PR_SET_PDEATHSIG`, so the kernel ends them with their parent and the
announcement does not outlive the service.
"""

from __future__ import annotations

import ctypes
import shutil
import signal
import subprocess
import sys
import time
from typing import TYPE_CHECKING

from aurasync.lan import MDNS_NAME

if TYPE_CHECKING:
    from collections.abc import Callable

SERVICE_TYPE = "_aurasync._tcp"
PR_SET_PDEATHSIG = 1


def _die_with_parent() -> None:
    """Runs in the child before exec (Linux): SIGTERM when the service dies, even by SIGKILL."""
    libc = ctypes.CDLL("libc.so.6", use_errno=True)
    libc.prctl(PR_SET_PDEATHSIG, signal.SIGTERM)


class Announcer:
    def __init__(self, log: Callable[[str], None], which: Callable[[str], str | None] = shutil.which) -> None:
        self.log = log
        self.which = which
        self._children: list[subprocess.Popen] = []

    def commands(self, name: str, port: int, txt: dict[str, str], ip: str | None) -> list[list[str]]:
        records = [f"{k}={v}" for k, v in txt.items()]
        out = [["avahi-publish-service", name, SERVICE_TYPE, str(port), *records]]
        if ip:
            out.append(["avahi-publish-address", "-R", MDNS_NAME, ip])
        return out

    def start(self, name: str, port: int, txt: dict[str, str], ip: str | None) -> bool:
        if self.which("avahi-publish-service") is None:
            self.log("mdns: avahi-publish-service is not installed (package avahi-utils); not announced")
            return False
        for command in self.commands(name, port, txt, ip):
            try:
                child = subprocess.Popen(
                    command,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    preexec_fn=_die_with_parent if sys.platform == "linux" else None,  # noqa: PLW1509
                )
            except OSError as exc:
                self.log(f"mdns: {command[0]} did not start: {exc}")
                continue
            self._children.append(child)
        time.sleep(0.3)
        for child in self._children:
            if child.poll() is not None:
                self.log(f"mdns: {child.args[0]} exited at once (code {child.returncode}); a name collision?")
        alive = [c for c in self._children if c.poll() is None]
        if alive:
            self.log(f"mdns: announced {SERVICE_TYPE} on port {port}" + (f" and {MDNS_NAME} → {ip}" if ip else ""))
        return bool(alive)

    def stop(self) -> None:
        for child in self._children:
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    child.kill()
        self._children.clear()

    @property
    def running(self) -> int:
        return sum(c.poll() is None for c in self._children)
