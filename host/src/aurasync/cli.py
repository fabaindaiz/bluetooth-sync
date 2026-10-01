"""Punto de entrada de la línea de comandos.

Hoy existe un solo subcomando, `panel`, que sirve el panel de control sobre el motor
simulado (docs/research/09, d-7c8794-b1eaac). Los demás subcomandos planificados
(doctor, scan, tone, play, calibrate, assign; docs/research/08 §6) no existen
todavía. Un subcomando desconocido termina con error, en lugar de no hacer nada en
silencio.
"""

import argparse
import asyncio
import contextlib
import logging
import sys
import time
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass

from aurasync import __version__

DEFAULT_PORT = 8737
LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost"})


def _say(text: str = "", *, error: bool = False) -> None:
    # Con flush: si la salida va a un archivo o a un servicio, la URL con el token
    # tiene que aparecer apenas el panel arranca, no al terminar.
    stream = sys.stderr if error else sys.stdout
    stream.write(text + "\n")
    stream.flush()


class PanelConfigError(Exception):
    """El panel no puede arrancar con estas opciones."""


@dataclass(frozen=True)
class PanelConfig:
    bind: str
    port: int
    token: str
    allowed_hosts: frozenset[str]
    pairing_url: str | None

    @property
    def local_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/?t={self.token}"


def panel_config(*, port: int, lan: bool, lan_address: Callable[[], str | None], token: str) -> PanelConfig:
    """Solo localhost por defecto; la red local es una opción explícita (fail-closed)."""
    if not lan:
        return PanelConfig("127.0.0.1", port, token, LOCAL_HOSTS, None)
    address = lan_address()
    if address is None:
        msg = "no se encontró una dirección en la red local; ¿hay red?"
        raise PanelConfigError(msg)
    from aurasync.panel.pairing import pairing_url  # noqa: PLC0415 (dependencia del panel)

    return PanelConfig(
        "0.0.0.0",  # noqa: S104 (pedido explícito con --lan, y todo pedido exige token)
        port,
        token,
        LOCAL_HOSTS | {address},
        pairing_url(address, port, token),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aurasync",
        description="Un canal de audio distinto a cada parlante Bluetooth, sincronizados.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command")

    panel = commands.add_parser("panel", help="sirve el panel de control en el navegador")
    panel.add_argument(
        "--demo",
        action="store_true",
        help="usar el motor simulado (hoy es el único; el real espera la decisión de seguir)",
    )
    panel.add_argument("--lan", action="store_true", help="aceptar conexiones de la red local, con QR para el teléfono")
    panel.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"puerto (por defecto {DEFAULT_PORT})")
    panel.add_argument("--open", action="store_true", help="abrir el panel en el navegador del PC")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "panel":
        return _panel(args)
    parser.print_help()
    return 0


def _panel(args: argparse.Namespace) -> int:
    if not args.demo:
        _say(
            "aurasync panel: el motor real está bloqueado hasta la decisión de seguir "
            "(i-7c8794-0d129c). Usa `aurasync panel --demo` para el motor simulado.",
            error=True,
        )
        return 2

    from aurasync.panel.auth import new_token  # noqa: PLC0415
    from aurasync.panel.pairing import lan_address, qr_terminal  # noqa: PLC0415

    try:
        config = panel_config(port=args.port, lan=args.lan, lan_address=lan_address, token=new_token())
    except PanelConfigError as error:
        _say(f"aurasync panel: {error}", error=True)
        return 2

    _say("aurasync panel · motor SIMULADO: nada de lo que muestra viene de un parlante real")
    _say(f"  en este equipo: {config.local_url}")
    if config.pairing_url:
        _say(f"  en la red local: {config.pairing_url}")
        _say(qr_terminal(config.pairing_url))
    _say("  el token cambia cada vez que se reinicia. Ctrl-C para salir.")
    if args.open:
        webbrowser.open(config.local_url)

    # La terminal muestra INFO y más; el panel recibe también DEBUG (docs/research/09 §7.2).
    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    logging.getLogger().addHandler(console)
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(_serve(config))
    return 0


async def _serve(config: PanelConfig) -> None:
    from aiohttp import web  # noqa: PLC0415

    from aurasync.engine.simulated import SimulatedEngine  # noqa: PLC0415
    from aurasync.logbuffer import LogBuffer  # noqa: PLC0415
    from aurasync.panel.server import create_app  # noqa: PLC0415

    logs = LogBuffer()
    logs.attach()  # antes de crear el motor, para que sus primeras líneas lleguen al panel
    app = create_app(
        SimulatedEngine(now=time.monotonic),
        token=config.token,
        allowed_hosts=config.allowed_hosts,
        pairing_url=config.pairing_url,
        logs=logs,
    )
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, config.bind, config.port)
    await site.start()
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()
