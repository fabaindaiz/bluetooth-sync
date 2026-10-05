"""Changing which real speakers play in a running session (spec 2026-10-05-virtual-speakers-and-hot-join
§5): a new real part prepared off the engine thread, swapped at the bottom of a `motor.cortar` fade.

Without PipeWire: the players, the virtual sink and the microphone are fakes. The fake motor's
`cortar(action)` keeps the action and runs it on the next `procesar`, like the real one."""

from __future__ import annotations

import itertools
import threading
import time
from types import SimpleNamespace

import numpy as np
import pytest

from aurasync import cushion as cushion_module
from aurasync import motor as motor_module
from aurasync import session as session_module
from aurasync import snapshot, sonido
from aurasync.config import Instalacion, Parlante
from aurasync.session import AudioSession, SessionError, SessionOptions
from aurasync.simulated import SimulatedSession

BLOCK = 64
WAIT_S = 5.0


class FakePlayer:
    """The real part: what was written to it, where it was closed from, and its routing."""

    def __init__(self, nodes, *_args, nombre=None, **_kwargs) -> None:
        self.nodes = list(nodes)
        self.name = nombre
        self.alive = list(nodes)
        self.written: list[dict] = []
        self.wrong: dict = {}
        self.repairs = 0
        self.closed = False
        self.closed_on: str | None = None
        self.entered = threading.Event()
        self.hold: threading.Event | None = None
        """Set by a test: the routing check waits for it (a check still running)."""
        self.close_hold: threading.Event | None = None
        """Set by a test: closing waits for it (a player that takes long to play out)."""

    def __enter__(self):
        self.entered.set()
        return self

    def __exit__(self, *_) -> None:
        self.cerrar()

    @property
    def vivos(self):
        return [] if self.closed else list(self.alive)

    @property
    def pids(self):
        return {n: 100 + i for i, n in enumerate(self.vivos)}

    def escribir(self, blocks) -> None:
        if self.closed:
            msg = "write after close"
            raise RuntimeError(msg)
        unknown = set(blocks) - set(self.nodes)
        if unknown:
            raise KeyError(sorted(unknown))
        self.written.append({k: np.array(v, copy=True) for k, v in blocks.items()})

    def mal_ruteados(self):
        if self.hold is not None:
            self.hold.wait(WAIT_S)
        return dict(self.wrong)

    def reparar_ruteo(self):
        self.repairs += 1
        return {}

    def soltar(self, node) -> None:
        if node in self.alive:
            self.alive.remove(node)

    def cerrar(self) -> None:
        if self.close_hold is not None:
            self.close_hold.wait(WAIT_S)
        if not self.closed:
            self.closed = True
            self.closed_on = threading.current_thread().name


class FakeSink:
    def __init__(self, *_args, **_kwargs) -> None:
        self.pid = 7

    def __enter__(self):
        return self

    def __exit__(self, *_) -> None:
        self.cerrar()

    def cerrar(self) -> None:
        self.pid = None

    def leer(self, n, espera_s=0.05):  # noqa: ARG002
        return np.zeros(n), np.zeros(n)


class FakeMotor:
    """`cortar(action)` keeps the action; the next `procesar` runs it (the bottom of the fade)."""

    volumen_db = 0.0

    def __init__(self, names) -> None:
        self.names = list(names)
        self.pending: list = []
        self.cuts = 0
        self.ran = 0

    @property
    def en_corte(self) -> bool:
        return bool(self.pending)

    def cortar(self, action=None) -> None:
        self.cuts += 1
        self.pending.append(action)

    def procesar(self, izq, _der, canales=None):  # noqa: ARG002
        actions, self.pending = self.pending, []
        for action in actions:
            if action is not None:
                action()
                self.ran += 1
        # Never silence: a block of music is told apart from the silence the worker feeds.
        return {n: izq + 0.01 * (i + 1) for i, n in enumerate(self.names)}


class FakeMic:
    pid = 9

    def __enter__(self):
        return self

    def __exit__(self, *_) -> None:
        self.cerrar()

    def cerrar(self) -> None:
        self.pid = None

    def bombear(self, espera_s=0.0):  # noqa: ARG002
        return 0

    def ultimos(self, _seconds):
        return None


class FakeMonitor:
    def __init__(self) -> None:
        self.pushed: list[dict] = []

    def push(self, _pair, blocks) -> None:
        self.pushed.append(dict(blocks))

    def close(self) -> None:
        pass


class Done:
    """The `done` callback: what it got, and on which thread."""

    def __init__(self) -> None:
        self.calls: list[str | None] = []
        self.event = threading.Event()

    def __call__(self, error: str | None) -> None:
        self.calls.append(error)
        self.event.set()

    def wait(self) -> list[str | None]:
        assert self.event.wait(WAIT_S), "done was never called"
        return self.calls


def open_session(monkeypatch, sinks, present, factory=None, block=BLOCK, **options):
    players: list[FakePlayer] = []

    def player(nodes, *args, **kwargs):
        players.append((factory or FakePlayer)(nodes, *args, **kwargs))
        return players[-1]

    monkeypatch.setattr(
        sonido, "salidas_bluetooth", lambda: [sonido.SalidaBluetooth(n, n, "sbc") for n in sorted(present)]
    )
    monkeypatch.setattr(sonido, "nodo_existe", lambda _name: False)
    monkeypatch.setattr(sonido, "Reproductor", player)
    monkeypatch.setattr(sonido, "ReproductorCombinado", player)
    monkeypatch.setattr(sonido, "SinkVirtual", FakeSink)
    monkeypatch.setattr(session_module.time, "sleep", lambda _s: None)
    monkeypatch.setattr(sonido, "_pw_dump", list)
    monkeypatch.setattr(sonido, "leer_nombres_de_nodo", lambda _: {*sinks.values(), "aurasync"} - {None})
    monkeypatch.setattr(AudioSession, "_microphone", lambda _self, _name, _seconds: FakeMic())
    monkeypatch.setattr(AudioSession, "SWAP_SETTLE_S", 0.0)
    monkeypatch.setattr(AudioSession, "SWAP_CHECK_S", 0.0)
    inst = Instalacion(parlantes=[Parlante(n, sink) for n, sink in sinks.items()])
    events: list[tuple[str, dict]] = []
    motor = FakeMotor(list(sinks))
    s = AudioSession(inst, motor, SessionOptions(block=block, **options), lambda kind, **f: events.append((kind, f)))
    s.open()
    return s, motor, players, events


def wait_ready(s) -> None:
    change = s._change  # noqa: SLF001
    assert change is not None
    assert change.ready.wait(WAIT_S), "the new player never got ready"


def swap(s) -> None:
    """Prepared → the cut is asked for (one step) → its action runs at the bottom (the next)."""
    wait_ready(s)
    s.step()
    s.step()


def is_silence(block: dict) -> bool:
    return all(not np.any(x) for x in block.values())


THREE = {"A": "sA", "B": "sB", "C": "sC"}


def test_join_swaps_at_the_bottom_of_the_cut(monkeypatch):
    s, motor, players, events = open_session(monkeypatch, THREE, {"sA", "sB"})
    old = players[0]
    assert s.output_states() == {"A": "playing", "B": "playing", "C": "absent"}
    attached_after: list[int] = []
    attach = s.outputs.attach
    monkeypatch.setattr(s.outputs, "attach", lambda *a: (attached_after.append(motor.ran), attach(*a))[1])
    done = Done()

    s.request_output({"A", "B", "C"}, done)
    wait_ready(s)
    new = players[1]
    assert new.nodes == ["sA", "sB", "sC"]
    assert new.name == "aurasync_salida_b", "the alternate name: the old combine sink still exists"
    assert new.written, "fed with silence while it waits"
    assert all(is_silence(b) for b in new.written)

    s.step()  # the cut is asked for; nothing is swapped yet
    assert motor.cuts == 1
    assert attached_after == []
    assert s.outputs.player is old
    assert s.output_states()["C"] == "absent"

    old_before = len(old.written)
    new_music_before = sum(not is_silence(b) for b in new.written)
    s.step()  # the bottom: the action runs inside `procesar`, the swap after the write
    assert attached_after == [1], "attach only after the cut's action ran"
    assert s.outputs.player is new
    assert len(old.written) == old_before + 1, "the old player got the block that ends at the bottom"
    assert not is_silence(old.written[-1])
    assert sum(not is_silence(b) for b in new.written) == new_music_before == 0, (
        "the new player got no engine block in the bottom step"
    )
    assert done.wait() == [None]
    assert s.output_states() == {"A": "playing", "B": "playing", "C": "playing"}
    for _ in range(3):
        s.step()
    first = next(i for i, b in enumerate(new.written) if not is_silence(b))
    assert all(not is_silence(b) for b in new.written[first:]), "no silence fed after the engine took over"
    assert set(new.written[-1]) == {"sA", "sB", "sC"}
    assert any(kind == "salida" for kind, _ in events)
    s.close()


def test_failed_preparation_leaves_state_intact(monkeypatch):
    def factory(nodes, *args, **kwargs):
        p = FakePlayer(nodes, *args, **kwargs)
        if len(nodes) == 3:  # the new player, with C
            p.wrong = {"sC": None}
        return p

    s, motor, players, events = open_session(monkeypatch, THREE, {"sA", "sB"}, factory=factory)
    before = s.output_states()
    done = Done()
    s.request_output({"A", "B", "C"}, done)
    [message] = done.wait()
    assert message is not None
    assert "sC" in message
    assert players[1].closed, "the player that failed is closed"
    assert s.outputs.player is players[0]
    s.step()
    s.step()
    assert motor.cuts == 0, "nothing is swapped, nothing is cut"
    assert s.output_states() == before
    assert any(kind == "salida" and "sC" in f["motivo"] for kind, f in events)
    s.request_output({"A", "B", "C"}, Done())  # free again: no conflict
    s.close()


def test_leave_keeps_the_speaker_computed(monkeypatch):
    s, _, players, _ = open_session(monkeypatch, {"A": "sA", "B": "sB"}, {"sA", "sB"})
    monitor = FakeMonitor()
    s.attach_monitor(monitor)
    done = Done()
    s.request_output({"B"}, done)
    swap(s)
    assert done.wait() == [None]
    assert players[1].nodes == ["sB"]
    assert s.output_states() == {"A": "absent", "B": "playing"}
    monitor.pushed.clear()
    s.step()
    assert set(monitor.pushed[-1]) == {"A", "B"}, "A is still computed and heard on the monitor"
    assert set(players[1].written[-1]) == {"sB"}
    s.close()


def test_leaving_the_last_speaker_leaves_no_player(monkeypatch):
    s, _, players, _ = open_session(monkeypatch, {"A": "sA"}, {"sA"})
    done = Done()
    s.request_output(set(), done)
    swap(s)
    assert done.wait() == [None]
    assert len(players) == 1, "no new player for an empty set"
    assert s.outputs.player is None
    assert s.output_states() == {"A": "absent"}
    s.step()
    s.close()


def test_second_request_while_pending_is_a_conflict(monkeypatch):
    s, _, _, _ = open_session(monkeypatch, THREE, {"sA", "sB"})
    s.request_output({"A", "B", "C"}, Done())
    with pytest.raises(SessionError) as err:
        s.request_output({"A"}, Done())
    assert err.value.code == "conflict"
    assert "already in progress" in err.value.message
    s.close()


def test_request_errors(monkeypatch):
    s, _, _, _ = open_session(monkeypatch, {"A": "sA", "V": None}, {"sA"})
    with pytest.raises(SessionError) as err:
        s.request_output({"A", "X"}, Done())
    assert err.value.code == "not_found"
    with pytest.raises(SessionError) as err:
        s.request_output({"A", "V"}, Done())
    assert err.value.code == "conflict"
    s.close()
    with pytest.raises(SessionError) as err:
        s.request_output({"A"}, Done())
    assert err.value.code == "unavailable"


def test_old_player_is_closed_off_the_engine_thread(monkeypatch):
    s, _, players, _ = open_session(monkeypatch, THREE, {"sA", "sB"})
    done = Done()
    s.request_output({"A", "B", "C"}, done)
    swap(s)
    done.wait()
    s._routing_pool.submit(lambda: None).result(timeout=WAIT_S)  # noqa: SLF001
    old = players[0]
    assert old.closed
    assert old.closed_on is not None
    assert old.closed_on.startswith("aurasync-ruteo"), old.closed_on
    assert old.closed_on != threading.current_thread().name
    s.close()


def test_the_next_change_alternates_the_combine_sink_name(monkeypatch):
    s, _, players, _ = open_session(monkeypatch, THREE, {"sA", "sB"})
    for target in ({"A", "B", "C"}, {"A", "B"}):
        done = Done()
        s.request_output(target, done)
        swap(s)
        assert done.wait() == [None]
    assert [p.name for p in players] == ["aurasync_salida", "aurasync_salida_b", "aurasync_salida"]
    assert players[1].closed, "the previous one is closed before its name is used again"
    s.close()


def test_separado_rebuilds_its_streams_over_the_new_set(monkeypatch):
    s, _, players, _ = open_session(monkeypatch, THREE, {"sA", "sB"}, output="separado")
    done = Done()
    s.request_output({"A", "B", "C"}, done)
    swap(s)
    assert done.wait() == [None]
    assert players[1].nodes == ["sA", "sB", "sC"]
    assert players[1].name is None
    s.close()


def test_closing_with_a_change_pending_closes_the_new_player(monkeypatch):
    s, _, players, _ = open_session(monkeypatch, THREE, {"sA", "sB"})
    done = Done()
    s.request_output({"A", "B", "C"}, done)
    wait_ready(s)
    s.close()
    [message] = done.wait()
    assert message is not None
    assert players[1].closed


def test_loop_restarts_over_the_new_playing_set(monkeypatch):
    s, _, _, events = open_session(monkeypatch, THREE, {"sA", "sB"})
    s.enable_recalibration("mic")
    assert s._measured == ["A", "B"]  # noqa: SLF001
    done = Done()
    s.request_output({"A", "B", "C"}, done)
    swap(s)
    done.wait()
    assert s.loop is not None
    assert s._measured == ["A", "B", "C"]  # noqa: SLF001
    s.step()
    assert set(s._emission._anillos) == {"A", "B", "C"}  # noqa: SLF001
    assert set(s._probe_emission._anillos) == {"A", "B", "C"}  # noqa: SLF001
    assert [f["motivo"] for kind, f in events if kind == "lazo"][-2:] == [
        "recalibración apagada",
        "recalibración encendida, con mic",
    ]
    s.close()


def test_the_loop_comes_back_when_a_speaker_that_left_joins_again(monkeypatch):
    """Spec §5: with the loop on, it measures again by itself. With two playing, one leave turns
    it off (nothing to align); the join must bring it back, since the user never turned it off."""
    s, _, _, _ = open_session(monkeypatch, {"A": "sA", "B": "sB"}, {"sA", "sB"})
    s.enable_recalibration("mic")
    for target, on in (({"A"}, False), ({"A", "B"}, True)):
        done = Done()
        s.request_output(target, done)
        swap(s)
        assert done.wait() == [None]
        s.step()
        assert (s.loop is not None) is on, target
    assert s._measured == ["A", "B"]  # noqa: SLF001
    s.close()


def test_the_loop_comes_back_when_a_lost_speaker_returns(monkeypatch):
    s, _, players, _ = open_session(monkeypatch, {"A": "sA", "B": "sB"}, {"sA", "sB"})
    s.enable_recalibration("mic")
    players[0].alive.remove("sB")
    s.step()
    assert s.output_states()["B"] == "lost"
    assert s.loop is None
    done = Done()
    s.request_output({"A", "B"}, done)  # the automatic return (Task 9) takes this path
    swap(s)
    assert done.wait() == [None]
    s.step()
    assert s.loop is not None
    assert s._measured == ["A", "B"]  # noqa: SLF001
    s.close()


def test_a_loop_the_user_turned_off_does_not_come_back(monkeypatch):
    s, _, _, _ = open_session(monkeypatch, {"A": "sA", "B": "sB"}, {"sA", "sB"})
    s.enable_recalibration("mic")
    s.disable_recalibration()
    done = Done()
    s.request_output({"A"}, done)
    swap(s)
    done.wait()
    done = Done()
    s.request_output({"A", "B"}, done)
    swap(s)
    done.wait()
    s.step()
    assert s.loop is None
    s.close()


def test_loop_restarts_when_a_speaker_is_lost(monkeypatch):
    """Without a swap: a lost speaker's music reference is the correlated-reference artefact of
    experimentos/08, and could move a silent speaker's delay."""
    s, _, players, events = open_session(monkeypatch, THREE, {"sA", "sB", "sC"})
    s.enable_recalibration("mic")
    assert s._measured == ["A", "B", "C"]  # noqa: SLF001
    players[0].alive.remove("sC")
    s.step()
    assert s.output_states()["C"] == "lost"
    assert s.loop is not None
    assert s._measured == ["A", "B"]  # noqa: SLF001
    assert set(s._emission._anillos) == {"A", "B"}  # noqa: SLF001
    players[0].alive.remove("sB")
    s.step()
    assert s.loop is None, "one playing speaker: the loop cannot align anything"
    assert any(kind == "lazo" and "needs two" in f["motivo"] for kind, f in events)
    s.close()


def test_a_routing_check_of_the_previous_player_is_discarded(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(session_module.time, "monotonic", lambda: clock[0])
    s, _, players, events = open_session(monkeypatch, THREE, {"sA", "sB"})
    old = players[0]
    old.hold = threading.Event()
    old.wrong = {"sA": "aurasync"}
    clock[0] += session_module.ROUTING_CHECK_S + 10
    s.step()  # the check of the old player starts, and waits
    assert s._routing_future is not None  # noqa: SLF001
    done = Done()
    s.request_output({"A", "B", "C"}, done)
    swap(s)
    done.wait()
    assert s.outputs.player is players[1]
    old.hold.set()
    s._routing_future.result(timeout=WAIT_S)  # noqa: SLF001
    s.step()  # what it found is about a player that is gone
    s._routing_pool.submit(lambda: None).result(timeout=WAIT_S)  # noqa: SLF001
    assert s.routing_repairs == 0
    assert players[1].repairs == 1, "only the repair of its own preparation"
    assert players[1].alive == ["sA", "sB", "sC"]
    assert not any(kind in {"ruteo", "parlante perdido"} for kind, _ in events)
    s.close()


def test_a_routing_check_of_the_current_player_still_applies(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(session_module.time, "monotonic", lambda: clock[0])
    s, _, players, events = open_session(monkeypatch, THREE, {"sA", "sB"})
    repairs = players[0].repairs  # `open` repairs once, before its own check
    players[0].wrong = {"sA": "aurasync"}
    clock[0] += session_module.ROUTING_CHECK_S + 10
    s.step()
    s._routing_future.result(timeout=WAIT_S)  # noqa: SLF001
    s.step()
    s._routing_pool.submit(lambda: None).result(timeout=WAIT_S)  # noqa: SLF001
    assert s.routing_repairs == 1
    assert players[0].repairs == repairs + 1
    assert any(kind == "ruteo" for kind, _ in events)
    s.close()


def test_simulated_session_leaves_and_joins(monkeypatch):
    monkeypatch.setattr(AudioSession, "SWAP_SETTLE_S", 0.0)
    monkeypatch.setattr(AudioSession, "SWAP_CHECK_S", 0.0)
    inst = Instalacion(parlantes=[Parlante("s0", "sink0", pan=-0.7), Parlante("s1", "sink1", pan=0.7)])
    s = SimulatedSession(inst, motor_module.Motor(inst, 48000), SessionOptions(block=4096), lambda *_, **__: None)
    s.open()
    played: list[dict] = []
    room_play = s.room.play
    s.room.play = lambda blocks: (played.append(dict(blocks)), room_play(blocks))[1]
    for target, heard in (({"s0"}, {"s0"}), ({"s0", "s1"}, {"s0", "s1"})):
        done = Done()
        s.request_output(target, done)
        wait_ready(s)
        for _ in range(6):
            s.step()
            if done.event.is_set():
                break
        assert done.wait() == [None]
        played.clear()
        s.step()
        assert played
        assert all(set(b) == heard for b in played)
        assert {n for n, state in s.output_states().items() if state == "playing"} == heard
    s.close()


def test_closing_while_waiting_for_the_old_player_spawns_nothing(monkeypatch):
    """A change waits for the previous player to finish closing (its sink name is reused); the
    session closing in the meantime must stop the wait at once and build nothing."""
    s, _, players, _ = open_session(monkeypatch, THREE, {"sA", "sB"})
    players[0].close_hold = threading.Event()  # the old player takes long to play out
    done = Done()
    s.request_output({"A", "B", "C"}, done)
    swap(s)
    assert done.wait() == [None]
    second = Done()
    s.request_output({"A", "B"}, second)
    assert not s._change.ready.wait(0.2), "it waits for the old player"  # noqa: SLF001
    started = time.perf_counter()
    s.close()
    assert time.perf_counter() - started < 1.0, "the wait stops as soon as the change is cancelled"
    assert second.event.is_set(), "done was called before close returned"
    [message] = second.wait()
    assert message is not None
    assert len(players) == 2, "no player was built for the cancelled change"
    assert players[1].closed
    players[0].close_hold.set()


def test_closing_during_the_routing_check_closes_the_new_player(monkeypatch):
    s, _, players, _ = open_session(monkeypatch, THREE, {"sA", "sB"})
    monkeypatch.setattr(AudioSession, "SWAP_SETTLE_S", 30.0)
    done = Done()
    s.request_output({"A", "B", "C"}, done)
    assert _wait_for(lambda: len(players) == 2 and players[1].entered.is_set())
    assert not s._change.ready.is_set()  # noqa: SLF001
    s.close()
    [message] = done.wait()
    assert message is not None
    assert players[1].closed
    assert players[1].repairs == 0, "the check after the settling time never ran"


def _wait_for(predicate, timeout=WAIT_S) -> bool:
    event = threading.Event()
    for _ in range(int(timeout / 0.01)):
        if predicate():
            return True
        event.wait(0.01)
    return predicate()


# -- the speakers' cushion (spec §9): one value, refilled for all at the bottom of a cut -------

RATE = 48000
REAL_BLOCK = 4096
QUANTUM = cushion_module.DRIVER_QUANTUM_FRAMES
GAP_BLOCKS = cushion_module.MIN_GAP_S * RATE / REAL_BLOCK


class PipePlayer(FakePlayer):
    """Each sink's pipe to its `pw-play` as a level in frames. The driver takes one quantum per
    cycle (`cycle`), plus `drift[sink]` frames when that output's clock runs faster than the
    input's (fewer when slower); a cycle that finds less than it takes is a starvation. A write that
    does not fit waits for the driver, as a blocking write to a full pipe does (the pipe size `open`
    asks); a pad that has to wait is counted in `blocked_pads`."""

    capacity = round(session_module.pipe_size_ms(REAL_BLOCK, RATE, 50) * RATE / 1000)

    def __init__(self, nodes, *args, **kwargs) -> None:
        super().__init__(nodes, *args, **kwargs)
        self.level = dict.fromkeys(self.nodes, 0)
        self.drift = dict.fromkeys(self.nodes, 0)
        self.starved = 0
        self.cycles = 0
        self.blocked_pads = 0

    def nivel_ms(self):
        return min(self.level.values()) / RATE * 1000

    def espacio_ms(self):
        return min(self.capacity - v for v in self.level.values()) / RATE * 1000

    def cycle(self) -> None:
        self.cycles += 1
        for n in self.nodes:
            take = QUANTUM + self.drift[n]
            if self.level[n] < take:
                self.starved += 1
            self.level[n] = max(0, self.level[n] - take)

    def escribir(self, blocks) -> None:
        super().escribir(blocks)
        pad = getattr(self, "opened_with", None) is not None and is_silence(blocks)
        for n, x in blocks.items():
            while self.level[n] + len(x) > self.capacity:
                self.blocked_pads += pad
                self.cycle()
            self.level[n] += len(x)


def run_pipes(s, player, steps, on_step=None) -> None:
    """Blocks arrive in bursts of two quanta: the driver takes two, then the engine writes one."""
    for i in range(steps):
        player.cycle()
        player.cycle()
        s.step()
        if on_step is not None:
            on_step(i)


def pads(player) -> list[dict]:
    """The writes of silence after the open: the motor fake never returns silence."""
    return [b for b in player.written[player.opened_with :] if is_silence(b)]


def open_pipes(monkeypatch, output="combinado", factory=PipePlayer):
    s, motor, players, events = open_session(
        monkeypatch, THREE, {"sA", "sB", "sC"}, factory=factory, block=REAL_BLOCK, output=output
    )
    players[0].opened_with = len(players[0].written)
    return s, motor, players[0], events


def state_of(s) -> dict:
    svc = SimpleNamespace(session=s, observer=SimpleNamespace(view={}), streams={})
    return snapshot._health(svc, REAL_BLOCK / RATE * 1000, [])["output_cushion"]  # noqa: SLF001


@pytest.mark.parametrize("output", ["combinado", "separado"])
def test_blocks_in_two_quantum_bursts_never_starve_a_speaker_pipe(monkeypatch, output):
    """By construction: `open` primes the pipe and the write waits while it is full, so between
    two blocks the pipe holds its size minus a block (135 ms here), never under a quantum."""
    s, motor, player, _ = open_pipes(monkeypatch, output)
    run_pipes(s, player, 2000)
    assert player.starved == 0
    assert motor.cuts == 0
    assert s.outputs.cushion.refills == 0
    assert pads(player) == []
    assert s.pipe_ms > QUANTUM / RATE * 1000
    s.close()


@pytest.mark.parametrize("output", ["combinado", "separado"])
def test_a_draining_pipe_is_refilled_for_every_speaker_at_the_bottom_of_a_cut(monkeypatch, output):
    """Every output's clock a little faster than the input's (the same for all): the pipes drain
    together, and each refill fits in all of them."""
    s, motor, player, events = open_pipes(monkeypatch, output)
    player.drift = dict.fromkeys(player.nodes, 2)
    ran_before, bottoms, padded = [motor.ran], [], []

    def watch(i):
        if motor.ran > ran_before[0]:
            bottoms.append(i)
        ran_before[0] = motor.ran
        if pads(player) and len(pads(player)) > len(padded):
            padded.append(i)

    run_pipes(s, player, 4500, watch)
    assert player.starved == 0, "the pipe was refilled before it ran dry"
    refills = s.outputs.cushion.refills
    assert refills >= 3
    assert len(pads(player)) == refills
    assert motor.cuts == refills, "one cut per refill"
    assert padded == bottoms, "silence only in the steps whose cut reached its bottom"
    assert min(b - a for a, b in itertools.pairwise(bottoms)) >= GAP_BLOCKS
    assert player.blocked_pads == 0, "a pad never makes the engine wait"
    for pad in pads(player):
        assert set(pad) == {"sA", "sB", "sC"}, "every speaker in the same write"
        assert len({len(x) for x in pad.values()}) == 1, "the same silence for each"
    assert any(kind == "salida" and "colchón" in f.get("motivo", "") for kind, f in events)
    assert state_of(s) == {
        "target_ms": round((REAL_BLOCK + QUANTUM) / RATE * 1000, 1),
        "refills": refills,
        "pending": False,
        "reason": None,
        "gave_up": False,
    }
    s.close()


def test_a_refill_leaves_the_alignment_between_speakers_as_it_was(monkeypatch):
    """`separado`: each speaker its own pipe, at its own level (what they waited for differs). The
    refill is sized on the lowest pipe and the same silence goes to every one, so the difference
    between their levels, which sets their relative timing, does not move; equalising the levels
    would move it."""
    s, _, player, _ = open_pipes(monkeypatch, "separado")
    player.drift = dict.fromkeys(player.nodes, 2)
    player.level["sB"] -= 200
    player.level["sC"] -= 100
    seen: list[tuple[dict, dict, int]] = []
    before = [dict(player.level), player.cycles]

    def watch(_i):
        if len(pads(player)) > len(seen):
            seen.append((before[0], dict(player.level), player.cycles - before[1]))
        before[:] = [dict(player.level), player.cycles]

    run_pipes(s, player, 4500, watch)
    assert len(seen) >= 3, "refills happened"
    sizes = [len(next(iter(pad.values()))) for pad in pads(player)]
    for (level_before, after, cycles), size in zip(seen, sizes, strict=True):
        # In that step every pipe gave the driver the same cycles and got the block and the pad:
        # what each one gave differs by its drift; what each one got is the same.
        got = {n: after[n] - level_before[n] + cycles * (QUANTUM + player.drift[n]) for n in player.nodes}
        assert set(got.values()) == {REAL_BLOCK + size}
    totals = {n: sum(len(b[n]) for b in player.written) for n in player.nodes}
    assert len(set(totals.values())) == 1, "every speaker got exactly the same number of frames"
    assert player.starved == 0
    assert player.blocked_pads == 0
    assert (player.level["sA"] - player.level["sB"], player.level["sA"] - player.level["sC"]) == (200, 100)
    s.close()


@pytest.mark.parametrize("drift", [(-64, 64, 0), (-8, 8, 0), (0, 64, 0)])
def test_separado_with_clocks_that_differ_does_not_cut_over_and_over(monkeypatch, drift):
    """Review of Task 11: the slowest pipe fills and holds the write back, so the fastest one stays low
    whatever is padded; a cushion that kept refilling cut the music every ~4 blocks. The pad would
    not fit in the fullest pipe: no cut, and the state says why."""
    s, motor, player, _ = open_pipes(monkeypatch, "separado")
    player.drift = dict(zip(player.nodes, drift, strict=True))
    steps = 3000
    run_pipes(s, player, steps)
    seconds = steps * REAL_BLOCK / RATE
    assert motor.cuts <= seconds / cushion_module.MIN_GAP_S
    assert player.blocked_pads == 0
    state = state_of(s)
    assert state["reason"] == "separado: relojes distintos"
    assert state["pending"] is False
    s.close()


class LyingPipe(PipePlayer):
    """A pipe whose level always reads empty however much is written: refills never help."""

    def nivel_ms(self):
        return 0.0

    def espacio_ms(self):
        return self.capacity / RATE * 1000


def test_after_three_refills_that_did_not_help_it_stops_asking(monkeypatch, caplog):
    s, motor, player, events = open_pipes(monkeypatch, factory=LyingPipe)
    with caplog.at_level("WARNING", logger="aurasync.session"):
        run_pipes(s, player, 3000)
    assert motor.cuts == cushion_module.MAX_FAILED
    state = state_of(s)
    assert state["gave_up"] is True
    assert state["pending"] is False
    warnings = [r for r in caplog.records if r.levelname == "WARNING" and "colchón" in r.getMessage()]
    assert len(warnings) == 1, "one warning, not one per block"
    assert sum("dejó de rellenar" in f.get("motivo", "") for kind, f in events if kind == "salida") == 1
    s.close()


def test_a_swap_at_the_same_bottom_takes_the_place_of_the_refill(monkeypatch):
    """A speaker change and a refill whose cuts land on the same bottom: the new real part was primed
    by its own worker, so the refill computed on the old pipe is dropped."""
    s, motor, player, _ = open_pipes(monkeypatch)
    players = [player]
    player.level = dict.fromkeys(player.nodes, REAL_BLOCK + 1000)  # reads 1000 frames before each write
    run_pipes(s, player, cushion_module.LOW_BLOCKS - 1)
    done = Done()
    s.request_output({"A", "B", "C"}, done)
    wait_ready(s)
    new = s._change.player  # noqa: SLF001
    players.append(new)
    run_pipes(s, player, 1)  # the third low reading asks for a cut, and the ready change asks for one
    assert motor.cuts == 2
    assert s.outputs.cushion.pending
    ran = motor.ran
    run_pipes(s, player, 1)  # the bottom of both
    assert motor.ran == ran + 2
    assert done.wait() == [None]
    assert s.outputs.player is new
    assert pads(player) == []
    assert s.outputs.cushion.refills == 0
    assert not s.outputs.cushion.pending
    s.close()


def test_a_late_block_does_not_cut_the_music(monkeypatch):
    """A stall leaves the pipe low for one block; the input's backlog fills it again. No cut."""
    s, motor, player, _ = open_pipes(monkeypatch)
    run_pipes(s, player, 50)
    for _ in range(3):  # the engine came back 125 ms late
        player.cycle()
    run_pipes(s, player, 1)
    s.step()  # the input's backlog: a block at once
    run_pipes(s, player, 50)
    assert motor.cuts == 0
    assert pads(player) == []
    s.close()


def test_the_state_has_no_cushion_without_a_session():
    svc = SimpleNamespace(session=None, observer=SimpleNamespace(view={}), streams={})
    assert snapshot._health(svc, 85.3, [])["output_cushion"] is None  # noqa: SLF001
