"""OutputSet and its parts: virtual speakers, the pacer, who is playing."""

import numpy as np
import pytest

from aurasync import cushion as cushion_module
from aurasync.cushion import Cushion, SharedCushion
from aurasync.dsp.stretch import OutputStretcher
from aurasync.outputs import OutputSet, Pacer, output_kind

RATE, BLOCK = 48000, 4096
DUR = BLOCK / RATE


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += max(0.0, s)


class FakePlayer:
    def __init__(self, nodes):
        self.opened = set(nodes)
        self.alive = list(nodes)
        self.written = []
        self.closed = False
        self.wrong = {}
        self.repairs = 0

    @property
    def vivos(self):
        return list(self.alive)

    @property
    def pids(self):
        return {n: 100 + i for i, n in enumerate(self.alive)}

    def escribir(self, blocks):
        for node in blocks:
            if node not in self.opened:
                raise KeyError(node)
        self.written.append(blocks)

    def mal_ruteados(self):
        return dict(self.wrong)

    def reparar_ruteo(self):
        self.repairs += 1
        return {}

    def soltar(self, node):
        self.alive.remove(node)

    def cerrar(self):
        self.closed = True


@pytest.mark.parametrize(
    ("sink", "kind"),
    [
        (None, "virtual"),
        ("bluez_output.90_F2_60_75_4A_83.1", "bluetooth"),
        ("alsa_output.pci-0000_00_1f.3.analog-stereo", "wired"),
    ],
)
def test_output_kind(sink, kind):
    assert output_kind(sink) == kind


def test_n_blocks_take_n_block_durations():
    clock = FakeClock()
    p = Pacer(RATE, BLOCK, clock.now, clock.sleep)
    for _ in range(100):
        p.wait()
    assert clock.t == pytest.approx(100 * DUR, abs=DUR)


def test_deadline_is_a_function_of_one_clock():
    a, b = FakeClock(), FakeClock()
    pa, pb = Pacer(RATE, BLOCK, a.now, a.sleep), Pacer(RATE, BLOCK, b.now, b.sleep)
    for i in range(50):
        a.t += 0.03 if i % 2 else 0.0
        pa.wait()
        pb.wait()
    assert a.t == pytest.approx(b.t)


def test_late_by_more_than_two_blocks_resyncs_without_burst():
    clock = FakeClock()
    p = Pacer(RATE, BLOCK, clock.now, clock.sleep)
    p.wait()
    clock.t += 1.0
    p.wait()
    before = clock.t
    p.wait()
    assert clock.t - before == pytest.approx(DUR)


def test_reset_moves_the_origin_to_now():
    clock = FakeClock()
    p = Pacer(RATE, BLOCK, clock.now, clock.sleep)
    clock.t = 10.0
    p.reset()
    p.wait()
    assert clock.t == pytest.approx(10.0 + DUR)


def _set(sinks):
    clock = FakeClock()
    return OutputSet(sinks, Pacer(RATE, BLOCK, clock.now, clock.sleep)), clock


X = np.zeros(BLOCK)


def test_only_playing_speakers_reach_the_player():
    out, _ = _set({"A": "sA", "V": None, "B": "sB"})
    player = FakePlayer(["sA"])
    out.attach(player, {"A"})
    out.write({"A": X, "V": X, "B": X}, input_paced=True)
    assert list(player.written[-1]) == ["sA"]
    assert out.states() == {"A": "playing", "V": "virtual", "B": "absent"}
    assert out.playing() == ["A"]


def test_without_a_real_output_the_pacer_keeps_time():
    out, clock = _set({"V": None})
    out.attach(None, set())
    for _ in range(10):
        out.write({"V": X}, input_paced=False)
    assert clock.t == pytest.approx(10 * DUR, abs=DUR)


def test_input_paced_blocks_do_not_sleep():
    out, clock = _set({"V": None})
    out.attach(None, set())
    for _ in range(10):
        out.write({"V": X}, input_paced=True)
    assert clock.t == 0.0


def test_a_live_real_stream_paces_so_the_pacer_stays_quiet():
    out, clock = _set({"A": "sA"})
    out.attach(FakePlayer(["sA"]), {"A"})
    out.write({"A": X}, input_paced=False)
    assert clock.t == 0.0


def test_a_dead_stream_becomes_lost_and_writing_goes_on():
    out, clock = _set({"A": "sA", "V": None})
    player = FakePlayer(["sA"])
    out.attach(player, {"A"})
    player.alive.remove("sA")
    assert out.refresh() == ["A"]
    assert out.refresh() == []
    assert out.states()["A"] == "lost"
    out.write({"A": X, "V": X}, input_paced=False)
    assert clock.t > 0
    assert player.written == []


def test_a_lost_speaker_in_the_new_playing_set_plays_again():
    out, _ = _set({"A": "sA"})
    player = FakePlayer(["sA"])
    out.attach(player, {"A"})
    player.alive.remove("sA")
    out.refresh()
    out.attach(FakePlayer(["sA"]), {"A"})
    assert out.states() == {"A": "playing"}


def test_attach_returns_the_previous_player():
    out, _ = _set({"A": "sA"})
    first, second = FakePlayer(["sA"]), FakePlayer(["sA"])
    assert out.attach(first, {"A"}) is None
    assert out.attach(second, {"A"}) is first
    assert out.attach(None, set()) is second
    assert out.states() == {"A": "absent"}


def test_passthroughs_and_close():
    out, _ = _set({"A": "sA"})
    assert out.vivos == []
    assert out.pids == {}
    assert out.mal_ruteados() == {}
    assert out.reparar_ruteo() == {}
    player = FakePlayer(["sA"])
    out.attach(player, {"A"})
    assert out.vivos == ["sA"]
    assert out.pids == {"sA": 100}
    player.wrong = {"sA": "x"}
    assert out.mal_ruteados() == {"sA": "x"}
    out.reparar_ruteo()
    assert player.repairs == 1
    out.soltar("sA")
    assert player.alive == []
    out.close()
    assert player.closed
    assert out.states() == {"A": "absent"}


# -- the speakers' cushion: one for the whole real part (spec §9) ------------------------------

QUANTUM = cushion_module.DRIVER_QUANTUM_FRAMES
ROOM = 10**6
"""Room left in the fullest pipe: plenty, unless a test says otherwise."""
GAP_BLOCKS = -(-cushion_module.MIN_GAP_S * RATE // BLOCK)
CHECK_BLOCKS = -(-cushion_module.CHECK_S * RATE // BLOCK)


def _ask(shared, level=QUANTUM - 1, room=ROOM, **kwargs):
    """Low readings until the cushion asks for a cut; how many it took."""
    for n in range(1, 100_000):
        if shared.observe(level, room, **kwargs):
            return n
    return None


def test_the_speakers_target_is_the_monitors_calculation():
    shared = SharedCushion(BLOCK, RATE)
    assert shared.target_frames == Cushion(BLOCK, RATE).target_frames == BLOCK + QUANTUM
    assert round(shared.target_ms) == 128
    assert SharedCushion(48000, RATE).target_frames == 19200, "capped at 400 ms, as the monitor's"


def test_a_cut_is_asked_once_the_pipe_stays_under_a_quantum():
    shared = SharedCushion(BLOCK, RATE)
    low = QUANTUM - 1
    asked = [shared.observe(low, ROOM) for _ in range(cushion_module.LOW_BLOCKS)]
    assert asked == [False] * (cushion_module.LOW_BLOCKS - 1) + [True]
    assert shared.pending
    assert not shared.observe(low, ROOM), "one cut per refill: not asked again while it is pending"
    assert shared.refills == 0, "nothing is written until the bottom of the cut"


def test_one_low_reading_is_not_a_refill():
    """A late engine block leaves the pipe low once and the next writes catch up: no cut for it."""
    shared = SharedCushion(BLOCK, RATE)
    for _ in range(20):
        assert not shared.observe(QUANTUM - 1, ROOM)
        assert not shared.observe(shared.target_frames, ROOM)
    assert not shared.observe(None, ROOM)
    assert not shared.pending


def test_the_refill_at_the_bottom_brings_the_level_to_the_target():
    shared = SharedCushion(BLOCK, RATE)
    _ask(shared, 1000)
    assert shared.at_bottom(ROOM) == shared.target_frames - 1000
    assert shared.refills == 1
    assert not shared.pending
    assert shared.at_bottom(ROOM) == 0, "a bottom without a refill pending writes nothing"
    assert shared.refills == 1


def test_a_pipe_that_recovered_by_the_bottom_gets_nothing():
    shared = SharedCushion(BLOCK, RATE)
    _ask(shared, 1000)
    shared.observe(shared.target_frames + 10, ROOM)
    assert shared.at_bottom(ROOM) == 0
    assert shared.refills == 0
    assert not shared.pending


def test_no_cut_is_asked_while_one_may_not_be():
    """During a calibration the stimulus owns the speakers: the level is still read, no cut asked."""
    shared = SharedCushion(BLOCK, RATE)
    for _ in range(10):
        assert not shared.observe(0, ROOM, may_cut=False)
    assert shared.level_frames == 0
    assert shared.observe(0, ROOM), "asked as soon as it may be"


def test_cancel_drops_the_pending_refill():
    shared = SharedCushion(BLOCK, RATE)
    _ask(shared, 0)
    shared.cancel()
    assert not shared.pending
    assert shared.at_bottom(ROOM) == 0


def test_no_cut_when_the_pad_would_not_fit_in_every_pipe():
    """`separado` with clocks that differ: the slowest pipe is full and holds the write back, so a
    pad sized on the fastest would block the engine. No cut; the state says why."""
    shared = SharedCushion(BLOCK, RATE)
    need = shared.target_frames - 1000
    for _ in range(50):
        assert not shared.observe(1000, need + BLOCK - 1, separate=True)
    assert not shared.pending
    assert shared.reason == "separado: relojes distintos"
    assert shared.observe(1000, need + BLOCK), "it fits once the fullest pipe leaves room for it and a block"
    assert shared.reason is None


def test_an_unknown_room_is_no_room():
    shared = SharedCushion(BLOCK, RATE)
    for _ in range(10):
        assert not shared.observe(0, None)
    assert shared.reason is not None


def test_the_pad_never_exceeds_the_room_at_the_bottom():
    shared = SharedCushion(BLOCK, RATE)
    _ask(shared, 0)
    assert shared.at_bottom(1000) == 1000, "what fits, not more: the write must not wait"
    _ask(shared, 0)
    assert shared.at_bottom(0) == 0


def test_cuts_are_at_least_30_s_apart():
    shared = SharedCushion(BLOCK, RATE)
    _ask(shared, 0)
    shared.at_bottom(ROOM)
    waited = _ask(shared, 0)
    assert waited is not None
    assert waited >= GAP_BLOCKS - 1


def test_a_refill_that_brings_the_level_back_is_not_a_failure():
    shared = SharedCushion(BLOCK, RATE)
    for _ in range(5):
        _ask(shared, 0)
        shared.at_bottom(ROOM)
        # The level reads just under the target after the pad: the drain since then.
        shared.observe(shared.target_frames - QUANTUM // 2, ROOM)
    assert shared.failed == 0
    assert not shared.gave_up


def test_three_refills_that_do_not_help_give_up():
    shared = SharedCushion(BLOCK, RATE)
    cuts = 0
    for _ in range(int(20 * GAP_BLOCKS)):
        if shared.observe(0, ROOM):
            cuts += 1
            shared.at_bottom(ROOM)
    assert cuts == cushion_module.MAX_FAILED
    assert shared.failed == cushion_module.MAX_FAILED
    assert shared.gave_up
    assert not shared.pending


def test_pad_writes_the_same_silence_to_every_playing_stream():
    out, _ = _set({"A": "sA", "V": None, "B": "sB", "C": "sC"})
    player = FakePlayer(["sA", "sB"])
    out.attach(player, {"A", "B"})
    out.pad(1234)
    [written] = player.written
    assert set(written) == {"sA", "sB"}, "only the playing ones, in one write"
    assert all(len(x) == 1234 and not np.any(x) for x in written.values())


def test_pad_without_a_real_part_or_frames_writes_nothing():
    out, _ = _set({"A": "sA"})
    out.pad(100)
    player = FakePlayer(["sA"])
    out.attach(player, {"A"})
    out.pad(0)
    assert player.written == []


def test_room_is_the_players_or_none():
    out, _ = _set({"A": "sA"})
    assert out.espacio_ms() is None
    player = FakePlayer(["sA"])
    out.attach(player, {"A"})
    assert out.espacio_ms() is None, "a player that cannot say"
    player.espacio_ms = lambda: 12.5
    assert out.espacio_ms() == 12.5


def test_the_cushion_outlives_a_swap_of_the_real_part():
    shared = SharedCushion(BLOCK, RATE)
    clock = FakeClock()
    out = OutputSet({"A": "sA"}, Pacer(RATE, BLOCK, clock.now, clock.sleep), shared)
    out.attach(FakePlayer(["sA"]), {"A"})
    out.attach(FakePlayer(["sA"]), {"A"})
    assert out.cushion is shared


# -- the speakers' cushion by stretching (spec 2026-10-08 §4b, stage 4) ----------------------------


def _stretching(**kwargs) -> SharedCushion:
    return SharedCushion(BLOCK, RATE, stretcher=OutputStretcher(["sA", "sB"], RATE, tolerance=QUANTUM), **kwargs)


def test_a_pipe_under_the_target_asks_the_stretcher_not_a_cut():
    shared = _stretching()
    level = shared.target_frames - QUANTUM - 1
    asked = [shared.observe(level, ROOM) for _ in range(cushion_module.LOW_BLOCKS)]
    assert asked == [False] * cushion_module.LOW_BLOCKS, "no cut"
    assert not shared.pending
    assert shared.stretcher.active
    assert shared.stretcher.pending_frames == shared.target_frames - level, "the missing frames"


def test_one_low_reading_does_not_stretch():
    shared = _stretching()
    for _ in range(20):
        shared.observe(shared.target_frames - QUANTUM - 1, ROOM)
        shared.observe(shared.target_frames, ROOM)
    assert not shared.stretcher.active


def test_under_a_quantum_the_stretch_yields_to_the_cut():
    """The pipe about to run dry: a real hole is worse than a cut. The stretch lands (by a ramp)."""
    shared = _stretching()
    for _ in range(cushion_module.LOW_BLOCKS):
        shared.observe(shared.target_frames - QUANTUM - 1, ROOM)
    s = shared.stretcher
    x = np.zeros(BLOCK)
    for _ in range(10):
        s.process({"sA": x, "sB": x})
    assert s.epsilon_ppm > 0
    assert _ask(shared, QUANTUM - 1) == cushion_module.LOW_BLOCKS
    s.process({"sA": x, "sB": x})
    for _ in range(100):
        s.process({"sA": x, "sB": x})
        if not s.active:
            break
    assert not s.active
    assert shared.at_bottom(ROOM) == shared.target_frames - (QUANTUM - 1), "today's pad, as before"


def test_while_stretching_a_pipe_that_keeps_falling_steps_up_and_one_that_recovers_lands():
    shared = _stretching()
    s = shared.stretcher
    level = shared.target_frames - QUANTUM - 1
    for _ in range(cushion_module.LOW_BLOCKS):
        shared.observe(level, ROOM)
    x = np.zeros(BLOCK)
    for _ in range(10):
        s.process({"sA": x})
    start = s.epsilon_ppm
    shared.observe(level - QUANTUM - 100, ROOM)  # a quantum lower than what is still owed
    for _ in range(10):
        s.process({"sA": x})
    assert s.epsilon_ppm > start
    shared.observe(shared.target_frames + 10, ROOM)  # back over the target: the frames are in
    for _ in range(100):
        s.process({"sA": x})
        if not s.active:
            break
    assert not s.active
    assert s.epsilon_ppm == 0


def test_a_pipe_far_over_its_target_drops_by_stretching():
    """No trim for the speakers, but a pipe that reads over two blocks above its target (the input
    backed up: the speakers' clock is slower) is drained back to that line, playing faster."""
    shared = _stretching()
    high = shared.target_frames + cushion_module.BACKLOG_BLOCKS * BLOCK
    for _ in range(cushion_module.LOW_BLOCKS):
        assert not shared.observe(high + 500, ROOM)
    assert shared.stretcher.pending_frames == -500
    assert not shared.observe(shared.target_frames + BLOCK, ROOM), "the normal paced level is no reason"


def test_no_stretch_while_a_calibration_owns_the_speakers():
    shared = _stretching()
    for _ in range(10):
        shared.observe(shared.target_frames - QUANTUM - 1, ROOM, may_cut=False)
    assert not shared.stretcher.active
    for _ in range(cushion_module.LOW_BLOCKS):
        shared.observe(shared.target_frames - QUANTUM - 1, ROOM)
    s = shared.stretcher
    for _ in range(10):
        s.process({"sA": np.zeros(BLOCK)})
    owed = s.pending_frames
    shared.observe(shared.target_frames - QUANTUM - 1, ROOM, may_cut=False)
    assert 0 < s.pending_frames < owed, "it lands, by a ramp"


def test_no_stretch_when_the_frames_would_not_fit_in_every_pipe():
    shared = _stretching()
    level = shared.target_frames - QUANTUM - 1
    need = shared.target_frames - level
    for _ in range(10):
        shared.observe(level, need + BLOCK - 1, separate=True)
    assert not shared.stretcher.active
    assert shared.reason == "separado: relojes distintos"


def test_with_the_stretch_turned_off_the_cushion_cuts_as_before():
    shared = _stretching()
    shared.stretcher.set_limits(0, 5000)
    for _ in range(10):
        assert not shared.observe(shared.target_frames - QUANTUM - 1, ROOM)
    assert not shared.stretcher.active
    assert _ask(shared, QUANTUM - 1) == cushion_module.LOW_BLOCKS


def test_the_output_set_stretches_every_playing_stream_alike():
    shared = _stretching()
    out = OutputSet({"A": "sA", "V": None, "B": "sB"}, Pacer(RATE, BLOCK), shared)
    player = FakePlayer(["sA", "sB"])
    out.attach(player, {"A", "B"})
    x = np.sin(np.arange(BLOCK) / 10)
    out.write({"A": x, "V": x, "B": 0.5 * x}, input_paced=True)
    assert player.written[-1]["sA"] is x, "idle: the very block, no copy"
    shared.stretcher.want(200)
    total = 0
    for _ in range(300):
        out.write({"A": x, "V": x, "B": 0.5 * x}, input_paced=True)
        written = player.written[-1]
        assert set(written) == {"sA", "sB"}
        assert len(written["sA"]) == len(written["sB"])
        np.testing.assert_array_equal(written["sB"], 0.5 * written["sA"])
        total += len(written["sA"]) - BLOCK
        if not shared.stretcher.active:
            break
    assert total == 200


def test_the_stretch_waits_while_a_cut_is_pending_and_stops_when_the_cushion_gives_up():
    """Review 2026-10-09: after the cut was asked, the next readings stepped the stretch up again
    during the pending cut, and after giving up it ran at its maximum for good."""
    shared = _stretching()
    s = shared.stretcher
    x = np.zeros(BLOCK)
    assert _ask(shared, QUANTUM - 1) == cushion_module.LOW_BLOCKS
    for _ in range(20):
        assert not shared.observe(QUANTUM - 1, ROOM)  # the cut is pending
        s.process({"sA": x})
    assert not s.active
    shared.gave_up = True
    shared.pending = False
    for _ in range(20):
        shared.observe(shared.target_frames - QUANTUM - 1, ROOM)
        s.process({"sA": x})
    assert not s.active


def test_a_stretch_that_does_not_raise_the_pipe_gives_up():
    """A pipe that reads the same however much is stretched into it (a reading that lies, or a drain
    faster than the maximum): after more than a target's worth of frames, the stretch lands and is
    not asked again in this session."""
    shared = _stretching()
    s = shared.stretcher
    level = shared.target_frames - QUANTUM - 1
    x = np.zeros(BLOCK)
    for _ in range(5000):
        shared.observe(level, ROOM)
        s.process({"sA": x})
        if shared.stretch_gave_up and not s.active:
            break
    assert shared.stretch_gave_up
    assert not s.active
    moved = s.stretched_frames
    assert moved <= shared.target_frames + 2 * BLOCK
    for _ in range(50):
        shared.observe(level, ROOM)
        s.process({"sA": x})
    assert not s.active
    assert s.stretched_frames == moved
