"""What the engine sent of the masked probe, kept for a while (spec 2026-10-03 §6.2).

A phone measures each speaker's arrival against the probe that speaker carried (`probe_measure`),
so the server keeps, per speaker, the probe `MaskedProbe.last` added to each block, with the server
time (`time.monotonic`) of the block's first sample as it left the engine. The playback latency
after that is common to every speaker and goes into the measurement's common term.

A fixed-size ring per speaker (d-7c8794-589dec: the engine thread writes one block, O(1)); a span
older than `keep_s` is gone.
"""

from __future__ import annotations

import numpy as np

KEEP_S = 30.0
"""`REFERENCE_KEEP_S` of the spec: enough for an 8 s recording, its upload and some slack."""


class RingError(ValueError):
    """The span asked is not in the ring."""


class ProbeRing:
    def __init__(self, names: list[str], sr: int, keep_s: float = KEEP_S) -> None:
        self.names = list(names)
        self.sr = sr
        self.size = int(keep_s * sr)
        self._data = {n: np.zeros(self.size, dtype=np.float32) for n in self.names}
        self._written = 0
        """Samples written since the start (the ring's write position is this modulo `size`)."""
        self._t_end: float | None = None
        """Server time of the sample after the last one written."""

    @property
    def nbytes(self) -> int:
        return sum(a.nbytes for a in self._data.values())

    def write(self, t_start: float, blocks: dict[str, np.ndarray]) -> None:
        """One block per speaker (absent: no probe, silence), starting at server time `t_start`."""
        n = len(next(iter(blocks.values()))) if blocks else 0
        if n == 0:
            return
        at = self._written % self.size
        idx = (at + np.arange(n)) % self.size
        for name, data in self._data.items():
            block = blocks.get(name)
            data[idx] = 0.0 if block is None else block
        self._written += n
        self._t_end = t_start + n / self.sr

    def reference(self, t_from: float, seconds: float) -> dict[str, np.ndarray]:
        """Each speaker's probe from server time `t_from`, for `seconds`."""
        if self._t_end is None:
            msg = "no probe was played yet"
            raise RingError(msg)
        n = round(seconds * self.sr)
        back = round((self._t_end - t_from) * self.sr)
        if back < n:
            msg = "part of that span was not played yet; ask again later"
            raise RingError(msg)
        if back > min(self.size, self._written):
            msg = f"that span is gone: the server keeps the last {self.size / self.sr:.0f} s"
            raise RingError(msg)
        start = self._written - back
        idx = (start + np.arange(n)) % self.size
        return {name: data[idx].astype(float) for name, data in self._data.items()}
