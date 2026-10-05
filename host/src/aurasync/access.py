"""Who may talk to the service: credentials, scopes, failed attempts, stream tickets, CORS.

`rest.py` asks this module three things about each request: who it is (`authenticate`),
whether its address is blocked after failed attempts (`limiter`), and which origin may read the
reply (`cors_origin`). The contract's access operations (`pair_*`, `clients`, `client_*`) run
here too, through `Service.handle`, so REST and a future serial line answer them alike.

**Failed attempts** (`AttemptLimiter`). A presented credential that is wrong (a bearer, a
`?token=`, a cookie, a stream ticket, a pairing code, a pairing request id) counts against the
address; a missing one does not (opening the panel without the cookie is not an attack).
`FREE_FAILURES` are free; each one after that blocks the address for twice as long as the last,
up to `MAX_BLOCK_S`. While blocked, even a right credential is refused: otherwise guessing just
continues. The table is bounded, and a full table never lets an address out: addresses that are
not blocked are forgotten first, and when every entry is blocked, new addresses share one
`overflow` entry that blocks like any other (card *abuser-controlled-exemption*).

**Stream tickets** (`StreamTickets`). `EventSource` cannot send headers (WHATWG HTML §9.2), so a
panel on another origin trades its bearer for a ticket (`POST /v1/stream/ticket`) and opens
`GET /v1/stream?ticket=…`. A ticket works once, within `TICKET_TTL_S`, and carries the client
it was made for; nothing logs it. They are meant to die with their client: a revoked client's
tickets stop working (card *secrets-survive-rotation*, its *Not when*).
"""

from __future__ import annotations

import hmac
import secrets
import threading
import time
from typing import TYPE_CHECKING, Any

from aurasync import connection_code, control
from aurasync.clients import ClientStore, Principal, allows, required_scope
from aurasync.control import ContractError
from aurasync.pairing import PairingDesk

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

ACCESS_OPS = frozenset(
    {"pair_start", "pair_status", "pair_approve", "pair_deny", "clients", "client_revoke", "client_rename"}
)
"""Contract operations this module answers. They never touch the engine."""

DEFAULT_ORIGINS = ("https://fabaindaiz.github.io", "http://localhost:5173")
"""The PWA on GitHub Pages (d-7c8794-37f9bc) and Vite's development server."""

FREE_FAILURES = 5
FIRST_BLOCK_S = 1.0
MAX_BLOCK_S = 300.0
LIMITER_SIZE = 1024
OVERFLOW = "overflow"

TICKET_TTL_S = 30.0
MAX_TICKETS = 64

MASTER = Principal("master", "master token", "admin")


class AttemptLimiter:
    def __init__(self, clock: Callable[[], float] = time.monotonic, size: int = LIMITER_SIZE) -> None:
        self.clock = clock
        self.size = size
        self._lock = threading.Lock()
        self._entries: dict[str, list[float]] = {}
        """address → [failures, blocked_until, last_failure]"""

    def _key(self, ip: str) -> str:
        if ip in self._entries or len(self._entries) < self.size:
            return ip
        now = self.clock()
        loose = [k for k, (_, until, _) in self._entries.items() if until <= now and k != OVERFLOW]
        if loose:
            oldest = min(loose, key=lambda k: self._entries[k][2])
            del self._entries[oldest]
            return ip
        return OVERFLOW

    def blocked_for(self, ip: str) -> float:
        """Seconds this address must still wait; 0 when it may try."""
        with self._lock:
            entry = self._entries.get(ip)
            if entry is None and len(self._entries) >= self.size:
                entry = self._entries.get(OVERFLOW)
            return max(0.0, entry[1] - self.clock()) if entry else 0.0

    def fail(self, ip: str) -> None:
        with self._lock:
            key = self._key(ip)
            now = self.clock()
            entry = self._entries.setdefault(key, [0, 0.0, now])
            entry[0] += 1
            entry[2] = now
            over = entry[0] - FREE_FAILURES
            if over > 0:
                entry[1] = now + min(MAX_BLOCK_S, FIRST_BLOCK_S * 2 ** (over - 1))

    def succeed(self, ip: str) -> None:
        with self._lock:
            entry = self._entries.get(ip)
            if entry is not None and entry[1] <= self.clock():
                del self._entries[ip]


class StreamTickets:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self.clock = clock
        self._lock = threading.Lock()
        self._tickets: dict[str, tuple[Principal, float]] = {}

    def issue(self, principal: Principal) -> dict:
        with self._lock:
            now = self.clock()
            for key in [k for k, (_, until) in self._tickets.items() if until <= now]:
                del self._tickets[key]
            if len(self._tickets) >= MAX_TICKETS:
                raise ContractError("busy", f"{MAX_TICKETS} stream tickets are unused already")
            ticket = secrets.token_urlsafe(24)
            self._tickets[ticket] = (principal, now + TICKET_TTL_S)
        return {"ticket": ticket, "expires_in_s": TICKET_TTL_S}

    def redeem(self, ticket: str) -> Principal | None:
        """The ticket's client, once. None: unknown, used or expired."""
        with self._lock:
            found = None
            for key in self._tickets:
                if hmac.compare_digest(key.encode(), ticket.encode()):
                    found = key
            if found is None:
                return None
            principal, until = self._tickets.pop(found)
            return principal if self.clock() < until else None


class Access:
    """The master token, the clients, pairing, the limiter, the tickets and the origins."""

    def __init__(
        self,
        master_token: str,
        store: ClientStore | None = None,
        *,
        origins: Iterable[str] = DEFAULT_ORIGINS,
        window_s: float = 600.0,
        log: Callable[[str], None] = lambda _: None,
        show_code: Callable[[str, float, str | None], None] = lambda *_: None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.master = master_token.encode()
        self.store = store if store is not None else ClientStore(None)
        self.origins = frozenset(o.rstrip("/") for o in origins)
        self.log = log
        self._show_code = show_code
        self.desk = PairingDesk(self.store, window_s=window_s, clock=clock, log=log, show_code=self._shown)
        self.where: Callable[[], str | None] = lambda: None
        """This device's address on the local network (`serve` sets it): what a connection code carries."""
        self.limiter = AttemptLimiter(clock)
        self.tickets = StreamTickets(clock)
        self.tls: dict | None = None
        """`{"port", "root_sha256", "cert_sha256"}` when HTTPS is on; `hello` shows it."""

    # -- credentials ----------------------------------------------------------------

    def verify(self, token: str, ip: str = "") -> Principal | None:
        if not token:
            return None
        if hmac.compare_digest(token.encode(), self.master):
            return MASTER
        return self.store.verify(token, ip)

    def alive(self, principal: Principal) -> bool:
        return self.store.alive(principal)

    @staticmethod
    def needs(op: Any) -> str:
        return required_scope(op)

    @staticmethod
    def allows(principal: Principal, needed: str) -> bool:
        return allows(principal.scope, needed)

    def cors_origin(self, origin: str | None) -> str | None:
        """The origin to echo in `Access-Control-Allow-Origin`, or None."""
        return origin if origin and origin.rstrip("/") in self.origins else None

    # -- the contract's access operations (`Service.handle` calls this) -------------

    def handle(self, command: control.Command) -> dict:
        try:
            result = self._dispatch(command.op, command.args)
        except ContractError as exc:
            return control.error(command.id, exc.code, exc.message)
        return control.ok(command.id, result)

    def connection_code(self, code: str | None) -> str | None:
        """The PWA's single code for this pairing code (`connection_code.py`), or None: without
        HTTPS the PWA cannot reach the device, and only an IPv4 address fits in a code."""
        host = self.where()
        if not code or not self.tls or not self.tls.get("root_sha256") or not host:
            return None
        try:
            return connection_code.encode(host, int(self.tls["port"]), code, str(self.tls["root_sha256"]))
        except ValueError:
            return None

    def _shown(self, code: str, seconds: float) -> None:
        self._show_code(code, seconds, self.connection_code(code))

    def _dispatch(self, op: str, args: dict) -> dict:
        match op:
            case "pair_start":
                started = self.desk.start_code(args.get("seconds", 120.0))
                return {**started, "connection_code": self.connection_code(started["code"])}
            case "pair_status":
                status = self.desk.status()
                code = status["code"]
                return {**status, "code": {**code, "connection_code": self.connection_code(code["code"])}}
            case "pair_approve":
                return self.desk.approve(args["request"], args.get("scope"))
            case "pair_deny":
                return self.desk.deny(args["request"])
            case "clients":
                return {"clients": self.store.list(), "master": {"scope": MASTER.scope}}
            case "client_revoke":
                client = self.store.revoke(args["client"])
                self.log(f"clients: '{client.name}' ({client.id}) revoked")
                return {"revoked": client.id}
            case "client_rename":
                return {"client": self.store.rename(args["client"], args["name"]).public()}
        raise ContractError("unknown_op", f"{op} is not an access operation")

    # -- unauthenticated -------------------------------------------------------------

    def hello(self, name: str, version: str) -> dict:
        return {
            "service": "aurasync",
            "contract": control.VERSION,
            "version": version,
            "id": self.store.device,
            "name": name,
            "tls": dict(self.tls) if self.tls else {"enabled": False},
            "pairing": {
                "accepting": self.desk.accepting(),
                "first_window_s": round(self.desk.window_remaining(), 1),
            },
        }
