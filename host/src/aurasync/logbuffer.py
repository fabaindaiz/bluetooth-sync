"""The process's own log, kept in memory for the panel.

A plain `logging.Handler`: whatever the code logs with `logging` lands here, with no second
path. Each line gets a growing `seq`, so a client asks only for what it has not seen
(`logs` with `since`). The service names its parts `aurasync.svc.<part>`, and the panel
filters by that part. Ported from the `panel-demo` branch (spec §15).
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from datetime import datetime

PREFIX = "aurasync.svc."


def part_of(logger_name: str) -> str:
    if logger_name.startswith(PREFIX):
        return logger_name[len(PREFIX) :].split(".")[0]
    return logger_name.split(".")[-1]


class LogBuffer(logging.Handler):
    def __init__(self, capacity: int = 2000) -> None:
        super().__init__(level=logging.DEBUG)
        self._records: deque[dict] = deque(maxlen=capacity)
        self._seq = 0
        self._lock = threading.Lock()
        self.setFormatter(logging.Formatter("%(message)s"))

    @property
    def last_seq(self) -> int:
        return self._seq

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = self.format(record)
        except Exception:  # noqa: BLE001 - a malformed line must not break whoever logged it
            message = str(record.msg)
        at = datetime.fromtimestamp(record.created).astimezone().isoformat(timespec="milliseconds")
        with self._lock:
            self._seq += 1
            self._records.append(
                {
                    "seq": self._seq,
                    "at": at,
                    "level": record.levelname.lower(),
                    "service": part_of(record.name),
                    "message": message,
                }
            )

    def since(self, seq: int, limit: int = 500) -> dict:
        """The lines after `seq`, at most `limit` (the newest). `gap` says lines were lost."""
        with self._lock:
            records = [r for r in self._records if r["seq"] > seq]
            oldest = self._records[0]["seq"] if self._records else self._seq + 1
        return {"records": records[-limit:], "last": self._seq, "gap": seq + 1 < oldest and seq > 0}
