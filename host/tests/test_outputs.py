"""OutputSet and its parts: virtual speakers, the pacer, who is playing."""

import numpy as np
import pytest

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
