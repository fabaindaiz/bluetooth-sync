"""Writes the PWA's demo (web/src/demo/fixture.json): what a simulated service answers to the read
operations, with a demo installation, so the panel can be tried with no device at all.

From host/:  hatch run python scripts/demo_fixture.py

Nothing in it may identify a real device or machine (the site is public): the installation is made
up, every path and the machine's name are replaced, and `tests/test_demo_fixture.py` checks it with the
same patterns as `web/pwa/privacy.ts`.
"""

from __future__ import annotations

import json
import re
import tempfile
import threading
import time
from pathlib import Path

from aurasync.config import Instalacion, Parlante
from aurasync.logbuffer import LogBuffer
from aurasync.service import Service
from aurasync.session import SessionOptions
from aurasync.simulated import SimulatedObserver, SimulatedSession

OUT = Path(__file__).resolve().parents[1] / "web" / "src" / "demo" / "fixture.json"
NAME = "Equipo de demostración"
SPEAKERS = (("JBL Go 4 Red", -0.7, 0.15), ("JBL Go 4 Black", 0.7, 0.15), ("JBL Go 4 Blue", 0.0, 0.55))
READS = ("state", "chain", "presets", "sync_state")
SLOW_READS = ("sync_explain", "spatial_explain")
"""Built off the engine thread: asked until they are ready."""


MAC = re.compile(r"\b[0-9A-F]{2}([:_-])[0-9A-F]{2}(?:\1[0-9A-F]{2}){4}\b", re.IGNORECASE)
_names: dict[str, str] = {}


def _no_mac(match: re.Match) -> str:
    """Each address-shaped text (the simulated nearby speaker has one) becomes `demo-N`, the same N
    for the same address: the public site refuses anything that looks like a MAC (pwa/privacy.ts)."""
    key = match.group(0).upper().replace("_", ":").replace("-", ":")
    return _names.setdefault(key, f"demo-{len(_names) + 1}")


def _clean(value, machine: str):
    """Paths, addresses and the machine's name out: the fixture goes to a public site."""
    if isinstance(value, dict):
        return {k: _clean(v, machine) for k, v in value.items()}
    if isinstance(value, list):
        return [_clean(v, machine) for v in value]
    if isinstance(value, str):
        if re.search(r"(^|/)(home|tmp|Users)/", value):
            return "(una ruta del equipo)"
        value = MAC.sub(_no_mac, value).replace("bluez_output.", "")
        return value.replace(machine, NAME) if machine else value
    return value


def capture() -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        inst = Instalacion(
            parlantes=[Parlante(n, f"demo{i + 1}.sink", pan=p, ambiente=a) for i, (n, p, a) in enumerate(SPEAKERS)]
        )
        inst.guardar(root / "i.json")
        svc = Service(
            root / "i.json",
            root / "p.json",
            options=SessionOptions(microphone="simulado", block=1024),
            session_factory=SimulatedSession,
            observer=SimulatedObserver(inst),
            simulated=True,
            log=lambda _: None,
            logs=LogBuffer(),
        )
        thread = threading.Thread(target=svc.run, daemon=True)
        thread.start()

        def call(op: str, **args) -> dict:
            reply = svc.handle({"v": 1, "op": op, **args})
            if not reply.get("ok"):
                msg = f"{op}: {reply}"
                raise RuntimeError(msg)
            return reply["result"]

        try:
            call("start")
            call("source", kind="tone")
            for name in ("Película", "Fiesta"):
                call("preset_save", name=name)
            time.sleep(4.0)  # the meters, the input analysis and the cuts fill in
            out = {op: call(op) for op in READS}
            for op in SLOW_READS:
                deadline = time.monotonic() + 60
                while (result := call(op)).get("pending") and time.monotonic() < deadline:
                    time.sleep(0.5)
                out[op] = result
            out["logs"] = call("logs", since=0, limit=60)
        finally:
            svc.handle({"v": 1, "op": "shutdown"})
            thread.join(timeout=5)
            svc.close()
    machine = str(out["state"].get("service", {}).get("name", "") or "")
    return _clean(out, machine)


def main() -> None:
    data = capture()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n")
    print(f"wrote {OUT} ({OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
