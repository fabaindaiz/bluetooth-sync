"""The headphone monitor: a second output of the engine, not synchronised with the speakers
(spec docs/superpowers/specs/2026-10-04-headphone-monitor-design.md).

`stereo` sends the input pair as the application plays it; `mix` folds the speaker channels to
L/R by each speaker's angle; `binaural` sends them to PipeWire's SOFA spatializer, each one a
virtual speaker at its angle, so the room is heard on headphones. The monitor never changes
what the speakers get, and never makes the engine wait: it writes from its own thread and drops
blocks when the device falls behind.
"""

from __future__ import annotations

import contextlib
import fcntl
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

if TYPE_CHECKING:
    from collections.abc import Callable

MODES = ("off", "stereo", "mix", "binaural")
GAIN_RANGE_DB = (-40.0, 0.0)
DEFAULT_GAIN_DB = -12.0
MAX_INPUTS = 8
"""The builtin `mixer` of PipeWire's filter-chain takes up to 8 inputs: the speaker maximum."""
SOFA = "/usr/share/libmysofa/MIT_KEMAR_normal_pinna.sofa"
"""The HRTF that comes with libmysofa (VERIFICADO on PC-Ryzen5, 2026-10-04)."""
log = logging.getLogger("aurasync.monitor")
NO_MOVE = "node.dont-move = true node.dont-reconnect = true node.dont-fallback = true"
LATENCY_MS = 100
QUEUE_BLOCKS = 4
DRIVER_QUANTUM_FRAMES = 2048
"""The Bluetooth driver's quantum with A2DP (WH-CH520, AAC): 2048 frames, 42.7 ms at 48 kHz, as
`pw-top` showed on HP-O16 on 2026-10-05 (MEDIDO). `pw-play` asks the pipe for one every cycle."""
MAX_CUSHION_S = 0.4
"""Cap of the cushion: more than this is latency the listener hears against the picture."""
BACKLOG_BLOCKS = 2
SETTLE_S = 0.8
"""How long after opening the routing is read: PipeWire links a stream after its first data."""


class MonitorError(ValueError):
    """Settings the monitor cannot use."""


class LoopError(MonitorError):
    """The target is a speaker or one of aurasync's own nodes."""


@dataclass(frozen=True)
class MonitorSettings:
    mode: str = "off"
    target: str | None = None
    gain_db: float = DEFAULT_GAIN_DB

    def __post_init__(self) -> None:
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
        return cls(mode=str(data.get("mode", "off")), target=str(target) if target else None, gain_db=gain)

    def to_json(self) -> dict[str, Any]:
        return {"mode": self.mode, "target": self.target, "gain_db": float(self.gain_db)}


def check_target(settings: MonitorSettings, forbidden: set[str]) -> None:
    """A speaker of the installation or one of aurasync's own nodes would loop the audio back
    into the speakers or into the input (experimentos/09): refused."""
    if settings.mode != "off" and settings.target in forbidden:
        msg = f"{settings.target} is a speaker or an aurasync node: the monitor would loop back into it"
        raise LoopError(msg)


def list_sinks(dump: list[dict]) -> list[dict[str, str]]:
    """Every output PipeWire has (`Audio/Sink`), from `pw-dump`, in its order."""
    out = []
    for o in dump:
        props = (o.get("info") or {}).get("props") or {}
        if str(o.get("type")).endswith("Node") and props.get("media.class") == "Audio/Sink" and props.get("node.name"):
            name = str(props["node.name"])
            out.append({"node": name, "description": str(props.get("node.description") or name)})
    return out


def candidates(sinks: list[dict[str, str]], forbidden: set[str]) -> list[dict[str, str]]:
    """The sinks the monitor may go to: not a speaker of the installation, not aurasync's own."""
    return [s for s in sinks if s["node"] not in forbidden and not s["node"].startswith("aurasync")]


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


def frame(
    mode: str,
    pair: tuple[np.ndarray, np.ndarray] | None,
    blocks: dict[str, np.ndarray],
    names: list[str],
    angles: dict[str, float],
    gain: float,
) -> np.ndarray:
    """What goes out for one block, interleaved as (samples, channels)."""
    n = len(next(iter(blocks.values()))) if blocks else (len(pair[0]) if pair else 0)
    if mode == "stereo":
        out = np.zeros((n, 2)) if pair is None else np.column_stack(pair)
    elif mode == "mix":
        out = np.column_stack(fold(blocks, angles))
    else:
        out = np.column_stack([blocks.get(name, np.zeros(n)) for name in names])
    return out * gain


def _write_all(stream: Any, data: bytes) -> None:
    """An unbuffered pipe may take part of the data: write until all of it is in."""
    view = memoryview(data)
    while view:
        n = stream.write(view)
        view = view[n:] if isinstance(n, int) else view[len(view) :]


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
        self.target_frames = min(block + DRIVER_QUANTUM_FRAMES, int(MAX_CUSHION_S * rate))
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


class Writer:
    """Writes frames from its own thread; `push` never waits. When the device falls behind the
    oldest frame is dropped and counted: a monitor that stutters is better than an engine that
    is late for the speakers. With a `cushion` it reads the pipe `level` (frames) before each
    block and refills with silence or drops the block as the cushion says."""

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
        self._gain = 10 ** (settings.gain_db / 20)
        self._module: subprocess.Popen | None = None
        self._play: subprocess.Popen | None = None
        self.writer: Writer | None = None
        self.cushion = Cushion(block, rate)
        self.pipe_bytes: int | None = None
        """The pipe's real size after asking for room for the cushion; `None` if it could not be set."""

    @property
    def channels(self) -> int:
        return len(self.names) if self.settings.mode == "binaural" else 2

    def open(self) -> None:
        from aurasync import sonido  # noqa: PLC0415 - sonido pulls the PipeWire helpers, not needed to test the rest

        target = str(self.settings.target)
        if self.settings.mode == "binaural":
            if not Path(SOFA).is_file():
                msg = f"no HRTF at {SOFA}: install libmysofa"
                raise MonitorError(msg)
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
        positions = [f"AUX{i}" for i in range(self.channels)] if self.settings.mode == "binaural" else ["FL", "FR"]
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
                ",".join(positions),
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

    def push(self, pair: tuple[np.ndarray, np.ndarray] | None, blocks: dict[str, np.ndarray]) -> None:
        if self.writer is not None:
            self.writer.push(frame(self.settings.mode, pair, blocks, self.names, self.angles, self._gain))

    @property
    def pid(self) -> int | None:
        return self._play.pid if self._play is not None and self._play.poll() is None else None

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
