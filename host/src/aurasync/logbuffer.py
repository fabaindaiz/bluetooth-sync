"""Búfer en memoria del log del proceso, para mostrarlo en el panel
(docs/research/09 §7.2).

Es un `logging.Handler` común: lo que el código anota con `logging` llega aquí, sin
un segundo camino. Cada línea lleva un número creciente (`seq`) para que el servidor
mande a cada cliente solo las nuevas.
"""

import logging
import threading
from collections import deque
from datetime import datetime

_SERVICE_PREFIXES = (
    ("aurasync.svc.", None),  # el servicio es el segmento que sigue
    ("aurasync.panel", "panel"),
    ("aurasync.engine", "motor"),
)


def service_of(logger_name: str) -> str:
    for prefix, fixed in _SERVICE_PREFIXES:
        if logger_name.startswith(prefix):
            return fixed or logger_name[len(prefix) :].split(".")[0]
    return logger_name.split(".")[0]


class LogBuffer(logging.Handler):
    def __init__(self, capacity: int = 2000) -> None:
        super().__init__(level=logging.DEBUG)
        self._records: deque[dict] = deque(maxlen=capacity)
        self._seq = 0
        self._lock = threading.Lock()
        self._attached: tuple[str, int] | None = None
        self.setFormatter(logging.Formatter("%(message)s"))

    @property
    def last_seq(self) -> int:
        return self._seq

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = self.format(record)
        except Exception:  # noqa: BLE001 (un log mal formado no puede romper a quien anota)
            message = str(record.msg)
        at = datetime.fromtimestamp(record.created).astimezone().isoformat(timespec="milliseconds")
        with self._lock:
            self._seq += 1
            self._records.append(
                {
                    "seq": self._seq,
                    "at": at,
                    "level": record.levelname.lower(),
                    "service": service_of(record.name),
                    "logger": record.name,
                    "message": message,
                }
            )

    def attach(self, name: str = "aurasync") -> None:
        """Engancha el búfer al logger `name` en DEBUG. Se hace antes de crear el motor,
        para que sus primeras líneas también lleguen al panel."""
        logger = logging.getLogger(name)
        if self in logger.handlers:
            return
        self._attached = (name, logger.level)
        logger.setLevel(logging.DEBUG)
        logger.addHandler(self)

    def detach(self) -> None:
        if self._attached is None:
            return
        name, level = self._attached
        logger = logging.getLogger(name)
        logger.removeHandler(self)
        logger.setLevel(level)
        self._attached = None

    def tail(self, count: int) -> list[dict]:
        with self._lock:
            return list(self._records)[-count:]

    def since(self, seq: int) -> list[dict]:
        with self._lock:
            return [r for r in self._records if r["seq"] > seq]
