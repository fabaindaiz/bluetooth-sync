"""A session with virtual and absent speakers (spec 2026-10-05-virtual-speakers-and-hot-join-design.md
§3 and §4, d-7c8794-05bdd6). Without PipeWire: the player, the virtual sink and the microphone are
fakes, and the "no process" test lets the real classes run against a `Popen` that fails."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from aurasync import cli, sonido
from aurasync import motor as motor_module
from aurasync import session as session_module
from aurasync.config import Instalacion, Parlante
from aurasync.session import AudioSession, SessionError, SessionOptions, missing_speakers
from aurasync.simulated import SimulatedObserver, SimulatedSession

BLOCK = 64


class FakePlayer:
    """The real part, refusing what the real one refuses: a node it was not opened with, a
    write after close."""

    def __init__(self, nodes, *_args, **_kwargs) -> None:
        self.nodes = list(nodes)
        self.alive = list(nodes)
        self.written: list[dict] = []
        self.closed = False

    def __enter__(self):
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
        self.written.append(dict(blocks))

    def mal_ruteados(self):
        return {}

    def reparar_ruteo(self):
        return {}

    def soltar(self, node) -> None:
        if node in self.alive:
            self.alive.remove(node)

    def cerrar(self) -> None:
        self.closed = True


class FakeSink:
    """The `aurasync` input sink: zeros while something plays, `None` otherwise."""

    def __init__(self, *_args, **_kwargs) -> None:
        self.playing = True
        self.pid = 7

    def __enter__(self):
        return self

    def __exit__(self, *_) -> None:
        self.cerrar()

    def cerrar(self) -> None:
        self.pid = None

    def leer(self, n, espera_s=0.05):  # noqa: ARG002
        return (np.zeros(n), np.zeros(n)) if self.playing else None


class FakeMotor:
    en_corte = False
    volumen_db = 0.0

    def __init__(self, names) -> None:
        self.names = list(names)

    def procesar(self, izq, _der, canales=None):  # noqa: ARG002
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
        self.pushed.append({k: np.array(v, copy=True) for k, v in blocks.items()})

    def close(self) -> None:
        pass


def open_session(monkeypatch, sinks, present, **options):
    """An `AudioSession` opened through `open()`, with `present` the Bluetooth sinks PipeWire lists."""
    players: list[FakePlayer] = []

    def player(nodes, *args, **kwargs):
        players.append(FakePlayer(nodes, *args, **kwargs))
        return players[-1]

    monkeypatch.setattr(
        sonido, "salidas_bluetooth", lambda: [sonido.SalidaBluetooth(n, n, "sbc") for n in sorted(present)]
    )
    monkeypatch.setattr(sonido, "nodo_existe", lambda _name: False)
    monkeypatch.setattr(sonido, "Reproductor", player)
    monkeypatch.setattr(sonido, "ReproductorCombinado", player)
    monkeypatch.setattr(sonido, "SinkVirtual", FakeSink)
    monkeypatch.setattr(session_module.time, "sleep", lambda _s: None)
    monkeypatch.setattr(AudioSession, "_microphone", lambda _self, _name, _seconds: FakeMic())
    inst = Instalacion(parlantes=[Parlante(n, sink) for n, sink in sinks.items()])
    events: list[tuple[str, dict]] = []
    s = AudioSession(
        inst,
        FakeMotor(list(sinks)),
        SessionOptions(block=BLOCK, **options),
        lambda kind, **f: events.append((kind, f)),
    )
    s.open()
    return s, players, events


# -- opening ------------------------------------------------------------------------------


def test_missing_speakers_never_reports_a_virtual_one(monkeypatch):
    monkeypatch.setattr(sonido, "salidas_bluetooth", lambda: [sonido.SalidaBluetooth("sA", "A", "sbc")])
    inst = Instalacion(parlantes=[Parlante("A", "sA"), Parlante("B", "sB"), Parlante("V", None)])
    assert missing_speakers(inst) == ["B"]


@pytest.mark.parametrize("output", ["combinado", "separado"])
def test_opens_with_a_real_speaker_absent(monkeypatch, output):
    s, players, _ = open_session(monkeypatch, {"A": "sA", "B": "sB", "V": None}, {"sA"}, output=output)
    assert s.output_states() == {"A": "playing", "B": "absent", "V": "virtual"}
    assert len(players) == 1
    assert players[0].nodes == ["sA"], "only the sinks of the speakers that play reach PipeWire"
    s.step()
    assert set(players[0].written[-1]) == {"sA"}
    assert s.pids()["players"] == {"A": 100, "B": None, "V": None}
    s.close()
    assert players[0].closed


def test_all_virtual_launches_no_process(monkeypatch):
    """Only `pw-record` (the `aurasync` input sink) may start: no `pw-play`, no combine module."""
    launched: list[str] = []

    def popen(args, *_a, **_k):
        launched.append(args[0])
        if args[0] != "pw-record":
            pytest.fail(f"a process was launched for a virtual speaker: {args}")
        return SimpleNamespace(
            pid=4242,
            stdout=None,
            stdin=None,
            poll=lambda: None,
            terminate=lambda: None,
            kill=lambda: None,
            wait=lambda timeout=None: 0,  # noqa: ARG005
        )

    def run(args, *_a, **_k):
        pytest.fail(f"a command was run: {args}")

    monkeypatch.setattr(sonido.subprocess, "Popen", popen)
    monkeypatch.setattr(sonido.subprocess, "run", run)
    monkeypatch.setattr(sonido, "_pw_dump", list)
    inst = Instalacion(parlantes=[Parlante("V1", None, pan=-0.7), Parlante("V2", None, pan=0.7)])
    s = AudioSession(inst, FakeMotor(["V1", "V2"]), SessionOptions(block=BLOCK), lambda *_a, **_k: None)
    s.open()
    try:
        assert s.output_states() == {"V1": "virtual", "V2": "virtual"}
        for _ in range(3):
            s.step()
        assert launched == ["pw-record"]
        assert s.pids()["players"] == {"V1": None, "V2": None}
    finally:
        s.close()


# -- playing ------------------------------------------------------------------------------


def test_losing_every_real_speaker_does_not_end_the_session(monkeypatch):
    """Until 2026-10-05 this raised SessionError("unavailable", "every speaker disconnected")."""
    s, players, events = open_session(monkeypatch, {"A": "sA", "V": None}, {"sA"})
    s.step()
    players[0].alive.clear()
    s.step()
    s.step()
    assert s.output_states() == {"A": "lost", "V": "virtual"}
    assert s.lost == ["A"]
    assert any(kind == "parlante perdido" for kind, _ in events)
    assert [e["kind"] for e in s.cuts.summary()["events"]].count("lost") == 1
    s.close()


def test_monitor_gets_every_channel(monkeypatch):
    s, _, _ = open_session(monkeypatch, {"A": "sA", "B": "sB", "V": None}, {"sA"})
    monitor = FakeMonitor()
    s.attach_monitor(monitor)
    s.step()
    assert set(monitor.pushed[-1]) == {"A", "B", "V"}
    s.close()


def test_monitor_gets_every_channel_during_calibration(monkeypatch):
    """The monitor's binaural filter has one input per speaker: none may go missing."""
    s, players, _ = open_session(monkeypatch, {"A": "sA", "B": "sB", "V": None}, {"sA"})
    monitor = FakeMonitor()
    s.attach_monitor(monitor)
    s.start_calibration(5.0, 0.05, "mic")
    for _ in range(int(session_module.CAL_BEFORE_S * 48000 / BLOCK) + 4):
        s.step()
    last = monitor.pushed[-1]
    assert set(last) == {"A", "B", "V"}
    assert not np.any(last["B"])
    assert not np.any(last["V"])
    assert np.any(last["A"]), "the stimulus started on the playing speaker"
    assert set(players[0].written[-1]) == {"sA"}
    s.close()


# -- measuring only what sounds -----------------------------------------------------------


def test_calibration_uses_only_playing_speakers(monkeypatch):
    s, _, events = open_session(monkeypatch, {"A": "sA", "B": "sB", "V": None}, {"sA"})
    s.start_calibration(5.0, 0.05, "mic")
    assert set(s.calibration.references) == {"A"}
    assert any(kind == "calibración" and "B" in f["motivo"] and "V" in f["motivo"] for kind, f in events)
    s.close()


def test_calibration_without_a_playing_speaker_is_refused(monkeypatch):
    s, _, _ = open_session(monkeypatch, {"V1": None, "V2": None}, set())
    with pytest.raises(SessionError) as info:
        s.start_calibration(5.0, 0.05, "mic")
    assert info.value.code == "conflict"
    assert "no speaker is playing" in info.value.message
    assert s.calibration is None
    s.close()


def test_a_refused_calibration_leaves_the_loop_on(monkeypatch):
    s, players, _ = open_session(monkeypatch, {"A": "sA", "B": "sB"}, {"sA", "sB"})
    s.enable_recalibration("mic")
    players[0].alive.clear()
    s.step()
    with pytest.raises(SessionError):
        s.start_calibration(5.0, 0.05, "mic")
    assert s.loop is not None
    s.close()


def test_recalibration_needs_two_playing(monkeypatch):
    s, _, events = open_session(monkeypatch, {"A": "sA", "V": None}, {"sA"})
    s.enable_recalibration("mic")
    assert s.loop is None
    assert any(kind == "lazo" and "needs two" in f["motivo"] for kind, f in events)
    s.step()
    s.close()


def test_recalibration_on_at_start_with_all_virtual_stays_off(monkeypatch):
    s, _, events = open_session(monkeypatch, {"V1": None, "V2": None}, set(), recalibrate=True, microphone="mic")
    assert s.loop is None
    assert any(kind == "lazo" and "no speaker is playing" in f["motivo"] for kind, f in events)
    s.close()


def test_recalibration_measures_only_the_playing_speakers(monkeypatch):
    s, _, _ = open_session(monkeypatch, {"A": "sA", "B": "sB", "V": None}, {"sA", "sB"})
    s.enable_recalibration("mic")
    assert s.loop is not None
    for _ in range(3):
        s.step()  # the emission windows take the playing speakers' blocks, and no others
    assert set(s._emission._anillos) == {"A", "B"}  # noqa: SLF001
    assert set(s._probe_emission._anillos) == {"A", "B"}  # noqa: SLF001
    s.close()


# -- the simulated room ----------------------------------------------------------------------


def test_simulated_room_never_hears_a_virtual_speaker():
    inst = Instalacion(parlantes=[Parlante("s0", "sink0", pan=-0.7), Parlante("V", None, pan=0.7)])
    s = SimulatedSession(inst, motor_module.Motor(inst, 48000), SessionOptions(block=4096), lambda *_, **__: None)
    s.open()
    assert set(s.room.delays) == {"s0"}
    played: list[dict] = []
    room_play = s.room.play
    s.room.play = lambda blocks: (played.append(dict(blocks)), room_play(blocks))[1]
    for _ in range(3):
        s.step()
    assert played
    assert all(set(b) == {"s0"} for b in played)
    assert s.output_states() == {"s0": "playing", "V": "virtual"}
    s.close()


def test_simulated_observer_lists_no_device_for_a_virtual_speaker():
    inst = Instalacion(parlantes=[Parlante("A", "bluez_output.AA_BB_CC_DD_EE_01.1"), Parlante("V", None)])
    view = SimulatedObserver(inst).view
    names = [d["name"] for d in view["devices"]]
    assert "A" in names
    assert "V" not in names
    assert None not in {o["sink"] for o in view["outputs"]}


# -- the calibrate command -------------------------------------------------------------------


def test_calibrate_cli_with_only_virtual_speakers_says_so(tmp_path, monkeypatch, capsys):
    path = tmp_path / "i.json"
    Instalacion(parlantes=[Parlante("V1", None), Parlante("V2", None)]).guardar(path)
    monkeypatch.setattr(sonido, "salidas_bluetooth", list)
    assert cli.main(["--config", str(path), "calibrate"]) == 1
    assert "virtual" in capsys.readouterr().err


def test_calibrate_cli_plays_only_to_real_speakers(tmp_path, monkeypatch):
    path = tmp_path / "i.json"
    Instalacion(parlantes=[Parlante("A", "sA"), Parlante("V", None), Parlante("B", "sB")]).guardar(path)
    players: list[FakePlayer] = []

    def player(nodes, *args, **kwargs):
        players.append(FakePlayer(nodes, *args, **kwargs))
        return players[-1]

    def calibrar(_recording, references, *_a, **_k):
        names = list(references)
        return SimpleNamespace(
            retardos_ms=dict.fromkeys(names, 1.5),
            ganancias_db=dict.fromkeys(names, -1.0),
            estabilidad_ms=dict.fromkeys(names, 0.01),
            sin_sonar=list,
            dudosos=list,
            confiable=True,
        )

    monkeypatch.setattr(
        sonido, "salidas_bluetooth", lambda: [sonido.SalidaBluetooth(n, n, "sbc") for n in ("sA", "sB")]
    )
    monkeypatch.setattr(cli, "resolver_microfono", lambda _args: "mic")
    monkeypatch.setattr(sonido, "grabar", lambda *_a, **_k: None)
    monkeypatch.setattr(sonido, "terminar_grabacion", lambda _p: None)
    monkeypatch.setattr(sonido, "leer_wav_mono", lambda _p: np.zeros(48000))
    monkeypatch.setattr(sonido, "Reproductor", player)
    monkeypatch.setattr(cli.time, "sleep", lambda _s: None)
    monkeypatch.setattr("aurasync.medicion.calibrar", calibrar)
    assert cli.main(["--config", str(path), "calibrate", "--segundos", "1"]) == 0
    assert players[0].nodes == ["sA", "sB"]
    back = Instalacion.cargar(path)
    assert {p.nombre: p.retardo_ms for p in back.parlantes} == {"A": 1.5, "V": 0.0, "B": 1.5}


# -- the service and the contract (task 5) ---------------------------------------------------

import threading  # noqa: E402

from aurasync import control  # noqa: E402
from aurasync.clients import required_scope  # noqa: E402
from aurasync.config import Instalacion as _Inst  # noqa: E402
from aurasync.dsp.response import THIRDS  # noqa: E402
from aurasync.service import Service  # noqa: E402
from tests.test_bt_volume import FakePactl, _volume  # noqa: E402
from tests.test_service import FakeSession, _err, _ok, _wait  # noqa: E402

NOTHING_PLAYS = "no speaker is playing"
BT = "bluez_output.AA_BB_CC_DD_EE_01.1"


@pytest.fixture
def served(tmp_path):
    made = []

    def make(parlantes, **kwargs):
        FakeSession.instances = []
        _Inst(parlantes=parlantes).guardar(tmp_path / "inst.json")
        svc = Service(
            tmp_path / "inst.json", tmp_path / "presets.json", session_factory=FakeSession, log=lambda _: None, **kwargs
        )
        thread = threading.Thread(target=svc.run, daemon=True)
        thread.start()
        made.append((svc, thread))
        return svc

    yield make
    for svc, thread in made:
        svc.handle({"v": 1, "op": "shutdown"})
        thread.join(timeout=5)
        svc.close()


def speaker(state, name):
    return next(p for p in state["speakers"] if p["name"] == name)


def test_speaker_add_virtual_takes_the_next_free_role(served):
    svc = served([Parlante("A", BT, pan=-0.7, ambiente=0.15)])
    _ok(svc, op="speaker_add_virtual")
    state = _ok(svc, op="state")
    added = speaker(state, "Virtual 1")
    taken = control.layout_roles(svc.settings.layout, 2)
    assert (added["pan"], added["ambience"]) in taken.values()
    assert added["role"] != speaker(state, "A")["role"]
    assert svc.installation.por_nombre("Virtual 1").sink is None
    assert state["dirty"]


def test_speaker_add_virtual_default_names_and_conflict(served):
    svc = served([])
    _ok(svc, op="speaker_add_virtual")
    _ok(svc, op="speaker_add_virtual")
    assert [p["name"] for p in _ok(svc, op="state")["speakers"]] == ["Virtual 1", "Virtual 2"]
    assert _err(svc, op="speaker_add_virtual", name="Virtual 2") == "conflict"
    _ok(svc, op="speaker_add_virtual", name="Sala")
    # The first free default fills the gap.
    _ok(svc, op="speaker_remove", speaker="Virtual 1")
    _ok(svc, op="speaker_add_virtual")
    assert speaker(_ok(svc, op="state"), "Virtual 1")["output_kind"] == "virtual"


def test_speaker_add_virtual_needs_the_session_stopped(served):
    svc = served([Parlante("A", "s0")])
    _ok(svc, op="start")
    assert _err(svc, op="speaker_add_virtual") == "conflict"


def test_speaker_add_virtual_needs_admin():
    assert required_scope("speaker_add_virtual") == "admin"


def test_snapshot_virtual_speaker_has_no_bluetooth_fields(served):
    svc = served([Parlante("A", BT), Parlante("V", None), Parlante("W", "alsa_output.usb")])
    _ok(svc, op="speaker_add_virtual")
    state = _ok(svc, op="state")
    v = speaker(state, "V")
    assert v["output_kind"] == "virtual"
    assert v["sink"] is None
    for key in ("address", "battery_pct", "codec", "rssi_dbm", "modalias"):
        assert v[key] is None, key
    wired = speaker(state, "W")
    assert wired["output_kind"] == "wired"
    assert wired["sink"] == "alsa_output.usb"
    for key in ("address", "battery_pct", "codec", "rssi_dbm", "modalias"):
        assert wired[key] is None, key
    bt = speaker(state, "A")
    assert bt["output_kind"] == "bluetooth"
    assert bt["address"] == "AA:BB:CC:DD:EE:01"


def test_snapshot_output_is_null_without_session_and_follows_the_session(served):
    svc = served([Parlante("A", "s0"), Parlante("V", None)])
    assert speaker(_ok(svc, op="state"), "A")["output"] is None
    _ok(svc, op="start")
    state = _ok(svc, op="state")
    assert (speaker(state, "A")["output"], speaker(state, "V")["output"]) == ("playing", "virtual")
    assert speaker(state, "A")["playing"] is True
    assert speaker(state, "V")["playing"] is False, "playing equals output == 'playing'"


def test_avrcp_skips_non_bluetooth(served):
    fake = FakePactl({BT: 80.0})
    svc = served([Parlante("A", BT), Parlante("V", None), Parlante("W", "alsa_output.usb")], bt_volume=_volume(fake))
    assert svc._sinks() == {"A": BT}  # noqa: SLF001
    _ok(svc, op="start")
    _ok(svc, op="chain_set", stage="volume", algorithm="avrcp")
    assert svc.bt_volume.wait_idle()
    _wait(lambda: _ok(svc, op="state")["volume_avrcp"]["state"] in {"on", "failed"})
    assert {c[2] for c in fake.calls} == {BT}


def test_avrcp_with_no_bluetooth_speaker_does_not_enter(served):
    fake = FakePactl({})
    svc = served([Parlante("V", None)], bt_volume=_volume(fake))
    _ok(svc, op="start")
    _ok(svc, op="chain_set", stage="volume", algorithm="avrcp")
    assert fake.calls == []
    assert _ok(svc, op="state")["volume_avrcp"]["state"] != "on"


def test_calibrate_without_playing_speakers_is_refused(served, monkeypatch):
    svc = served([Parlante("V", None)])
    _ok(svc, op="start")

    def refuse(*_a, **_k):
        raise SessionError("conflict", NOTHING_PLAYS)  # noqa: EM101 - the code, not the message

    monkeypatch.setattr(FakeSession, "start_calibration", refuse, raising=False)
    assert _err(svc, op="calibrate") == "conflict"
    reply = svc.handle({"v": 1, "op": "calibrate"})
    assert "no speaker is playing" in reply["error"]["message"]


def test_the_forget_and_radio_lookups_ignore_virtual_speakers(served):
    svc = served([Parlante("A", BT), Parlante("V", None)])
    assert svc._speaker_by_address("AA:BB:CC:DD:EE:01") == "A"  # noqa: SLF001
    assert svc._speaker_by_address("00:00:00:00:00:00") is None  # noqa: SLF001


def test_a_calibration_of_a_subset_leaves_the_others_untouched(served):
    svc = served(
        [
            Parlante("A", "s0", retardo_ms=3.0, ganancia_db=-2.0, ecualizacion_db=[1.0] * len(THIRDS)),
            Parlante("B", "s1", retardo_ms=5.0, ganancia_db=-4.0, ecualizacion_db=[2.0] * len(THIRDS)),
            Parlante("V", None, retardo_ms=7.0, ganancia_db=-6.0, ecualizacion_db=[3.0] * len(THIRDS)),
        ]
    )
    _ok(svc, op="start")
    session = FakeSession.instances[0]
    result = {
        "silent": False,
        "doubtful": False,
        "delay_ms": 0.0,
        "gain_db": 0.0,
        "applied_delay_ms": 3.0,
        "applied_gain_db": -2.0,
        "response_db": [-3.0] * len(THIRDS),
        "applied_eq_db": [0.0] * len(THIRDS),
        "band_hz": (None, None),
        "speaker": "A",
    }
    session.calibration = SimpleNamespace(state="done", results=[result], describe=lambda: {"state": "done"})
    session.loop = None
    svc.motor.cortar = lambda action: action()
    _ok(svc, op="calibration_apply")
    for name, delay, gain in (("B", 5.0, -4.0), ("V", 7.0, -6.0)):
        p = svc.installation.por_nombre(name)
        assert (p.retardo_ms, p.ganancia_db) == (delay, gain), name
    _ok(svc, op="eq_apply")
    assert svc.installation.por_nombre("A").ecualizacion_db != [1.0] * len(THIRDS), "the participant was equalised"
    assert svc.installation.por_nombre("B").ecualizacion_db == [2.0] * len(THIRDS)
    assert svc.installation.por_nombre("V").ecualizacion_db == [3.0] * len(THIRDS)
