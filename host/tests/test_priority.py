"""Raising the engine thread's priority (`priority.py`, roadmap i-7c8794-246f79).

The system calls are replaced by fakes: the tests decide what `setpriority`, RealtimeKit and
`RLIMIT_NICE` allow, and check what the module asks for, in which order, and what it reports.
"""

from __future__ import annotations

import threading
import time

import pytest

from aurasync import priority


class FakeSystem:
    """A thread whose niceness only changes through the calls the module makes."""

    def __init__(self, *, nice: int = 1, rlimit: int = 31, rtkit_ok: bool = True, min_rtkit: int = -15) -> None:
        self.nice = nice
        self.rlimit = rlimit
        self.rtkit_ok = rtkit_ok
        self.min_rtkit = min_rtkit
        self.calls: list[tuple] = []

    def getpriority(self, _which: int, _who: int) -> int:
        return self.nice

    def setpriority(self, _which: int, who: int, value: int) -> None:
        self.calls.append(("setpriority", who, value))
        if value < self.nice and value < 20 - self.rlimit:
            raise PermissionError(13, "Permission denied")
        self.nice = value

    def rtkit(self, _pid: int, tid: int, value: int) -> str | None:
        self.calls.append(("rtkit", tid, value))
        if not self.rtkit_ok:
            return "org.freedesktop.DBus.Error.ServiceUnknown"
        if value < self.min_rtkit:
            return "org.freedesktop.DBus.Error.AccessDenied"
        self.nice = value
        return None

    def raise_(self, wanted: int | None) -> priority.PriorityResult:
        return priority.raise_engine_priority(
            wanted,
            tid=4242,
            pid=4200,
            getpriority=self.getpriority,
            setpriority=self.setpriority,
            rtkit=self.rtkit,
            rlimit_nice=lambda: self.rlimit,
        )


def test_off_touches_nothing():
    system = FakeSystem()
    result = system.raise_(None)
    assert system.calls == []
    assert result == priority.PriorityResult(wanted=None, nice=1, how=None, reason=None)


def test_within_the_rlimit_it_is_setpriority_on_the_thread_alone():
    system = FakeSystem(rlimit=31)  # nice down to -11 allowed
    result = system.raise_(-10)
    assert system.calls == [("setpriority", 4242, -10)]
    assert result == priority.PriorityResult(wanted=-10, nice=-10, how="setpriority", reason=None)


def test_beyond_the_rlimit_it_asks_realtimekit():
    system = FakeSystem(rlimit=31)
    result = system.raise_(-15)
    assert system.calls == [("setpriority", 4242, -15), ("rtkit", 4242, -15)]
    assert result == priority.PriorityResult(wanted=-15, nice=-15, how="rtkit", reason=None)


def test_without_realtimekit_it_takes_what_the_rlimit_allows_and_says_why():
    system = FakeSystem(rlimit=31, rtkit_ok=False)
    result = system.raise_(-15)
    assert system.calls == [("setpriority", 4242, -15), ("rtkit", 4242, -15), ("setpriority", 4242, -11)]
    assert result.nice == -11
    assert result.how == "setpriority"
    assert "RealtimeKit" in result.reason
    assert "-11" in result.reason


def test_a_thread_already_higher_is_never_lowered():
    system = FakeSystem(nice=-18)
    result = system.raise_(-15)
    assert system.calls == []
    assert result == priority.PriorityResult(
        wanted=-15, nice=-18, how=None, reason="already at -18, above the -15 asked"
    )


def test_a_privileged_service_gets_it_by_setpriority_whatever_the_rlimit():
    system = FakeSystem(rlimit=0)
    system.setpriority = lambda _which, who, value: (
        system.calls.append(("setpriority", who, value)),
        setattr(system, "nice", value),
    )
    result = system.raise_(-15)
    assert system.calls == [("setpriority", 4242, -15)]
    assert result == priority.PriorityResult(wanted=-15, nice=-15, how="setpriority", reason=None)


def test_with_nothing_allowed_it_stays_and_says_why():
    system = FakeSystem(nice=1, rlimit=0, rtkit_ok=False)
    result = system.raise_(-15)
    assert result.nice == 1
    assert result.how is None
    assert "RLIMIT_NICE" in result.reason


def test_the_result_is_what_the_system_reads_back_not_what_was_asked():
    """Verified, not assumed (CLAUDE.md): a call that "succeeds" but leaves another value is reported."""
    system = FakeSystem(rlimit=31)
    system.setpriority = lambda _which, who, value: system.calls.append(("setpriority", who, value))  # a no-op
    result = system.raise_(-10)
    assert result.nice == 1
    assert result.how is None
    assert "reads back 1" in result.reason


@pytest.mark.parametrize("value", [-21, 20, 3.5, "high", True])
def test_only_a_niceness_from_minus_20_to_19_is_valid(value):
    assert priority.check_nice(value) is not None


@pytest.mark.parametrize("value", [None, -20, -15, 0, 19])
def test_null_and_valid_niceness_pass(value):
    assert priority.check_nice(value) is None


def test_the_real_calls_build_the_documented_busctl_command(monkeypatch):
    seen = {}

    def run(cmd, **_kwargs):
        seen["cmd"] = cmd

        class Done:
            returncode = 0
            stderr = ""

        return Done()

    monkeypatch.setattr(priority.subprocess, "run", run)
    assert priority.rtkit_high_priority(10, 11, -15) is None
    assert seen["cmd"] == [
        "busctl",
        "--system",
        "call",
        "--",
        "org.freedesktop.RealtimeKit1",
        "/org/freedesktop/RealtimeKit1",
        "org.freedesktop.RealtimeKit1",
        "MakeThreadHighPriorityWithPID",
        "tti",
        "10",
        "11",
        "-15",
    ]


def _config(tmp_path, **extra):
    import json

    path = tmp_path / "service.json"
    path.write_text(json.dumps({"token": "x" * 40, **extra}))
    path.chmod(0o600)
    return path


def test_service_json_takes_engine_nice_and_refuses_a_bad_one(tmp_path):
    from aurasync.service import ConfigError, load_config

    assert load_config(_config(tmp_path)).engine_nice == -15  # the default, also for older files
    assert load_config(_config(tmp_path, engine_nice=None)).engine_nice is None
    assert load_config(_config(tmp_path, engine_nice=-15)).engine_nice == -15
    with pytest.raises(ConfigError, match="engine_nice"):
        load_config(_config(tmp_path, engine_nice="high"))


def test_run_raises_the_engine_thread_alone_and_reports_it(tmp_path, monkeypatch):
    from aurasync.config import Instalacion, Parlante
    from aurasync.service import Service
    from tests.test_service import FakeSession

    asked = {}

    def fake(wanted):
        asked["wanted"], asked["thread"] = wanted, threading.get_native_id()
        return priority.PriorityResult(
            wanted, -11, "setpriority", "RealtimeKit refused -15 (x); RLIMIT_NICE allows -11"
        )

    monkeypatch.setattr(priority, "raise_engine_priority", fake)
    Instalacion(parlantes=[Parlante("A", "s0")]).guardar(tmp_path / "inst.json")
    svc = Service(
        tmp_path / "inst.json",
        tmp_path / "presets.json",
        session_factory=FakeSession,
        log=lambda _: None,
        engine_nice=-15,
    )
    thread = threading.Thread(target=svc.run, daemon=True)
    thread.start()
    try:
        # `state` answers from the caller's thread with the last snapshot: wait for the loop's first.
        deadline = time.monotonic() + 5
        while True:
            reply = svc.handle({"v": 1, "op": "state"})
            if reply["result"]["health"]["engine_priority"] is not None or time.monotonic() > deadline:
                break
            time.sleep(0.01)
        assert asked["wanted"] == -15
        assert asked["thread"] == thread.native_id  # the engine thread, not the caller's
        assert reply["result"]["health"]["engine_priority"] == {
            "wanted": -15,
            "nice": -11,
            "how": "setpriority",
            "reason": "RealtimeKit refused -15 (x); RLIMIT_NICE allows -11",
        }
    finally:
        svc.handle({"v": 1, "op": "shutdown"})
        thread.join(timeout=5)
