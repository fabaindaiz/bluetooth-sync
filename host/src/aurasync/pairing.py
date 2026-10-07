"""Pairing: how a new device gets its token (d-7c8794-37f9bc, research H §4).

A device asks without credentials (`POST /v1/pair/request {name}`), gets a request id, and
polls `GET /v1/pair/{id}` until the request is decided. The token is handed over **once**, in
the first poll after the approval; from then on the request only says `delivered`. A request
lives `REQUEST_TTL_S` while pending and as long again after the approval; a token nobody
collected is revoked when its request expires.

A request is approved in one of three ways:

1. **By an admin client** (`pair_approve`, which may set the scope). The admin sees each
   request's name, address and a 4-digit `check` that the requesting device also shows, so the
   owner can tell two phones apart (like Bluetooth's numeric comparison).
2. **The first-client window.** While no client is paired (the master token does not count),
   the first request in the `window_s` after the service started is approved alone, as
   `admin`: starting the service is the proof of presence, as the link button of a Hue
   bridge. Once a client exists the window is closed for good, until the service restarts
   with no clients again. `window_s = 0` turns it off.
3. **A 6-digit code** that `pair_start` (admin, or `aurasync clients code` with the master
   token) creates for `CODE_TTL_S`, and that the service prints to its terminal (never to the
   log buffer, which `read` clients see). A request that carries it is approved with the scope
   it asked for. `CODE_ATTEMPTS` wrong codes burn it: with 10⁶ codes, 5 guesses are not a way
   in, and the burn is the control firing, not switching off.

Hook for later: a code **played through the speakers** (research H §4.2) would call
`start_code` and hand the digits to the `tone` source instead of the terminal.

Requests are capped: one pending per address (a new one replaces it) and `MAX_PENDING` in all;
above that a request is refused (`busy`), it never evicts another.
"""

from __future__ import annotations

import contextlib
import hmac
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from aurasync.clients import SCOPES, ClientStore
from aurasync.control import ContractError

if TYPE_CHECKING:
    from collections.abc import Callable

REQUEST_TTL_S = 300.0
MAX_PENDING = 8
CODE_TTL_S = 120.0
CODE_ATTEMPTS = 5
WINDOW_S = 600.0


@dataclass
class Request:
    id: str
    name: str
    ip: str
    scope: str
    check: str
    created: float
    status: str = "pending"
    """pending · approved · delivered · denied · expired"""
    decided: float = 0.0
    how: str = ""
    client_id: str = ""
    token: str = field(default="", repr=False)

    def public(self, now: float) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "ip": self.ip,
            "scope": self.scope,
            "check": self.check,
            "status": self.status,
            "age_s": round(now - self.created, 1),
        }


def _request_id() -> str:
    """A url-safe id that never starts with '-': `aurasync clients approve <id>` would read it as
    an option (1 in 64 ids did)."""
    while True:
        request_id = secrets.token_urlsafe(18)
        if not request_id.startswith("-"):
            return request_id


class PairingDesk:
    def __init__(
        self,
        store: ClientStore,
        *,
        window_s: float = WINDOW_S,
        clock: Callable[[], float] = time.monotonic,
        log: Callable[[str], None] = lambda _: None,
        show_code: Callable[[str, float], None] = lambda *_: None,
    ) -> None:
        self.store = store
        self.window_s = window_s
        self.clock = clock
        self.log = log
        self.show_code = show_code
        """Where a new code is shown: the terminal, if the service has one."""
        self.started = clock()
        self._lock = threading.Lock()
        self._requests: dict[str, Request] = {}
        self._code: str | None = None
        self._code_until = 0.0
        self._code_misses = 0
        self._window_used = False

    # -- the window and the code ----------------------------------------------------

    def window_remaining(self) -> float:
        if self.window_s <= 0 or self._window_used or len(self.store) > 0:
            return 0.0
        return max(0.0, self.window_s - (self.clock() - self.started))

    def start_code(self, seconds: float = CODE_TTL_S) -> dict:
        code = f"{secrets.randbelow(10**6):06d}"
        with self._lock:
            self._code, self._code_until, self._code_misses = code, self.clock() + seconds, 0
        self.show_code(code, seconds)
        self.log(f"pairing: a code is active for {seconds:.0f} s")
        return {"code": code, "expires_in_s": seconds}

    def _code_view(self, now: float) -> dict:
        active = self._code is not None and now < self._code_until
        return {
            "active": active,
            "code": self._code if active else None,
            "expires_in_s": round(max(0.0, self._code_until - now), 1) if active else 0,
        }

    def _code_matches(self, code: str) -> bool:
        """Called with the lock held. A wrong code counts towards burning it."""
        now = self.clock()
        if self._code is None or now >= self._code_until:
            return False
        if hmac.compare_digest(code.encode(), self._code.encode()):
            self._code = None
            return True
        self._code_misses += 1
        if self._code_misses >= CODE_ATTEMPTS:
            self._code = None
            self.log(f"pairing: the code was burnt after {CODE_ATTEMPTS} wrong attempts")
        return False

    # -- requests -------------------------------------------------------------------

    def _expire(self, now: float) -> None:
        for req in list(self._requests.values()):
            since = req.decided if req.status != "pending" else req.created
            if now - since < REQUEST_TTL_S:
                continue
            if req.status == "approved":
                # Approved but never collected: the token must not outlive its request.
                with contextlib.suppress(ContractError):
                    self.store.revoke(req.client_id)
                self.log(f"pairing: '{req.name}' never collected its token; client {req.client_id} revoked")
            del self._requests[req.id]

    def request(self, name: str, ip: str, scope: str = "control", code: str | None = None) -> tuple[dict, bool]:
        """A new request. Returns its public view and whether a code was offered and was wrong."""
        if scope not in SCOPES:
            raise ContractError("out_of_range", f"scope must be one of {list(SCOPES)}")
        with self._lock:
            now = self.clock()
            self._expire(now)
            for old in [r for r in self._requests.values() if r.ip == ip and r.status == "pending"]:
                del self._requests[old.id]
            if sum(r.status == "pending" for r in self._requests.values()) >= MAX_PENDING:
                raise ContractError("busy", f"{MAX_PENDING} pairing requests are pending already")
            req = Request(_request_id(), name, ip, scope, f"{secrets.randbelow(10**4):04d}", now)
            self._requests[req.id] = req
            wrong_code = False
            if self.window_remaining() > 0:
                self._window_used = True
                req.scope = "admin"
                self._approve(req, "first-client window")
            elif code:
                if self._code_matches(code):
                    self._approve(req, "code")
                else:
                    wrong_code = True
            if req.status == "pending":
                self.log(f"pairing: '{name}' from {ip} asks for {scope} (check {req.check})")
            view = {"id": req.id, "check": req.check, "status": req.status, "expires_in_s": REQUEST_TTL_S}
        return view, wrong_code

    def _approve(self, req: Request, how: str) -> None:
        client, token = self.store.add(req.name, req.scope, req.ip)
        req.status, req.decided, req.how, req.client_id, req.token = "approved", self.clock(), how, client.id, token
        self.log(f"pairing: '{req.name}' from {req.ip} approved ({how}) as {req.scope}, client {client.id}")

    def poll(self, request_id: str) -> dict | None:
        """What the requesting device sees. None: no such request (or long expired)."""
        with self._lock:
            now = self.clock()
            self._expire(now)
            req = self._requests.get(request_id)
            if req is None:
                return None
            if req.status == "approved":
                token, req.token, req.status = req.token, "", "delivered"
                return {
                    "status": "approved",
                    "token": token,
                    "client": {"id": req.client_id, "name": req.name, "scope": req.scope},
                }
            return {"status": req.status}

    def approve(self, request_id: str, scope: str | None = None) -> dict:
        with self._lock:
            self._expire(self.clock())
            req = self._requests.get(request_id)
            if req is None or req.status != "pending":
                raise ContractError("not_found", f"no pending pairing request {request_id!r}")
            if scope is not None:
                req.scope = scope
            self._approve(req, "admin")
            return {"client": {"id": req.client_id, "name": req.name, "scope": req.scope}}

    def deny(self, request_id: str) -> dict:
        with self._lock:
            self._expire(self.clock())
            req = self._requests.get(request_id)
            if req is None or req.status != "pending":
                raise ContractError("not_found", f"no pending pairing request {request_id!r}")
            req.status, req.decided = "denied", self.clock()
            self.log(f"pairing: '{req.name}' from {req.ip} denied")
            return {"denied": request_id}

    def status(self) -> dict:
        with self._lock:
            now = self.clock()
            self._expire(now)
            return {
                "requests": [r.public(now) for r in self._requests.values() if r.status == "pending"],
                "window": {"open": self.window_remaining() > 0, "remaining_s": round(self.window_remaining(), 1)},
                "code": self._code_view(now),
            }

    def accepting(self) -> bool:
        """For `hello`: whether a request can succeed without an admin at hand."""
        with self._lock:
            return self.window_remaining() > 0 or self._code_view(self.clock())["active"]
