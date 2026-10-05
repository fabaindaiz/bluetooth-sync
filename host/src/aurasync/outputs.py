"""The output side of a session: which speakers play, and who keeps the time.

Spec: `docs/superpowers/specs/2026-10-05-virtual-speakers-and-hot-join-design.md` (sections 3 and 4).

A speaker is *virtual* (its sink is `None`: the chain computes its channel, nothing plays it),
*absent* (a real sink that is not in the player), *playing*, or *lost* (it was playing and its
stream died). `OutputSet` holds the real part, a player, and drops the blocks of everyone
who is not playing. When no real stream is alive nothing blocks the loop, so a `Pacer` does.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Literal, Protocol

if TYPE_CHECKING:
    from collections.abc import Callable

    import numpy as np

BLUETOOTH_PREFIX = "bluez_output."


def output_kind(sink: str | None) -> Literal["virtual", "bluetooth", "wired"]:
    """What kind of output a sink is: no sink, a BlueZ node, or anything else."""
    if sink is None:
        return "virtual"
    return "bluetooth" if sink.startswith(BLUETOOTH_PREFIX) else "wired"


class Pacer:
    """Real-time pacing for a loop that no stream paces.

    The deadline of block `k` is `origin + k * block / rate`: a function of one clock, so
    uneven work between waits does not accumulate error. More than two blocks late means a
    stall; it resynchronises instead of bursting to catch up.
    """

    def __init__(
        self,
        rate: int,
        block: int,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._period = block / rate
        self._clock = clock
        self._sleep = sleep
        self._origin = clock()
        self._k = 0

    def reset(self) -> None:
        self._origin = self._clock()
        self._k = 0

    def wait(self) -> None:
        """Sleep until the next block's deadline, then advance."""
        deadline = self._origin + (self._k + 1) * self._period
        if self._clock() - deadline > 2 * self._period:
            self.reset()
            deadline = self._origin + self._period
        delay = deadline - self._clock()
        if delay > 0:
            self._sleep(delay)
        self._k += 1


class PlayerLike(Protocol):
    """The shape of `Reproductor`, `ReproductorCombinado` and `SimulatedPlayer`."""

    @property
    def vivos(self) -> list[str]: ...

    @property
    def pids(self) -> dict[str, int]: ...

    def escribir(self, blocks: dict[str, np.ndarray]) -> None: ...

    def mal_ruteados(self) -> dict: ...

    def reparar_ruteo(self) -> dict: ...

    def soltar(self, node: str) -> None: ...

    def cerrar(self) -> None: ...


class OutputSet:
    """Speaker names to sinks, the player that plays the real ones, and the pacer."""

    def __init__(self, sinks: dict[str, str | None], pacer: Pacer) -> None:
        self._sinks = dict(sinks)
        self._pacer = pacer
        self._player: PlayerLike | None = None
        self._playing: set[str] = set()
        self._lost: set[str] = set()

    def attach(self, player: PlayerLike | None, playing: set[str]) -> PlayerLike | None:
        """Install the real part; return the previous one for the caller to close."""
        previous, self._player = self._player, player
        self._playing = {n for n in playing if self._sinks.get(n) is not None} if player else set()
        self._lost -= self._playing
        return previous

    def write(self, blocks: dict[str, np.ndarray], *, input_paced: bool) -> None:
        real = {self._sinks[n]: x for n, x in blocks.items() if n in self._playing}
        if real and self._player is not None:
            self._player.escribir(real)
        if input_paced:
            self._pacer.reset()
        elif not self.vivos:
            self._pacer.wait()

    def refresh(self) -> list[str]:
        """Move to `lost` the playing speakers whose node left the player; return them."""
        alive = set(self.vivos)
        gone = sorted(n for n in self._playing if self._sinks[n] not in alive)
        self._playing.difference_update(gone)
        self._lost.update(gone)
        return gone

    def states(self) -> dict[str, str]:
        out = {}
        for name, sink in self._sinks.items():
            if sink is None:
                out[name] = "virtual"
            elif name in self._lost:
                out[name] = "lost"
            else:
                out[name] = "playing" if name in self._playing else "absent"
        return out

    def playing(self) -> list[str]:
        return [n for n in self._sinks if n in self._playing]

    @property
    def vivos(self) -> list[str]:
        return self._player.vivos if self._player is not None else []

    @property
    def pids(self) -> dict[str, int]:
        return self._player.pids if self._player is not None else {}

    def nivel_ms(self) -> float | None:
        """Audio waiting in the pipe to the player, in ms; `None` without one that reports it."""
        level = getattr(self._player, "nivel_ms", None)
        return level() if level is not None else None

    def mal_ruteados(self) -> dict:
        return self._player.mal_ruteados() if self._player is not None else {}

    def reparar_ruteo(self) -> dict:
        return self._player.reparar_ruteo() if self._player is not None else {}

    def soltar(self, node: str) -> None:
        if self._player is not None:
            self._player.soltar(node)

    def close(self) -> None:
        """Close the player and leave every real speaker absent."""
        player, self._player = self._player, None
        self._playing.clear()
        self._lost.clear()
        if player is not None:
            player.cerrar()
