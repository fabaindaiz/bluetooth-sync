"""How much audio an output keeps ahead in the pipe of its `pw-play`, as pure decisions.

Two outputs use it: the headphone monitor (`Cushion`, commit 5efc460) and the speakers
(`SharedCushion`, spec docs/superpowers/specs/2026-10-05-virtual-speakers-and-hot-join-design.md §9).
The target is the same calculation for both: one engine block plus one driver quantum, capped at
`MAX_CUSHION_S`. What differs is when the pipe may be touched. The monitor is not synchronised with
anything, so it refills or drops at any block. The speakers must stay aligned with each other, so
their cushion is one value for the whole real part and it only changes for all of them at once, at
the bottom of a `motor.cortar` fade.

Since stage 4 of the seamless transitions (spec 2026-10-08 §4b) both cushions first ask a stretcher
(dsp/stretch.py) for the frames: the output plays slightly slower (or faster) until the pipe is back,
with no silence and no cut. Silence (and the speakers' cut) stays as the last resort, under a quantum.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aurasync.chain import ChainValues
    from aurasync.dsp.stretch import OutputStretcher

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


def stretch_limits(chain: ChainValues) -> tuple[float, float]:
    """The stage-4 stretch knobs (`transition.start_stretch_ppm`, `max_stretch_ppm`)."""
    return chain.param("transition", "start_stretch_ppm"), chain.param("transition", "max_stretch_ppm")


def new_stretcher(channels: int | list[str], rate: int) -> OutputStretcher:
    """A cushion's stretcher at the knobs' defaults (the session moves it to the chain's every block).
    A cushion's level reads move by a driver quantum on their own: that is its tolerance."""
    from aurasync.chain import ChainValues  # noqa: PLC0415 - the chain pulls the DSP; the cushion stays light
    from aurasync.dsp.stretch import OutputStretcher  # noqa: PLC0415

    start, top = stretch_limits(ChainValues())
    return OutputStretcher(channels, rate, start, top, tolerance=DRIVER_QUANTUM_FRAMES)


class _Steering:
    """When a cushion asks its stretcher for frames (dsp/stretch.py; spec
    docs/superpowers/specs/2026-10-08-seamless-transitions-design.md §4b), the same for both cushions.

    A pipe that reads more than a quantum under the target (`low`) for `LOW_BLOCKS` blocks in a row
    asks for the missing frames, `target - level`; one that reads over `high` as long asks to drop down
    to `drain_to`. The rule is the one the last resort uses against a late block: a single reading
    starts nothing. While the stretcher runs, every reading tells it again what is missing as the pipe
    reads now; the stretcher steps `ε` up when that is more than it still owes by a quantum (the pipe
    kept falling), down when it is less (it recovered), and lands once nothing is missing.

    **Its own brake** (review 2026-10-09): an episode that has moved more than a target's worth of
    frames while the pipe reads no better than when it started is not helping (a reading that lies, or
    a drain faster than the maximum). It lands, and no stretch is asked again in this session
    (`gave_up`), as the speakers' cut stops after `MAX_FAILED`."""

    def __init__(self, stretcher: OutputStretcher, target: int, high: int, drain_to: int) -> None:
        self.stretcher = stretcher
        self.target = target
        self.low = target - DRIVER_QUANTUM_FRAMES
        self.high = high
        self.drain_to = drain_to
        self._short = 0
        self._over = 0
        self.gave_up = False
        self._start: tuple[int, int] | None = None
        """At the episode's first reading: the level and the stretcher's `stretched_frames`."""

    @property
    def enabled(self) -> bool:
        return self.stretcher.enabled and not self.gave_up

    def wanted(self, level: int) -> int:
        """Frames to add (negative: to drop) for a pipe at `level`, in the direction it is stretching."""
        if self.stretcher.direction < 0:
            return min(0, self.drain_to - level)
        return max(0, self.target - level)

    def step(self, level: int, *, fits: bool = True) -> bool:
        """After each reading the last resort did not take. True when it asked for a new stretch.
        `fits`: the frames can go to every pipe (the speakers' room check); a stretch that cannot lands."""
        s = self.stretcher
        self._short = self._short + 1 if level < self.low else 0
        self._over = self._over + 1 if level > self.high else 0
        if not fits:
            self.cancel()
            return False
        if s.active:
            if self._not_helping(level):
                self.gave_up = True
                self.cancel()
                return False
            s.want(self.wanted(level))
            return False
        self._start = None
        if self._short >= LOW_BLOCKS:
            self._short = 0
            self._start = (level, s.stretched_frames)
            s.want(self.target - level)
            return True
        if self._over >= LOW_BLOCKS:
            self._over = 0
            self._start = (level, s.stretched_frames)
            s.want(self.drain_to - level)
            return True
        return False

    def _not_helping(self, level: int) -> bool:
        if self._start is None:
            self._start = (level, self.stretcher.stretched_frames)
            return False
        level0, frames0 = self._start
        if self.stretcher.stretched_frames - frames0 <= self.target:
            return False
        return level <= level0 if self.stretcher.direction > 0 else level >= level0

    def cancel(self) -> None:
        self._short = self._over = 0
        self.stretcher.cancel()


class Cushion:
    """How much audio the monitor keeps ahead in the pipe of `pw-play`, as a pure decision.

    The engine hands over one block every `block` frames but the driver takes a quantum every
    cycle: with nothing written ahead each block lands just after the cycle that needed it, and
    half the cycles are silent (MEDIDO on HP-O16, 2026-10-05). The delay is part of the calculation:
    the target is one block plus one driver quantum, capped at `MAX_CUSHION_S`. `plan` is asked
    before every block with the pipe level in frames."""

    def __init__(self, block: int, rate: int, stretcher: OutputStretcher | None = None) -> None:
        self.block = block
        self.rate = rate
        self.target_frames = target_frames(block, rate)
        self.refills = 0
        self.trims = 0
        self.level_frames: int | None = None
        self._primed = False
        """The first level read after the open is the priming: the open-time silence has been
        draining while the routing was checked, so finding the pipe low then is not a starvation."""
        self.stretcher = stretcher
        """The monitor's own stretcher (stage 4): a pipe more than a quantum under the target, or more
        than a block over it, is refilled or drained by playing slightly slower or faster. The silence
        refill (under a quantum) and the trim (over two blocks) stay as the last resort."""
        self._steer = (
            None
            if stretcher is None
            else _Steering(stretcher, self.target_frames, self.target_frames + block, self.target_frames)
        )

    @property
    def target_ms(self) -> float:
        return self.target_frames / self.rate * 1000

    @property
    def level_ms(self) -> float | None:
        return None if self.level_frames is None else self.level_frames / self.rate * 1000

    @property
    def stretched_frames(self) -> int:
        return 0 if self.stretcher is None else self.stretcher.stretched_frames

    @property
    def stretch_ppm(self) -> float:
        return 0.0 if self.stretcher is None else self.stretcher.epsilon_ppm

    @property
    def stretch_gave_up(self) -> bool:
        return self._steer is not None and self._steer.gave_up

    def plan(self, level_frames: int | None) -> tuple[int, bool]:
        """`(frames of silence to write first, whether to write the block)`. The block then goes
        through `process`."""
        self.level_frames = level_frames
        steer = self._steer if self._steer is not None and self._steer.enabled else None
        if level_frames is None:
            if steer is not None:
                steer.cancel()
            return 0, True
        first, self._primed = not self._primed, True
        if first and level_frames < DRIVER_QUANTUM_FRAMES:
            return self.target_frames - level_frames, True
        if level_frames < DRIVER_QUANTUM_FRAMES:
            self.refills += 1
            if steer is not None:
                steer.cancel()
            return self.target_frames - level_frames, True
        if level_frames > self.target_frames + BACKLOG_BLOCKS * self.block:
            self.trims += 1
            if steer is not None:
                steer.cancel()
            return 0, False
        if steer is not None:
            steer.step(level_frames)
        return 0, True

    def process(self, block):
        """The block as it goes into the pipe: through the stretcher, the very same one while idle."""
        return block if self.stretcher is None else self.stretcher.process(block)


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

    Stage 4: that cut and pad are now the last resort. Before them, a lowest pipe more than a quantum
    under the target (`LOW_BLOCKS` in a row) asks the speakers' one stretcher for the missing frames,
    which every speaker gets alike, so their alignment holds as with the pad. A lowest pipe more than
    `BACKLOG_BLOCKS` over the target (the input backed up: the speakers' clock slower) is drained to that
    line by playing faster (INFERIDO that it reads that high only then: with the default pipe the paced
    level sits about a block and a half over the target). Not during a calibration, and only for frames
    that fit in the fullest pipe; a pipe that still reaches the last resort gets it, and the stretch lands.

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

    def __init__(self, block: int, rate: int, stretcher: OutputStretcher | None = None) -> None:
        self.block = block
        self.rate = rate
        self.target_frames = target_frames(block, rate)
        self.refills = 0
        self.stretcher = stretcher
        """One for every real speaker (stage 4): the output set runs their blocks through it."""
        high = self.target_frames + BACKLOG_BLOCKS * block
        self._steer = None if stretcher is None else _Steering(stretcher, self.target_frames, high, high)
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
        no_room = NO_READING if room_frames is None else NO_ROOM_SEPARATE if separate else NO_ROOM
        if level_frames is None or level_frames >= DRIVER_QUANTUM_FRAMES:
            self._low = 0
            refused = self._stretch(level_frames, room_frames, may_cut=may_cut)
            self.reason = no_room if refused else None
            return False
        self._low += 1
        if self._low < LOW_BLOCKS:
            self._stretch(level_frames, room_frames, may_cut=may_cut)  # one late block is not dry
        elif self._steer is not None:
            # The pipe is about to run dry anyway: the stretch yields to the last resort, whether
            # the cut can be asked now or not (spec §4b; review 2026-10-09).
            self._steer.cancel()
        if self.pending or self.gave_up or not may_cut or self._low < LOW_BLOCKS:
            return False
        if not self._fits(level_frames, room_frames):
            self.reason = no_room
            return False
        if self._last_cut is not None and self._blocks - self._last_cut < self._blocks_for(MIN_GAP_S):
            return False
        self.reason = None
        self.pending = True
        self._last_cut = self._blocks
        if self._steer is not None:
            self._steer.cancel()  # the last resort: the pad brings the pipe back, the stretch lands
        return True

    def _fits(self, level_frames: int, room_frames: int | None) -> bool:
        """The frames that bring the lowest pipe to the target fit in the fullest one with a block."""
        need = self.target_frames - level_frames
        return need <= 0 or (room_frames is not None and need <= room_frames - self.block)

    def _stretch(self, level_frames: int | None, room_frames: int | None, *, may_cut: bool) -> bool:
        """Stage 4: ask the stretcher before the pipe gets near the last resort (`_Steering`). Not while
        a calibration owns the speakers (the stretch would move what the microphone measures), nor
        without a reading, nor while a cut is pending or after the cushion gave up (the last resort has
        the pipe then; review 2026-10-09), and only for frames that fit in every pipe, as the pad. True
        when the pipe is in the stretch zone and the frames do not fit (the state says why). Called
        when no cut is asked in this reading: one that is asked lands the stretch itself."""
        steer = self._steer
        if steer is None or not steer.enabled:
            if steer is not None:
                steer.stretcher.cancel()  # turned off by its knob, or given up: it lands
            return False
        if not may_cut or level_frames is None or self.pending or self.gave_up:
            steer.cancel()
            return False
        fits = self._fits(level_frames, room_frames)
        steer.step(level_frames, fits=fits)
        return not fits and level_frames < steer.low

    @property
    def stretched_frames(self) -> int:
        return 0 if self.stretcher is None else self.stretcher.stretched_frames

    @property
    def stretch_ppm(self) -> float:
        return 0.0 if self.stretcher is None else self.stretcher.epsilon_ppm

    @property
    def stretch_gave_up(self) -> bool:
        """The stretch moved a target's worth of frames without raising the pipe: no more this session."""
        return self._steer is not None and self._steer.gave_up

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
