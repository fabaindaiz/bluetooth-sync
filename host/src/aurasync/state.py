"""Contrato entre el motor y el panel: el snapshot y su única serialización.

El PC y el teléfono ven el mismo JSON, que sale de `Snapshot.to_json()` y de ningún
otro lado (docs/research/09 §4).
"""

import json
from dataclasses import asdict, dataclass, field

# Canales por modo. LCRS es el formato de Pro Logic: L, C, R y un surround mono
# (docs/research/07 §4.5).
MODES: dict[str, tuple[str, ...]] = {
    "quad": ("FL", "FR", "RL", "RR"),
    "lcrs": ("FL", "FC", "FR", "RC"),
}

SOURCE_KINDS = ("system", "app", "file", "tone")
SPEAKER_STATES = ("unseen", "seen", "synced", "lost")
SERVICE_STATES = ("stopped", "starting", "running", "stopping", "failed", "unavailable")
SERVICE_KINDS = ("proceso", "tarea", "enlace", "sistema")
SILENCE_DB = -120.0

# Configuración del motor y su dominio cerrado (docs/research/09 §7.3). Las claves
# "en vivo" no reinician el emisor; el resto sí.
CONFIG_CHOICES: dict[str, tuple] = {
    "upmix": ("simple", "psd", "surround"),
    "presentation_delay_us": (20_000, 40_000, 80_000),
    "bitrate_kbps": (80, 96, 124),
    "transport": ("low_latency", "high_reliability"),
}
REAR_DELAY_RANGE_MS = (0.0, 30.0)
BROADCAST_NAME_MAX = 32
LIVE_CONFIG_KEYS = frozenset({"upmix", "rear_delay_ms"})
FIXED_CONFIG_KEYS = frozenset({"manufacturer_data"})


@dataclass
class Source:
    kind: str
    name: str | None


@dataclass
class EngineInfo:
    kind: str
    running: bool
    mode: str
    source: Source
    master_db: float
    scanning: bool = False


@dataclass
class EngineConfig:
    upmix: str = "psd"
    rear_delay_ms: float = 12.0
    presentation_delay_us: int = 40_000
    bitrate_kbps: int = 80
    transport: str = "high_reliability"
    broadcast_name: str = "aurasync"
    manufacturer_data: str = "harman"


@dataclass
class Latency:
    capture_ms: float = 0.0
    codec_ms: float = 0.0
    transport_ms: float = 0.0
    presentation_ms: float = 0.0
    total_ms: float = 0.0


@dataclass
class Service:
    name: str
    label: str
    kind: str
    managed: bool
    state: str
    depends_on: list[str] = field(default_factory=list)
    pid: int | None = None
    started_at: str | None = None
    uptime_s: float | None = None
    restarts: int = 0
    detail: str = ""
    last_error: str | None = None


@dataclass
class BigState:
    state: str = "idle"
    num_bis: int = 0
    presentation_delay_us: int = 40_000
    sdu_interval_us: int = 10_000


@dataclass
class ControllerState:
    present: bool
    port: str | None
    unit: str | None
    firmware: str | None
    # True, False o None. None significa "no observado", nunca "no puede".
    iso_broadcaster: bool | None
    big: BigState


@dataclass
class ClockState:
    queue_sdus: float = 0.0
    queue_target: int = 3
    ratio_ppm: float = 0.0
    drift_ppm: float = 0.0
    underruns: int = 0
    overruns: int = 0
    history: list[float] = field(default_factory=list)


@dataclass
class Speaker:
    address: str
    name: str
    model: str
    firmware: str | None
    channel: str | None
    bis_index: int | None
    state: str
    rssi_dbm: int | None
    volume_db: float
    muted: bool
    delay_ms: float
    gain_db: float


@dataclass
class Meter:
    rms_db: float = SILENCE_DB
    peak_db: float = SILENCE_DB


@dataclass
class CalibrationResult:
    channel: str
    delay_ms: float
    gain_db: float
    confidence: float


@dataclass
class CalibrationState:
    state: str = "idle"
    progress: float = 0.0
    results: list[CalibrationResult] = field(default_factory=list)
    measured_at: str | None = None
    simulated: bool = False


@dataclass
class Snapshot:
    seq: int
    engine: EngineInfo
    controller: ControllerState
    clock: ClockState
    speakers: list[Speaker]
    meters: dict[str, Meter]
    calibration: CalibrationState
    services: list[Service]
    config: EngineConfig
    latency: Latency

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, separators=(",", ":"))

    def speaker(self, address: str) -> Speaker | None:
        return next((s for s in self.speakers if s.address == address), None)

    def service(self, name: str) -> Service | None:
        return next((s for s in self.services if s.name == name), None)
