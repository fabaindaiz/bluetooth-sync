"""Punto de entrada de la línea de comandos.

Los subcomandos planificados (doctor, scan, tone, play, calibrate, assign) están
en docs/research/08 §6 y no existen todavía. Un subcomando desconocido termina con
error, en lugar de no hacer nada en silencio.
"""

import argparse

from aurasync import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aurasync",
        description="Un canal de audio distinto a cada parlante Bluetooth, sincronizados.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    parser.parse_args(argv)
    parser.print_help()
    return 0
