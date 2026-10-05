"""Clients: one token per paired device, kept only as a hash (d-7c8794-37f9bc).

A token is `asc_<id>_<secret>`: `id` names the client (8 hex characters, safe to show and to
log), `secret` is 256 random bits. The file keeps `sha256(salt ‖ secret)` with a salt of its
own per client; the plain token exists only in the reply that hands it over, once.

**Rotation does not disconnect anyone** (card *secrets-survive-rotation*). The master token of
`service.json` stays valid as an `admin` credential, but no client token derives from it or is
signed with it: rotating it touches nothing here. Revocation is explicit, one client at a time.

**Scopes.** `read` sees the state, the logs and the stream; `control` does everything about
listening; `admin` also pairs and revokes clients, forgets Bluetooth devices, restarts
services, changes the microphone and shuts the service down. An operation the table does not
name needs `admin`: one added later is closed until someone classifies it
(card *fail-closed-defaults*).

`clients.json` (0600) is written atomically. `last_used` moves in memory and reaches the file at
most once every `TOUCH_WRITE_S`; every other change is written at once. A client unused for
`IDLE_DAYS` stops verifying (inactivity expiry).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import stat
import threading
import time
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any

from aurasync.control import ContractError
from aurasync.presets import write_atomic

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

SCOPES = ("read", "control", "admin")
RANK = {name: i for i, name in enumerate(SCOPES)}
IDLE_DAYS = 180
TOUCH_WRITE_S = 60.0
MAX_CLIENTS = 64
PREFIX = "asc"
TOKEN_RE = re.compile(r"^asc_([0-9a-f]{8})_([A-Za-z0-9_-]{43})$")

READ_OPS = frozenset(
    {"state", "logs", "presets", "chain", "sync_state", "sync_explain", "spatial_explain", "sync_time"}
)
ADMIN_OPS = frozenset(
    {
        "shutdown",
        "forget",
        "speaker_add",
        "speaker_remove",
        "microphone_set",
        "service_start",
        "service_stop",
        "service_restart",
        "radio_log",
        "calibration_dump",
        "pair_start",
        "pair_status",
        "pair_approve",
        "pair_deny",
        "clients",
        "client_revoke",
        "client_rename",
    }
)
CONTROL_OPS = frozenset(
    {
        "sync_set",
        "probe_reference",
        "sync_measure",
        "sync_apply",
        "start",
        "stop",
        "set",
        "assign",
        "preset_save",
        "preset_load",
        "preset_delete",
        "save",
        "source",
        "tone",
        "recalibrate",
        "calibrate",
        "calibrate_cancel",
        "calibration_apply",
        "measurement_save",
        "eq_apply",
        "eq_reset",
        "scan",
        "connect",
        "disconnect",
        "ab_start",
        "ab_play",
        "ab_answer",
        "ab_stop",
        "chain_set",
        "chain_reset",
        "monitor_set",
    }
)


def required_scope(op: Any) -> str:
    """The scope an operation needs. Unknown or unclassified: `admin`."""
    if op in READ_OPS:
        return "read"
    if op in CONTROL_OPS:
        return "control"
    return "admin"


def allows(scope: str, needed: str) -> bool:
    return RANK.get(scope, -1) >= RANK[needed]


def _iso(t: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


@dataclass
class Client:
    id: str
    name: str
    scope: str
    salt: str
    hash: str
    created: float
    last_used: float
    last_ip: str = ""

    def public(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "scope": self.scope,
            "created": _iso(self.created),
            "last_used": _iso(self.last_used),
            "last_ip": self.last_ip,
            "expires": _iso(self.last_used + IDLE_DAYS * 86400),
        }


@dataclass(frozen=True)
class Principal:
    """Who made a request."""

    id: str
    """A client's id, or `master`."""
    name: str
    scope: str

    @property
    def is_master(self) -> bool:
        return self.id == "master"


def _digest(salt: str, secret: str) -> str:
    return hashlib.sha256(bytes.fromhex(salt) + secret.encode()).hexdigest()


class ClientStore:
    """The paired clients. `path=None` keeps them in memory only (tests, a bare `make_server`)."""

    def __init__(self, path: Path | None, clock: Callable[[], float] = time.time) -> None:
        self.path = path
        self.clock = clock
        self._lock = threading.Lock()
        self._clients: dict[str, Client] = {}
        self._written_at = 0.0
        self._dirty = False
        self.device = secrets.token_hex(4)
        """A stable id for this device, for `hello` and the mDNS record."""
        self._load()

    # -- the file ---------------------------------------------------------------------

    def _load(self) -> None:
        if self.path is None:
            return
        if not self.path.exists():
            self._write()
            return
        mode = stat.S_IMODE(self.path.stat().st_mode)
        if mode & 0o077:
            msg = f"{self.path} has mode {mode:o}: it holds the clients' hashes. Fix: chmod 600 {self.path}"
            raise PermissionError(msg)
        data = json.loads(self.path.read_text())
        if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("clients"), list):
            msg = f"{self.path} is not a clients file of version 1. Move it away to start with no clients"
            raise ValueError(msg)
        self.device = str(data.get("device") or self.device)
        for raw in data["clients"]:
            client = Client(**raw)
            if client.scope not in SCOPES:
                msg = f"{self.path}: client {client.id} has an unknown scope {client.scope!r}"
                raise ValueError(msg)
            self._clients[client.id] = client

    def _write(self) -> None:
        if self.path is None:
            return
        data = {"version": 1, "device": self.device, "clients": [asdict(c) for c in self._clients.values()]}
        write_atomic(self.path, json.dumps(data, indent=2) + "\n", mode=0o600)
        self._written_at = self.clock()
        self._dirty = False

    def flush(self) -> None:
        with self._lock:
            if self._dirty:
                self._write()

    # -- clients ----------------------------------------------------------------------

    def __len__(self) -> int:
        return len(self._clients)

    def add(self, name: str, scope: str, ip: str = "") -> tuple[Client, str]:
        """A new client and its token. The token is not kept anywhere."""
        if scope not in SCOPES:
            raise ContractError("out_of_range", f"scope must be one of {list(SCOPES)}")
        with self._lock:
            if len(self._clients) >= MAX_CLIENTS:
                raise ContractError("conflict", f"{MAX_CLIENTS} clients are paired already: revoke one first")
            client_id = secrets.token_hex(4)
            while client_id in self._clients or client_id == "master":
                client_id = secrets.token_hex(4)
            secret = secrets.token_urlsafe(32)
            salt = secrets.token_hex(16)
            now = self.clock()
            client = Client(client_id, name, scope, salt, _digest(salt, secret), now, now, ip)
            self._clients[client_id] = client
            self._write()
        return client, f"{PREFIX}_{client_id}_{secret}"

    def verify(self, token: str, ip: str = "") -> Principal | None:
        """The client a token belongs to, or None (unknown, wrong, revoked or expired)."""
        match = TOKEN_RE.match(token)
        if match is None:
            return None
        client_id, secret = match.groups()
        with self._lock:
            client = self._clients.get(client_id)
            if client is None:
                # Spend the same time as a real check, so a timing does not tell ids apart.
                hmac.compare_digest(_digest("00" * 16, secret), "0" * 64)
                return None
            if not hmac.compare_digest(_digest(client.salt, secret), client.hash):
                return None
            now = self.clock()
            if now - client.last_used > IDLE_DAYS * 86400:
                return None
            client.last_used = now
            if ip:
                client.last_ip = ip
            self._dirty = True
            if now - self._written_at >= TOUCH_WRITE_S:
                self._write()
            return Principal(client.id, client.name, client.scope)

    def alive(self, principal: Principal) -> bool:
        """Whether a credential checked earlier still holds (a stream asks this every tick)."""
        if principal.is_master:
            return True
        client = self._clients.get(principal.id)
        return client is not None and client.scope == principal.scope

    def revoke(self, client_id: str) -> Client:
        with self._lock:
            client = self._clients.pop(client_id, None)
            if client is None:
                raise ContractError("not_found", f"no client {client_id!r}")
            self._write()
        return client

    def rename(self, client_id: str, name: str) -> Client:
        with self._lock:
            client = self._clients.get(client_id)
            if client is None:
                raise ContractError("not_found", f"no client {client_id!r}")
            client.name = name
            self._write()
        return client

    def list(self) -> list[dict]:
        with self._lock:
            return [c.public() for c in sorted(self._clients.values(), key=lambda c: c.created)]
