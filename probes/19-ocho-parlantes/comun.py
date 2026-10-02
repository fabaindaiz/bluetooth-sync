"""Shared pieces of probe 19 (eight speakers): installations for N speakers, the ring
layout, and the patch that lets the engine take more than `MAXIMO_FIJOS` speakers.

SIMULATED. Nothing here touches `host/`: the limit is lifted only inside this process.
"""

from __future__ import annotations

import contextlib
import json
import platform
import sys
from pathlib import Path

import numpy as np

from aurasync.config import Instalacion, Parlante
from aurasync.dsp import decorrelate

SR = 48000
ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "docs/research/experimentos/datos/16"


def ring_angles(n: int) -> np.ndarray:
    """Evenly spaced azimuths (degrees, 0 = front, positive = right), none straight ahead.

    N = 4 gives -135, -45, 45, 135 (RL, FL, FR, RR); N = 8 the octagon at ±22.5, ±67.5, ...
    """
    return -180.0 + (np.arange(n) + 0.5) * 360.0 / n


def role_from_angle(theta_deg: float) -> tuple[float, float]:
    """(pan, ambience) for a speaker at `theta_deg` on the ring.

    Chosen to reproduce today's roles (`control.ROLES`): FL at -45 degrees gives (-0.7, 0.15),
    RL at -135 gives (-0.7, 0.55), FC (0, 0.1), RC (0, 0.6 vs 0.55 today).
    """
    t = np.radians(theta_deg)
    pan = float(np.clip(np.sin(t), -1, 1))
    ambience = float(np.clip(0.35 - 0.2 * np.cos(t) / np.cos(np.pi / 4), 0.1, 0.6))
    return round(pan, 2), round(ambience, 2)


def installation(n: int, *, charge_first: bool = False, eq_db: float | None = None) -> Instalacion:
    speakers = []
    for k, theta in enumerate(ring_angles(n)):
        pan, amb = role_from_angle(theta)
        name = "JBL Charge 6" if (charge_first and k == 0) else f"JBL Go 4 #{k}"
        speakers.append(
            Parlante(name, f"s{k}", pan=pan, ambiente=amb, ecualizacion_db=None if eq_db is None else [eq_db] * 27)
        )
    return Instalacion(parlantes=speakers)


@contextlib.contextmanager
def lifted_limit(n: int = 8):
    """Lets `Motor` build a decorrelator bank of up to `n` filters (the product stops at 6)."""
    old = decorrelate.MAXIMO_FIJOS
    decorrelate.MAXIMO_FIJOS = n
    try:
        yield
    finally:
        decorrelate.MAXIMO_FIJOS = old


def environment() -> dict:
    import os  # noqa: PLC0415

    load = os.getloadavg() if hasattr(os, "getloadavg") else None
    return {
        "machine": platform.node(),
        "arch": platform.machine(),
        "python": sys.version.split()[0],
        "numpy": np.__version__,
        "load_avg": [round(x, 1) for x in load] if load else None,
    }


def save(name: str, payload: dict) -> Path:
    DATA.mkdir(parents=True, exist_ok=True)
    dest = DATA / name
    dest.write_text(json.dumps({"environment": environment(), **payload}, indent=1, ensure_ascii=False) + "\n")
    return dest
