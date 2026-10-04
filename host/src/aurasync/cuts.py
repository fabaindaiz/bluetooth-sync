"""Every interruption of the sound, when it happened and the likeliest reason.

A cut is the kind of failure no other log shows: it is only heard (CLAUDE.md). This keeps
the evidence for each one, so the panel can say *when* and *why*, and so a cause can be
proven or ruled out by counting (experimentos/10 §9).

Kinds, from the engine's own view and the system's:

- `underrun`: the pipe to `pw-play` was empty when the engine came back to write. The
  speakers had nothing to play: a real gap.
- `low`: the pipe was nearly empty (under `LOW_MS`): the same thing, narrowly missed.
- `late`: the engine came back more than `LATE_MS` after one block's time. Says *why*
  the pipe emptied, with what the engine thread was doing (`context`).
- `xrun`: PipeWire counted an xrun on a stream to a speaker (`pw-top`).
- `input_gap`: an application was playing but no audio reached the engine for a block.
- `fade`: an intentional cut (a preset, a calibration being applied...). Listed so it is
  never mistaken for a fault.
- `lost`, `routing`: a stream died, or went to the wrong sink and was moved back.
- `radio`: PipeWire's Bluetooth sink threw a packet away because the link could not take
  it (`reduce bitpool` in the journal, `radio.py`). It happens *after* PipeWire, where the
  other kinds cannot see; `where` is the speaker, or the sink pointer when the journal did
  not say which speaker it was (`identified` is then false).

Each event carries the context the engine knew then: whether the loop was measuring,
whether Bluetooth was searching, the last order and how long it took.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from datetime import datetime
from typing import Any

LOW_MS = 20.0
LATE_MS = 40.0
CAPACITY = 300

CAUSES = {
    "underrun": "la salida se quedó sin audio",
    "low": "la salida casi se queda sin audio",
    "late": "el motor llegó tarde a entregar el bloque",
    "xrun": "PipeWire registró un corte en el stream",
    "input_gap": "no llegó audio de la aplicación",
    "fade": "corte intencional",
    "lost": "se perdió el stream de un parlante",
    "routing": "un stream fue a otro destino y se devolvió",
    "radio": "el enlace Bluetooth descartó un paquete",
}
FAULTS = {"underrun", "low", "late", "xrun", "input_gap", "lost", "routing", "radio"}
SUMMARY_S = 1.0
"""How often the summary is rebuilt apart while nothing new happens (its 1 and 10 min
windows age). A new event rebuilds it at once."""
COINCIDE_S = 1.0
"""A cut this close to a radio drop is counted as the radio's (the journal lags a little)."""


class CutLog:
    """The events, and a summary of them built on a thread of its own.

    The engine thread adds events and reads `latest()`, both O(1): the summary goes over up to
    `CAPACITY` events (~0.7 ms with a full log, MEDIDO on PC-Ryzen5, 2026-10-03), and the
    snapshot that carries it is built every block. Until then it was built there, so a log
    filling with cuts made every block slower.
    """

    def __init__(self, clock=time.monotonic) -> None:
        self.clock = clock
        self._events: deque[dict[str, Any]] = deque(maxlen=CAPACITY)
        self._lock = threading.Lock()
        self._seq = 0
        self._latest: dict[str, Any] | None = None
        self._changed = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.context: dict[str, Any] = {}
        """What the engine thread is doing; copied into each event."""

    def add(self, kind: str, where: str | None = None, detail: str = "", **extra: Any) -> None:
        event = {
            "seq": 0,
            "t": round(self.clock(), 3),
            "at": datetime.now().astimezone().isoformat(timespec="milliseconds"),
            "kind": kind,
            "fault": kind in FAULTS,
            "where": where,
            "what": CAUSES.get(kind, kind),
            "detail": detail,
            "context": {k: v for k, v in self.context.items() if v not in (None, False, "")},
            **extra,
        }
        with self._lock:
            self._seq += 1
            event["seq"] = self._seq
            self._events.append(event)
        self._changed.set()

    def since(self, seq: int) -> list[dict[str, Any]]:
        """The events after `seq`, oldest first. Costs what is new, not what is kept."""
        out = []
        with self._lock:
            for e in reversed(self._events):
                if e["seq"] <= seq:
                    break
                out.append(e)
        return out[::-1]

    def latest(self) -> dict[str, Any]:
        """The last summary built apart. Before the first one is ready: an empty one (the thread
        starts on the first call and builds it at once)."""
        if self._thread is None:
            self._latest = {
                "now": round(self.clock(), 3),
                "events": [],
                "faults_10min": 0,
                "faults_1min": 0,
                "by_kind": {},
                "likely": None,
            }
            self._changed.set()
            self._thread = threading.Thread(target=self._work, name="aurasync-cut-summary", daemon=True)
            self._thread.start()
        return self._latest  # type: ignore[return-value]

    def _work(self) -> None:
        while not self._stop.is_set():
            self._changed.wait(SUMMARY_S)
            self._changed.clear()
            if self._stop.is_set():
                return
            self._latest = self.summary()

    def close(self) -> None:
        """Stops the summary's thread. Safe to call twice."""
        self._stop.set()
        self._changed.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)

    def recent(self, seconds: float = 600.0) -> list[dict[str, Any]]:
        now = self.clock()
        with self._lock:
            return [e for e in self._events if now - e["t"] <= seconds]

    def summary(self) -> dict[str, Any]:
        events = self.recent()
        faults = [e for e in events if e["fault"]]
        last_minute = [e for e in faults if self.clock() - e["t"] <= 60.0]  # noqa: PLR2004
        by_kind: dict[str, int] = {}
        for e in faults:
            by_kind[e["kind"]] = by_kind.get(e["kind"], 0) + 1
        return {
            "now": round(self.clock(), 3),
            "events": events[-60:],
            "faults_10min": len(faults),
            "faults_1min": len(last_minute),
            "by_kind": by_kind,
            "likely": likely_cause(faults),
        }


def likely_cause(faults: list[dict[str, Any]]) -> str | None:
    """A one-line reading of the recent faults, for the panel. None when there are none."""
    if not faults:
        return None
    n = len(faults)
    measuring = sum(1 for e in faults if e["context"].get("loop_measuring"))
    scanning = sum(1 for e in faults if e["context"].get("bt_discovering"))
    slow_order = [e for e in faults if e["context"].get("slow_order")]
    xruns = [e for e in faults if e["kind"] == "xrun"]
    inputs = [e for e in faults if e["kind"] == "input_gap"]
    if scanning >= max(1, n // 2):
        return "Casi todos los cortes ocurrieron mientras Bluetooth buscaba dispositivos: buscar le quita radio a los parlantes."
    if reading := _radio_reading(faults):
        return reading
    if measuring >= max(1, n // 2):
        return "La mayoría ocurrió mientras el lazo medía: la medición le está quitando tiempo al motor."
    if slow_order:
        op = slow_order[-1]["context"]["slow_order"]
        return f"Hubo cortes justo después de una orden lenta ({op})."
    if inputs and len(inputs) >= n // 2:
        return "La aplicación dejó de mandar audio por momentos: el corte viene de la fuente, no de los parlantes."
    if xruns and len(xruns) == n:
        where = {e["where"] for e in xruns}
        if len(where) == 1:
            return f"Solo {next(iter(where))} corta, y el motor entregó a tiempo: apunta al enlace Bluetooth de ese parlante."
        return "PipeWire corta sin que el motor llegue tarde: apunta a los enlaces Bluetooth (distancia, obstáculos, batería)."
    return None


def _radio_reading(faults: list[dict[str, Any]]) -> str | None:
    """The radio's share: its drops, plus the other cuts within `COINCIDE_S` of one.

    Says "radio" only when that share is at least half of the faults.
    """
    radio = [e for e in faults if e["kind"] == "radio"]
    if not radio:
        return None
    times = [e["t"] for e in radio if "t" in e]
    coincident = [
        e for e in faults if e["kind"] != "radio" and "t" in e and any(abs(e["t"] - t) <= COINCIDE_S for t in times)
    ]
    if len(radio) + len(coincident) < max(1, (len(faults) + 1) // 2):
        return None
    counts: dict[str, int] = {}
    unidentified = 0
    for e in radio:
        if e.get("identified") is False:
            unidentified += 1
        else:
            counts[str(e["where"])] = counts.get(str(e["where"]), 0) + 1
    if len(counts) == 1 and not unidentified:
        where, k = next(iter(counts.items()))
        return (
            f"radio: el enlace Bluetooth de {where} descartó paquetes ({k} veces): "
            "distancia, obstáculos o batería de ese parlante."
        )
    if not counts:
        return (
            f"radio: un enlace Bluetooth sin identificar descartó paquetes ({unidentified} veces). "
            "Activá el registro de radio antes de que empiece a sonar para saber cuál."
        )
    parts = ", ".join(f"{w} {k}" for w, k in sorted(counts.items(), key=lambda kv: -kv[1]))
    if unidentified:
        parts += f", sin identificar {unidentified}"
    return (
        f"radio: los enlaces Bluetooth descartaron paquetes ({parts}): "
        "si son todos parejos apunta al controlador o a interferencia; si uno domina, a ese parlante."
    )
