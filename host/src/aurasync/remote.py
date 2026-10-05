"""How the service is reached from other devices: HTTPS, clients, pairing and mDNS.

`service.serve` calls `open_listeners` once and `Listeners.close` on its way out. Everything a
PWA on another origin needs (d-7c8794-37f9bc) is set up here, so `service.py` only grows by the
keys of `service.json`:

| key | default | what |
|---|---|---|
| `tls` | `false` in an old file, `true` in a new one | HTTPS with the service's own root (`tls.py`) |
| `https_port` | 8443 | where HTTPS listens; HTTP stays on `port` (the panel served locally, the fallback) |
| `panel_origins` | GitHub Pages + Vite | the origins that get CORS headers |
| `pair_window_s` | 600 | the first-client window after start (`pairing.py`); 0 turns it off |
| `mdns` | `false` | announce `_aurasync._tcp` and `aurasync.local` with avahi (`mdns.py`) |
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from aurasync.access import DEFAULT_ORIGINS, Access
from aurasync.clients import ClientStore
from aurasync.lan import LocalNames

if TYPE_CHECKING:
    from collections.abc import Callable
    from http.server import ThreadingHTTPServer
    from pathlib import Path

    from aurasync.mdns import Announcer
    from aurasync.service import Service, ServiceConfig
    from aurasync.tls import CertificateKeeper, Certificates

DEFAULT_HTTPS_PORT = 8443
REMOTE_KEYS = {"tls", "https_port", "panel_origins", "pair_window_s", "mdns"}
MAX_PORT = 65535
MAX_WINDOW_S = 3600
ORIGIN_PREFIXES = ("https://", "http://localhost", "http://127.0.0.1")


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def check_config(config: ServiceConfig, path: Path) -> list[str]:
    """What is wrong with the remote keys of `service.json` (empty: nothing)."""
    problems = []
    if not isinstance(config.tls, bool):
        problems.append(f"{path}: tls must be true or false")
    port = config.https_port
    if not (_is_number(port) and isinstance(port, int) and 0 < port <= MAX_PORT):
        problems.append(f"{path}: https_port must be a number from 1 to {MAX_PORT}")
    elif port == config.port:
        problems.append(f"{path}: https_port and port must differ")
    origins = config.panel_origins
    if not isinstance(origins, list) or not all(isinstance(o, str) and o.startswith(ORIGIN_PREFIXES) for o in origins):
        problems.append(
            f"{path}: panel_origins must be a list of https:// origins (or http://localhost for development)"
        )
    elif any(urlsplit(o.rstrip("/")).path for o in origins):
        problems.append(f"{path}: panel_origins are origins, without a path: https://user.github.io, not …/aurasync/")
    if not (_is_number(config.pair_window_s) and 0 <= config.pair_window_s <= MAX_WINDOW_S):
        problems.append(f"{path}: pair_window_s must be a number of seconds from 0 to {MAX_WINDOW_S}")
    if not isinstance(config.mdns, bool):
        problems.append(f"{path}: mdns must be true or false")
    return problems


def default_origins() -> list[str]:
    return list(DEFAULT_ORIGINS)


@dataclass
class Listeners:
    http: ThreadingHTTPServer
    access: Access
    names: LocalNames
    https: ThreadingHTTPServer | None = None
    certificates: Certificates | None = None
    keeper: CertificateKeeper | None = None
    announcer: Announcer | None = None
    threads: list[threading.Thread] = field(default_factory=list)

    def start(self) -> None:
        for server, label in ((self.http, "rest"), (self.https, "rest-tls")):
            if server is None:
                continue
            thread = threading.Thread(target=server.serve_forever, name=f"aurasync-{label}", daemon=True)
            thread.start()
            self.threads.append(thread)
        if self.keeper is not None:
            self.keeper.start()

    def close(self) -> None:
        """Every exit of `serve` passes here: the announcement, the renewals and the ports end."""
        if self.announcer is not None:
            self.announcer.stop()
        if self.keeper is not None:
            self.keeper.stop()
        self.access.store.flush()
        for server in (self.http, self.https):
            if server is not None:
                server.shutdown()
                server.server_close()


def open_listeners(
    service: Service,
    config: ServiceConfig,
    *,
    bind: str,
    port: int,
    https_port: int | None,
    config_dir: Path | None,
    log: Callable[[str], None],
    show_code: Callable[[str, float, str | None], None],
) -> Listeners:
    """Open HTTP, and HTTPS when `tls` is on. Raises OSError when a port cannot be opened."""
    from aurasync.rest import make_server  # noqa: PLC0415 - rest imports service, which imports this

    names = LocalNames(bind)
    store = ClientStore(config_dir / "clients.json" if config_dir is not None else None)
    access = Access(
        config.token,
        store,
        origins=config.panel_origins,
        window_s=float(config.pair_window_s),
        log=log,
        show_code=show_code,
    )
    service.access = access
    certificates = keeper = https = None
    if config.tls and config_dir is not None:
        from aurasync.tls import CertificateKeeper, Certificates  # noqa: PLC0415 - cryptography only when used

        certificates = Certificates(config_dir / "tls")
        certificates.ensure_server(names.dns_names(), names.ip_addresses(), hostname=names.hostname)
        context = certificates.server_context()
        https = make_server(
            service,
            bind,
            https_port if https_port is not None else config.https_port,
            config.token,
            access=access,
            names=names,
            ssl_context=context,
            certificates=certificates,
        )
        keeper = CertificateKeeper(certificates, names, context, log)
        info = certificates.info()
        access.tls = {
            "enabled": True,
            "port": https.server_address[1],
            "root_sha256": info.root_sha256,
            "cert_sha256": info.cert_sha256,
        }
    try:
        http = make_server(service, bind, port, config.token, access=access, names=names, certificates=certificates)
    except OSError:
        if https is not None:
            https.server_close()
        raise
    listeners = Listeners(http, access, names, https, certificates, keeper)
    if config.mdns:
        from aurasync.mdns import Announcer  # noqa: PLC0415

        listeners.announcer = Announcer(log)
        txt: dict[str, Any] = {"v": "1", "id": store.device, "http": str(http.server_address[1])}
        if access.tls:
            txt |= {"tls": "1", "https": str(access.tls["port"]), "fp": access.tls["root_sha256"].replace(":", "")[:16]}
        main_port = access.tls["port"] if access.tls else http.server_address[1]
        ips = [ip for ip in names.ips if ip.startswith(("192.168.", "10.", "172."))]
        listeners.announcer.start(
            f"aurasync on {names.hostname or 'this machine'}", main_port, txt, ips[0] if ips else None
        )
    return listeners


STARTUP_CODE_S = 600.0


def announce_remote(
    listeners: Listeners, urls: list[str], announce: Callable[[str], None], *, show_token: bool
) -> None:
    """What the terminal says about HTTPS and pairing when the service starts."""
    access = listeners.access
    lan = [u for u in urls if "127.0.0.1" not in u]
    if access.tls:
        https_port = access.tls["port"]
        hosts = [u.split("//", 1)[1].rsplit(":", 1)[0] for u in lan] or ["127.0.0.1"]
        announce("  https: " + " · ".join(f"https://{h}:{https_port}" for h in hosts))
        announce(f"  root certificate sha256 {access.tls['root_sha256']}")
        announce(f"    iPhone: {lan[0] if lan else urls[0]}/v1/tls/root.mobileconfig · Android: …/v1/tls/root.crt")
    else:
        announce("  https: off (tls false in service.json); the PWA on GitHub Pages needs it")
    window = access.desk.window_remaining()
    if window > 0:
        announce(f"  pairing: no device is paired; the first that asks in the next {window / 60:.0f} min becomes admin")
    elif show_token:
        access.desk.start_code(STARTUP_CODE_S)
    else:
        announce("  pairing: approve from a paired admin device, or run `aurasync clients code`")
