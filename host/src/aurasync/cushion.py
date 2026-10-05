"""How much audio an output keeps ahead in the pipe of its `pw-play`, as pure decisions.

Two outputs use it: the headphone monitor (`Cushion`, commit 5efc460) and the speakers
(`SharedCushion`, spec docs/superpowers/specs/2026-10-05-virtual-speakers-and-hot-join-design.md §9).
The target is the same calculation for both: one engine block plus one driver quantum, capped at
`MAX_CUSHION_S`. What differs is when the pipe may be touched. The monitor is not synchronised with
anything, so it refills or drops at any block. The speakers must stay aligned with each other, so
their cushion is one value for the whole real part and it only changes for all of them at once, at
the bottom of a `motor.cortar` fade.
"""

from __future__ import annotations

import math

DRIVER_QUANTUM_FRAMES = 2048
"""The Bluetooth driver's quantum with A2DP: 2048 frames, 42.7 ms at 48 kHz. `pw-top` showed it on
HP-O16 for the WH-CH520 with AAC on 2026-10-05 (MEDIDO), and the speakers' jumps of exactly 2048
samples on PC-Ryzen5 are one of it (experimentos/10 §5.3, MEDIDO). `pw-play` asks the pipe for one
every cycle."""
MAX_CUSHION_S = 0.4
"""Cap of the cushion: more than this is latency the listener hears against the picture."""
BACKLOG_BLOCKS = 2
LOW_BLOCKS = 3
"""How many blocks in a row the speakers' pipe must read under a quantum before a refill is asked.

A late engine block leaves the pipe low for one reading, and the next blocks fill it again: the
input kept its audio meanwhile and the engine reads it at once (INFERIDO from how `SinkVirtual.leer`
and the blocking write work; not measured). A refill for that would be a cut the music did not need.
A pipe that drains because the speakers' clock runs faster than the input's stays low."""

MIN_GAP_S = 30.0
"""At least this long between two of the speakers' cushion cuts, whatever the pipes read: a cut is
heard, and a cushion that kept asking would cut the music over and over (review of Task 11)."""
CHECK_S = 10.0
"""How long after a refill the lowest pipe has to read back near the target for it to count as one
that worked."""
MAX_FAILED = 3
"""Refills that did not work before the speakers' cushion stops asking for cuts in this session."""
NO_ROOM_SEPARATE = "separado: relojes distintos"
NO_ROOM = "la tubería no tiene lugar para el relleno"
NO_READING = "no se puede leer el lugar libre en la tubería"


def target_frames(block: int, rate: int) -> int:
    """One engine block plus one driver quantum, capped at `MAX_CUSHION_S`."""
    return min(block + DRIVER_QUANTUM_FRAMES, int(MAX_CUSHION_S * rate))


class Cushion:
    """How much audio the monitor keeps ahead in the pipe of `pw-play`, as a pure decision.

    The engine hands over one block every `block` frames but the driver takes a quantum every
    cycle: with nothing written ahead each block lands just after the cycle that needed it, and
    half the cycles are silent (MEDIDO on HP-O16, 2026-10-05). The delay is part of the calculation:
    the target is one block plus one driver quantum, capped at `MAX_CUSHION_S`. `plan` is asked
    before every block with the pipe level in frames."""

    def __init__(self, block: int, rate: int) -> None:
        self.block = block
        self.rate = rate
        self.target_frames = target_frames(block, rate)
        self.refills = 0
        self.trims = 0
        self.level_frames: int | None = None
        self._primed = False
        """The first level read after the open is the priming: the open-time silence has been
        draining while the routing was checked, so finding the pipe low then is not a starvation."""

    @property
    def target_ms(self) -> float:
        return self.target_frames / self.rate * 1000

    @property
    def level_ms(self) -> float | None:
        return None if self.level_frames is None else self.level_frames / self.rate * 1000

    def plan(self, level_frames: int | None) -> tuple[int, bool]:
        """`(frames of silence to write first, whether to write the block)`."""
        self.level_frames = level_frames
        if level_frames is None:
            return 0, True
        first, self._primed = not self._primed, True
        if first and level_frames < DRIVER_QUANTUM_FRAMES:
            return self.target_frames - level_frames, True
        if level_frames < DRIVER_QUANTUM_FRAMES:
            self.refills += 1
            return self.target_frames - level_frames, True
        if level_frames > self.target_frames + BACKLOG_BLOCKS * self.block:
            self.trims += 1
            return 0, False
        return 0, True


class SharedCushion:
    """The speakers' cushion: one value for the whole real part, changed for all at a cut's bottom.

    Unlike the monitor, the speakers' pipe is already primed and paced: `open` writes half a second
    of silence, and the engine's write waits while the pipe is full, so between two blocks it holds
    its size minus a block (at least 135 ms with the default 50 ms player and 4096-frame blocks),
    above this target. What can still empty it is a drift: the speakers' clock running faster than
    the input's while music plays (the input paces the engine then), a few ms per minute
    (experimentos/10 §5.3). Left alone the pipe runs dry, and in `separado` the one that runs dry
    first jumps a quantum against the others.

    So the level is watched before every write (`observe`, the lowest of the pipes). Once it has read
    under a quantum `LOW_BLOCKS` times in a row, a cut is asked for, and at its bottom `at_bottom`
    says how much silence brings that lowest level back to the target. The caller writes that same
    silence to every speaker stream in one go: every speaker gets the same delay, so their alignment
    is unchanged (sizing each pipe on its own level would move it). There is no trim: the pipe's size
    already bounds what can wait in it, and a full pipe holds the write back.

    In `separado` with clocks that differ between speakers the pipes spread apart. An equal refill
    keeps that spread (it is their alignment), so it cannot stop the fastest from running dry once the
    slowest has filled its pipe and holds the write back: that is what `combinado` is for
    (experimentos/10 §5.3). A pad sized on the fastest pipe would not fit in the slowest, and a write
    that waits there holds the engine while the fastest keeps draining; refilling over and over cut
    the music every ~4 blocks (review of Task 11). So a cut is only asked when the pad and the next
    block fit in the fullest pipe; otherwise `reason` says why not. And a brake for both modes: at
    least `MIN_GAP_S` between cuts, and after `MAX_FAILED` refills that did not bring the lowest pipe
    back near the target within `CHECK_S`, no more (`gave_up`). A new session starts a new cushion.
    """

    def __init__(self, block: int, rate: int) -> None:
        self.block = block
        self.rate = rate
        self.target_frames = target_frames(block, rate)
        self.refills = 0
        self.level_frames: int | None = None
        """The lowest pipe level read before the last write, in frames; `None` if unreadable."""
        self.pending = False
        """A refill was asked for and waits for the bottom of its cut."""
        self.reason: str | None = None
        """Why a refill the level asks for is not being asked: the pad would not fit in every pipe."""
        self.failed = 0
        """Refills after which the lowest pipe did not read back near the target within `CHECK_S`."""
        self.gave_up = False
        """`MAX_FAILED` refills did not work: no more cuts in this session."""
        self._low = 0
        self._blocks = 0
        """Blocks observed: the cushion's clock (one per engine block, so real time while playing)."""
        self._last_cut: int | None = None
        self._check_until: int | None = None
        """While a refill is being checked, the block by which the level must have come back."""

    @property
    def target_ms(self) -> float:
        return self.target_frames / self.rate * 1000

    def _blocks_for(self, seconds: float) -> int:
        return math.ceil(seconds * self.rate / self.block)

    def observe(
        self, level_frames: int | None, room_frames: int | None, *, may_cut: bool = True, separate: bool = False
    ) -> bool:
        """Before each write: the lowest pipe level, and the room left in the fullest pipe. True when
        the caller must ask for a cut now (once per refill). No cut when `may_cut` is false (a
        calibration owns the speakers), within `MIN_GAP_S` of the last one, after giving up, or when
        the pad would not fit in every pipe with the next block (`room_frames`, unknown = no room): a
        write that waits there would hold the engine while the lowest pipe keeps draining. `separate`
        says the pipes are one per speaker, for the reason the state shows."""
        self._blocks += 1
        self.level_frames = level_frames
        self._check(level_frames)
        if level_frames is None or level_frames >= DRIVER_QUANTUM_FRAMES:
            self._low = 0
            self.reason = None
            return False
        self._low += 1
        if self.pending or self.gave_up or not may_cut or self._low < LOW_BLOCKS:
            return False
        if room_frames is None or self.target_frames - level_frames > room_frames - self.block:
            self.reason = NO_READING if room_frames is None else NO_ROOM_SEPARATE if separate else NO_ROOM
            return False
        if self._last_cut is not None and self._blocks - self._last_cut < self._blocks_for(MIN_GAP_S):
            return False
        self.reason = None
        self.pending = True
        self._last_cut = self._blocks
        return True

    def _check(self, level_frames: int | None) -> None:
        """Whether the last refill brought the lowest pipe back. Read before a write, a refilled pipe
        reads just under the target (what the driver took since), so within a quantum of it counts."""
        if self._check_until is None:
            return
        if level_frames is not None and level_frames >= self.target_frames - DRIVER_QUANTUM_FRAMES:
            self._check_until = None
        elif self._blocks > self._check_until:
            self._check_until = None
            self.failed += 1
            self.gave_up = self.failed >= MAX_FAILED

    def at_bottom(self, room_frames: int | None) -> int:
        """At the bottom of the cut, after its block: frames of silence to write to every speaker
        stream now. Never more than `room_frames`, the room left in the fullest pipe, so the write
        does not wait."""
        if not self.pending:
            return 0
        self.pending = False
        self._low = 0
        level = self.level_frames
        if level is None or level >= self.target_frames:
            return 0
        self._check_until = self._blocks + self._blocks_for(CHECK_S)
        frames = max(0, min(self.target_frames - level, room_frames or 0))
        if frames:
            self.refills += 1
        return frames

    def cancel(self) -> None:
        """The refill asked for will not be written (the real part was replaced at that bottom)."""
        self.pending = False
        self._low = 0
