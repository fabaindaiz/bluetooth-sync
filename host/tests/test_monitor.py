"""The headphone monitor (spec 2026-10-04-headphone-monitor-design.md): what can be checked
without PipeWire. Where its audio really goes is checked on PC-Ryzen5 (spec §3)."""

import threading
import time

import numpy as np
import pytest

from aurasync import monitor


def test_a_speaker_on_the_left_folds_only_to_the_left():
    x = np.ones(64)
    left, right = monitor.fold({"a": x}, {"a": -90.0})
    assert np.allclose(left, 1.0)
    assert np.allclose(right, 0.0)


def test_a_speaker_in_front_folds_with_equal_power():
    x = np.ones(64)
    left, right = monitor.fold({"a": x}, {"a": 0.0})
    assert np.allclose(left, right)
    assert np.allclose(left**2 + right**2, 1.0)


def test_a_speaker_without_an_angle_folds_to_the_middle():
    left, right = monitor.fold({"a": np.ones(8)}, {})
    assert np.allclose(left, right)


def test_sofa_azimuth_turns_counter_clockwise():
    assert monitor.sofa_azimuth(0.0) == 0.0
    assert monitor.sofa_azimuth(-60.0) == 60.0  # front left
    assert monitor.sofa_azimuth(60.0) == 300.0  # front right
    assert monitor.sofa_azimuth(180.0) == 180.0


def test_the_binaural_module_has_one_spatializer_per_speaker_into_both_mixers():
    args = monitor.binaural_args(["a", "b", "c"], {"a": -60.0, "b": 60.0, "c": 180.0}, "mon", "headphones", "/h.sofa")
    assert args.count("label = spatializer") == 3
    assert '"Azimuth" = 60.0' in args
    assert '"Azimuth" = 300.0' in args
    for i in range(3):
        assert f'output = "sp{i}:Out L" input = "mixL:In {i + 1}"' in args
        assert f'output = "sp{i}:Out R" input = "mixR:In {i + 1}"' in args
    assert "audio.position = [ AUX0 AUX1 AUX2 ]" in args
    assert 'target.object = "headphones"' in args
    for flag in ("node.dont-move = true", "node.dont-reconnect = true", "node.dont-fallback = true"):
        assert flag in args
    assert 'filename = "/h.sofa"' in args


def test_more_speakers_than_a_mixer_takes_are_refused():
    names = [f"s{i}" for i in range(monitor.MAX_INPUTS + 1)]
    with pytest.raises(ValueError, match="mixer"):
        monitor.binaural_args(names, {}, "mon", "headphones", "/h.sofa")


@pytest.mark.parametrize("mode", ["stereo", "mix", "binaural"])
def test_a_target_that_would_loop_back_is_refused(mode):
    settings = monitor.MonitorSettings(mode=mode, target="bluez_output.red")
    with pytest.raises(monitor.MonitorError, match="loop"):
        monitor.check_target(settings, forbidden={"bluez_output.red", "aurasync"})


def test_the_settings_are_validated():
    with pytest.raises(monitor.MonitorError):
        monitor.MonitorSettings.from_json({"mode": "loud"})
    with pytest.raises(monitor.MonitorError):
        monitor.MonitorSettings.from_json({"mode": "stereo", "gain_db": 6})
    s = monitor.MonitorSettings.from_json({"mode": "mix", "target": "x", "gain_db": -20})
    assert s.to_json() == {
        "mode": "mix",
        "target": "x",
        "gain_db": -20.0,
        "volume_control": "device",
        "device_volume_pct": None,
    }
    assert monitor.MonitorSettings().mode == "off"


def test_what_goes_out_per_mode():
    pair = (np.full(4, 0.5), np.full(4, -0.5))
    blocks = {"a": np.ones(4), "b": np.ones(4)}
    angles = {"a": -90.0, "b": 90.0}
    stereo = monitor.frame("stereo", pair, blocks, ["a", "b"], angles, gain=1.0)
    assert stereo.shape == (4, 2)
    assert np.allclose(stereo[:, 0], 0.5)
    mix = monitor.frame("mix", pair, blocks, ["a", "b"], angles, gain=0.5)
    assert np.allclose(mix[:, 0], 0.5)
    assert np.allclose(mix[:, 1], 0.5)
    binaural = monitor.frame("binaural", pair, blocks, ["a", "b"], angles, gain=1.0)
    assert binaural.shape == (4, 2)  # one channel per speaker, in order
    silent = monitor.frame("stereo", None, blocks, ["a", "b"], angles, gain=1.0)
    assert np.allclose(silent, 0.0)


def _node(ident, name, media_class="Audio/Sink", description=None):
    props = {"node.name": name, "media.class": media_class}
    if description:
        props["node.description"] = description
    return {"type": "PipeWire:Interface:Node", "id": ident, "info": {"props": props}}


def test_the_candidate_sinks_leave_out_speakers_and_aurasync():
    dump = [
        _node(1, "alsa_out", description="PC"),
        _node(2, "bluez_output.red"),
        _node(3, "aurasync"),
        _node(4, "mic", "Audio/Source"),
        _node(5, "bluez_output.phones", description="Phones"),
    ]
    sinks = monitor.list_sinks(dump)
    assert [s["node"] for s in sinks] == ["alsa_out", "bluez_output.red", "aurasync", "bluez_output.phones"]
    assert monitor.candidates(sinks, forbidden={"bluez_output.red", "aurasync"}) == [
        {"node": "alsa_out", "description": "PC"},
        {"node": "bluez_output.phones", "description": "Phones"},
    ]


def _graph(stream_to: str | None):
    """pw-dump of: our pw-play (pid 42) → `stream_to`; the filter's output → headphones."""
    dump = [
        {"type": "PipeWire:Interface:Client", "id": 9, "info": {"props": {"application.process.id": 42}}},
        {
            "type": "PipeWire:Interface:Node",
            "id": 10,
            "info": {"props": {"node.name": "pw-play", "client.id": 9, "media.class": "Stream/Output/Audio"}},
        },
        _node(11, "mon"),
        _node(12, "mon_out", "Stream/Output/Audio"),
        _node(13, "headphones"),
        _node(14, "elsewhere"),
        {
            "type": "PipeWire:Interface:Link",
            "id": 20,
            "info": {"props": {"link.output.node": 12, "link.input.node": 13}},
        },
    ]
    if stream_to:
        ident = {"mon": 11, "elsewhere": 14, "headphones": 13}[stream_to]
        dump.append(
            {
                "type": "PipeWire:Interface:Link",
                "id": 21,
                "info": {"props": {"link.output.node": 10, "link.input.node": ident}},
            }
        )
    return dump


class _Live(monitor.MonitorOutput):
    pid = 42


def test_binaural_reaches_the_headphones_only_if_the_stream_enters_the_filter():
    out = _Live(monitor.MonitorSettings(mode="binaural", target="headphones"), ["a"], {}, 48000, "mon")
    assert out.routing(_graph("mon")) == "headphones"
    assert out.routing(_graph("elsewhere")) == "elsewhere"  # moved away from the filter
    assert out.routing(_graph(None)) is None


def test_stereo_says_where_the_stream_went():
    out = _Live(monitor.MonitorSettings(mode="stereo", target="headphones"), ["a"], {}, 48000, "mon")
    assert out.routing(_graph("headphones")) == "headphones"
    assert out.routing(_graph("elsewhere")) == "elsewhere"


class _StalledSink:
    """A writer that never returns until released: pw-play stuck."""

    def __init__(self):
        self.release = threading.Event()
        self.written = 0

    def write(self, data: bytes) -> None:  # noqa: ARG002 - the writer's signature
        self.release.wait(5)
        self.written += 1


def test_push_never_blocks_the_engine_and_counts_what_it_drops():
    sink = _StalledSink()
    writer = monitor.Writer(sink.write, depth=2)
    try:
        started = time.perf_counter()
        for _ in range(50):
            writer.push(np.zeros((1024, 2)))
        assert time.perf_counter() - started < 0.05
        assert writer.drops >= 50 - 2 - 1
    finally:
        sink.release.set()
        writer.close()


# -- the cushion: the pipe of the monitor's pw-play never runs dry (2026-10-05) ----------

BLOCK = 4096
QUANTUM = monitor.DRIVER_QUANTUM_FRAMES


class _DriverSim:
    """A pipe drained by one driver quantum per cycle, fed by a `Cushion` (or by nothing).
    pw-play keeps one quantum in its own ring, so a cycle is only served if the pipe holds two:
    with nothing written ahead that starves every other cycle, as `pw-top` showed."""

    def __init__(self, cushion, level=0):
        self.cushion, self.level, self.starved = cushion, level, 0

    def cycle(self):
        if self.level < 2 * QUANTUM:
            self.starved += 1
        self.level = max(0, self.level - QUANTUM)

    def block(self):
        if self.cushion is None:
            self.level += BLOCK
            return
        silence, write = self.cushion.plan(self.level)
        self.level += silence + (BLOCK if write else 0)

    def run(self, cycles):
        """Blocks every two cycles on average, but the writer thread is late every other time:
        block k lands after cycle 2k + (k % 2), so gaps alternate between 3 cycles and 1."""
        due = {2 * k + k % 2 for k in range(cycles)}
        for i in range(cycles):
            self.cycle()  # the driver asks first; the block lands just after
            if i in due:
                self.block()


def test_the_target_is_a_block_plus_a_quantum_capped_at_400_ms():
    assert monitor.Cushion(BLOCK, 48000).target_frames == BLOCK + 2048
    assert monitor.Cushion(48000, 48000).target_frames == 19200
    assert round(monitor.Cushion(BLOCK, 48000).target_ms) == 128


def test_without_a_cushion_the_pipe_runs_dry_in_a_quarter_of_the_cycles():
    sim = _DriverSim(None)
    sim.run(1000)
    assert sim.starved >= 200


def test_with_the_cushion_the_pipe_never_runs_dry():
    cushion = monitor.Cushion(BLOCK, 48000)
    sim = _DriverSim(cushion, level=cushion.target_frames)
    sim.run(1000)
    assert sim.starved == 0
    assert cushion.refills == 0


def test_a_long_pause_is_one_refill_not_many():
    cushion = monitor.Cushion(BLOCK, 48000)
    sim = _DriverSim(cushion, level=cushion.target_frames)
    sim.run(200)
    for _ in range(100):  # no blocks: the pipe drains
        sim.cycle()
    sim.block()  # the music resumes: this block finds the pipe empty
    sim.starved = 0
    sim.run(1000)
    assert cushion.refills == 1
    assert sim.starved == 0


def test_a_backlog_is_trimmed_and_the_level_stays_bounded():
    cushion = monitor.Cushion(BLOCK, 48000)
    sim = _DriverSim(cushion, level=cushion.target_frames)
    peak = 0
    seen = []
    for _ in range(500):  # a block every cycle: twice what the driver drains
        seen.append(sim.level)  # what the level read says before the block is written
        sim.block()
        sim.cycle()
        peak = max(peak, sim.level)
    assert cushion.trims > 0
    assert cushion.refills == 0
    assert max(seen) <= cushion.target_frames + 2 * BLOCK + QUANTUM
    assert peak <= cushion.target_frames + 3 * BLOCK


def test_an_unreadable_level_writes_the_block_unchanged():
    cushion = monitor.Cushion(BLOCK, 48000)
    assert cushion.plan(None) == (0, True)
    assert cushion.refills == cushion.trims == 0
    assert cushion.level_frames is None


class _Stdin:
    def __init__(self):
        self.chunks = []

    def write(self, data):
        self.chunks.append(data)

    def flush(self):
        pass


def test_the_writer_refills_with_silence_before_the_block():
    stdin, level = _Stdin(), [0]
    cushion = monitor.Cushion(BLOCK, 48000)
    writer = monitor.Writer(stdin.write, cushion=cushion, level=lambda: level[0], channels=2)
    try:
        writer.push(np.ones((BLOCK, 2)))
        for _ in range(100):
            if len(stdin.chunks) >= 2:
                break
            time.sleep(0.01)
    finally:
        writer.close()
    assert len(stdin.chunks) == 2
    assert stdin.chunks[0] == bytes(4 * 2 * cushion.target_frames)
    assert len(stdin.chunks[1]) == 4 * 2 * BLOCK
    assert cushion.refills == 0  # the first read after the open is the priming, not a starvation


def test_the_writer_drops_a_block_over_the_backlog():
    stdin = _Stdin()
    cushion = monitor.Cushion(BLOCK, 48000)
    writer = monitor.Writer(stdin.write, cushion=cushion, level=lambda: cushion.target_frames + 3 * BLOCK, channels=2)
    try:
        writer.push(np.ones((BLOCK, 2)))
        for _ in range(100):
            if cushion.trims:
                break
            time.sleep(0.01)
    finally:
        writer.close()
    assert cushion.trims == 1
    assert stdin.chunks == []


class _FakePlay:
    def __init__(self):
        self.stdin = _Stdin()
        self.pid = 1

    def poll(self):
        return None


def test_the_open_writes_the_target_in_silence_first(monkeypatch):
    play = _FakePlay()
    monkeypatch.setattr(monitor.subprocess, "Popen", lambda *_a, **_k: play)
    out = monitor.MonitorOutput(monitor.MonitorSettings(mode="stereo", target="hp"), ["a"], {}, 48000, "s", block=BLOCK)
    out.open()
    try:
        assert play.stdin.chunks[0] == bytes(4 * 2 * (BLOCK + 2048))
        assert out.cushion.target_frames == BLOCK + 2048
    finally:
        out.writer.close()


def test_the_first_read_after_the_open_is_priming_not_a_refill():
    cushion = monitor.Cushion(BLOCK, 48000)
    silence, write = cushion.plan(0)  # the open-time silence drained while the routing was checked
    assert (silence, write) == (cushion.target_frames, True)
    assert cushion.refills == 0
    assert cushion.plan(cushion.target_frames) == (0, True)
    assert cushion.plan(0) == (cushion.target_frames, True)  # a later starvation counts
    assert cushion.refills == 1


def test_the_play_process_is_opened_unbuffered(monkeypatch):
    play, seen = _FakePlay(), {}

    def popen(*_a, **kw):
        seen.update(kw)
        return play

    monkeypatch.setattr(monitor.subprocess, "Popen", popen)
    out = monitor.MonitorOutput(monitor.MonitorSettings(mode="stereo", target="hp"), ["a"], {}, 48000, "s", block=BLOCK)
    out.open()
    out.writer.close()
    assert seen["bufsize"] == 0


def test_a_play_process_that_exits_at_open_gives_a_reason(monkeypatch):
    class _Dead(_FakePlay):
        def __init__(self):
            super().__init__()
            self.stdin.write = self._boom

        @staticmethod
        def _boom(_data):
            raise BrokenPipeError(32, "Broken pipe")

    monkeypatch.setattr(monitor.subprocess, "Popen", lambda *_a, **_k: _Dead())
    out = monitor.MonitorOutput(monitor.MonitorSettings(mode="stereo", target="hp"), ["a"], {}, 48000, "s", block=BLOCK)
    with pytest.raises(monitor.MonitorError, match="pw-play exited at open"):
        out.open()


def test_a_pipe_that_cannot_be_resized_is_logged_and_reported(monkeypatch, caplog):
    play = _FakePlay()  # no fileno: the resize fails
    monkeypatch.setattr(monitor.subprocess, "Popen", lambda *_a, **_k: play)
    out = monitor.MonitorOutput(monitor.MonitorSettings(mode="stereo", target="hp"), ["a"], {}, 48000, "s", block=BLOCK)
    with caplog.at_level("WARNING", logger="aurasync.monitor"):
        out.open()
    out.writer.close()
    assert out.pipe_bytes is None
    assert "could not be resized" in caplog.text
