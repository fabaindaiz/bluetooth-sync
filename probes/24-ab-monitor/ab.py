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


# --- Listening of stage 1 of seamless transitions (experiment 23 §5) ---------------------------------
ESCUCHA_BACKUP = os.path.expanduser("~/.local/share/aurasync/escucha-respaldo.json")
# Per speaker (by position in the installation): pan, ambience, gain_db.
ESCUCHA_BASE = [(-0.8, 0.2, 0.0), (0.8, 0.2, 0.0), (-0.5, 0.8, -2.0), (0.5, 0.8, -2.0)]
ESCUCHA_RAMPAS_B = [(-0.2, 0.6, -4.0), (0.2, 0.6, -4.0), (-0.9, 0.3, 0.0), (0.9, 0.3, 0.0)]
ESCUCHA_PAIRS = {
    # Only ramps: rear delay 0, so no effective delay moves.
    "escucha-rampas": ((ESCUCHA_BASE, 0.0), (ESCUCHA_RAMPAS_B, 0.0)),
    # Only delays: the rear delay goes 0 -> 25 ms (speakers at ambience 0.8 move 20 ms, at 0.2 move 5 ms).
    "escucha-retardo": ((ESCUCHA_BASE, 0.0), (ESCUCHA_BASE, 25.0)),
}


def _speakers_and_rear(values: list, rear: float) -> None:
    names = [sp["name"] for sp in state()["speakers"]]
    for name, (pan, amb, gain) in zip(names, values, strict=False):
        call("PATCH", f"/speakers/{urllib.request.quote(name)}", {"pan": pan, "ambience": amb, "gain_db": gain})
    call("PATCH", "/global", {"rear_delay_ms": rear})


def _transition_values() -> dict:
    st = next(x for x in command("chain")["stages"] if x["id"] == "transition")
    return {"algorithm": st["value"]["algorithm"], "params": st["value"]["params"]}


def escucha_preparar() -> None:
    s = state()
    if not os.path.exists(ESCUCHA_BACKUP):
        backup = {
            "speakers": {sp["name"]: {k: sp[k] for k in ("pan", "ambience", "gain_db")} for sp in s["speakers"]},
            "rear_delay_ms": (s.get("global") or {}).get("rear_delay_ms"),
            "transition": _transition_values(),
        }
        json.dump(backup, open(ESCUCHA_BACKUP, "w"), indent=1)
        print(f"respaldo: {ESCUCHA_BACKUP}")
    for pair, ((a_vals, a_rear), (b_vals, b_rear)) in ESCUCHA_PAIRS.items():
        for label, vals, rear in (("a", a_vals, a_rear), ("b", b_vals, b_rear)):
            _speakers_and_rear(vals, rear)
            call("PUT", f"/presets/{pair}-{label}")
            print(f"preset {pair}-{label}")
    _speakers_and_rear(ESCUCHA_BASE, 0.0)
    print("listo: base cargada; transición:", _transition_values())


def modo(algorithm: str, fade_ms: float | None = None) -> None:
    params = {"fade_ms": fade_ms} if fade_ms is not None else None
    command("chain_set", stage="transition", algorithm=algorithm, **({"params": params} if params else {}))
    mark("transition", algorithm=algorithm, fade_ms=fade_ms)
    print("transición:", _transition_values())


def alternar(pair: str, times: int = 6, seconds: float = 5.0) -> None:
    """Load a, b, a, b… every `seconds`, announcing each, so the listener hears every change."""
    for k in range(times):
        label = "ab"[k % 2]
        mark("preset_load", preset=f"{pair}-{label}")
        call("POST", f"/presets/{pair}-{label}/load")
        time.sleep(seconds)


def escucha_restaurar() -> None:
    backup = json.load(open(ESCUCHA_BACKUP))
    for name, fields in backup["speakers"].items():
        call("PATCH", f"/speakers/{urllib.request.quote(name)}", fields)
    if backup.get("rear_delay_ms") is not None:
        call("PATCH", "/global", {"rear_delay_ms": backup["rear_delay_ms"]})
    t = backup["transition"]
    command("chain_set", stage="transition", algorithm=t["algorithm"], params=t["params"])
    for pair in ESCUCHA_PAIRS:
        for label in "ab":
            try:
                call("DELETE", f"/presets/{pair}-{label}")
            except SystemExit:
                pass
    os.remove(ESCUCHA_BACKUP)
    print("restaurado")


# --- Short preference tournament (not blind) ----------------------------------------------------------
def torneo_preparar() -> None:
    """Save the current settings and three render variants as presets torneo-*."""
    original = {x["id"]: x["value"]["algorithm"] for x in command("chain")["stages"]}
    json.dump({"spatial": original["spatial"], "eq": original["eq"]},
              open(os.path.expanduser("~/.local/share/aurasync/torneo-respaldo.json"), "w"))
    call("PUT", "/presets/torneo-actual")
    for render in ("direct", "classic", "spatial"):
        command("chain_set", stage="spatial", algorithm=render)
        call("PUT", f"/presets/torneo-{render}")
    command("chain_set", stage="spatial", algorithm=original["spatial"])
    print("presets: torneo-actual (render", original["spatial"] + "), torneo-direct, torneo-classic, torneo-spatial")


def ronda(a: str, b: str, seconds: float = 10.0, times: int = 2) -> None:
    """first (a), second (b), first, second… `seconds` each, announced in the log with the time."""
    for _ in range(times):
        for label, preset in (("primero", a), ("segundo", b)):
            mark("preset_load", preset=preset, label=label)
            call("POST", f"/presets/{preset}/load")
            time.sleep(seconds)


def uno(preset: str, seconds: float = 20.0) -> None:
    """One preset alone for `seconds`, for the listener to describe and score."""
    mark("preset_load", preset=preset, label="clasificar")
    call("POST", f"/presets/{preset}/load")
    time.sleep(seconds)
    mark("preset_end", preset=preset)


# --- A series of numbered presets, 5 s each, for quick classification -------------------------------
# Each recipe: (description, [(stage, algorithm or None, params or None), ...]) applied on torneo-actual.
SERIE = [
    ("lo de ahora: front", []),
    ("direct (estéreo puro)", [("spatial", "direct", None)]),
    ("front sin difusión", [("diffuse", "off", None)]),
    ("front con difusión fuerte", [("diffuse", None, {"level_db": -6.0, "rt60_s": 1.2})]),
    ("front con poco ambiente", [("ambience", None, {"mix": 0.3})]),
    ("front con todo el ambiente", [("ambience", None, {"mix": 1.0})]),
    ("front sin decorrelación", [("decorrelate", "off", None)]),
    ("front sin EQ", [("eq", "off", None)]),
    ("front con graves armónicos altos", [("bass", None, {"harmonics_db": 0.0})]),
    ("spatial", [("spatial", "spatial", None)]),
    ("spatial sin difusión", [("spatial", "spatial", None), ("diffuse", "off", None)]),
    ("front seco: sin difusión, sin decorrelación, poco ambiente",
     [("diffuse", "off", None), ("decorrelate", "off", None), ("ambience", None, {"mix": 0.4})]),
    ("direct sin EQ", [("spatial", "direct", None), ("eq", "off", None)]),
    ("classic cercano: sin difusión, poco ambiente",
     [("spatial", "classic", None), ("diffuse", "off", None), ("ambience", None, {"mix": 0.4})]),
]


def serie_preparar() -> None:
    for n, (desc, changes) in enumerate(SERIE, start=1):
        call("POST", "/presets/torneo-actual/load")
        for stage, algorithm, params in changes:
            fields = {}
            if algorithm:
                fields["algorithm"] = algorithm
            if params:
                fields["params"] = params
            command("chain_set", stage=stage, **fields)
        call("PUT", f"/presets/serie-{n:02d}")
        print(f"serie-{n:02d}: {desc}")
    call("POST", "/presets/torneo-actual/load")


def _mute_all(muted: bool) -> None:
    for sp in state()["speakers"]:
        call("PATCH", f"/speakers/{urllib.request.quote(sp['name'])}", {"muted": muted})


def serie(seconds: float = 5.0, first: int = 1, last: int | None = None, gap: float = 1.0) -> None:
    """Each preset after a clear silence: every speaker muted (50 ms fade) for `gap` seconds while it
    loads, so the listener always knows where a new number starts."""
    last = last or len(SERIE)
    for n in range(first, last + 1):
        _mute_all(True)
        time.sleep(gap / 2)
        mark("preset_load", preset=f"serie-{n:02d}", label=SERIE[n - 1][0])
        call("POST", f"/presets/serie-{n:02d}/load")
        time.sleep(gap / 2)
        _mute_all(False)
        print(f"[{time.strftime('%H:%M:%S')}] {n:2d}", flush=True)
        time.sleep(seconds)
    mark("serie_end")


# --- Elimination among the best: repeats and mixes, shuffled ----------------------------------------
MEZCLAS = {
    # name: (description, base preset, changes)
    "mezcla-7-difusion": ("7 + difusión fuerte", "serie-07", [("diffuse", None, {"level_db": -6.0, "rt60_s": 1.2})]),
    "mezcla-7-amb03": ("7 + ambiente 0,3", "serie-07", [("ambience", None, {"mix": 0.3})]),
    "mezcla-7-amb10": ("7 + ambiente 1,0", "serie-07", [("ambience", None, {"mix": 1.0})]),
    "mezcla-7-dif-amb10": ("7 + difusión fuerte + ambiente 1,0", "serie-07",
                           [("diffuse", None, {"level_db": -6.0, "rt60_s": 1.2}), ("ambience", None, {"mix": 1.0})]),
}


def mezclas_preparar() -> None:
    for name, (desc, base, changes) in MEZCLAS.items():
        call("POST", f"/presets/{base}/load")
        for stage, algorithm, params in changes:
            fields = {}
            if algorithm:
                fields["algorithm"] = algorithm
            if params:
                fields["params"] = params
            command("chain_set", stage=stage, **fields)
        call("PUT", f"/presets/{name}")
        print(f"{name}: {desc}")


def lista(presets: list[str], seconds: float = 10.0, gap: float = 1.0, first_playing: bool = True) -> None:
    """Numbered presets in the given order; the first one is what already plays (no silence before it)."""
    for n, preset in enumerate(presets, start=1):
        if n > 1 or not first_playing:
            _mute_all(True)
            time.sleep(gap / 2)
        mark("preset_load", preset=preset, label=f"{n}")
        call("POST", f"/presets/{preset}/load")
        if n > 1 or not first_playing:
            time.sleep(gap / 2)
            _mute_all(False)
        print(f"[{time.strftime('%H:%M:%S')}] {n:2d} {preset}", flush=True)
        time.sleep(seconds)
    mark("lista_end")


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
    elif cmd == "escucha-preparar":
        escucha_preparar()
    elif cmd == "escucha-restaurar":
        escucha_restaurar()
    elif cmd == "modo":
        modo(sys.argv[2], float(sys.argv[3]) if len(sys.argv) > 3 else None)
    elif cmd == "alternar":
        alternar(sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 6, float(sys.argv[4]) if len(sys.argv) > 4 else 5.0)
    elif cmd == "torneo-preparar":
        torneo_preparar()
    elif cmd == "serie-preparar":
        serie_preparar()
    elif cmd == "serie":
        serie(float(sys.argv[2]) if len(sys.argv) > 2 else 5.0)
    elif cmd == "mezclas-preparar":
        mezclas_preparar()
    elif cmd == "lista":
        lista(sys.argv[2].split(","))
    elif cmd == "uno":
        uno(sys.argv[2], float(sys.argv[3]) if len(sys.argv) > 3 else 20.0)
    elif cmd == "ronda":
        ronda(sys.argv[2], sys.argv[3])
    elif cmd == "cambios":
        cambios(int(sys.argv[2]) if len(sys.argv) > 2 else 6)
    else:
        raise SystemExit(f"unknown command {cmd}")
