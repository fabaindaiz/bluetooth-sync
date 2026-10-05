"""Joining and leaving a running session, and a lost speaker returning by itself
(spec 2026-10-05-virtual-speakers-and-hot-join-design.md §5 and §6, d-7c8794-618666)."""

from __future__ import annotations

import threading
import time
from typing import ClassVar

import pytest

from aurasync.clients import required_scope
from aurasync.config import Instalacion, Parlante
from aurasync.rejoin import RejoinPolicy
from aurasync.rest import route
from aurasync.service import Service
from aurasync.session import SessionError
from tests.test_service import FakeSession, _err, _ok, _wait


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def test_policy_waits_ten_seconds_between_tries():
    clock = Clock()
    policy = RejoinPolicy(clock)
    policy.note_lost("A")
    assert policy.may_try("A")
    policy.note_try("A")
    clock.t += 9.9
    assert not policy.may_try("A")
    clock.t += 0.1  # exactly 10 s since the try: allowed (>=)
    assert policy.may_try("A")


def test_policy_gives_up_after_three_drops_in_five_minutes():
    clock = Clock()
    policy = RejoinPolicy(clock)
    policy.note_lost("A")
    policy.note_lost("A")
    assert not policy.gave_up("A")
    assert policy.may_try("A")  # two drops still earn a try
    policy.note_lost("A")
    assert policy.gave_up("A")
    assert not policy.may_try("A")
    clock.t += 300.0  # the window is inclusive: a drop of exactly 300 s ago still counts
    assert policy.gave_up("A")
    clock.t += 0.01
    assert not policy.gave_up("A")
    assert not policy.may_try("A")  # no drop left in the window: nothing to return from
    policy.note_lost("A")
    assert policy.may_try("A")
    policy.note_lost("A")
    policy.note_lost("A")
    policy.clear("A")  # the user asked: the history starts again
    assert not policy.gave_up("A")


def test_policy_never_tries_a_speaker_that_did_not_drop():
    policy = RejoinPolicy(Clock())
    assert not policy.may_try("never-played")
    policy.note_lost("A")
    policy.clear("A")  # a leave, or a join the user asked for
    assert not policy.may_try("A")


class JoinSession(FakeSession):
    """A fake whose speakers can be `absent` and that records the changes asked for."""

    absent: ClassVar[set[str]] = set()
    fail_request: ClassVar[SessionError | None] = None
    result: ClassVar[str | None] = None  # what `done` reports

    def __init__(self, *args):
        super().__init__(*args)
        self.requests = []
        self.state_overrides = dict.fromkeys(JoinSession.absent, "absent")

    def output_states(self):
        return {**super().output_states(), **self.state_overrides}

    def request_output(self, playing, done):
        if JoinSession.fail_request is not None:
            raise JoinSession.fail_request
        self.requests.append(set(playing))
        for name in self.state_overrides.copy():
            if name in playing:
                del self.state_overrides[name]
        for name in playing:
            self.state_overrides.pop(name, None)
        for p in self.installation.parlantes:
            if p.sink is not None and p.nombre not in playing:
                self.state_overrides[p.nombre] = "absent"
        threading.Thread(target=done, args=(JoinSession.result,)).start()


@pytest.fixture(autouse=True)
def _reset():
    FakeSession.instances = []
    FakeSession.fail_open = None
    FakeSession.fail_after = None
    JoinSession.absent = set()
    JoinSession.fail_request = None
    JoinSession.result = None


@pytest.fixture
def service(tmp_path):
    Instalacion(
        parlantes=[
            Parlante("Red", "bluez_output.R", pan=-0.7),
            Parlante("Blue", "bluez_output.B", pan=0.7),
            Parlante("Virt", None, pan=0.0),
        ]
    ).guardar(tmp_path / "i.json")
    svc = Service(tmp_path / "i.json", tmp_path / "p.json", session_factory=JoinSession, log=lambda _: None)
    svc.observer.view = {**svc.observer.view, "outputs": [{"sink": "bluez_output.R"}, {"sink": "bluez_output.B"}]}
    lines = []
    svc.log = lambda line, **kw: lines.append((line, kw))
    svc.lines = lines
    thread = threading.Thread(target=svc.run, daemon=True)
    thread.start()
    yield svc
    svc.handle({"v": 1, "op": "shutdown"})
    thread.join(timeout=5)


def test_join_errors(service):
    assert _err(service, op="speaker_join", speaker="Red") == "conflict"  # no session
    _ok(service, op="start")
    assert _err(service, op="speaker_join", speaker="Virt") == "conflict"  # virtual
    assert _err(service, op="speaker_join", speaker="Red") == "conflict"  # already playing
    assert _err(service, op="speaker_join", speaker="Nobody") == "not_found"
    JoinSession.instances[0].state_overrides["Blue"] = "absent"
    service.observer.view = {**service.observer.view, "outputs": [{"sink": "bluez_output.R"}]}
    assert _err(service, op="speaker_join", speaker="Blue") == "unavailable"
    service.observer.view = {**service.observer.view, "outputs": [{"sink": "bluez_output.B"}]}
    _ok(service, op="speaker_join", speaker="Blue")
    assert JoinSession.instances[0].requests == [{"Red", "Blue"}]


def test_join_refuses_a_session_error_with_its_code(service):
    _ok(service, op="start")
    JoinSession.instances[0].state_overrides["Blue"] = "absent"
    JoinSession.fail_request = SessionError("conflict", "a speaker change is already in progress")
    assert _err(service, op="speaker_join", speaker="Blue") == "conflict"


def test_leave_of_not_playing_is_conflict(service):
    assert _err(service, op="speaker_leave", speaker="Red") == "conflict"  # no session
    _ok(service, op="start")
    assert _err(service, op="speaker_leave", speaker="Virt") == "conflict"
    _ok(service, op="speaker_leave", speaker="Red")
    assert _err(service, op="speaker_leave", speaker="Red") == "conflict"
    _ok(service, op="speaker_leave", speaker="Blue")  # the last one: the session goes on
    assert JoinSession.instances[0].requests[-1] == set()


def test_a_failed_preparation_goes_to_errors_and_a_success_clears_it(service):
    _ok(service, op="start")
    JoinSession.result = "the stream did not reach its sink"
    _ok(service, op="speaker_leave", speaker="Red")
    _wait(lambda: service.errors.get("output") == "the stream did not reach its sink")
    assert any(kw.get("level") == 30 for line, kw in service.lines if "failed" in line)
    JoinSession.result = None
    JoinSession.instances[0].state_overrides["Red"] = "absent"
    _ok(service, op="speaker_join", speaker="Red")
    _wait(lambda: "output" not in service.errors)


def test_lost_speaker_returns_when_its_sink_reappears(service):
    _ok(service, op="start")
    session = JoinSession.instances[0]
    service.observer.view = {**service.observer.view, "outputs": [{"sink": "bluez_output.B"}]}
    session.lost = ["Red"]
    time.sleep(0.2)
    assert session.requests == []  # its sink is not there: nothing to do, no reconnect
    service.observer.view = {
        **service.observer.view,
        "outputs": [{"sink": "bluez_output.R"}, {"sink": "bluez_output.B"}],
    }
    _wait(lambda: session.requests)
    assert session.requests == [{"Blue", "Red"}]
    _wait(lambda: any("volvió Red" in line for line, _ in service.lines))


def test_a_speaker_that_left_does_not_return_by_itself(service):
    _ok(service, op="start")
    session = JoinSession.instances[0]
    _ok(service, op="speaker_leave", speaker="Red")  # its sink is still there, it is `absent`
    time.sleep(0.3)
    assert session.requests == [{"Blue"}]
    assert session.output_states()["Red"] == "absent"


def _lost_red(service, clock):
    service.rejoin = RejoinPolicy(clock)
    _ok(service, op="start")
    return JoinSession.instances[0]


def test_the_automatic_return_is_one_try_every_ten_seconds(service):
    clock = Clock()
    session = _lost_red(service, clock)
    session.lost = ["Red"]  # it stays lost: the fake's join does not take
    _wait(lambda: len(session.requests) == 1)
    time.sleep(0.3)  # many ticks
    assert len(session.requests) == 1
    clock.t += 10.0
    _wait(lambda: len(session.requests) == 2)
    time.sleep(0.2)
    assert len(session.requests) == 2


def test_failed_automatic_returns_count_as_drops_and_the_brake_engages(service):
    clock = Clock()
    session = _lost_red(service, clock)
    JoinSession.result = "the stream did not reach its sink"
    session.lost = ["Red"]
    _wait(lambda: len(session.requests) == 1)
    _wait(lambda: service.errors.get("output"))
    clock.t += 10.0
    _wait(lambda: len(session.requests) == 2)
    _wait(lambda: service.rejoin.gave_up("Red"))  # the drop + two failed returns
    for _ in range(3):
        clock.t += 10.0
        time.sleep(0.2)
    assert len(session.requests) == 2


def test_a_failed_manual_join_leaves_the_automatic_return_armed(service):
    clock = Clock()
    session = _lost_red(service, clock)
    service.rejoin.note_lost("Red")
    service.rejoin.note_try("Red")  # holds the automatic return off for ten seconds
    session.lost = ["Red"]
    JoinSession.result = "the stream did not reach its sink"
    _ok(service, op="speaker_join", speaker="Red")
    _wait(lambda: service.errors.get("output"))
    clock.t += 10.0
    _wait(lambda: len(session.requests) == 2)  # the manual one, then the automatic one


def test_join_route_decodes_the_name():
    msg = route("POST", "/v1/speakers/JBL%20Go%204%20Red/join", None)
    assert msg == {"v": 1, "op": "speaker_join", "speaker": "JBL Go 4 Red"}
    assert route("POST", "/v1/speakers/JBL%20Go%204%20Red/leave", None)["op"] == "speaker_leave"


def test_join_needs_control_scope():
    assert required_scope("speaker_join") == "control"
    assert required_scope("speaker_leave") == "control"
