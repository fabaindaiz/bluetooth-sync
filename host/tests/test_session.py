"""The parts of `session.py` that can be checked without PipeWire."""

import numpy as np
import pytest

from aurasync import session as session_module
from aurasync.config import Instalacion, Parlante
from aurasync.session import AudioSession, SessionOptions


class FakePlayer:
    def __init__(self, nodes):
        self.alive = list(nodes)
        self.wrong: dict = {}
        self.repairs = 0
        self.written = []

    @property
    def vivos(self):
        return list(self.alive)

    def escribir(self, blocks):
        self.written.append(blocks)

    def mal_ruteados(self):
        return dict(self.wrong)

    def soltar(self, node):
        self.alive.remove(node)
        self.wrong.pop(node, None)

    def reparar_ruteo(self):
        self.repairs += 1
        fixed, self.wrong = self.wrong, {}
        return fixed


class FakeInput:
    def leer(self, n):
        return np.zeros(n), np.zeros(n)


class FakeMotor:
    en_corte = False

    def procesar(self, izq, _der):
        return {"A": izq, "B": izq}


def _session(monkeypatch, existing=("sA", "sB", "aurasync")):
    clock = [100.0]
    monkeypatch.setattr(session_module.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(session_module.sonido, "_pw_dump", list)
    monkeypatch.setattr(session_module.sonido, "leer_nombres_de_nodo", lambda _: set(existing))
    inst = Instalacion(parlantes=[Parlante("A", "sA"), Parlante("B", "sB")])
    events = []
    s = AudioSession(inst, FakeMotor(), SessionOptions(block=64), lambda kind, **f: events.append((kind, f)))
    s._player = FakePlayer(["sA", "sB"])  # noqa: SLF001
    s._input = FakeInput()  # noqa: SLF001
    return s, clock, events


def _routing_round(s):
    """The check runs on a worker: one step asks, the next applies what it found."""
    s.step()
    if s._routing_future is not None:  # noqa: SLF001
        s._routing_future.result(timeout=5)  # noqa: SLF001
    s.step()
    s._routing_pool.submit(lambda: None).result(timeout=5)  # noqa: SLF001 - the repair, if any, is done


def test_a_stream_moved_while_playing_is_moved_back(monkeypatch):
    s, clock, events = _session(monkeypatch)
    s._player.wrong = {"sA": "aurasync"}  # noqa: SLF001
    s.step()
    assert s._player.repairs == 0, "not before the check is due"  # noqa: SLF001
    clock[0] += session_module.ROUTING_CHECK_S + 0.1
    _routing_round(s)
    assert s._player.repairs == 1  # noqa: SLF001
    assert s.routing_repairs == 1
    assert any(kind == "ruteo" and "aurasync" in f["motivo"] for kind, f in events)


def test_a_dead_stream_is_reported_and_the_others_keep_playing(monkeypatch):
    s, _, events = _session(monkeypatch)
    s._player.alive = ["sA"]  # noqa: SLF001
    s.step()
    assert s.lost == ["B"]
    assert any(kind == "parlante perdido" for kind, _ in events)


def test_every_stream_dead_ends_the_session(monkeypatch):
    s, _, _ = _session(monkeypatch)
    s._player.alive = []  # noqa: SLF001
    with pytest.raises(session_module.SessionError) as info:
        s.step()
    assert info.value.code == "unavailable"


def test_a_speaker_turned_off_has_its_stream_closed_instead_of_moved(monkeypatch):
    """Its orphan stream went elsewhere (here, the virtual sink): close it, never move it."""
    s, clock, events = _session(monkeypatch, existing=("sA", "aurasync"))
    s._player.wrong = {"sB": "aurasync"}  # noqa: SLF001
    clock[0] += session_module.ROUTING_CHECK_S + 0.1
    _routing_round(s)
    assert s._player.alive == ["sA"]  # noqa: SLF001
    assert s._player.repairs == 0  # noqa: SLF001
    assert any(kind == "parlante perdido" for kind, _ in events)
    s.step()
    assert s.lost == ["B"]


def test_every_speaker_turned_off_ends_the_session(monkeypatch):
    s, clock, _ = _session(monkeypatch, existing=("aurasync",))
    s._player.wrong = {"sA": "alsa_output.pc", "sB": "aurasync"}  # noqa: SLF001
    clock[0] += session_module.ROUTING_CHECK_S + 0.1
    with pytest.raises(session_module.SessionError):
        _routing_round(s)


def test_an_empty_pipe_and_a_late_engine_are_recorded_as_cuts(monkeypatch):
    s, clock, _ = _session(monkeypatch)
    for _ in range(4):
        s.step()
    s._player.nivel_ms = lambda: 0.0  # noqa: SLF001
    s.cuts.context["slow_order"] = "preset_load, 180 ms"
    clock[0] += 0.5  # half a second without a step
    s.step()
    kinds = {e["kind"]: e for e in s.cuts.summary()["events"]}
    assert "underrun" in kinds
    assert "late" in kinds
    assert kinds["underrun"]["context"]["slow_order"] == "preset_load, 180 ms"
    assert "orden lenta" in s.cuts.summary()["likely"]
