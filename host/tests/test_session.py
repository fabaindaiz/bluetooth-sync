"""The parts of `session.py` that can be checked without PipeWire."""

import numpy as np
import pytest

from aurasync import session as session_module
from aurasync.chain import ChainValues, validate_set
from aurasync.config import Instalacion, Parlante
from aurasync.motor import Motor
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


def _session(monkeypatch, existing=("sA", "sB", "aurasync"), motor=None, inst=None, block=64):
    clock = [100.0]
    monkeypatch.setattr(session_module.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(session_module.sonido, "_pw_dump", list)
    monkeypatch.setattr(session_module.sonido, "leer_nombres_de_nodo", lambda _: set(existing))
    inst = inst or Instalacion(parlantes=[Parlante("A", "sA"), Parlante("B", "sB")])
    events = []
    s = AudioSession(
        inst, motor or FakeMotor(), SessionOptions(block=block), lambda kind, **f: events.append((kind, f))
    )
    s.outputs.attach(FakePlayer(["sA", "sB"]), {"A", "B"})
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
    s.outputs._player.wrong = {"sA": "aurasync"}  # noqa: SLF001
    s.step()
    assert s.outputs._player.repairs == 0, "not before the check is due"  # noqa: SLF001
    clock[0] += session_module.ROUTING_CHECK_S + 0.1
    _routing_round(s)
    assert s.outputs._player.repairs == 1  # noqa: SLF001
    assert s.routing_repairs == 1
    assert any(kind == "ruteo" and "aurasync" in f["motivo"] for kind, f in events)


def test_a_dead_stream_is_reported_and_the_others_keep_playing(monkeypatch):
    s, _, events = _session(monkeypatch)
    s.outputs._player.alive = ["sA"]  # noqa: SLF001
    s.step()
    assert s.lost == ["B"]
    assert any(kind == "parlante perdido" for kind, _ in events)


def test_every_stream_dead_does_not_end_the_session(monkeypatch):
    """Until 2026-10-05 it raised "every speaker disconnected" (d-7c8794-05bdd6): now they are lost
    and the session goes on, heard on the monitor."""
    s, _, events = _session(monkeypatch)
    s.outputs._player.alive = []  # noqa: SLF001
    s.step()
    s.step()
    assert s.lost == ["A", "B"]
    assert s.output_states() == {"A": "lost", "B": "lost"}
    assert any(kind == "parlante perdido" for kind, _ in events)


def test_a_speaker_turned_off_has_its_stream_closed_instead_of_moved(monkeypatch):
    """Its orphan stream went elsewhere (here, the virtual sink): close it, never move it."""
    s, clock, events = _session(monkeypatch, existing=("sA", "aurasync"))
    s.outputs._player.wrong = {"sB": "aurasync"}  # noqa: SLF001
    clock[0] += session_module.ROUTING_CHECK_S + 0.1
    _routing_round(s)
    assert s.outputs._player.alive == ["sA"]  # noqa: SLF001
    assert s.outputs._player.repairs == 0  # noqa: SLF001
    assert any(kind == "parlante perdido" for kind, _ in events)
    s.step()
    assert s.lost == ["B"]


def test_every_speaker_turned_off_does_not_end_the_session(monkeypatch):
    s, clock, _ = _session(monkeypatch, existing=("aurasync",))
    s.outputs._player.wrong = {"sA": "alsa_output.pc", "sB": "aurasync"}  # noqa: SLF001
    clock[0] += session_module.ROUTING_CHECK_S + 0.1
    _routing_round(s)
    assert s.outputs._player.alive == []  # noqa: SLF001 - both orphan streams closed, none moved
    assert s.outputs._player.repairs == 0  # noqa: SLF001
    s.step()
    assert s.lost == ["A", "B"]


def test_an_empty_pipe_and_a_late_engine_are_recorded_as_cuts(monkeypatch):
    s, clock, _ = _session(monkeypatch)
    for _ in range(4):
        s.step()
    s.outputs._player.nivel_ms = lambda: 0.0  # noqa: SLF001
    s.cuts.context["slow_order"] = "preset_load, 180 ms"
    clock[0] += 0.5  # half a second without a step
    s.step()
    kinds = {e["kind"]: e for e in s.cuts.summary()["events"]}
    assert "underrun" in kinds
    assert "late" in kinds
    assert kinds["underrun"]["context"]["slow_order"] == "preset_load, 180 ms"
    assert "orden lenta" in s.cuts.summary()["likely"]


# -- intentional cuts against crossfades (spec seamless-transitions 2026-10-08) -------------------


def _real_motor_session(monkeypatch, mode, **params):
    inst = Instalacion(parlantes=[Parlante("A", "sA", pan=-0.7), Parlante("B", "sB", pan=0.7)])
    chain = ChainValues().with_algorithm("transition", mode)
    if params:
        chain = chain.with_change(validate_set("transition", params=params))
    motor = Motor(inst, 48000, ecualizar=False, chain=chain, semilla=1, bloque=4096)
    s, clock, _ = _session(monkeypatch, motor=motor, inst=inst, block=4096)
    for _ in range(2):
        s.step()
    return s, clock, motor


def _fades(s):
    return [e for e in s.cuts.summary()["events"] if e["kind"] == "fade"]


def _step_through(s, clock, motor):
    for _ in range(12):
        clock[0] += 4096 / 48000
        s.step()
        if not motor.en_corte:
            return
    pytest.fail("the motor never got quiet")


def test_a_crossfade_is_not_logged_as_a_cut(monkeypatch):
    """The cut log is the evidence of the listening (experiment 23): a crossfade has no hole, so it
    adds no `fade` event, while a cut adds one. 500 ms: the crossfade spans several steps."""
    s, clock, motor = _real_motor_session(monkeypatch, "crossfade", fade_ms=500.0)
    a = s.installation.parlantes[0]
    motor.cambiar(lambda: setattr(a, "pan", 0.4))
    s.step()
    assert motor.en_corte
    _step_through(s, clock, motor)
    assert _fades(s) == []
    motor.cortar(lambda: setattr(a, "pan", -0.4))
    _step_through(s, clock, motor)
    assert len(_fades(s)) == 1


def test_a_cut_in_cut_mode_is_logged_once(monkeypatch):
    s, clock, motor = _real_motor_session(monkeypatch, "cut")
    motor.cambiar(lambda: setattr(s.installation.parlantes[0], "pan", 0.4))
    _step_through(s, clock, motor)
    assert len(_fades(s)) == 1


def test_the_render_match_freezes_on_a_crossfade_inside_one_block(monkeypatch):
    """The render match freezes on whatever moves the speakers' gains. With the default 80 ms the
    whole crossfade fits in one 4096-sample block, so `en_corte` is already down when the match
    looks after the block: it is told from the look before the block, as the loop is."""
    s, clock, motor = _real_motor_session(monkeypatch, "crossfade")
    holds = []

    class _Spy:
        def after_block(self, _motor, _meter, _n, *, hold=False, **_kw):
            holds.append(hold)

    s.render_match = _Spy()
    motor.cambiar(lambda: setattr(s.installation.parlantes[0], "pan", 0.4))
    clock[0] += 4096 / 48000
    s.step()
    assert not motor.en_corte  # the whole crossfade was inside this block
    assert holds == [True]
    clock[0] += 4096 / 48000
    s.step()
    assert holds == [True, False]


class _Done:
    """A measurement of the loop that finished, with a result, and nothing launched after it."""

    ocupado = True

    def recoger(self):
        return True, object()


def test_a_loop_measurement_overlapping_a_crossfade_is_still_discarded(monkeypatch):
    """With the default 80 ms the whole crossfade fits in one 4096-sample block: the motor is quiet
    again when the step looks after `procesar`, so the step also looks before it."""
    s, clock, motor = _real_motor_session(monkeypatch, "crossfade")
    s._launched_at = clock[0]  # noqa: SLF001
    s._launch = {"window": 3.0}  # noqa: SLF001
    motor.cambiar(lambda: setattr(s.installation.parlantes[0], "pan", 0.4))
    _step_through(s, clock, motor)
    assert _fades(s) == []
    # The measurement comes back after the crossfade: it was measuring a moving output.
    s.loop = type("Loop", (), {"advance": lambda _self: None})()
    s._mic = type("Mic", (), {"bombear": lambda _self: None, "ultimos": lambda _self, _s: None})()  # noqa: SLF001
    s._measured = set()  # noqa: SLF001
    s._emission = s._probe_emission = type("E", (), {"agregar": lambda _self, _b: None})()  # noqa: SLF001
    s._measurer = _Done()  # noqa: SLF001
    s._recalibration_step({})  # noqa: SLF001
    assert s.last_recalibration["kind"] == "descartado"
    assert "corte" in s.last_recalibration["reason"]
