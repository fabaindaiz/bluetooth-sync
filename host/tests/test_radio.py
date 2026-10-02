"""The radio monitor: the packets PipeWire's Bluetooth sink throws away (spec 2026-10-02 §3.1).

The journal lines are built the way the code writes them, not invented: each fixture cites
the line of PipeWire 1.6.9 that prints its MESSAGE body, and the envelope is WirePlumber
0.5.17's `wp_log_fields_write_to_journal()` (lib/wp/log.c): for a topic at debug level,
MESSAGE = "<level letter> <topic>[<file>:<line>:<func>]: <text>", plus the fields TOPIC,
CODE_FILE, CODE_LINE, CODE_FUNC, PRIORITY and SYSLOG_IDENTIFIER. `journalctl -o json`
adds `__REALTIME_TIMESTAMP` (µs) and `_SYSTEMD_USER_UNIT`. Seen in a real journal: still
to verify on PC-Ryzen5 (`probes/14-microcortes/README.md`).
"""

import json
import subprocess
from datetime import datetime, timedelta, timezone

from aurasync import radio
from aurasync.cuts import CutLog, likely_cause

SINK = "0x5581d6f1e2a0"
SINK_2 = "0x5581d7a03c10"
TRANSPORT = "0x5581d6c4b8f0"
TRANSPORT_2 = "0x5581d6c51e00"
RED = "/org/bluez/hci0/dev_90_F2_60_DA_66_6D/sep1/fd3"
BLUE = "/org/bluez/hci0/dev_88_92_CC_68_91_C0/sep1/fd4"
T0 = 1_790_000_000.0


def entry(text, *, topic, file, line, func, t=T0, level="D"):
    return {
        "__REALTIME_TIMESTAMP": str(int(t * 1e6)),
        "_SYSTEMD_USER_UNIT": "wireplumber.service",
        "SYSLOG_IDENTIFIER": "wireplumber",
        "PRIORITY": "7" if level == "D" else "4",
        "TOPIC": topic,
        "CODE_FILE": f"../spa/plugins/bluez5/{file}",
        "CODE_LINE": str(line),
        "CODE_FUNC": func,
        "MESSAGE": f"{level} {topic}[{file}:{line}:{func}]: {text}",
    }


def reduce(bitpool, t=T0, sink=SINK):
    # media-sink.c:1090  spa_log_debug(this->log, "%p: reduce bitpool: %i", this, res);
    return entry(
        f"{sink}: reduce bitpool: {bitpool}",
        topic="spa.bluez5.sink.media",
        file="media-sink.c",
        line=1090,
        func="flush_data",
        t=t,
    )


def increase(bitpool, t=T0, sink=SINK):
    # media-sink.c:1164  spa_log_debug(this->log, "%p: increase bitpool: %i", this, res);
    return entry(
        f"{sink}: increase bitpool: {bitpool}",
        topic="spa.bluez5.sink.media",
        file="media-sink.c",
        line=1164,
        func="flush_data",
        t=t,
    )


def sink_transport(sink=SINK, transport=TRANSPORT, t=T0):
    # media-sink.c:2462  "%p: transport %p state %d->%d", this, this->transport, old, state
    return entry(
        f"{sink}: transport {transport} state 1->2",
        topic="spa.bluez5.sink.media",
        file="media-sink.c",
        line=2462,
        func="transport_state_changed",
        t=t,
    )


def acquired(transport=TRANSPORT, path=RED, t=T0):
    # bluez5-dbus.c:4181  "transport %p: Acquired %s, fd %d MTU %d:%d", transport, path, fd, read_mtu, write_mtu
    return entry(
        f"transport {transport}: Acquired {path}, fd 57 MTU 672:895",
        topic="spa.bluez5",
        file="bluez5-dbus.c",
        line=4181,
        func="transport_acquire_reply",
        t=t,
    )


def state_changed(transport=TRANSPORT, path=RED, t=T0):
    # bluez5-dbus.c:3309  "transport %p: %s state changed %d -> %d", transport, transport->path, old, state
    return entry(
        f"transport {transport}: {path} state changed 1 -> 2",
        topic="spa.bluez5",
        file="bluez5-dbus.c",
        line=3309,
        func="spa_bt_transport_set_state",
        t=t,
    )


def terminated(sink=SINK, path=RED, t=T0):
    # media-sink.c:1332  spa_log_warn(this->log, "%p: connection (%s) terminated unexpectedly", this, path);
    return entry(
        f"{sink}: connection ({path}) terminated unexpectedly",
        topic="spa.bluez5.sink.media",
        file="media-sink.c",
        line=1332,
        func="media_on_flush_error",
        t=t,
        level="W",
    )


class Clock:
    def __init__(self, t=T0):
        self.t = t

    def __call__(self):
        return self.t


NAMES = {"90:F2:60:DA:66:6D": "Red", "88:92:CC:68:91:C0": "Blue"}


def monitor(clock=None, **kwargs):
    m = radio.RadioMonitor(clock=clock or Clock(), speaker_name=NAMES.get, which=lambda _: None, **kwargs)
    m._started_at = (clock or Clock()).t  # as if start() had run, without a journal  # noqa: SLF001
    return m


# -- parse ------------------------------------------------------------------------------


def test_parse_reads_each_line_the_code_prints():
    e = radio.parse(reduce(38))
    assert (e.kind, e.sink, e.bitpool, e.t) == ("reduce", SINK, 38, T0)
    assert radio.parse(increase(40)).kind == "increase"
    e = radio.parse(sink_transport())
    assert (e.kind, e.sink, e.transport) == ("sink_transport", SINK, TRANSPORT)
    e = radio.parse(acquired())
    assert (e.kind, e.transport, e.address, e.write_mtu) == ("transport_path", TRANSPORT, "90:F2:60:DA:66:6D", 895)
    e = radio.parse(state_changed(path=BLUE))
    assert (e.kind, e.address) == ("transport_path", "88:92:CC:68:91:C0")
    e = radio.parse(terminated())
    assert (e.kind, e.sink, e.address) == ("terminated", SINK, "90:F2:60:DA:66:6D")


def test_parse_ignores_what_is_not_the_radio():
    other = entry("Create node: bluez_output.90_F2_60_DA_66_6D.1", topic="s-monitors-bluez", file="x", line=1, func="f")
    assert radio.parse(other) is None
    # The A2DP *source* prints the same transport line (media-source.c:2047): not a speaker.
    source = sink_transport()
    source["TOPIC"] = "spa.bluez5.source.media"
    assert radio.parse(source) is None


def test_parse_without_the_topic_field_reads_it_from_the_message():
    e = reduce(36)
    del e["TOPIC"]
    assert radio.parse(e).bitpool == 36
    e = sink_transport()
    del e["TOPIC"]
    e["MESSAGE"] = e["MESSAGE"].replace("spa.bluez5.sink.media", "spa.bluez5.source.media")
    assert radio.parse(e) is None


def test_a_codec_without_bitpool_still_drops_the_packet():
    """AAC has no reduce_bitpool: the line says 0 (media-sink.c:1086-1090), the drop is real."""
    e = radio.parse(reduce(0))
    assert (e.kind, e.bitpool) == ("reduce", None)


def test_a_non_utf8_message_comes_as_bytes():
    e = reduce(30)
    e["MESSAGE"] = list(e["MESSAGE"].encode())
    assert radio.parse(e).bitpool == 30


# -- the monitor ---------------------------------------------------------------------------


def test_the_pointer_maps_to_the_speaker_through_the_transport():
    drops = []
    m = monitor(on_drop=drops.append)
    m.feed(acquired())
    m.feed(sink_transport())
    m.feed(reduce(38))
    assert drops == [radio.Drop("Red", True, "90:F2:60:DA:66:6D", SINK, 38, T0)]
    s = m.snapshot()["speakers"]
    assert list(s) == ["Red"]
    assert s["Red"]["identified"] is True
    assert s["Red"]["write_mtu"] == 895


def test_without_mapping_the_drops_stay_per_pointer_and_move_when_it_appears():
    m = monitor()
    m.feed(reduce(38))
    m.feed(reduce(36, t=T0 + 1))
    s = m.snapshot()["speakers"]
    assert list(s) == [SINK]
    assert s[SINK]["identified"] is False
    m.feed(state_changed(path=BLUE))
    m.feed(sink_transport())
    s = m.snapshot()["speakers"]
    assert list(s) == ["Blue"]
    assert s["Blue"]["drops_total"] == 2
    assert s["Blue"]["bitpool"] == 36


def test_a_terminated_link_names_its_sink_directly():
    m = monitor()
    m.feed(terminated(sink=SINK_2, path=BLUE))
    m.feed(reduce(30, sink=SINK_2))
    assert list(m.snapshot()["speakers"]) == ["Blue"]


def test_a_speaker_without_a_name_is_called_by_its_address():
    m = radio.RadioMonitor(clock=Clock(), which=lambda _: None)
    m.feed(acquired(path=BLUE))
    m.feed(sink_transport())
    m.feed(reduce(30))
    assert list(m.snapshot()["speakers"]) == ["88:92:CC:68:91:C0"]


def test_drops_per_minute_count_only_the_last_60_seconds():
    clock = Clock()
    m = monitor(clock)
    m.feed(acquired())
    m.feed(sink_transport())
    for k in range(6):
        m.feed(reduce(38, t=T0 + k))
    clock.t = T0 + 5
    assert m.snapshot()["speakers"]["Red"]["drops_per_min"] is None  # 5 s: too little to say
    clock.t = T0 + 30
    red = m.snapshot()["speakers"]["Red"]
    assert red["drops_per_min"] == 12.0  # 6 in 30 s
    clock.t = T0 + 63
    m.feed(reduce(36, t=T0 + 62))
    red = m.snapshot()["speakers"]["Red"]
    assert red["drops_60s"] == 4  # T0+3, T0+4, T0+5 and T0+62
    assert red["drops_per_min"] == 4.0
    assert red["drops_total"] == 7
    assert red["last_drop_s"] == 1.0


def test_bitpool_median_max_and_current():
    clock = Clock()
    m = monitor(clock)
    m.feed(acquired())
    m.feed(sink_transport())
    for k, b in enumerate([40, 38, 36, 37, 38]):
        m.feed((increase if k in {0, 3, 4} else reduce)(b, t=T0 + k))
    red = m.snapshot()["speakers"]["Red"]
    assert (red["bitpool"], red["bitpool_max"], red["bitpool_median_60s"]) == (38, 40, 38)
    clock.t = T0 + 62.5  # the 40, 38 and 36 leave the window
    red = m.snapshot()["speakers"]["Red"]
    assert red["bitpool_median_60s"] == 37.5
    assert red["bitpool_max"] == 40


def test_old_lines_teach_the_mapping_but_are_not_counted():
    """The mapping lines are printed when playback starts, maybe long before the monitor."""
    drops = []
    m = monitor(on_drop=drops.append)
    m.feed(acquired(t=T0 - 3600))
    m.feed(sink_transport(t=T0 - 3600))
    m.feed(reduce(38, t=T0 - 3500))
    assert drops == []
    m.feed(reduce(36, t=T0 + 1))
    assert m.snapshot()["speakers"]["Red"]["drops_total"] == 1


def test_a_failing_callback_does_not_stop_the_monitor():
    def boom(_):
        raise RuntimeError

    m = monitor(on_drop=boom)
    m.feed(reduce(38))
    m.feed(reduce(36, t=T0 + 1))
    assert m.snapshot()["speakers"][SINK]["drops_total"] == 2


def test_without_journalctl_the_radio_is_unavailable_with_a_reason():
    m = radio.RadioMonitor(which=lambda _: None)
    m.start()
    s = m.snapshot()
    assert s["available"] is False
    assert "journalctl" in s["reason"]
    assert s["speakers"] == {}
    m.stop()
    m.stop()


def test_silence_means_the_log_is_off_or_nothing_plays():
    clock = Clock()
    m = monitor(clock)
    assert m.available is False
    assert "esperando" in m.reason
    clock.t = T0 + 20
    assert "ninguna línea" in m.reason
    m.feed(increase(40, t=T0 + 20))
    assert m.available is True
    assert m.reason is None
    clock.t = T0 + 40
    assert m.available is False
    assert "hace 20 s" in m.reason


class FakeProcess:
    def __init__(self, lines, stderr=""):
        self.stdout = iter(lines)
        self.stderr = type("E", (), {"read": lambda _self: stderr})()
        self.terminated = False

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):  # noqa: ARG002
        return 0

    def kill(self):
        pass


def test_the_thread_follows_journalctl_and_reports_its_end():
    lines = [json.dumps(e) + "\n" for e in (acquired(), sink_transport(), reduce(38, t=T0 + 1))] + ["not json\n"]
    calls = []
    process = FakeProcess(lines, stderr="Failed to iterate through journal")

    def popen(args, **_):
        calls.append(args)
        return process

    drops = []
    m = radio.RadioMonitor(
        clock=Clock(), speaker_name=NAMES.get, which=lambda n: f"/usr/bin/{n}", popen=popen, on_drop=drops.append
    )
    m.start()
    m._thread.join(2)  # noqa: SLF001
    assert calls == [
        ["/usr/bin/journalctl", "--user", "-f", "-o", "json", "-n", "2000", "-u", "wireplumber", "-u", "pipewire"]
    ]
    assert [d.speaker for d in drops] == ["Red"]
    s = m.snapshot()
    assert s["available"] is False
    assert "Failed to iterate" in s["reason"]
    m.stop()
    assert process.terminated


def test_to_cutlog_writes_a_radio_cut_per_drop():
    cuts = CutLog(Clock())
    add = radio.to_cutlog(cuts)
    add(radio.Drop("Red", True, "90:F2:60:DA:66:6D", SINK, 38, T0))
    add(radio.Drop(SINK_2, False, None, SINK_2, None, T0))
    events = cuts.summary()["events"]
    assert [(e["kind"], e["where"], e["fault"]) for e in events] == [
        ("radio", "Red", True),
        ("radio", f"enlace sin identificar {SINK_2}", True),
    ]
    assert events[0]["detail"] == "bitpool 38"
    assert events[1]["identified"] is False


# -- the cut log reads the radio ---------------------------------------------------------------


def cut(kind, where="x", t=10.0, **extra):
    return {"kind": kind, "where": where, "t": t, "context": {}, "fault": True, **extra}


def test_cuts_that_are_radio_drops_point_at_that_link():
    reading = likely_cause([cut("radio", "Red", t=10.0 + k) for k in range(3)])
    assert reading.startswith("radio: el enlace Bluetooth de Red descartó paquetes (3 veces)")
    reading = likely_cause([cut("radio", "Red"), cut("radio", "Blue"), cut("radio", "Red")])
    assert "Red 2, Blue 1" in reading
    reading = likely_cause([cut("radio", "enlace sin identificar 0x1", identified=False)])
    assert reading.startswith("radio: un enlace Bluetooth sin identificar descartó paquetes (1 veces)")


def test_cuts_that_coincide_with_radio_drops_are_the_radios():
    """An xrun 0.4 s after a drop of Red: the radio, not "only Red cuts and the engine was on time"."""
    faults = [cut("radio", "Red", t=10.0), cut("xrun", "Red", t=10.4), cut("underrun", "salida", t=10.6)]
    assert likely_cause(faults).startswith("radio: el enlace Bluetooth de Red")
    far = [cut("radio", "Red", t=10.0), cut("underrun", "salida", t=30.0), cut("underrun", "salida", t=40.0)]
    assert not (likely_cause(far) or "").startswith("radio")


def test_scanning_still_explains_radio_drops_first():
    faults = [{**cut("radio", "Red"), "context": {"bt_discovering": True}}] * 3
    assert "Bluetooth buscaba" in likely_cause(faults)


# -- the log level: written down before it is changed ------------------------------------------


class System:
    """`wpctl` and `pw-metadata` with WirePlumber's behaviour, recording what ran and in what order."""

    def __init__(self, changes, level=None):
        self.changes = changes
        self.level = level
        self.log = []

    def __call__(self, args, timeout):
        assert timeout <= 5
        name = args[0].rsplit("/", 1)[-1]
        written = self.changes.read_text().splitlines() if self.changes.exists() else []
        self.log.append((name, args[1:], len(written)))
        if name == "pw-metadata":
            out = "Found \"settings\" metadata 33\nupdate: id:0 key:'clock.force-quantum' value:'0' type:''\n"
            if self.level is not None:
                out += f"update: id:47 key:'log.level' value:'{self.level}' type:''\n"
            return subprocess.CompletedProcess(args, 0, stdout=out, stderr="")
        if name == "wpctl":
            self.level = None if args[2] == "-" else args[2]
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        raise AssertionError(args)


def level_helper(tmp_path, system):
    stamp = datetime(2026, 10, 2, 15, 0, tzinfo=timezone(timedelta(hours=-3)))
    return radio.LogLevel(
        tmp_path / "cambios-de-sistema.txt", run=system, which=lambda n: f"/usr/bin/{n}", now=lambda: stamp
    )


def test_the_change_is_written_down_before_wpctl_runs(tmp_path):
    changes = tmp_path / "cambios-de-sistema.txt"
    system = System(changes)
    level = level_helper(tmp_path, system)
    status = level.enable()
    wpctl = [entry for entry in system.log if entry[0] == "wpctl"]
    assert wpctl == [("wpctl", ["set-log-level", "spa.bluez5.sink.media:D,spa.bluez5:D,2"], 1)]
    assert changes.read_text().splitlines() == [
        "2026-10-02T15:00:00-03:00 · [aurasync radio_log] cambio temporal: "
        "wpctl set-log-level spa.bluez5.sink.media:D,spa.bluez5:D,2 (revertir: wpctl set-log-level -)"
    ]
    assert status["active"] is True
    assert status["verified"] is True
    assert status["heavy"] is False


def test_enable_is_idempotent_and_restore_puts_back_the_previous_level(tmp_path):
    changes = tmp_path / "cambios-de-sistema.txt"
    system = System(changes, level="I")
    level = level_helper(tmp_path, system)
    level.enable()
    level.enable()
    assert system.level == "spa.bluez5.sink.media:D,spa.bluez5:D,I"
    assert len(changes.read_text().splitlines()) == 1
    level.restore()
    level.restore()
    assert system.level == "I"
    assert [line.split(" · ")[1] for line in changes.read_text().splitlines()] == [
        "[aurasync radio_log] cambio temporal: wpctl set-log-level spa.bluez5.sink.media:D,spa.bluez5:D,I "
        "(revertir: wpctl set-log-level I)",
        "[aurasync radio_log] revertido: wpctl set-log-level I",
    ]
    assert level.status()["active"] is False


def test_heavy_is_flagged_and_reverts_to_the_level_before_light(tmp_path):
    system = System(tmp_path / "cambios-de-sistema.txt")
    level = level_helper(tmp_path, system)
    level.enable()
    assert level.enable("heavy")["heavy"] is True
    assert system.level == "4"
    level.restore()
    assert system.level is None  # `wpctl set-log-level -`: WirePlumber goes back to "2"


def test_a_change_left_by_a_killed_run_is_reverted_at_start(tmp_path):
    system = System(tmp_path / "cambios-de-sistema.txt")
    level_helper(tmp_path, system).enable()  # and the process dies without restore()
    assert system.level is not None
    fresh = level_helper(tmp_path, system)
    assert fresh.recover() is True
    assert system.level is None
    assert fresh.recover() is False
    assert "revertido (al arrancar)" in (tmp_path / "cambios-de-sistema.txt").read_text()


def test_without_wpctl_nothing_is_written(tmp_path):
    level = radio.LogLevel(tmp_path / "c.txt", run=System(tmp_path / "c.txt"), which=lambda _: None)
    status = level.enable()
    assert status["active"] is False
    assert "wpctl" in status["error"]
    assert not (tmp_path / "c.txt").exists()
