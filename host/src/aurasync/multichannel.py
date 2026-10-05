"""A multichannel source: a WAV with one channel per speaker, in the installation's order.

For a render made elsewhere (experimentos/17 §1.1: FFmpeg's `surround`, the HTDemucs stems) to be
heard through each speaker's alignment, EQ, gain, volume and limiter, the engine reads it itself,
bypassing the virtual input and the upmix (`motor.procesar(…, canales=…)`). Read whole at the start
and handed out in fixed-length blocks, looping (d-7c8794-589dec: the engine thread does fixed work).
"""

from __future__ import annotations

import wave
from pathlib import Path

import numpy as np


class MultichannelFile:
    def __init__(self, path: Path | str, names: list[str], rate: int) -> None:
        path = Path(path).expanduser()
        if not path.is_file():
            msg = f"no such file: {path}"
            raise ValueError(msg)
        with wave.open(str(path), "rb") as w:
            channels, width, file_rate, frames = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
            raw = w.readframes(frames)
        if channels != len(names):
            msg = f"{path.name} has {channels} channels; this installation has {len(names)} speakers"
            raise ValueError(msg)
        if file_rate != rate:
            msg = f"{path.name} is at {file_rate} Hz; the session runs at {rate} (convert it with ffmpeg -ar {rate})"
            raise ValueError(msg)
        if width != 2:  # noqa: PLR2004
            msg = f"{path.name} must be 16-bit PCM"
            raise ValueError(msg)
        data = np.frombuffer(raw, dtype="<i2").reshape(-1, channels).astype(float) / 32768
        if not len(data):
            msg = f"{path.name} is empty"
            raise ValueError(msg)
        self.path = path
        self.names = list(names)
        self.rate = rate
        self._data = data
        self._pos = 0

    @property
    def seconds(self) -> float:
        return len(self._data) / self.rate

    def read(self, n: int) -> dict[str, np.ndarray]:
        idx = (self._pos + np.arange(n)) % len(self._data)
        self._pos = (self._pos + n) % len(self._data)
        block = self._data[idx]
        return {name: block[:, i].copy() for i, name in enumerate(self.names)}

    @staticmethod
    def downmix(blocks: dict[str, np.ndarray]) -> tuple[np.ndarray, np.ndarray]:
        """The same mono mix on both sides: what the input meters show while it plays."""
        mono = np.mean(np.stack(list(blocks.values())), axis=0)
        return mono, mono.copy()
