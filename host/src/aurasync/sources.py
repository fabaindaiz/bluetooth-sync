"""What plays into the virtual sink: the whole system, one application, a file, or a test signal.

- **system**: nothing to do. Whatever the user routes to the "aurasync" output plays.
- **app**: the streams of one application are moved to the sink with `pactl move-sink-input`.
- **file** and **tone**: a `pw-play --target <sink>` in a loop. `tone` plays a generated
  signal (two low tones and faint pink noise, different per channel), the one the click
  detector of experiment 10 is built for.

**Every request is verified** (CLAUDE.md, experiment 09): after starting, the source looks
in `pw-dump` for where its stream really went. A `pw-play` whose target did not resolve goes
to the default sink, which can be a speaker; in that case the source is stopped at once.
"""

from __future__ import annotations

import contextlib
import json
import logging
import subprocess
import tempfile
import threading
import time
import wave
from pathlib import Path

import numpy as np

from aurasync import sonido

log = logging.getLogger("aurasync.svc.source")
SR = 48000


def probe_signal(seconds: float = 60.0, seed: int = 0) -> np.ndarray:
    """Stereo: 220 Hz left and 330 Hz right, plus faint pink noise different per channel."""
    n = int(SR * seconds)
    t = np.arange(n) / SR
    rng = np.random.default_rng(seed)

    def pink() -> np.ndarray:
        spectrum = np.fft.rfft(rng.standard_normal(n))
        f = np.fft.rfftfreq(n, 1 / SR)
        spectrum[1:] /= np.sqrt(f[1:])
        spectrum[0] = 0
        x = np.fft.irfft(spectrum, n)
        return x / np.max(np.abs(x))

    left = 0.22 * np.sin(2 * np.pi * 220 * t) + 0.06 * pink()
    right = 0.22 * np.sin(2 * np.pi * 330 * t) + 0.06 * pink()
    # Loops seamlessly: both tones complete whole cycles in a whole number of seconds,
    # and the noise is circular because it was built with an inverse FFT.
    return np.stack([left, right], axis=1)


def write_wav(path: Path, x: np.ndarray) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype("<i2").tobytes())


def streams_into(objects: list, sink_name: str) -> list[dict]:
    """The output streams linked to `sink_name`, from a parsed `pw-dump`."""
    nodes, links = {}, []
    for o in objects:
        props = (o.get("info") or {}).get("props") or {}
        kind = str(o.get("type"))
        if kind.endswith("Node"):
            nodes[o["id"]] = props
        elif kind.endswith("Link"):
            links.append(props)
    sink_ids = {i for i, p in nodes.items() if p.get("node.name") == sink_name}
    found = []
    for link in links:
        if link.get("link.input.node") in sink_ids:
            props = nodes.get(link.get("link.output.node"), {})
            found.append(
                {
                    "id": link.get("link.output.node"),
                    "app": props.get("application.name") or props.get("application.process.binary") or "?",
                    "pid": props.get("application.process.id"),
                    "target": props.get("target.object"),
                }
            )
    return found


def where_does_it_go(objects: list, pid: int) -> list[str]:
    """The sinks the output streams of process `pid` are linked to."""
    clients = {
        o["id"]
        for o in objects
        if str(o.get("type")).endswith("Client")
        and str(((o.get("info") or {}).get("props") or {}).get("application.process.id")) == str(pid)
    }
    nodes = {o["id"]: (o.get("info") or {}).get("props") or {} for o in objects if str(o.get("type")).endswith("Node")}
    streams = {i for i, p in nodes.items() if p.get("client.id") in clients}
    targets = []
    for o in objects:
        props = (o.get("info") or {}).get("props") or {}
        if str(o.get("type")).endswith("Link") and props.get("link.output.node") in streams:
            targets.append(str(nodes.get(props.get("link.input.node"), {}).get("node.name")))
    return sorted(set(targets))


def list_apps() -> list[dict]:
    """The applications playing audio now, with their stream indices (for `pactl`)."""
    out = subprocess.run(["pactl", "-f", "json", "list", "sink-inputs"], capture_output=True, text=True, check=False)
    try:
        inputs = json.loads(out.stdout or "[]")
    except json.JSONDecodeError:
        return []
    apps: dict[str, dict] = {}
    for item in inputs:
        props = item.get("properties", {})
        name = props.get("application.name") or props.get("application.process.binary")
        if not name or name in {"pw-play", "pw-record"}:
            continue
        entry = apps.setdefault(
            name,
            {"name": name, "indices": [], "sinks": [], "sample_spec": item.get("sample_specification")},
        )
        entry["indices"].append(item.get("index"))
        entry["sinks"].append(item.get("sink"))
    return sorted(apps.values(), key=lambda a: a["name"].lower())


class Source:
    """The current source of one session. Only the engine thread calls it."""

    def __init__(self, sink_name: str) -> None:
        self.sink_name = sink_name
        self.kind = "system"
        self.name: str | None = None
        self.error: str | None = None
        self.started_at: float | None = None
        self._process: subprocess.Popen | None = None
        self._loop: threading.Thread | None = None
        self._stop = threading.Event()
        self._tmp = tempfile.TemporaryDirectory(prefix="aurasync-source-")

    @property
    def pid(self) -> int | None:
        p = self._process
        return p.pid if p is not None and p.poll() is None else None

    @property
    def running(self) -> bool:
        return self.kind in {"system", "app"} or self._loop is not None

    def set(self, kind: str, name: str | None = None) -> None:
        """Switch to another source. Raises `ValueError` with a reason if it cannot."""
        self.stop()
        self.error = None
        if kind == "system":
            pass
        elif kind == "app":
            if not name:
                raise ValueError("choose the application by name")
            self._move_app(name)
        elif kind == "file":
            if not name or not Path(name).expanduser().is_file():
                raise ValueError(f"no such file: {name}")
            self._play(Path(name).expanduser())
        elif kind == "tone":
            path = Path(self._tmp.name) / "test-signal.wav"
            if not path.exists():
                write_wav(path, probe_signal())
            self._play(path)
        self.kind, self.name = kind, name
        self.started_at = time.monotonic()
        log.info("source: %s%s", kind, f" ({name})" if name else "")

    def _move_app(self, name: str) -> None:
        apps = {a["name"]: a for a in list_apps()}
        if name not in apps:
            raise ValueError(f"{name!r} is not playing audio now; playing: {sorted(apps) or 'nothing'}")
        for index in apps[name]["indices"]:
            subprocess.run(["pactl", "move-sink-input", str(index), self.sink_name], capture_output=True, check=False)
        time.sleep(0.3)
        after = {a["name"]: a for a in list_apps()}.get(name)
        sink_index = _sink_index(self.sink_name)
        if after is None or sink_index is None or any(s != sink_index for s in after["sinks"]):
            raise ValueError(f"{name!r} could not be moved to {self.sink_name!r}")

    def _play(self, path: Path) -> None:
        self._stop.clear()
        started = threading.Event()

        def loop() -> None:
            while not self._stop.is_set():
                self._process = subprocess.Popen(
                    ["pw-play", "--target", self.sink_name, str(path)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                started.set()
                while self._process.poll() is None and not self._stop.is_set():
                    time.sleep(0.1)
                if self._process.poll() is None:
                    self._process.terminate()
                with contextlib.suppress(subprocess.TimeoutExpired):
                    self._process.wait(timeout=2)

        self._loop = threading.Thread(target=loop, name="aurasync-source", daemon=True)
        self._loop.start()
        started.wait(timeout=2)
        # Verified, not assumed: where did this stream really go?
        time.sleep(1.0)
        pid = self.pid
        targets = where_does_it_go(sonido._pw_dump(), pid) if pid else []  # noqa: SLF001
        if targets != [self.sink_name]:
            self.stop()
            raise ValueError(f"the source went to {targets or 'no sink'} instead of {self.sink_name!r}; stopped it")

    def stop(self) -> None:
        self._stop.set()
        if self._process is not None and self._process.poll() is None:
            self._process.terminate()
        if self._loop is not None:
            self._loop.join(timeout=3)
        self._loop, self._process = None, None
        if self.kind in {"file", "tone"}:
            self.kind, self.name = "system", None

    def close(self) -> None:
        self.stop()
        self._tmp.cleanup()

    def describe(self) -> dict:
        return {"kind": self.kind, "name": self.name, "pid": self.pid, "error": self.error}


def _sink_index(name: str) -> int | None:
    out = subprocess.run(["pactl", "-f", "json", "list", "sinks"], capture_output=True, text=True, check=False)
    try:
        sinks = json.loads(out.stdout or "[]")
    except json.JSONDecodeError:
        return None
    return next((s.get("index") for s in sinks if s.get("name") == name), None)
