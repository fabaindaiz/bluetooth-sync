"""The headphone monitor: a second output of the engine, not synchronised with the speakers
(spec docs/superpowers/specs/2026-10-04-headphone-monitor-design.md).

`stereo` sends the input pair as the application plays it, at the chosen volume; `mix` folds the speaker channels to
L/R by each speaker's angle; `binaural` sends them to PipeWire's SOFA spatializer, each one a
virtual speaker at its angle, so the room is heard on headphones. The monitor never changes
what the speakers get, and never makes the engine wait: it writes from its own thread and drops
blocks when the device falls behind.

Every mode is heard at the same loudness (brief 2026-10-05): `stereo` is the input at the chosen
volume, the reference; `mix` and `binaural` follow it through a makeup gain per mode
(`loudness_match.py`), metered here with the same K-weighted meter as the quality strip.
"""

from __future__ import annotations

import contextlib
import fcntl
import functools
import hashlib
import logging
import math
import queue
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

# The cushion lives in its own module since the speakers use it too (cushion.py); its names stay
# importable from here for the monitor's callers.
from aurasync.cushion import (  # noqa: F401 - DRIVER_QUANTUM_FRAMES and MAX_CUSHION_S are re-exported
    BACKLOG_BLOCKS,
    DRIVER_QUANTUM_FRAMES,
    MAX_CUSHION_S,
    Cushion,
    new_stretcher,
)
from aurasync.dsp.loudness import LoudnessMeter

if TYPE_CHECKING:
    from collections.abc import Callable

    from aurasync.loudness_match import LoudnessMatch

MODES = ("off", "stereo", "mix", "binaural")
GAIN_RANGE_DB = (-40.0, 0.0)
DEFAULT_GAIN_DB = -12.0
VOLUME_CONTROLS = ("device", "software")
DEFAULT_DEVICE_LIMIT_PCT = 30.0
MAX_DEVICE_VOLUME_PCT = 100.0
MAX_INPUTS = 8
"""The builtin `mixer` of PipeWire's filter-chain takes up to 8 inputs: the speaker maximum."""
SOFA = "/usr/share/libmysofa/MIT_KEMAR_normal_pinna.sofa"
"""The HRTF that comes with libmysofa (VERIFICADO on PC-Ryzen5, 2026-10-04)."""
log = logging.getLogger("aurasync.monitor")
NO_MOVE = "node.dont-move = true node.dont-reconnect = true node.dont-fallback = true"
LATENCY_MS = 100
QUEUE_BLOCKS = 4
SETTLE_S = 0.8
"""How long after opening the routing is read: PipeWire links a stream after its first data."""
MIX_ESTIMATE_DB = {1: 6.2, 2: 1.9, 3: 0.5, 4: -0.3, 5: -1.2, 6: -2.2, 7: -2.9, 8: -3.9}
"""Where `mix` starts the first time, by number of speakers: minus how much louder than the input
the fold of the speakers is. MEDIDO offline on HP-O16 with the real engine (default chain, 0 dB),
the `auto` layout of each N and stereo pink noise at -20 dBFS RMS (experimentos/18 "Volumen entre
modos del monitor"); it grows about as `10 log10(N/4)`. Only a start: the match measures `mix`
itself."""
UNMEASURED_HRTF = "binaural compensation not measured for this HRTF"
KEMAR_SHA256 = "2768ac841213a7ae11d1ea7fd0f25a69b39216102dc5dd913ea6ba0f0dc57e28"
"""SHA-256 of libmysofa's `MIT_KEMAR_normal_pinna.sofa` (HP-O16, 2026-10-05)."""
HRTF_GAIN_DB: dict[str, dict[tuple[float, ...], float]] = {
    KEMAR_SHA256: {
        (0.0,): 6.10,
        (-90.0, 90.0): 6.40,
        (-60.0, 60.0, 180.0): 6.02,
        (-135.0, -45.0, 45.0, 135.0): 5.75,
        (-108.0, -36.0, 36.0, 108.0, 180.0): 5.60,
        (-150.0, -90.0, -30.0, 30.0, 90.0, 150.0): 5.69,
        (-128.6, -77.1, -25.7, 25.7, 77.1, 128.6, 180.0): 5.60,
        (-157.5, -112.5, -67.5, -22.5, 22.5, 67.5, 112.5, 157.5): 5.46,
    }
}
"""The binaural chain's broadband gain, dB: K-weighted loudness of its L/R output minus that of the
N channels it got (G = 1), with independent pink noise per channel, for the `auto` layout of each
N = 1..8 (sorted angles, `_angle_key`). MEDIDO on HP-O16, 2026-10-05, PipeWire 1.6.9, the monitor's
own filter-chain into a temporary null sink (`probes/21-ganancia-hrtf/`, experimentos/18 "Volumen
entre modos del monitor"); the user's quad measured again apart: 5.73. With the **same** signal in
every channel the gain went from +4.2 to +9.1 dB (the HRTF's ears sum it coherently): the table is
right for uncorrelated speakers and off by up to that much for correlated ones (INFERIDO for music,
which is in between)."""
HRTF_ANGLE_GAIN_DB: dict[str, dict[float, float]] = {
    KEMAR_SHA256: {
        0.0: 6.10,
        15.0: 6.27,
        30.0: 6.66,
        45.0: 6.92,
        60.0: 6.82,
        75.0: 6.57,
        90.0: 6.38,
        105.0: 5.52,
        120.0: 4.77,
        135.0: 4.21,
        150.0: 3.66,
        165.0: 3.59,
        180.0: 3.78,
    }
}
"""One input alone at each azimuth (deg from the front, either side), dB, same probe. -45, -90 and
-135 measured equal to +45, +90 and +135 (MEDIDO): the HRTF is symmetric. The power mean of these
reproduces the measured sets within 0.2 dB (auto4: 5.77 against 5.75; auto8: 5.65 against 5.46)."""


class MonitorError(ValueError):
    """Settings the monitor cannot use."""


class LoopError(MonitorError):
    """The target is a speaker or one of aurasync's own nodes."""


@dataclass(frozen=True)
class MonitorSettings:
    mode: str = "off"
    target: str | None = None
    gain_db: float = DEFAULT_GAIN_DB
    volume_control: str = "device"
    """`device`: the level is the target sink's volume (the headphones', AVRCP absolute volume for
    Bluetooth) and the software gain stays at 0 dB; `software`: `gain_db`, the sink untouched."""
    device_volume_pct: float | None = None
    """The last sink volume the listener set from the panel: the ceiling it is lowered to when the
    monitor opens (never raised). `None`: never set, `DEFAULT_DEVICE_LIMIT_PCT` applies."""

    def __post_init__(self) -> None:
        if self.volume_control not in VOLUME_CONTROLS:
            msg = f"volume_control must be one of {', '.join(VOLUME_CONTROLS)}"
            raise MonitorError(msg)
        if self.device_volume_pct is not None and not 0.0 <= self.device_volume_pct <= MAX_DEVICE_VOLUME_PCT:
            msg = "device_volume_pct must be within [0, 100]"
            raise MonitorError(msg)
        if self.mode not in MODES:
            msg = f"mode must be one of {', '.join(MODES)}"
            raise MonitorError(msg)
        lo, hi = GAIN_RANGE_DB
        if not lo <= self.gain_db <= hi:
            msg = f"gain_db must be within [{lo:g}, {hi:g}]"
            raise MonitorError(msg)
        if self.mode != "off" and not self.target:
            msg = "a monitor needs a target sink"
            raise MonitorError(msg)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> MonitorSettings:
        try:
            gain = float(data.get("gain_db", DEFAULT_GAIN_DB))
        except (TypeError, ValueError) as exc:
            msg = "gain_db must be a number"
            raise MonitorError(msg) from exc
        target = data.get("target")
        pct = data.get("device_volume_pct")
        try:
            pct = None if pct is None else float(pct)
        except (TypeError, ValueError) as exc:
            msg = "device_volume_pct must be a number"
            raise MonitorError(msg) from exc
        return cls(
            mode=str(data.get("mode", "off")),
            target=str(target) if target else None,
            gain_db=gain,
            volume_control=str(data.get("volume_control", "device")),
            device_volume_pct=pct,
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "target": self.target,
            "gain_db": float(self.gain_db),
            "volume_control": self.volume_control,
            "device_volume_pct": self.device_volume_pct,
        }


def check_target(settings: MonitorSettings, forbidden: set[str]) -> None:
    """A speaker of the installation or one of aurasync's own nodes would loop the audio back
    into the speakers or into the input (experimentos/09): refused."""
    if settings.mode != "off" and settings.target in forbidden:
        msg = f"{settings.target} is a speaker or an aurasync node: the monitor would loop back into it"
        raise LoopError(msg)


def list_sinks(dump: list[dict]) -> list[dict[str, Any]]:
    """Every output PipeWire has (`Audio/Sink`), from `pw-dump`, in its order, with its id: a
    Bluetooth profile switch rebuilds a sink under the same name, and only the id tells."""
    out = []
    for o in dump:
        props = (o.get("info") or {}).get("props") or {}
        if str(o.get("type")).endswith("Node") and props.get("media.class") == "Audio/Sink" and props.get("node.name"):
            name = str(props["node.name"])
            out.append(
                {
                    "node": name,
                    "description": str(props.get("node.description") or name),
                    "id": o.get("id"),
                    "address": props.get("api.bluez5.address"),
                }
            )
    return out


def candidates(sinks: list[dict[str, Any]], forbidden: set[str]) -> list[dict[str, str]]:
    """The sinks the monitor may go to: not a speaker of the installation, not aurasync's own."""
    return [
        {"node": s["node"], "description": s["description"]}
        for s in sinks
        if s["node"] not in forbidden and not s["node"].startswith("aurasync")
    ]


def fold(blocks: dict[str, np.ndarray], angles: dict[str, float]) -> tuple[np.ndarray, np.ndarray]:
    """Constant-power fold to L/R: `angle` in degrees, 0 in front, positive to the right; a
    speaker without an angle (an ambient one) goes to the middle. Only the side counts: behind
    folds like its mirror in front."""
    n = len(next(iter(blocks.values()))) if blocks else 0
    left, right = np.zeros(n), np.zeros(n)
    for name, x in blocks.items():
        theta = (math.sin(math.radians(angles.get(name, 0.0))) + 1.0) * math.pi / 4
        left += math.cos(theta) * x
        right += math.sin(theta) * x
    return left, right


def sofa_azimuth(angle: float) -> float:
    """SOFA's azimuth turns counter-clockwise (90 is the left); this repository's, clockwise."""
    return float((-angle) % 360.0) + 0.0


def binaural_args(names: list[str], angles: dict[str, float], sink: str, target: str, sofa: str = SOFA) -> str:
    """The `libpipewire-module-filter-chain` arguments: one spatializer per speaker, at its angle,
    into two mixers; its input is a sink with one AUX channel per speaker."""
    if len(names) > MAX_INPUTS:
        msg = f"the binaural monitor takes up to {MAX_INPUTS} speakers (the filter-chain mixer)"
        raise ValueError(msg)
    nodes = [
        f'{{ type = sofa label = spatializer name = sp{i} config = {{ filename = "{sofa}" }} '
        f'control = {{ "Azimuth" = {sofa_azimuth(angles.get(name, 0.0)):.1f} "Elevation" = 0.0 "Radius" = 1.0 }} }}'
        for i, name in enumerate(names)
    ]
    nodes += ["{ type = builtin label = mixer name = mixL }", "{ type = builtin label = mixer name = mixR }"]
    links = [
        f'{{ output = "sp{i}:Out {side}" input = "mix{side}:In {i + 1}" }}' for i in range(len(names)) for side in "LR"
    ]
    channels = " ".join(f"AUX{i}" for i in range(len(names)))
    inputs = " ".join(f'"sp{i}:In"' for i in range(len(names)))
    return (
        f'{{ node.description = "aurasync monitor (binaural)" media.name = "aurasync monitor" '
        f"filter.graph = {{ nodes = [ {' '.join(nodes)} ] links = [ {' '.join(links)} ] "
        f'inputs = [ {inputs} ] outputs = [ "mixL:Out" "mixR:Out" ] }} '
        f'capture.props = {{ node.name = "{sink}" media.class = Audio/Sink audio.channels = {len(names)} '
        f"audio.position = [ {channels} ] node.dont-move = true }} "
        f'playback.props = {{ node.name = "{sink}_out" audio.channels = 2 audio.position = [ FL FR ] '
        f'target.object = "{target}" {NO_MOVE} }} }}'
    )


def play_channel_map(mode: str, channels: int) -> str:
    """`pw-play --channel-map`. One name alone is read as a layout and refused ("channels and
    channel-map incompatible", MEDIDO with pw-play 1.6.9 on HP-O16): a trailing comma makes it a
    list of one, which a one-speaker binaural monitor needs."""
    if mode != "binaural":
        return "FL,FR"
    names = ",".join(f"AUX{i}" for i in range(channels))
    return names + "," if channels == 1 else names


def _angle_key(angle: float) -> float:
    """An angle in (-180, 180], to 0.1 degree: the HRTF tables' key."""
    a = round(float(angle), 1) % 360.0
    return round(a - 360.0 if a > 180.0 else a, 1) + 0.0  # noqa: PLR2004 - half a turn


def _single_gain(table: dict[float, float], angle: float) -> float:
    """One input's gain at `angle`, interpolated between the measured ones; the HRTF is taken as
    symmetric (the probe measured both sides: experimentos/18)."""
    a = abs(_angle_key(angle))
    keys = sorted(table)
    return float(np.interp(a, keys, [table[k] for k in keys]))


def hrtf_gain_db(sha: str, angles: list[float]) -> float | None:
    """How much the binaural chain changes the K-weighted loudness, out (L+R) minus in (the N
    channels, G = 1), for the SOFA file with this SHA-256 and these speaker angles. `None`: never
    measured for this file.

    A set measured as such (`HRTF_GAIN_DB`, the `auto` layouts) gives its own number; any other set
    the power mean of each angle's single-input gain (`HRTF_ANGLE_GAIN_DB`): what equal,
    uncorrelated channels would give (INFERIDO from the measured sets, experimentos/18)."""
    sets = HRTF_GAIN_DB.get(sha)
    if sets is None:
        return None
    key = tuple(sorted(_angle_key(a) for a in angles))
    if key in sets:
        return sets[key]
    single = HRTF_ANGLE_GAIN_DB.get(sha)
    if not single or not angles:
        return None
    powers = [10 ** (_single_gain(single, a) / 10) for a in angles]
    return round(10 * math.log10(sum(powers) / len(powers)), 2)


@functools.lru_cache(maxsize=4)
def sofa_sha256(path: str) -> str | None:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


@dataclass(frozen=True)
class Levels:
    """What the monitor needs from the engine for one block.

    `volume_db` is the volume the listener chose (the panel's knob); `digital_db` the digital
    volume the speakers' blocks **were made with** (`Motor.volumen_del_bloque_db`: per sample while
    it ramps, and the old value in the block whose cut it jumps at). With the digital volume both
    are the same; with `volume.avrcp` the speakers carry the knob and the digital one is 0 dB, so
    the monitor applies the difference to what comes after the chain. Reading the motor's target
    instead made the last block of the cut that leaves `avrcp` 14 dB too loud (review, 2026-10-05).
    `hold`: a cut or a calibration, when the speakers do not play the music and the loudness match
    must not learn from them."""

    volume_db: float = 0.0
    digital_db: float | np.ndarray = 0.0
    hold: bool = False


def frame(
    mode: str,
    pair: tuple[np.ndarray, np.ndarray] | None,
    blocks: dict[str, np.ndarray],
    names: list[str],
    angles: dict[str, float],
    gain: float,
    *,
    volume: float = 1.0,
    post: float = 1.0,
) -> np.ndarray:
    """What goes out for one block, interleaved as (samples, channels). `volume` scales the input
    pair (`stereo`, which comes before the chain), `post` the speakers' blocks (after it)."""
    n = len(next(iter(blocks.values()))) if blocks else (len(pair[0]) if pair else 0)
    if mode == "stereo":
        out = np.zeros((n, 2)) if pair is None else np.column_stack(pair) * volume
    elif mode == "mix":
        out = np.column_stack(fold(blocks, angles)) * post
    else:
        out = np.column_stack([blocks.get(name, np.zeros(n)) for name in names]) * post
    return out * gain


def _scaled(x: np.ndarray, db: float | np.ndarray) -> np.ndarray:
    """`x` (samples, channels) times a gain in dB, one for the block or one per sample."""
    if isinstance(db, np.ndarray):
        return x * (10 ** (db / 20))[:, None]
    return x * 10 ** (db / 20) if db != 0.0 else x


def _finite(x: float) -> float | None:
    return float(x) if x is not None and math.isfinite(x) else None


def _write_all(stream: Any, data: bytes) -> None:
    """An unbuffered pipe may take part of the data: write until all of it is in."""
    view = memoryview(data)
    while view:
        n = stream.write(view)
        view = view[n:] if isinstance(n, int) else view[len(view) :]


class Writer:
    """Writes frames from its own thread; `push` never waits. When the device falls behind the
    oldest frame is dropped and counted: a monitor that stutters is better than an engine that
    is late for the speakers. With a `cushion` it reads the pipe `level` (frames) before each
    block and refills with silence or drops the block as the cushion says, and sends the block through
    the cushion's stretcher (stage 4: slightly slower or faster while the pipe is off its target)."""

    def __init__(
        self,
        write: Callable[[bytes], None],
        depth: int = QUEUE_BLOCKS,
        *,
        cushion: Cushion | None = None,
        level: Callable[[], int | None] | None = None,
        channels: int = 2,
    ) -> None:
        self._write = write
        self.cushion = cushion
        self._level = level
        self._channels = channels
        self._queue: queue.Queue[np.ndarray | None] = queue.Queue(maxsize=depth)
        self.drops = 0
        self.failed = False
        self._thread = threading.Thread(target=self._run, name="aurasync-monitor", daemon=True)
        self._thread.start()

    def push(self, out: np.ndarray) -> None:
        while True:
            try:
                self._queue.put_nowait(out)
            except queue.Full:
                with contextlib.suppress(queue.Empty):
                    self._queue.get_nowait()
                    self.drops += 1
            else:
                return

    def _run(self) -> None:
        while (out := self._queue.get()) is not None:
            try:
                if self.cushion is not None and self._level is not None:
                    silence, keep = self.cushion.plan(self._level())
                    if silence:
                        self._write(bytes(4 * self._channels * silence))
                    if not keep:
                        continue
                    out = self.cushion.process(out)  # the cushion's stretcher (stage 4): idle, the same block
                self._write(np.clip(out, -1.0, 1.0).astype("<f4").tobytes())
            except (BrokenPipeError, OSError, ValueError):
                self.failed = True
                return

    def close(self) -> None:
        with contextlib.suppress(queue.Full):
            while True:
                try:
                    self._queue.put_nowait(None)
                    break
                except queue.Full:
                    with contextlib.suppress(queue.Empty):
                        self._queue.get_nowait()
        self._thread.join(timeout=2)


class MonitorOutput:
    """The monitor's PipeWire side: the filter-chain (binaural only) and one `pw-play --raw`."""

    def __init__(
        self,
        settings: MonitorSettings,
        names: list[str],
        angles: dict[str, float],
        rate: int,
        sink: str,
        block: int = 4096,
    ) -> None:
        self.settings = settings
        self.names = list(names)
        self.angles = dict(angles)
        self.rate = rate
        self.sink = sink
        # By the device the level is the sink's volume: the software gain stays at 0 dB.
        self._gain = 10 ** (settings.gain_db / 20) if settings.volume_control == "software" else 1.0
        self._module: subprocess.Popen | None = None
        self._play: subprocess.Popen | None = None
        self.writer: Writer | None = None
        self.cushion = Cushion(block, rate, new_stretcher(self.channels, rate))
        self.pipe_bytes: int | None = None
        """The pipe's real size after asking for room for the cushion; `None` if it could not be set."""
        self.levels: Callable[[], Levels] = Levels
        self.match: LoudnessMatch | None = None
        self.hrtf_gain_db: float | None = None
        self.match_reason: str | None = None
        self.loudness_reference: float | None = None
        """K-weighted short-term loudness (LUFS) of the input at the chosen volume, the reference."""
        self.loudness_monitor: float | None = None
        """The same of what the monitor sends, after the makeup and before its own level (`gain_db`)."""
        self._knob_db: float | None = None
        """The knob at the end of the last block: a change ramps from it over the next block."""
        self._reference = LoudnessMeter(rate, 2, history=False)
        self._candidate = (
            LoudnessMeter(rate, self.channels, history=False) if settings.mode in {"mix", "binaural"} else None
        )

    @property
    def channels(self) -> int:
        return len(self.names) if self.settings.mode == "binaural" else 2

    @property
    def gain(self) -> float:
        """The monitor's own level, linear: `gain_db` by software, 1 (0 dB) by the device."""
        return self._gain

    def use_software_gain(self) -> None:
        """From now on, the software gain: the device's volume could not be trusted (the audio
        went to another sink than the one checked). At most `DEFAULT_GAIN_DB`, whatever
        `gain_db` is stored (it may be 0 dB), and it only ever lowers the level."""
        self._gain = min(self._gain, 10 ** (min(self.settings.gain_db, DEFAULT_GAIN_DB) / 20))

    def set_hrtf(self, sha: str | None) -> None:
        """The binaural chain's gain for the SOFA file with this SHA-256 (`HRTF_GAIN_DB`); without
        a measurement, none is applied and `match_reason` says so."""
        self.hrtf_gain_db = hrtf_gain_db(sha, [self.angles.get(n, 0.0) for n in self.names]) if sha else None
        self.match_reason = UNMEASURED_HRTF if self.hrtf_gain_db is None else None

    def estimate_db(self) -> float:
        """The makeup a mode never seen starts from."""
        if self.settings.mode == "mix":
            return MIX_ESTIMATE_DB.get(len(self.names), 0.0)
        if self.settings.mode == "binaural" and self.hrtf_gain_db is not None:
            return -self.hrtf_gain_db
        return 0.0

    def bind(self, match: LoudnessMatch, levels: Callable[[], Levels]) -> None:
        """On the engine thread, before the first block: the service's match (it outlives this
        output, so each mode keeps its makeup) and where the volume and the hold come from."""
        self.match, self.levels = match, levels
        match.select(self.settings.mode, self.estimate_db())

    def open(self) -> None:
        from aurasync import sonido  # noqa: PLC0415 - sonido pulls the PipeWire helpers, not needed to test the rest

        target = str(self.settings.target)
        if self.settings.mode == "binaural":
            if not Path(SOFA).is_file():
                msg = f"no HRTF at {SOFA}: install libmysofa"
                raise MonitorError(msg)
            self.set_hrtf(sofa_sha256(SOFA))
            self._module = subprocess.Popen(
                ["pw-cli", "-m", "load-module", "libpipewire-module-filter-chain", self._args()],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            for _ in range(60):
                if sonido.nodo_existe(self.sink):
                    break
                time.sleep(0.05)
            else:
                self.close()
                msg = "the binaural filter did not appear in PipeWire"
                raise MonitorError(msg)
            target = self.sink
        self._play = subprocess.Popen(
            [
                "pw-play",
                "--target",
                target,
                "--rate",
                str(self.rate),
                "--channels",
                str(self.channels),
                "--channel-map",
                play_channel_map(self.settings.mode, self.channels),
                "--format",
                "f32",
                "--latency",
                f"{LATENCY_MS}ms",
                "-P",
                f"{{ {NO_MOVE} media.name = aurasync-monitor }}",
                "--raw",
                "-",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            bufsize=0,  # no BufferedWriter between the Writer and the pipe: FIONREAD then sees what is queued
        )
        stdin = self._play.stdin
        if stdin is not None:
            # Room for the cushion, a block and the backlog the cushion tolerates.
            frames = self.cushion.target_frames + (BACKLOG_BLOCKS + 1) * self.cushion.block
            try:
                self.pipe_bytes = int(fcntl.fcntl(stdin.fileno(), fcntl.F_SETPIPE_SZ, 4 * self.channels * frames))
            except (OSError, AttributeError, ValueError) as exc:
                log.warning("the monitor's pipe could not be resized (%s): the cushion may not fit", exc)
            try:
                _write_all(stdin, bytes(4 * self.channels * self.cushion.target_frames))
            except (BrokenPipeError, OSError) as exc:
                msg = f"pw-play exited at open: {exc}"
                raise MonitorError(msg) from exc

            def level() -> int | None:
                n = sonido.bytes_en_tuberia(stdin)
                return None if n is None else n // (4 * self.channels)

            self.writer = Writer(
                lambda data: _write_all(stdin, data), cushion=self.cushion, level=level, channels=self.channels
            )

    def _args(self) -> str:
        return binaural_args(self.names, self.angles, self.sink, str(self.settings.target))

    def render(self, pair: tuple[np.ndarray, np.ndarray] | None, blocks: dict[str, np.ndarray]) -> np.ndarray:
        """One block as it goes out: the mode's frame at the chosen volume, times the makeup and
        the monitor's own level. A knob change and the makeup move as ramps over the block, so no
        sample jumps. Meters the reference and the candidate for the match. On the engine thread."""
        lv = self.levels()
        mode = self.settings.mode
        body = frame(mode, pair, blocks, self.names, self.angles, 1.0)
        n = len(body)
        knob = self._knob_ramp(lv.volume_db, n)
        digital = lv.digital_db
        if isinstance(digital, np.ndarray) and len(digital) != n:
            digital = float(digital[-1]) if len(digital) else 0.0
        # Stereo comes before the chain: the knob. The others come after it, already at the
        # digital volume they were made with: the knob minus that.
        body = _scaled(body, knob if mode == "stereo" else knob - digital)
        match = self.match
        if match is None:
            return body * self._gain
        reference = _scaled(np.zeros((n, 2)) if pair is None else np.column_stack(pair), knob)
        self._reference.push(reference)
        ref_lufs = self._reference.short_term
        candidate: float | None = ref_lufs
        if self._candidate is not None:
            self._candidate.push(body)
            candidate = self._candidate.short_term
            if mode == "binaural":
                candidate = None if self.hrtf_gain_db is None else candidate + self.hrtf_gain_db
        # The pause is decided on the input, before the volume: at a quiet knob the music is
        # still music (review, 2026-10-05).
        gate = self._reference.momentary - lv.volume_db
        start, end = match.update(ref_lufs, candidate, n / self.rate, hold=lv.hold, gate_lufs=gate)
        self.loudness_reference = _finite(ref_lufs)
        self.loudness_monitor = None if candidate is None else _finite(candidate + end)
        if start == end:
            return body * (self._gain * 10 ** (end / 20))
        return _scaled(body * self._gain, start + (end - start) * np.arange(1, n + 1) / n)

    def _knob_ramp(self, volume_db: float, n: int) -> float | np.ndarray:
        """The knob for each sample of this block (dB): from where the last block ended to
        `volume_db`, linearly; a float when it did not move."""
        previous, self._knob_db = self._knob_db, float(volume_db)
        if previous is None or previous == volume_db or n == 0:
            return float(volume_db)
        return previous + (volume_db - previous) * np.arange(1, n + 1) / n

    def push(self, pair: tuple[np.ndarray, np.ndarray] | None, blocks: dict[str, np.ndarray]) -> None:
        if self.writer is not None:
            self.writer.push(self.render(pair, blocks))

    @property
    def pid(self) -> int | None:
        return self._play.pid if self._play is not None and self._play.poll() is None else None

    @property
    def lost(self) -> bool:
        """The player is gone: `pw-play` exited, or its pipe broke under the writer. Nothing
        it gets reaches the target any more (monitor_control.MonitorController.watch)."""
        if self._play is None:
            return False
        return self._play.poll() is not None or (self.writer is not None and self.writer.failed)

    def where(self) -> str | None:
        """`routing` on a fresh `pw-dump`, once the stream had time to link (it links after its
        first data). Off the engine thread: it waits and reads the whole graph."""
        from aurasync import sonido  # noqa: PLC0415

        time.sleep(SETTLE_S)
        return self.routing(sonido._pw_dump())  # noqa: SLF001 - the one reader of the graph

    def routing(self, dump: list[dict]) -> str | None:
        """Where the monitor's audio really ends: the sink the stream (in binaural, the filter's
        output) is linked to, from `pw-dump`. `None`: linked nowhere."""
        nodes: dict[int, dict] = {}
        client_of_pid: dict[int, int] = {}
        links: dict[int, set[int]] = {}
        for o in dump:
            kind, ident = str(o.get("type")), o.get("id")
            props = (o.get("info") or {}).get("props") or {}
            if kind.endswith("Client") and props.get("application.process.id") is not None and ident is not None:
                client_of_pid[int(props["application.process.id"])] = int(ident)
            elif kind.endswith("Node") and ident is not None:
                nodes[int(ident)] = props
            elif kind.endswith("Link") and "link.output.node" in props:
                links.setdefault(int(props["link.output.node"]), set()).add(int(props["link.input.node"]))
        name = {i: str(p.get("node.name", "")) for i, p in nodes.items()}

        def ends(start: int | None) -> list[str]:
            return sorted(name[d] for d in links.get(start, set()) if d in name) if start is not None else []

        client = client_of_pid.get(self.pid or -1)
        stream = next(
            (
                i
                for i, p in nodes.items()
                if p.get("client.id") == client and str(p.get("media.class", "")).startswith("Stream/Output")
            ),
            None,
        )
        first = ends(stream)
        if self.settings.mode == "binaural" and self.sink in first:
            # Through the filter: where its output goes is where the audio ends.
            first = ends(next((i for i, n in name.items() if n == f"{self.sink}_out"), None))
        return first[0] if first else None

    def close(self) -> None:
        if self.writer is not None:
            self.writer.close()
            self.writer = None
        if self._play is not None:
            if self._play.stdin is not None:
                with contextlib.suppress(BrokenPipeError, OSError):
                    self._play.stdin.close()
            try:
                self._play.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._play.kill()
            self._play = None
        if self._module is not None:
            self._module.terminate()
            try:
                self._module.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._module.kill()
            self._module = None
