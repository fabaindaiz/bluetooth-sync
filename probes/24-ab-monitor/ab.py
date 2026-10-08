"""Rehearsal of the A/B tests (engine and EQ) on the headphone monitor, HP-O16 (experiment 23).

Throwaway probe (d-7c8794-3208b7). Talks to the running service over its REST API with the token
in ~/.config/aurasync/service.json. Never touches PipeWire or Bluetooth itself.

    python ab.py preparar     # back up, put the test EQ curve on the virtual speakers, save the presets
    python ab.py estado       # session, engine, health, A/B score
    python ab.py motor rust   # engine_set (numpy | rust); waits until it reads
    python ab.py restaurar    # put back the backed-up curves and chain, delete the test presets
    python ab.py registrar    # (background) one line of state per second into DATA/registro.jsonl
    python ab.py barrido      # each preset and each engine: load, settle, record input and output
    python ab.py pesado       # every stage that is off by default on, each engine; then back
    python ab.py cambios N    # N engine switches at random moments, for the "did you hear it" count

The EQ curve is SYNTHETIC (the virtual speakers have no measured response): a lift of the lows and
of the highs, so that the EQ's knobs (on/off, budget, treble cap) are audible. It rehearses the
procedure; it says nothing about whether the EQ helps a real speaker (roadmap i-7c8794-d9c64a).
"""

from __future__ import annotations

import json
import os
import random
import subprocess
import sys
import time
import urllib.error
import urllib.request

CONFIG = os.path.expanduser("~/.config/aurasync/service.json")
BACKUP = os.path.expanduser("~/.local/share/aurasync/ab-ensayo-respaldo.json")
DATA = os.environ.get("AB_DATA", os.path.expanduser("~/.local/share/aurasync/ab-ensayo"))
LOG = os.path.join(DATA, "registro.jsonl")
INPUT_SINK = "aurasync"  # what the applications send (options.sink_name)
SETTLE_S = 3.0
CAPTURE_S = 20.0

THIRDS = [50, 62, 79, 99, 125, 157, 198, 250, 315, 397, 500, 630, 794, 1000, 1260, 1587, 2000,
          2520, 3175, 4000, 5040, 6350, 8000, 10079, 12699, 16000, 20159]


def test_curve() -> list[float]:
    """+6 dB at 50–125 Hz falling to 0 by 315 Hz; flat mids; +2 dB at 5 kHz rising to +5 dB above 8 kHz."""
    curve = []
    for f in THIRDS:
        if f <= 125:
            lift = 6.0
        elif f < 315:
            lift = 6.0 * (315 - f) / (315 - 125)
        elif f < 5000:
            lift = 0.0
        elif f < 8000:
            lift = 2.0 + 3.0 * (f - 5000) / 3000
        else:
            lift = 5.0
        curve.append(round(lift, 2))
    return curve


# The pairs, one variable each (the EQ campaign's variables that exist as knobs today; the
# Harman-type curve is not a knob yet). Every preset starts from the same base.
BASE = {"eq_active": True, "eq": {"max_boost_db": 6.0, "budget_db": 0.0, "treble_cap_db": 6.0}}
PAIRS = {
    "ensayo-eq": ({}, {"eq_active": False}),
    "ensayo-presupuesto": ({"eq": {"budget_db": 0.0}}, {"eq": {"budget_db": 3.0}}),
    "ensayo-agudos": ({"eq": {"treble_cap_db": 6.0}}, {"eq": {"treble_cap_db": 0.0}}),
}


def _api() -> tuple[str, dict]:
    cfg = json.load(open(CONFIG))
    return f"http://127.0.0.1:{cfg['port']}/v1", {"Authorization": f"Bearer {cfg['token']}",
                                                  "Content-Type": "application/json"}


def call(method: str, path: str, body: dict | None = None) -> dict:
    base, headers = _api()
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            reply = json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        raise SystemExit(f"{method} {path}: {e.code} {e.read().decode()[:300]}") from e
    if not reply.get("ok"):
        raise SystemExit(f"{method} {path}: {reply.get('error')}")
    return reply.get("result") or {}


def command(op: str, **fields) -> dict:
    return call("POST", "/command", {"v": 1, "op": op, **fields})


def eq_params() -> dict:
    stages = command("chain")["stages"]
    return next(s["value"]["params"] for s in stages if s["id"] == "eq")


def state() -> dict:
    return call("GET", "/state")


def _apply(variant: dict) -> None:
    merged_eq = dict(BASE["eq"], **variant.get("eq", {}))
    eq_active = variant.get("eq_active", BASE["eq_active"])
    call("PATCH", "/global", {"eq_active": eq_active})
    command("chain_set", stage="eq", params=merged_eq)


def preparar() -> None:
    s = state()
    speakers = [sp["name"] for sp in s.get("speakers", [])]
    if not speakers:
        raise SystemExit("no speakers in the state")
    if not os.path.exists(BACKUP):
        backup = {
            "eq_db": {sp["name"]: sp.get("eq_db") for sp in s["speakers"]},
            "eq_active": s.get("global", {}).get("eq_active"),
            "eq_params": eq_params(),
            "algorithms": {st["id"]: st["value"]["algorithm"] for st in command("chain")["stages"]},
            "engine": (s.get("engine") or {}).get("wanted"),
        }
        os.makedirs(os.path.dirname(BACKUP), exist_ok=True)
        json.dump(backup, open(BACKUP, "w"), indent=1)
        print(f"respaldo: {BACKUP}")
    curve = test_curve()
    for name in speakers:
        call("PATCH", f"/speakers/{urllib.request.quote(name)}", {"eq_db": curve})
    print(f"curva de prueba en {len(speakers)} parlantes: {curve}")
    for pair, (a, b) in PAIRS.items():
        for label, variant in (("a", a), ("b", b)):
            _apply(variant)
            call("PUT", f"/presets/{pair}-{label}")
            print(f"preset {pair}-{label}: {variant or 'base'}")
    _apply({})
    print("listo: cargada la base (EQ encendido, sin presupuesto, sin tope de agudos)")


def restaurar() -> None:
    if not os.path.exists(BACKUP):
        raise SystemExit("no hay respaldo")
    backup = json.load(open(BACKUP))
    for name, curve in backup["eq_db"].items():
        call("PATCH", f"/speakers/{urllib.request.quote(name)}", {"eq_db": curve})
    if backup.get("eq_active") is not None:
        call("PATCH", "/global", {"eq_active": backup["eq_active"]})
    for stage, algorithm in (backup.get("algorithms") or {}).items():
        if stage in HEAVY:
            command("chain_set", stage=stage, algorithm=algorithm)
    if backup.get("eq_params"):
        command("chain_set", stage="eq", params=backup["eq_params"])
    for pair in PAIRS:
        for label in ("a", "b"):
            try:
                call("DELETE", f"/presets/{pair}-{label}")
            except SystemExit:
                pass
    if backup.get("engine"):
        command("engine_set", engine=backup["engine"])
    os.remove(BACKUP)
    print("restaurado y respaldo borrado")


def mark(event: str, **fields) -> None:
    os.makedirs(DATA, exist_ok=True)
    with open(LOG, "a") as f:
        f.write(json.dumps({"t": time.time(), "mark": event, **fields}) + "\n")
    print(f"[{time.strftime('%H:%M:%S')}] {event} {fields or ''}")


def motor(engine: str) -> None:
    mark("engine_set", engine=engine)
    command("engine_set", engine=engine)
    for _ in range(40):
        e = state().get("engine") or {}
        if e.get("active") == engine:
            print(f"motor activo: {engine}")
            return
        time.sleep(0.25)
    print(f"el motor todavía no lee {engine}: {state().get('engine')}")


def estado() -> None:
    s = state()
    keys = ("session", "engine", "ab", "monitor")
    out = {k: s.get(k) for k in keys}
    health = s.get("health") or {}
    out["health"] = {k: health.get(k) for k in ("motor_ms", "realtime_x", "cuts", "xruns", "output_cushion")}
    print(json.dumps(out, indent=1, ensure_ascii=False)[:3000])


def registrar() -> None:
    """One compact line of state per second, until killed."""
    os.makedirs(DATA, exist_ok=True)
    while True:
        try:
            s = state()
        except SystemExit as e:
            row = {"t": time.time(), "error": str(e)}
        else:
            health = s.get("health") or {}
            monitor = s.get("monitor") or {}
            row = {
                "t": time.time(),
                "session": (s.get("session") or {}).get("status"),
                "engine": s.get("engine"),
                "preset": s.get("preset"),
                "chain": s.get("chain_summary"),
                "health": {k: health.get(k) for k in ("motor_ms", "budget_ms", "realtime_x", "blocks", "xruns",
                                                      "cuts", "output_cushion", "pipe_level_ms", "input_active")},
                "monitor": {k: monitor.get(k) for k in ("state", "reached", "drops", "cushion_ms", "level_ms",
                                                        "refills", "trims", "makeup_db", "loudness_reference",
                                                        "loudness_monitor", "match", "match_reason")},
                "ab": s.get("ab"),
                "latency": s.get("latency"),
                "quality": s.get("quality"),
                "volume_db": (s.get("global") or {}).get("volume_db"),
            }
        with open(LOG, "a") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        time.sleep(1.0)


def _links(node: str) -> list[str]:
    """The ports feeding a capture node, as pw-link sees them."""
    out = subprocess.run(["pw-link", "-l"], capture_output=True, text=True).stdout.splitlines()
    linked, inside = [], False
    for line in out:
        if not line.startswith(" "):
            inside = line.startswith(f"{node}:")
        elif inside and "|<-" in line:
            linked.append(line.split("|<-")[1].strip())
    return linked


def capturar(label: str, seconds: float = CAPTURE_S) -> None:
    """Record the service's input (the aurasync sink) and the monitor's output (the headphones'
    sink) at the same time, and check that each recorder is linked where it was asked."""
    target = (state().get("monitor") or {}).get("target")
    os.makedirs(os.path.join(DATA, "wav"), exist_ok=True)
    procs = {}
    for side, sink in (("in", INPUT_SINK), ("out", target)):
        node = f"ab23-{side}"
        path = os.path.join(DATA, "wav", f"{label}-{side}.wav")
        procs[side] = subprocess.Popen(
            ["pw-record", "--target", sink, "-P", f"{{ stream.capture.sink = true node.name = {node} }}",
             "--format", "f32", "--rate", "48000", "--channels", "2", path],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    time.sleep(1.0)
    wiring = {side: _links(f"ab23-{side}") for side in procs}
    mark("capture", label=label, seconds=seconds, wiring=wiring, out_target=target)
    ok_in = any(p.startswith(f"{INPUT_SINK}:monitor") for p in wiring["in"])
    ok_out = any(p.startswith(f"{target}:monitor") for p in wiring["out"])
    if not (ok_in and ok_out):
        print(f"  ATENCIÓN: cableado inesperado {wiring}")
    time.sleep(seconds)
    for p in procs.values():
        p.terminate()
    for p in procs.values():
        p.wait(5)
    mark("capture_end", label=label, ok=ok_in and ok_out)


def _load(preset: str) -> None:
    mark("preset_load", preset=preset)
    call("POST", f"/presets/{preset}/load")
    time.sleep(SETTLE_S)


def barrido() -> None:
    """Every preset once, the base again at the end (the repetition that says what is noise),
    and the base under each engine."""
    for pair in PAIRS:
        for label in ("a", "b"):
            _load(f"{pair}-{label}")
            capturar(f"{pair}-{label}")
    _load("ensayo-eq-a")
    capturar("ensayo-eq-a-repeticion")
    for engine in ("rust", "numpy"):
        motor(engine)
        time.sleep(SETTLE_S)
        capturar(f"motor-{engine}")


HEAVY = {"bass": "crossover", "diffuse": "noise_tail", "limiter": "true_peak"}


def pesado(seconds: float = 30.0) -> None:
    """The stages that are off by default switched on, under each engine: the engine's cost when
    it has the most to do. Back to the base preset afterwards."""
    _load("ensayo-eq-a")
    stages = {s["id"]: s for s in command("chain")["stages"]}
    for stage, algorithm in HEAVY.items():
        algo = next(a for a in stages[stage]["algorithms"] if a["id"] == algorithm)
        if not algo["available"]:
            print(f"  {stage}={algorithm} no disponible: {algo['unavailable_reason']}")
            continue
        command("chain_set", stage=stage, algorithm=algorithm)
    mark("heavy_on", chain=state().get("chain_summary"))
    for engine in ("numpy", "rust", "numpy"):
        motor(engine)
        mark("heavy_run", engine=engine)
        time.sleep(seconds)
    _load("ensayo-eq-a")
    mark("heavy_off", chain=state().get("chain_summary"))


def cambios(count: int = 6) -> None:
    """Engine switches at random moments 8–20 s apart; the listener counts what they hear."""
    mark("switches_start", count=count)
    current = (state().get("engine") or {}).get("active") or "numpy"
    for _ in range(count):
        time.sleep(random.uniform(8.0, 20.0))
        current = "rust" if current == "numpy" else "numpy"
        motor(current)
    time.sleep(5.0)
    mark("switches_end")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "estado"
    simple = {"preparar": preparar, "restaurar": restaurar, "estado": estado, "registrar": registrar,
              "barrido": barrido, "pesado": pesado}
    if cmd in simple:
        simple[cmd]()
    elif cmd == "motor":
        motor(sys.argv[2])
    elif cmd == "capturar":
        capturar(sys.argv[2], float(sys.argv[3]) if len(sys.argv) > 3 else CAPTURE_S)
    elif cmd == "cambios":
        cambios(int(sys.argv[2]) if len(sys.argv) > 2 else 6)
    else:
        raise SystemExit(f"unknown command {cmd}")
