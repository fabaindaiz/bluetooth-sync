"""One audio session: from the speakers' streams to the virtual sink and back, block by block.

This is the loop that lived inside `cmd_run`, moved here so that `aurasync run` and the
control service run **the same code**. It was moved, not rewritten: the order of the
steps is the one `docs/research/experimentos/09-primera-escucha-con-3-go-4.md` §2 paid
for (streams first, half a second of silence, then the virtual sink, then the routing
check), and no test without speakers can see it.

The core is deliberately small (`open`, `step`, `close`) so that the service's tests can
replace it with a fake of the same shape (spec §8). Around it, for the panel (spec §15):
levels, timing, process ids, a test tone on one speaker, the recalibration loop switched
on and off while playing, a calibration with a stimulus inside the open session, and the
source that plays into the virtual sink.

**Nothing slow runs on the engine thread.** `pw-play` keeps 200 ms of buffer; a step that
takes longer starves the speakers, and an A2DP stream that runs dry comes back with a
different offset (`docs/research/experimentos/05-…`). Switching the source and computing a
calibration run on worker threads.
"""

from __future__ import annotations

import contextlib
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

import numpy as np

from aurasync import arrival_loop, estimulos, group_calibration, medicion, probe_measure, sincronia, sonido
from aurasync.cushion import SharedCushion
from aurasync.cuts import LATE_MS, LOW_MS, CutLog
from aurasync.dsp import eq, response
from aurasync.dsp import probe as masked_probe
from aurasync.dsp.input_analysis import InputAnalyzer
from aurasync.dsp.retardo import LineaDeRetardo
from aurasync.multichannel import MultichannelFile
from aurasync.outputs import OutputSet, Pacer
from aurasync.probe_ring import ProbeRing
from aurasync.quality import QualityMeter
from aurasync.sources import Source
from aurasync.telemetry import Telemetry

if TYPE_CHECKING:
    from collections.abc import Callable

    from aurasync.config import Instalacion
    from aurasync.motor import Motor
    from aurasync.outputs import PlayerLike
    from aurasync.render_match import RenderMatch


ROUTING_CHECK_S = 2.0
"""How often a playing session checks where its streams really go. `pw-dump` takes ~16 ms
on `PC-Ryzen5`, well inside the 200 ms of `pw-play` buffer."""
SILENCE_DB = -120.0
TONE_HZ = 660.0
TONE_AMPLITUDE = 0.05
"""The identification tone: -26 dBFS, after the volume, so that it is heard even with the
volume down and never loud."""
PEAK_HOLD_S = 1.5
PEAK_FALL_DB_S = 20.0 / 1.7
"""Peak hold and fall like a type I peak meter (IEC 60268-10: 20 dB in 1.7 s)."""
MIC_SLACK_S = 0.4
"""The microphone starts a little after the stimulus: the recording leaves out this much."""
MIC_CHECK_S = 8.0
"""How long the microphone stays open for the panel's level check when the loop is off: a few
seconds on demand, never continuously (it may be the laptop's own microphone: privacy)."""
SIGNAL_RMS = 0.005
"""-46 dBFS: below this, before the master volume, the loop does not measure (no content)."""
CAL_BEFORE_S = 0.7
CAL_AFTER_S = 2.5
"""Silence around the stimulus: the microphone must hear the end of it after the ~0.5 s of
`pw-play` buffer and A2DP latency (`calibrate` waits 0.7 s; here there is room to spare)."""
CAL_GAP_S = CAL_AFTER_S
"""Silence between the two groups of a calibration of more than six speakers
(`group_calibration.py`): the first group's stimulus must be heard out before the second's."""
PROBE_WINDOW_S = 4.0
"""The window the loop measures against the masked probe: experimentos/11 (-20 dB, 4 s: none
of 240 measurements off by more than 1 ms) and 16 §4.1 (the same with 8 simultaneous probes)."""
PROBE_EVERY_S = 4.0
"""With the probe, a measurement every window: every speaker is measured every 4 s, which
keeps N x window x drift far under the dead band (experimentos/16 §4.2)."""


_log = logging.getLogger("aurasync.session")

OUTPUT_MARGIN_BLOCKS = 2
"""Blocks of audio kept in the pipe beyond what `pw-play` takes per cycle.

One block (85 ms) was the margin until 2026-10-02: a stall of the engine thread longer than
that emptied the pipe and the speakers cut. Two give ~170 ms of slack for 85 ms more
latency, which the calibration measures anyway (experimentos/10 §9)."""


def pipe_size_ms(block: int, rate: int, player_latency_ms: float) -> float:
    block_ms = block / rate * 1000
    return player_latency_ms + OUTPUT_MARGIN_BLOCKS * block_ms


class SessionError(Exception):
    """The session cannot go on. `code` is a contract error code (`control.py`)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class SessionOptions:
    rate: int = 48000
    block: int = 4096
    sink_name: str = "aurasync"
    sink_description: str = "aurasync (envolvente)"
    recalibrate: bool = False
    microphone: str | None = None
    every_s: float = 20.0
    measure_s: float = sincronia.VentanaDeEmision.SEGUNDOS_DE_MEDICION
    player_latency_ms: int = 50
    output: str = "combinado"
    """`combinado`: un stream de N canales a un sink combine-stream, un solo reloj (por
    defecto desde el 2026-10-01, experimentos/10 §5). `separado`: un `pw-play` por parlante,
    como antes; queda para comparar."""
    probe: bool = False
    """The masked probe under the music (`dsp/probe.py`), for the loop to measure against.
    Off by default until the blind A/B says it is inaudible (i-7c8794-e3e40d, step 4)."""
    probe_margin_db: float = masked_probe.MARGIN_DB


def missing_speakers(installation: Instalacion) -> list[str]:
    """The real speakers whose sink PipeWire does not list now. A virtual one is never missing."""
    nodes = {s.nodo for s in sonido.salidas_bluetooth()}
    return [p.nombre for p in installation.parlantes if p.sink is not None and p.sink not in nodes]


def level_db(x: np.ndarray) -> tuple[float, float]:
    """(RMS, peak) in dBFS of a block."""
    if len(x) == 0:
        return SILENCE_DB, SILENCE_DB
    rms = float(np.sqrt(np.mean(x * x)))
    peak = float(np.max(np.abs(x)))
    return (20 * np.log10(rms) if rms > 1e-6 else SILENCE_DB), (20 * np.log10(peak) if peak > 1e-6 else SILENCE_DB)  # noqa: PLR2004


class Meters:
    """RMS per block, and a peak that holds `PEAK_HOLD_S` and then falls at `PEAK_FALL_DB_S`."""

    def __init__(self) -> None:
        self.values: dict[str, dict[str, float]] = {}
        self._held: dict[str, tuple[float, float]] = {}

    def update(self, name: str, x: np.ndarray, seconds: float) -> None:
        rms, peak = level_db(x)
        held, age = self._held.get(name, (SILENCE_DB, 0.0))
        age += seconds
        if peak >= held:
            held, age = peak, 0.0
        elif age > PEAK_HOLD_S:
            held = max(peak, held - PEAK_FALL_DB_S * seconds)
        self._held[name] = (held, age)
        self.values[name] = {"rms_db": round(rms, 1), "peak_db": round(max(held, SILENCE_DB), 1)}


class Calibration:
    """A stimulus played inside the open session, recorded, and measured on a worker thread.

    **It measures what is left to correct, not the raw offset.** Each speaker's stimulus goes
    out through the delay and gain already applied to it (`applied`), and is compared with
    the stimulus as generated. So the result is the residual: zero when the current
    corrections are right, and a deliberate extra delay shows up as its opposite. Before
    2026-10-01 the stimulus went out raw, which made a closure test (calibrate, apply,
    calibrate again) repeat the same numbers instead of giving zero. With no corrections
    applied the two are the same measurement.
    """

    def __init__(
        self,
        names: list[str],
        seconds: float,
        amplitude: float,
        rate: int,
        applied: dict[str, tuple[float, float]] | None = None,
        applied_eq: dict[str, list[float] | None] | None = None,
    ) -> None:
        self.state = "running"
        self.seconds, self.amplitude, self.rate = seconds, amplitude, rate
        # More than six speakers: two groups sharing the first, one after the other, each for
        # the whole duration (`group_calibration.py`). Each speaker's reference spans the whole
        # stimulus, silent outside its group's stretch. One group: the stimulus of always.
        self.groups = group_calibration.groups(list(names))
        self.segments: list[tuple[int, int]] = []
        """(start, length) of each group's stimulus, in samples from the stimulus's start."""
        per_group = []
        for g, group in enumerate(self.groups):
            tracks = estimulos.calibracion(len(group), seconds, semilla=g)
            start = self.segments[-1][0] + self.segments[-1][1] + int(CAL_GAP_S * rate) if self.segments else 0
            self.segments.append((start, len(tracks[0])))
            per_group.append(dict(zip(group, tracks, strict=True)))
        if len(self.groups) == 1:
            self.references = {n: amplitude * t for n, t in per_group[0].items()}
        else:
            length = self.segments[-1][0] + self.segments[-1][1]
            self.references = {n: np.zeros(length) for n in names}
            for (start, size), tracks_of in zip(self.segments, per_group, strict=True):
                for n, t in tracks_of.items():
                    self.references[n][start : start + size] = amplitude * t
        self.applied = {n: (applied or {}).get(n, (0.0, 0.0)) for n in names}
        # Through the EQ in place too: the response it measures is what is left to correct,
        # and the same filter latency as the motor keeps the measured latency honest.
        self.applied_eq = {n: (applied_eq or {}).get(n) for n in names}
        self._eq = {n: eq.StreamingFIR(eq.fir(self.applied_eq[n])) for n in names}
        self._lines = {}
        for n, (delay, _) in self.applied.items():
            line = LineaDeRetardo(rate, maximo_ms=max(250.0, 2 * delay))
            line.saltar_a(delay)
            self._lines[n] = line
        self.before = int(CAL_BEFORE_S * rate)
        self.after = int(CAL_AFTER_S * rate)
        self.total = self.before + len(next(iter(self.references.values()))) + self.after
        self.pos = 0
        self.results: list[dict] = []
        self.reliable: bool | None = None
        self.error: str | None = None
        self.measured_at: str | None = None
        self.raw: Any = None
        self.latency_ms: float | None = None
        self._thread: threading.Thread | None = None

    @property
    def progress(self) -> float:
        return min(1.0, self.pos / self.total) if self.state == "running" else (1.0 if self.state == "done" else 0.0)

    def next_blocks(self, n: int) -> dict[str, np.ndarray]:
        """What each speaker plays now: silence, the stimulus, silence."""
        start = self.pos - self.before
        out = {}
        for name, ref in self.references.items():
            x = np.zeros(n)
            lo, hi = max(0, start), min(len(ref), start + n)
            if hi > lo:
                x[lo - start : hi - start] = ref[lo:hi]
            _, gain = self.applied[name]
            out[name] = self._eq[name].process(self._lines[name].procesar(x)) * 10 ** (gain / 20)
        self.pos += n
        return out

    @property
    def emitted(self) -> bool:
        return self.pos >= self.total

    def measure(self, recording: np.ndarray, done: Callable[[], None]) -> None:
        self.state = "measuring"
        self.recording = recording

        def run() -> None:
            try:
                result = self._calibrate(recording)
                if result is None:
                    self.state, self.error = (
                        "error",
                        "could not align the recording with what was played: is the microphone hearing the speakers?",
                    )
                else:
                    self.raw = result
                    # The recording starts MIC_SLACK_S after the first written sample, and
                    # the stimulus was written CAL_BEFORE_S after that: what is left of its
                    # position in the recording is how long a written sample takes to be
                    # heard (and recorded: it includes the microphone's own latency).
                    self.latency_ms = round(
                        float(result.desfase_grueso_ms) - (self.before / self.rate - MIC_SLACK_S) * 1000, 1
                    )
                    silent = set(result.sin_sonar())
                    doubtful = set(result.dudosos())
                    self.results = [
                        {
                            "speaker": n,
                            "delay_ms": round(float(result.retardos_ms[n]), 3),
                            "gain_db": round(float(result.ganancias_db[n]), 2),
                            "stability_ms": round(float(result.estabilidad_ms[n]), 3),
                            "silent": n in silent,
                            "doubtful": n in doubtful,
                            **self._response(recording, n),
                            "applied_delay_ms": round(self.applied[n][0], 3),
                            "applied_gain_db": round(self.applied[n][1], 2),
                            "applied_eq_db": self.applied_eq[n],
                        }
                        for n in self.references
                    ]
                    self.reliable = bool(result.confiable)
                    self.state = "done"
                self.measured_at = datetime.now().astimezone().isoformat(timespec="seconds")
            except Exception as exc:  # noqa: BLE001 - reported in the state; the session goes on
                self.state, self.error = "error", repr(exc)
            done()

        self._thread = threading.Thread(target=run, name="aurasync-calibration", daemon=True)
        self._thread.start()

    def _calibrate(self, recording: np.ndarray) -> medicion.Calibracion | None:
        """One group: `medicion.calibrar` as always. Two: each group on its own stretch of the
        recording (the same offset into it as the first group's), joined through the anchor."""
        if len(self.groups) == 1:
            return medicion.calibrar(recording, self.references)
        results = []
        for (start, size), group in zip(self.segments, self.groups, strict=True):
            piece = recording[start : start + self.before + size + self.after]
            results.append(medicion.calibrar(piece, {n: self.references[n][start : start + size] for n in group}))
        return group_calibration.join(results, self.groups)

    def _response(self, recording: np.ndarray, name: str) -> dict:
        """The speaker's frequency response at the microphone, and how far to trust each third
        (see `dsp/response.py`): `coherence` is gamma^2, `response_error_db` the 1sigma random error of
        the third's level. The panel fades the thirds whose error is large or None."""
        reference = self.references[name]
        lag = round(
            medicion.gcc_phat(recording, reference, self.rate, retardo_maximo_ms=2500.0).retardo_ms * self.rate / 1000
        )
        bands, coherence, error = response.response_with_coherence(recording, reference, lag, self.rate)
        low, high = response.usable_band(bands)
        return {
            "response_db": [None if not np.isfinite(b) else round(float(b), 1) for b in bands],
            "band_hz": [low, high],
            "coherence": [None if not np.isfinite(c) else round(float(c), 3) for c in coherence],
            "response_error_db": [None if not np.isfinite(e) else round(float(e), 2) for e in error],
        }

    def describe(self) -> dict:
        return {
            "state": self.state,
            "response_hz": [round(float(f)) for f in response.THIRDS],
            "progress": round(self.progress, 3),
            "seconds": self.seconds,
            "amplitude": self.amplitude,
            "results": self.results,
            "reliable": self.reliable,
            "error": self.error,
            "measured_at": self.measured_at,
            "latency_ms": self.latency_ms,
            "groups": [list(g) for g in self.groups],
        }


class OutputChange:
    """One change of which real speakers play, in flight (spec 2026-10-05-virtual-speakers-and-hot-join §5).

    A worker builds the new real part, primes it and checks its routing while the old one keeps
    playing, then feeds it silence until the engine takes it over at the bottom of a cut. The
    hand-over is guarded by `feed_lock`: the engine sets `stop` and takes the lock once, and the
    worker checks `stop` under the lock before every write, so **no silence reaches the new
    player after the engine's first block** (a dropout in the middle of the music).
    """

    def __init__(self, playing: set[str], nodes: list[str], name: str, done: Callable[[str | None], None]) -> None:
        self.playing = set(playing)
        self.nodes = nodes
        self.name = name
        self.done = done
        self.player: PlayerLike | None = None
        self.ready = threading.Event()
        """The new player is built, primed and routed where it was asked: the cut may be asked for."""
        self.stop = threading.Event()
        """The worker writes no more (the swap, or the session closing)."""
        self.over = threading.Event()
        """The engine swapped it in (`swapped`) or the session gave it up (`cancel_message`)."""
        self.feed_lock = threading.Lock()
        self.cut_requested = False
        self.at_bottom = False
        """Set by the cut's action, on the engine thread, inside `motor.procesar`."""
        self.swapped = False
        self.cancel_message = "the session closed before the speaker change"
        self.thread: threading.Thread | None = None

    def cancel(self, message: str) -> None:
        self.cancel_message = message
        self.stop.set()
        self.over.set()


class AudioSession:
    """Opens the streams and the virtual sink, processes one block per `step`, closes."""

    SWAP_SETTLE_S = 1.0
    """How long a new player is fed before its routing is repaired: the same second `open` waits
    after creating the input sink (WirePlumber moves a stream when it takes a default)."""
    SWAP_CHECK_S = 0.5
    """And after the repair, before its routing is checked (as `open`)."""

    def __init__(
        self,
        installation: Instalacion,
        motor: Motor,
        options: SessionOptions,
        log: Callable[..., None],
    ) -> None:
        """`log(kind, **fields)` receives every event worth reporting."""
        self.installation = installation
        self.motor = motor
        self.options = options
        self.log = log
        self.loop: arrival_loop.ArrivalLoop | None = None
        self._measured: list[str] = []
        """The speakers the loop measures: the ones playing when it started (or restarted)."""
        self._loop_wanted = False
        """The user wants the loop on (`enable_recalibration`), even while fewer than two speakers
        play and it cannot run: it comes back by itself when two play again (spec §5). Only an
        explicit `disable_recalibration` clears it, not the session's own restarts."""
        self.last_recalibration: dict[str, Any] | None = None
        self.recalibration_history: list[dict] = []
        """The delay the loop applied to each speaker, after each decision (for the chart)."""
        self.last_residual: dict[str, Any] | None = None
        """The residual misalignment the loop measured last, through the corrections in place
        (the spread of the measured delays, the speakers heard), with its clock: the panel's
        "Sincronía" (spec 2026-10-02 §7.3.4). Only a reliable measurement without a cut."""
        self.lost: list[str] = []
        """Speakers whose stream died (`output == "lost"`). The rest keep playing, as `run` always
        did, and the session goes on even when every real one is lost (d-7c8794-05bdd6)."""
        self.routing_repairs = 0
        """How many times a stream was found on the wrong sink and moved back."""
        self.meters = Meters()
        self.input_analysis = InputAnalyzer(options.rate)
        self.telemetry = Telemetry(options.rate)
        self.quality = QualityMeter(options.rate, [p.nombre for p in installation.parlantes])
        """Loudness in and out, PSR, true peak (spec 2026-10-02 §6.2): 0.3 ms per block."""
        self.cuts = CutLog()
        self.on_measurement: Callable[[dict[str, float], frozenset[str], float], None] | None = None
        """Set by the service: each loop measurement (arrivals, the speakers believed, the window's
        middle on `time.monotonic`) also goes to the sync estimator (spec 2026-10-03 §4.1)."""
        self._routing_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="aurasync-ruteo")
        self._routing_future = None
        self._routing_player: PlayerLike | None = None
        """The player the routing check in flight reads: its result is only about that one."""
        self._change: OutputChange | None = None
        """The change of which speakers play now in flight, if any (`request_output`)."""
        self._retiring: list[tuple[Any, PlayerLike]] = []
        """Players swapped out and handed to the routing pool to close, with their futures."""
        self._output_name = f"{options.sink_name}_salida"
        """The combine sink's node name last created: a change creates the other one, since the
        old sink still exists while the new one is prepared."""
        self._last_step_at: float | None = None
        self._input_gap_since: float | None = None
        self._was_cutting = False
        self.block_ms = 0.0
        """Motor time per block, smoothed. Against `options.block / rate` it says how much
        of real time the processing takes."""
        self.input_active = False
        self.blocks = 0
        self.calibration: Calibration | None = None
        self.source: Source | None = None
        self.source_busy = False
        self.microphone = options.microphone
        self._stack = contextlib.ExitStack()
        self._recal_stack = contextlib.ExitStack()
        self._sinks = {p.nombre: p.sink for p in installation.parlantes}
        self._silence = np.zeros(options.block)
        self.outputs = OutputSet(
            self._sinks, Pacer(options.rate, options.block), SharedCushion(options.block, options.rate)
        )
        """Who plays and who is only computed; the only way to the speakers (outputs.py)."""
        self._cushion_warned = False
        self._cushion_bottom = False
        """The cut the speakers' cushion asked for reached its bottom in this step (`_refill`)."""
        self._fade_detail: str | None = None
        """What the next intentional cut is for, when the session itself asked for it."""
        self._input: sonido.SinkVirtual | None = None
        self._mic: sonido.MicrofonoContinuo | None = None
        self._cal_mic: sonido.MicrofonoContinuo | None = None
        self._check_mic: sonido.MicrofonoContinuo | None = None
        self._check_until = 0.0
        self.monitor = None
        """The headphone monitor (monitor.MonitorOutput), not synchronised with the speakers."""
        self.render_match: RenderMatch | None = None
        """Every render at `classic`'s loudness (render_match.py): the service's, set before
        `open`. None: no makeup moves (the motor keeps the one it has)."""
        self.multichannel: MultichannelFile | None = None
        """The multichannel render the engine plays while the source is `multichannel`."""
        self.probe_ring = ProbeRing([p.nombre for p in installation.parlantes], options.rate)
        """The probe each speaker carried, with its server time: a phone measures against it."""
        self._next_measure = 0.0
        self._started = time.monotonic()
        self._last_fade = -1e9
        self._launched_at = 0.0
        self._next_routing_check = time.monotonic() + ROUTING_CHECK_S
        self._tones: dict[str, list[int]] = {}
        self.probe_since: float | None = None
        """When the probe last reached full level (monotonic): the loop measures against it once
        a whole window of it has been heard."""
        self._launch: dict[str, Any] = {}
        if options.probe:
            self.set_probe(True, options.probe_margin_db)

    # -- lifecycle ------------------------------------------------------------------

    def open(self) -> None:
        o = self.options
        if sonido.nodo_existe(o.sink_name):
            raise SessionError(
                "conflict",
                f"a PipeWire node named {o.sink_name!r} already exists: is another aurasync running?",
            )
        if o.recalibrate and not o.microphone:
            raise SessionError("unavailable", "recalibration needs a microphone and none was found")
        try:
            self._open_streams()
        except BaseException:
            self.close()
            raise

    def _connected(self) -> set[str]:
        """The real speakers whose sink PipeWire lists now: the ones that will play. A missing one
        is `absent` and only computed (d-7c8794-05bdd6)."""
        present = {s.nodo for s in sonido.salidas_bluetooth()}
        return {n for n, sink in self._sinks.items() if sink is not None and sink in present}

    def _open_streams(self) -> None:
        o = self.options
        connected = self._connected()
        # Installation order, as before: the combined stream's channels follow it.
        nodes = [sink for n, sink in self._sinks.items() if n in connected and sink is not None]
        # The output set closes the player, after the input sink (the reverse of opening).
        self._stack.callback(self.outputs.close)
        if not nodes:
            # No real speaker: no `pw-play`, no combine module, nothing to route or check. Only
            # the input sink; the `Pacer` keeps the time while nothing comes in (outputs.py).
            self._input = self._stack.enter_context(sonido.SinkVirtual(o.sink_name, o.sink_description, o.rate))
            self._after_streams()
            return
        # **El orden importa, y el silencio también.** `pw-play` no se enlaza a su destino
        # hasta que recibe datos, y resuelve `--target` en ese momento. Si el sink virtual ya
        # existiera, WirePlumber puede tomarlo como salida por defecto y un target que no
        # resuelva caería ahí, cerrando un lazo de realimentación
        # (`docs/research/experimentos/09-primera-escucha-con-3-go-4.md`). Así que primero
        # los parlantes, después medio segundo de silencio, después la entrada.
        # Two blocks of pipe: enough so that one slow step does not starve the speakers, and
        # not the 0.68 s the kernel gives by default (see `Reproductor`).
        # The pipe must hold more than what pw-play asks for in one cycle (its `--latency`),
        # plus a margin (`OUTPUT_MARGIN_BLOCKS`). With two blocks (8192 samples) and pw-play asking for 9600,
        # pw-play came up short on every cycle: ~10 xruns per second and badly degraded
        # audio (MEASURED 2026-10-01 with pw-top, experimentos/10 §6).
        player = self._new_player(nodes, self._output_name)
        with contextlib.ExitStack() as guard:
            guard.enter_context(player)
            self.outputs.attach(player, connected)
            guard.pop_all()
        for _ in range(max(1, int(0.5 * o.rate / o.block))):
            player.escribir(dict.fromkeys(nodes, self._silence))
        self._input = self._stack.enter_context(sonido.SinkVirtual(o.sink_name, o.sink_description, o.rate))

        # **La comprobación va después de crear la entrada, y no antes.** Al aparecer,
        # WirePlumber toma el sink virtual como salida por defecto y mueve el stream que
        # apuntaba al default anterior. Comprobar antes no veía nada.
        time.sleep(1.0)
        for asked, real in player.reparar_ruteo().items():
            self.log("ruteo", motivo=f"se desvió a {real or 'ningún destino'}: {asked} — devuelto")
        time.sleep(0.5)
        lost = player.mal_ruteados()
        if lost:
            detail = "; ".join(f"{asked} → {real or 'ningún destino'}" for asked, real in lost.items())
            raise SessionError("unavailable", f"the streams did not reach their speakers: {detail}")
        self._after_streams()

    def _new_player(self, nodes: list[str], name: str) -> PlayerLike:
        """The real part over `nodes` (not yet entered). `name` is the combine sink's node name."""
        o = self.options
        pipe_ms = pipe_size_ms(o.block, o.rate, o.player_latency_ms)
        if o.output == "combinado":
            return sonido.ReproductorCombinado(nodes, o.rate, o.player_latency_ms, tuberia_ms=pipe_ms, nombre=name)
        return sonido.Reproductor(nodes, o.rate, o.player_latency_ms, tuberia_ms=pipe_ms)

    def _after_streams(self) -> None:
        o = self.options
        self._stack.callback(self._recal_stack.close)
        if o.recalibrate:
            self.enable_recalibration(o.microphone)
        self.source = self._new_source()
        self._stack.callback(self.source.close)
        self._next_routing_check = time.monotonic() + ROUTING_CHECK_S

    def close(self) -> None:
        """Closes everything in reverse order. Safe to call twice."""
        change, self._change = self._change, None
        if change is not None:
            # Its worker closes the player it built and calls `done` with the reason.
            change.cancel("the session closed before the speaker change")
            if change.thread is not None:
                change.thread.join(timeout=5.0)
        self.attach_monitor(None)
        self._stack.close()
        self.quality.close()
        self.cuts.close()
        self._routing_pool.shutdown(wait=False, cancel_futures=True)
        self._routing_future = self._routing_player = None
        # A player swapped out whose close never started would keep its streams (and, combined, its
        # sink) alive: closed here instead. One already closing finishes on the pool's thread.
        retiring, self._retiring = self._retiring, []
        for future, player in retiring:
            if future.cancelled():
                with contextlib.suppress(Exception):
                    player.cerrar()
        if self._cal_mic is not None:
            self._cal_mic.cerrar()
        self._close_mic_check()
        self.outputs.close()
        self._input = self._mic = self._cal_mic = None
        self.loop = None

    # -- one block ------------------------------------------------------------------

    def output_states(self) -> dict[str, str]:
        """Each speaker's `output`: `virtual`, `absent`, `playing` or `lost` (outputs.py)."""
        return self.outputs.states()

    def step(self) -> None:
        if self._input is None:
            msg = "the session is not open"
            raise RuntimeError(msg)
        o = self.options
        seconds = o.block / o.rate
        started = time.monotonic()
        late_ms = None
        if self._last_step_at is not None:
            late_ms = (started - self._last_step_at - seconds) * 1000
        self._last_step_at = started
        moving = self.motor.en_corte  # what an order between steps asked for (see `_last_fade`)
        pair = self._input.leer(o.block)
        channels = None
        reader = self.multichannel
        if reader is not None and self.source is not None and self.source.kind == "multichannel":
            # The virtual input is still read (so it does not back up) and set aside: the engine
            # plays the render, one channel per speaker (multichannel.py).
            channels = reader.read(o.block)
            pair = MultichannelFile.downmix(channels)
        self._watch_input(pair)
        self.input_active = pair is not None
        t0 = time.perf_counter()
        if self.calibration is not None and self.calibration.state == "running":
            # The calibration owns the speakers: its stimulus instead of the motor's output.
            # Only the playing ones take part; the others get silence, so every consumer (the
            # monitor's per-speaker inputs above all) still receives every channel.
            stimulus = self.calibration.next_blocks(o.block)
            blocks = {n: stimulus.get(n, self._silence) for n in self._sinks}
        elif pair is None:
            # Con nada reproduciéndose se manda silencio igual, para que los streams A2DP no
            # se suspendan: al despertar traerían un desfase distinto. **Y el silencio pasa por
            # el motor**: un corte pendiente (aplicar una calibración, cargar un preset) solo
            # avanza cuando el motor procesa. Sin esto, con la música en pausa, "Aplicar"
            # quedaba esperando para siempre (2026-10-01, campaña de experimentos/10).
            blocks = self.motor.procesar(self._silence, self._silence)
        elif channels is not None:
            blocks = self.motor.procesar(*pair, canales=channels)
        else:
            blocks = self.motor.procesar(*pair)
        self.block_ms = 0.9 * self.block_ms + 0.1 * (time.perf_counter() - t0) * 1000
        blocks = self._add_tones(blocks)
        self._watch_output(late_ms)
        # Only the playing speakers reach the player; with no real stream alive and nothing
        # coming in, the output set's `Pacer` keeps the loop in real time.
        self.outputs.write(blocks, input_paced=pair is not None)
        # A change of which speakers play is swapped in here, after the write: the block that ends
        # at the bottom of its cut went to the old player, which plays out down to zero, and the
        # new one starts with the fade-in (request_output).
        player = self.outputs.player
        self._advance_output_change()
        # The speakers' cushion is refilled here too, after the block that ends at the bottom.
        self._refill(swapped=self.outputs.player is not player)
        # The probe as it left the engine, at the time it left (silence while it is off).
        probe = getattr(self.motor, "sonda", None)
        self.probe_ring.write(time.monotonic(), probe.last if probe is not None and probe.active else {})
        self._feed_monitor(pair, blocks)
        self.blocks += 1
        self._update_meters(pair, blocks, seconds)
        try:
            measured = self.calibration.latency_ms if self.calibration is not None else None
            if measured is not None:
                self.telemetry.set_latency(measured)
            limiter = getattr(self.motor, "reduccion_limitador_db", None)
            self.telemetry.record(blocks, pair, limiter() if limiter else None)
            self.quality.push(pair, blocks)
        except Exception:  # noqa: BLE001 - a report must never stop the audio (best-effort side channel)
            self._telemetry_failures = getattr(self, "_telemetry_failures", 0) + 1
            if self._telemetry_failures == 1:
                self.log("telemetría", motivo="falló el registro de métricas; el audio sigue")
        self._match_render(
            calibrating=self.calibration is not None and self.calibration.state == "running",
            multichannel=channels is not None,
            moving=moving,
        )
        gone = self.outputs.refresh()
        for name in gone:
            self.cuts.add("lost", name, "el stream hacia el parlante murió")
        if gone:
            # Losing every real speaker no longer ends the session (d-7c8794-05bdd6): it goes on,
            # heard on the monitor, until the user stops it.
            rest = "siguen los demás" if self.outputs.playing() else "ninguno suena; la sesión sigue"
            self.log("parlante perdido", motivo=f"sin stream: {', '.join(gone)}; {rest}")
        self.lost = [n for n, state in self.outputs.states().items() if state == "lost"]
        self._follow_playing_set()
        self._check_routing()
        if moving or self.motor.en_corte:
            # The loop discards a measurement with a cut or a crossfade inside its window. Looked at
            # before the block too: an 80 ms crossfade starts and ends within one 4096-sample block.
            self._last_fade = time.monotonic()
        # Only a cut is logged as one: a crossfade has no hole, and the log is what the listening
        # counts (experiment 23). A motor without the difference (the tests' fakes) logs any.
        cutting = getattr(self.motor, "cortando", self.motor.en_corte)
        if cutting:
            if not self._was_cutting:
                detail, self._fade_detail = self._fade_detail, None
                self.cuts.add("fade", None, detail or self.cuts.context.get("last_order") or "")
        else:
            # A detail asked into a fade already under way (merged into it) is not the next one's.
            self._fade_detail = None
        self._was_cutting = cutting
        self._calibration_step()
        self._mic_check_step(blocks)
        if (
            getattr(self, "_loop_paused_for_calibration", False)
            and self.calibration is not None
            and self.calibration.state not in {"running", "measuring"}
        ):
            self._loop_paused_for_calibration = False
            self.enable_recalibration(self.microphone)
        if self.loop is not None and (self.calibration is None or self.calibration.state != "running"):
            self._recalibration_step(blocks)

    # -- the render's loudness match (render_match.py) ------------------------------------

    def _match_render(self, *, calibrating: bool, multichannel: bool, moving: bool = False) -> None:
        """After the quality meter took the block. Frozen while a calibration plays its stimulus
        (the speakers do not play the render), and on a block that a cut or a crossfade moved
        (`moving`: an 80 ms crossfade starts and ends within one block, so `en_corte` after it
        misses it); a multichannel source (made elsewhere) plays with no makeup.
        A failure never stops the audio: the makeup stays where it is."""
        match = self.render_match
        if match is None or not hasattr(self.motor, "render"):
            return
        try:
            match.after_block(
                self.motor, self.quality, self.options.block, hold=calibrating or moving, bypass=multichannel
            )
        except Exception:  # noqa: BLE001 - a side channel: the speakers go on at the makeup they have
            self._render_match_failures = getattr(self, "_render_match_failures", 0) + 1
            if self._render_match_failures == 1:
                self.log("igualación de sonoridad", motivo="falló; el audio sigue con la ganancia que tenía")

    # -- the headphone monitor (monitor.py) ----------------------------------------------

    def attach_monitor(self, monitor) -> None:
        """Replace the monitor (None: none). The previous one is closed."""
        previous, self.monitor = self.monitor, monitor
        if previous is not None and previous is not monitor:
            previous.close()

    def _feed_monitor(self, pair, blocks: dict[str, np.ndarray]) -> None:
        """After the speakers, never before: the monitor must not delay them. It does not wait
        (its writer drops blocks), and if it fails it is dropped and the speakers go on."""
        monitor = self.monitor
        if monitor is None:
            return
        # Every channel, virtual and absent ones included: the binaural filter has one input
        # per speaker and none may go missing.
        if blocks.keys() != self._sinks.keys():
            blocks = {n: blocks.get(n, self._silence) for n in self._sinks}
        try:
            monitor.push(pair, blocks)
        except Exception as exc:  # noqa: BLE001 - the monitor is a side output: it never stops the audio
            self.monitor = None
            with contextlib.suppress(Exception):
                monitor.close()
            self.log("monitor", motivo=f"se cerró: {exc!r}; los parlantes siguen")

    # -- cuts: what the engine sees of each interruption (cuts.py) --------------------

    def _watch_output(self, late_ms: float | None) -> None:
        """Before writing: how much audio was still waiting for the speakers."""
        measurer = getattr(self, "_measurer", None)
        self.cuts.context["loop_measuring"] = self.loop is not None and measurer is not None and measurer.ocupado
        level = self.outputs.nivel_ms()
        self.pipe_ms = level
        self._watch_cushion(level)
        if late_ms is not None and late_ms > LATE_MS:
            self.cuts.add("late", "motor", f"{late_ms:.0f} ms tarde", late_ms=round(late_ms))
        if level is None or self.blocks < 3:  # noqa: PLR2004 - the pipe fills during the first blocks
            return
        if level < 1.0:
            self.cuts.add("underrun", "salida", "la tubería estaba vacía", level_ms=round(level, 1))
        elif level < LOW_MS:
            self.cuts.add("low", "salida", f"quedaban {level:.0f} ms", level_ms=round(level, 1))

    def _watch_cushion(self, level_ms: float | None) -> None:
        """The speakers' cushion (cushion.SharedCushion, spec 2026-10-05-virtual-speakers-and-hot-join
        §9): a pipe that stays nearly empty asks for a cut, and the refill waits for its bottom.
        Never while a calibration plays its stimulus: the motor does not run then, and silence
        added under the stimulus would move what the microphone measures."""
        cushion = self.outputs.cushion
        if cushion is None:
            return
        level = self._frames(level_ms)
        room = self._room() if level is not None else None
        calibrating = self.calibration is not None and self.calibration.state == "running"
        separate = self.options.output == "separado"
        if cushion.observe(level, room, may_cut=not calibrating, separate=separate):
            self._fade_detail = "colchón de la salida: la tubería se vaciaba"
            self.motor.cortar(lambda: setattr(self, "_cushion_bottom", True))
        if cushion.gave_up and not self._cushion_warned:
            self._cushion_warned = True
            message = (
                f"colchón: dejó de rellenar; {cushion.failed} rellenos no devolvieron la tubería a su nivel "
                "y no se piden más cortes en esta sesión"
            )
            _log.warning("%s", message)
            self.log("salida", motivo=message)

    def _frames(self, ms: float | None) -> int | None:
        return None if ms is None else round(ms * self.options.rate / 1000)

    def _room(self) -> int | None:
        """Room left in the fullest pipe, in whole frames, rounded down: a pad of this size never waits."""
        ms = self.outputs.espacio_ms()
        return None if ms is None else int(ms * self.options.rate / 1000)

    def _refill(self, *, swapped: bool) -> None:
        """At the bottom of the cushion's cut, after its block: the same silence to every speaker
        stream, so each one is delayed alike and their alignment does not move. If the real part was
        swapped at this same bottom, the new player was primed by its own worker: nothing to add."""
        cushion = self.outputs.cushion
        if not self._cushion_bottom or cushion is None:
            return
        self._cushion_bottom = False
        if swapped:
            cushion.cancel()
            return
        # The room is read now, after the bottom block: the pad must fit without the write waiting.
        frames = cushion.at_bottom(self._room())
        if frames:
            self.outputs.pad(frames)
            ms = frames / self.options.rate * 1000
            self.log(
                "salida",
                motivo=f"colchón: la tubería se vaciaba; {ms:.0f} ms de silencio a todos los parlantes, en un corte",
            )

    def _watch_input(self, pair) -> None:
        """A short silence from the application while it plays is a gap; a long one is a pause."""
        now = time.monotonic()
        if pair is None:
            if self.input_active and self._input_gap_since is None:
                self._input_gap_since = now
            return
        if self._input_gap_since is not None:
            gap = now - self._input_gap_since
            if gap < 1.0:
                self.cuts.add("input_gap", "entrada", f"{gap * 1000:.0f} ms sin audio de la aplicación")
            self._input_gap_since = None

    def _update_meters(self, pair, blocks: dict[str, np.ndarray], seconds: float) -> None:
        if pair is None:
            silence = np.zeros(0)
            self.meters.update("in L", silence, seconds)
            self.meters.update("in R", silence, seconds)
        else:
            self.meters.update("in L", pair[0], seconds)
            self.meters.update("in R", pair[1], seconds)
            self.input_analysis.update(pair[0], pair[1])
        for name, x in blocks.items():
            self.meters.update(name, x, seconds)

    def _check_routing(self) -> None:
        """Where each stream really goes, checked while playing and not only at `open`.

        WirePlumber can move a stream at any time, for instance when the default sink
        changes, and the symptom is a silent speaker with no error anywhere
        (`docs/research/experimentos/09-primera-escucha-con-3-go-4.md`). What is asked of
        PipeWire is verified, not assumed (CLAUDE.md).

        **Read on a worker thread** (2026-10-02): `pw-dump` and its JSON take tens of ms,
        and on the engine thread every 2 s that came out of the pipe's margin. The engine
        thread only applies what the worker found.
        """
        future = self._routing_future
        if future is not None and future.done():
            checked = self._routing_player
            self._routing_future = self._routing_player = None
            if checked is not self.outputs.player:
                # The real part was swapped while the check ran (request_output): what it found is
                # about a player that is gone, and its node names would be read against the new one.
                wrong, existing = {}, set()
            else:
                try:
                    wrong, existing = future.result()
                except Exception as exc:  # noqa: BLE001 - a failed check must not stop the audio
                    self.log("ruteo", motivo=f"no se pudo comprobar: {exc!r}")
                    wrong, existing = {}, set()
            alive = set(self.outputs.vivos)
            wrong = {asked: real for asked, real in wrong.items() if asked in alive}
            # A stream whose speaker no longer exists cannot be moved back: the speaker was
            # turned off. Its stream is closed so that it cannot play anywhere else.
            gone = [asked for asked in wrong if asked not in existing]
            for asked in gone:
                self.outputs.soltar(asked)
                self.log(
                    "parlante perdido",
                    motivo=f"{asked} ya no existe; su stream iba a {wrong[asked] or 'ningún destino'}",
                )
            movable = {asked: real for asked, real in wrong.items() if asked not in gone}
            if movable and checked is not None:
                self._routing_pool.submit(checked.reparar_ruteo)
                self.routing_repairs += 1
                for asked, real in movable.items():
                    self.cuts.add("routing", asked, f"había ido a {real or 'ningún destino'}")
                    self.log("ruteo", motivo=f"se desvió a {real or 'ningún destino'}: {asked} — devuelto")
            # A stream closed here is `lost` at the next step's `refresh`; the session goes on.
        now = time.monotonic()
        if now < self._next_routing_check or self._routing_future is not None:
            return
        self._next_routing_check = now + ROUTING_CHECK_S
        player = self.outputs.player
        if player is None or not self.outputs.vivos:
            return  # no real stream: nothing PipeWire could have moved
        # The player is captured now, not read on the worker: a swap in between must not make the
        # check read one player and its result be applied to another.
        self._routing_player = player
        self._routing_future = self._routing_pool.submit(self._read_routing, player)

    @staticmethod
    def _read_routing(player) -> tuple[dict[str, str | None], set[str]]:
        """On the worker: the streams that are not where they were asked, and the nodes that exist."""
        wrong = player.mal_ruteados()
        existing = sonido.leer_nombres_de_nodo(sonido._pw_dump()) if wrong else set()  # noqa: SLF001
        return wrong, existing

    # -- changing which speakers play, without stopping (spec 2026-10-05 §5) ------------

    def request_output(self, playing: set[str], done: Callable[[str | None], None]) -> None:
        """Make exactly `playing` the real speakers that play. Returns at once.

        A worker builds the new real part over their sinks, primes it with silence and checks its
        routing while the old one keeps playing, then feeds it silence; the next `step` asks the
        motor for a cut and swaps the players at its bottom (`_swap`). `done(None)` after the swap;
        `done(message)` if the preparation failed, with nothing changed. One change at a time.

        Both output modes rebuild the whole real part over the new set, and a leave takes the same
        path with one sink fewer (spec 2026-10-05-virtual-speakers-and-hot-join §5, amended during
        Task 8): in `separado` too, not only the new `pw-play`.
        """
        if self._input is None:
            raise SessionError("unavailable", "the session is not open")
        if self._change is not None:
            raise SessionError("conflict", "a speaker change is already in progress")
        for name in sorted(playing):
            if name not in self._sinks:
                raise SessionError("not_found", f"no speaker {name!r}")
            if self._sinks[name] is None:
                raise SessionError("conflict", f"{name} is virtual: it has no output to play on")
        # Installation order, as at `open`: the combined stream's channels follow it.
        nodes = [sink for n, sink in self._sinks.items() if n in playing and sink is not None]
        alternate = f"{self.options.sink_name}_salida"
        name = f"{alternate}_b" if self._output_name == alternate else alternate
        change = OutputChange(set(playing), nodes, name, done)
        change.thread = threading.Thread(
            target=self._prepare, args=(change,), name="aurasync-output-change", daemon=True
        )
        self._change = change
        change.thread.start()

    def _prepare(self, change: OutputChange) -> None:
        """On the worker: build, prime and check the new player; feed it until it is swapped in."""
        error = None
        try:
            # A player swapped out earlier may still be closing, and its combine sink still
            # carries the name this change is about to reuse: it must be gone first.
            self._wait_retired(change)
            if change.nodes and not change.over.is_set():
                change.player = self._new_player(change.nodes, change.name)
                change.player.__enter__()
                self._settle(change)
        except SessionError as exc:
            error = exc.message
        except Exception as exc:  # noqa: BLE001 - reported through `done`; the session goes on as it was
            error = f"the new output could not be opened: {exc!r}"
        if error is None and not change.over.is_set():
            change.ready.set()
            try:
                self._feed(change, None)
            except Exception as exc:  # noqa: BLE001 - the swap still comes; `done` must still be called
                self.log("salida", motivo=f"dejó de alimentar el reproductor nuevo: {exc!r}")
            change.over.wait()
        if change.swapped:
            self._call_done(change, None)
            return
        if change.player is not None:
            with contextlib.suppress(Exception):
                change.player.cerrar()
        if self._change is change:
            self._change = None
        if error is not None:
            self.log("salida", motivo=f"no cambió qué parlantes suenan: {error}; todo sigue como estaba")
        self._call_done(change, error or change.cancel_message)

    def _call_done(self, change: OutputChange, error: str | None) -> None:
        try:
            change.done(error)
        except Exception as exc:  # noqa: BLE001 - the caller's callback must not kill the worker silently
            self.log("salida", motivo=f"el aviso del cambio de parlantes falló: {exc!r}")

    def _settle(self, change: OutputChange) -> None:
        """Prime the new player, then repair and check its routing as `open` does (experimentos/09):
        a stream that did not reach its sink fails the change instead of playing somewhere else."""
        player = change.player
        self._prime(change)
        self._feed(change, self.SWAP_SETTLE_S)
        if change.over.is_set():
            return
        for asked, real in player.reparar_ruteo().items():
            self.log("ruteo", motivo=f"se desvió a {real or 'ningún destino'}: {asked} — devuelto")
        self._feed(change, self.SWAP_CHECK_S)
        if change.over.is_set():
            return
        wrong = player.mal_ruteados()
        if wrong:
            detail = "; ".join(f"{asked} → {real or 'ningún destino'}" for asked, real in wrong.items())
            raise SessionError("unavailable", f"the streams did not reach their speakers: {detail}")

    def _prime(self, change: OutputChange) -> None:
        """Half a second of silence, as `open`: `pw-play` links to its target once it gets data."""
        o = self.options
        for _ in range(max(1, int(0.5 * o.rate / o.block))):
            if not self._feed_one(change):
                return

    def _feed(self, change: OutputChange, seconds: float | None) -> None:
        """Silence at the pace of real time for `seconds` (`None`: until `stop`), so the new
        stream never runs dry while it waits: an A2DP stream that wakes up comes back with
        another offset (experimentos/05)."""
        o = self.options
        pacer = Pacer(o.rate, o.block)
        ticks = None if seconds is None else int(seconds * o.rate / o.block)
        k = 0
        while (ticks is None or k < ticks) and not change.stop.is_set():
            pacer.wait()
            k += 1
            if not self._feed_one(change):
                return

    def _feed_one(self, change: OutputChange) -> bool:
        """One block of silence, unless the pipe already holds enough. False once `stop` is set.

        The write must not block (the engine may be waiting for the lock): it only writes while
        the new pipe holds less than the old one, and never more than its own size allows."""
        with change.feed_lock:
            if change.stop.is_set():
                return False
            player = change.player
            if player is not None and self._new_pipe_has_room(player):
                player.escribir(dict.fromkeys(change.nodes, self._silence))
        return True

    def _new_pipe_has_room(self, player: PlayerLike) -> bool:
        level_of = getattr(player, "nivel_ms", None)
        level = self._level(level_of) if level_of is not None else None
        if level is None:
            return True
        o = self.options
        block_ms = o.block / o.rate * 1000
        cap = pipe_size_ms(o.block, o.rate, o.player_latency_ms) - block_ms
        # As much waiting as in the old pipe, so the new player's fade-in comes out about when the
        # old one's fade-out ends; at least a block, so it never runs dry.
        old = self._level(self.outputs.nivel_ms)
        return level < max(block_ms, cap if old is None else min(old, cap))

    @staticmethod
    def _level(read: Callable[[], float | None]) -> float | None:
        """A pipe level read from the worker: a player closing under it reads as unknown."""
        try:
            return read()
        except Exception:  # noqa: BLE001 - only a guess of how full a pipe is
            return None

    def _advance_output_change(self) -> None:
        """On the engine thread, after the write: ask for the cut once the new player is ready,
        and swap at its bottom. Never while a calibration plays its stimulus (the motor does not
        run then, and the cut would wait for it with the participants changed under it)."""
        change = self._change
        if change is None:
            return
        if change.at_bottom:
            self._swap(change)
        elif (
            change.ready.is_set()
            and not change.cut_requested
            and (self.calibration is None or self.calibration.state != "running")
        ):
            change.cut_requested = True
            self.motor.cortar(lambda: setattr(change, "at_bottom", True))

    def _swap(self, change: OutputChange) -> None:
        """At the bottom of the cut, on the engine thread: the new player in, the old one out."""
        change.stop.set()
        with change.feed_lock:
            pass  # the worker's last write, if one was under way, is done; it writes no more
        previous = self.outputs.attach(change.player, change.playing)
        if change.player is not None:
            self._output_name = change.name
        self._change = None
        if previous is not None:
            self._retire(previous)
        now = self.outputs.playing()
        self.log("salida", motivo=f"suenan: {', '.join(now) if now else 'ninguno'}")
        change.swapped = True
        change.over.set()

    def _retire(self, player: PlayerLike) -> None:
        """Close a swapped-out player on the routing pool: `cerrar` waits for its `pw-play` to play
        out what it holds (down to the bottom of the cut) and can take seconds."""

        def close() -> None:
            try:
                player.cerrar()
            except Exception as exc:  # noqa: BLE001 - the old output closing badly must not stop the audio
                self.log("salida", motivo=f"el reproductor anterior no cerró bien: {exc!r}")

        future = self._routing_pool.submit(close)
        self._retiring = [(f, p) for f, p in self._retiring if not f.done()] + [(future, player)]

    def _wait_retired(self, change: OutputChange, limit_s: float = 15.0) -> None:
        """Wait for the players still closing, in short slices: a cancelled change stops waiting."""
        deadline = time.perf_counter() + limit_s
        for future, _ in list(self._retiring):
            while not change.over.is_set() and time.perf_counter() < deadline:
                try:
                    future.result(timeout=0.05)
                except TimeoutError:
                    continue
                except Exception:  # noqa: BLE001 - closing it failed or was cancelled: it is not closing any more
                    break
                break

    def _follow_playing_set(self) -> None:
        """The loop measures the speakers that were playing when it started. When that set changes
        (a swap, a stream lost) it starts again over the new one: a speaker that no longer plays
        still has a music reference correlated with the others', and measuring it can move its
        delay while it is silent (experimentos/08). A loop the user wants but that stopped for
        lack of two playing speakers comes back once two play again (spec §5: it measures again
        by itself). Never while a calibration owns the microphone."""
        if self.calibration is not None and self.calibration.state in {"running", "measuring"}:
            return
        playing = self.outputs.playing()
        if self.loop is not None:
            if playing == self._measured:
                return
            self.log("lazo", motivo="cambió qué parlantes suenan: el lazo vuelve a empezar")
            self._stop_loop()
        elif not (self._loop_wanted and len(playing) >= 2 and self.microphone):  # noqa: PLR2004 - two to align
            return
        else:
            self.log("lazo", motivo="vuelven a sonar dos parlantes: el lazo vuelve")
        try:
            self.enable_recalibration(self.microphone)
        except Exception as exc:  # noqa: BLE001 - the loop is a side channel: it never ends the audio
            # Not tried again on every block: the user turns it on again.
            self._loop_wanted = False
            self.log("lazo", motivo=f"no volvió a encender: {exc!r}")

    # -- the masked probe (dsp/probe.py), switchable while playing --------------------

    def set_probe(self, active: bool, margin_db: float | None = None) -> None:  # noqa: FBT001
        """Switch the masked probe on or off (50 ms ramp), and/or change its margin, live.

        With the probe on, the loop correlates the microphone against each speaker's probe;
        off, against the music, as before. The probe is added by the engine (`motor.sonda`)."""
        if margin_db is not None:
            lo, hi = masked_probe.MARGIN_RANGE_DB
            if not lo <= margin_db <= hi:
                raise SessionError("out_of_range", f"probe_margin_db must be within {lo} and {hi}")
        if not hasattr(self.motor, "sonda"):
            raise SessionError("unavailable", "this engine cannot carry the probe")
        probe = self.motor.sonda
        if probe is None:
            if not active:
                return
            names = [p.nombre for p in self.installation.parlantes]
            margin = self.options.probe_margin_db if margin_db is None else margin_db
            probe = masked_probe.MaskedProbe(names, self.options.rate, margin)
            self.motor.sonda = probe
        if margin_db is not None:
            probe.margin_db = margin_db
        was = probe.enabled
        probe.enabled = active
        if active != was:
            self.probe_since = None
            self.log("sonda", motivo=f"sonda {'encendida' if active else 'apagada'}, a {probe.margin_db:+.0f} dB")

    def probe_state(self) -> dict[str, Any]:
        """For the panel: whether the probe is on, its margin, and what the loop measures against."""
        probe = getattr(self.motor, "sonda", None)
        on = probe is not None and probe.enabled
        return {
            "active": on,
            "margin_db": probe.margin_db if probe is not None else self.options.probe_margin_db,
            "reference": "probe" if self._probe_ready(time.monotonic()) else "music",
        }

    def _probe_ready(self, now: float) -> bool:
        """A whole measurement window (and the margin not yet heard) of the probe at full level."""
        return (
            self.probe_since is not None
            and now - self.probe_since >= PROBE_WINDOW_S + sincronia.VentanaDeEmision.MARGEN_S
        )

    def _probe_blocks(self, blocks: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
        """What the probe added to each speaker in this block (zeros while it is off)."""
        probe = getattr(self.motor, "sonda", None)
        n = len(next(iter(blocks.values()), []))
        if probe is None or not probe.active or not set(blocks) <= set(probe.last):
            return {name: np.zeros(n) for name in blocks}
        return {name: probe.last[name] for name in blocks}

    # -- the recalibration loop, switchable while playing ----------------------------

    def enable_recalibration(self, microphone: str | None) -> None:
        if self.loop is not None:
            return
        if not microphone:
            raise SessionError("unavailable", "recalibration needs a microphone and none was found")
        o = self.options
        self.microphone = microphone
        self._loop_wanted = True
        # Only what sounds is measured: a virtual, absent or lost speaker never reaches the
        # microphone, and measuring it would give false numbers (spec §3). The loop needs two.
        measured = self.outputs.playing()
        if len(measured) < 2:  # noqa: PLR2004 - an alignment needs two speakers
            why = (
                "no speaker is playing"
                if not measured
                else f"the recalibration loop needs two playing speakers; only {measured[0]} is playing"
            )
            self.log("lazo", motivo=f"{why}: the loop stays off")
            return
        self._measured = measured
        self._emission = sincronia.VentanaDeEmision(measured, o.rate, segundos=o.measure_s + 2.0)
        self._probe_emission = sincronia.VentanaDeEmision(measured, o.rate, segundos=o.measure_s + 2.0)
        self._measurer = sincronia.MedicionEnSegundoPlano(self._measure)
        self._close_mic_check()  # the loop reads the microphone's level itself
        self._mic = self._recal_stack.enter_context(self._microphone(microphone, o.measure_s + 4.0))
        self._recal_stack.callback(self._measurer.cerrar)
        # Per speaker, following each one's drift, on absolute arrivals: the references are
        # taken after the delay line, so the loop's own corrections are not in what it measures
        # (`arrival_loop.py`; `sincronia.Controlador` treated them as residuals and added them
        # again on every round).
        self.loop = arrival_loop.ArrivalLoop(self.installation, self.motor)
        probe = getattr(self.motor, "sonda", None)
        self._next_measure = time.monotonic() + (PROBE_EVERY_S if probe is not None and probe.enabled else o.every_s)
        self.log("lazo", motivo=f"recalibración encendida, con {microphone}")

    def disable_recalibration(self) -> None:
        """The user turns the loop off: it does not come back by itself."""
        self._loop_wanted = False
        self._stop_loop()

    def _stop_loop(self) -> None:
        """Stop the loop, leaving what the user wants as it is (a restart, a calibration's pause)."""
        if self.loop is None:
            return
        self._recal_stack.close()
        self._recal_stack = contextlib.ExitStack()
        self.loop, self._mic = None, None
        self.log("lazo", motivo="recalibración apagada")

    @property
    def drift_ms_h(self) -> dict[str, float] | None:
        """What the loop moves each speaker's delay per hour to follow its drift."""
        return self.loop.deriva_ms_h() if self.loop is not None else None

    @property
    def drift_ppm(self) -> dict[str, float] | None:
        """Each speaker's playback latency drift against the others, in ppm (positive: falls behind)."""
        return (self.loop.drift_ppm() or None) if self.loop is not None else None

    @staticmethod
    def _measure(reference: str, recorded: np.ndarray, references: dict[str, np.ndarray], rate: int) -> Any:
        """On the measuring thread: against the probe, or against the music as before."""
        if reference == "probe":
            return probe_measure.measure(recorded, references, rate)
        return medicion.calibrar(recorded, references, rate)

    def _arrivals(self, result: Any) -> tuple[dict[str, float], set[str], str | None]:
        """A measurement as absolute arrivals, the speakers to believe, and why none if none."""
        if isinstance(result, probe_measure.ProbeMeasurement):
            why = "; ".join(f"{n}: {r}" for n, r in sorted(result.reasons.items())) or None
            return dict(result.arrivals_ms), set(result.valid), why
        # `calibrar` gives corrections (last arrival - arrival): an arrival up to a constant.
        # As before, its measurement is believed whole or not at all (correlated references).
        arrivals = {n: -v for n, v in result.retardos_ms.items()}
        if not result.confiable:
            return arrivals, set(), f"la medición no es estable en: {', '.join(result.dudosos())}"
        return arrivals, set(arrivals) - set(result.sin_sonar()), None

    def _calibration_delays(self) -> dict[str, float]:
        """Each speaker's calibration delay as it plays now (the line's position minus Haas)."""
        now = getattr(self.motor, "retardos_actuales_ms", None)
        current = now() if now is not None else {}
        rear = self.installation.retardo_traseros_ms
        return {
            p.nombre: current.get(p.nombre, p.retardo_ms + p.ambiente * rear) - p.ambiente * rear
            for p in self.installation.parlantes
        }

    def _recalibration_step(self, blocks: dict[str, np.ndarray]) -> None:
        o = self.options
        # Se guarda **exactamente lo que se mandó**, que es la referencia del lazo, y lo que la
        # sonda sumó (ceros si está apagada), que es la referencia con la sonda. Solo de los
        # parlantes que el lazo mide (los que sonaban al encenderlo).
        blocks = {n: blocks[n] for n in self._measured}
        self._emission.agregar(blocks)
        self._probe_emission.agregar(self._probe_blocks(blocks))
        probe = getattr(self.motor, "sonda", None)
        if probe is None or not probe.full:
            self.probe_since = None
        elif self.probe_since is None:
            self.probe_since = time.monotonic()
        self.loop.advance()
        self._mic.bombear()
        # The microphone's own level, so the panel shows it is hearing the room.
        heard = self._mic.ultimos(0.1)
        if heard is not None:
            self.meters.update("mic", heard, len(next(iter(blocks.values()), [])) / o.rate)
            self.telemetry.record_microphone(heard)

        ready, result = self._measurer.recoger()
        if ready:
            window = self._launch.get("window", o.measure_s)
            if isinstance(result, Exception):
                self._record("error", motivo=f"la medición falló: {result}")
            elif self._last_fade >= self._launched_at - window - sincronia.VentanaDeEmision.MARGEN_S:
                # The loop proposes nothing while a fade was inside the measured window:
                # it would be measuring a cut signal (spec §6.5).
                self._record("descartado", motivo="hubo un corte durante la medición")
            elif result is None:
                self._record("descartado", motivo="no se pudo alinear la grabación con las referencias")
            else:
                arrivals, valid, why = self._arrivals(result)
                self._note_residual(arrivals, valid)
                if self.on_measurement is not None:
                    try:
                        self.on_measurement(arrivals, frozenset(valid), self._launch.get("t_mid", time.monotonic()))
                    except Exception:  # noqa: BLE001 - a side channel never stops the loop or the audio
                        self.log("sincronía", motivo="el estimador no tomó la medición; el lazo sigue")
                adjustment = self.loop.propose(arrivals, valid, self._launch.get("t_mid", time.monotonic()))
                self._record(
                    "ajuste" if adjustment.aceptado else "descartado",
                    motivo=adjustment.motivo + (f" ({why})" if why else ""),
                    referencia=self._launch.get("reference"),
                    cambios_ms={n: round(v, 3) for n, v in adjustment.cambios_ms.items()},
                    retardos_ms={p.nombre: round(p.retardo_ms, 3) for p in self.installation.parlantes},
                )

        now = time.monotonic()
        if now < self._next_measure or self._measurer.ocupado:
            return
        on_probe = self._probe_ready(now)
        window = PROBE_WINDOW_S if on_probe else o.measure_s
        probe = getattr(self.motor, "sonda", None)
        self._next_measure = now + (PROBE_EVERY_S if probe is not None and probe.enabled else o.every_s)
        source = self._probe_emission if on_probe else self._emission
        references = source.referencias(window)
        recorded = self._mic.ultimos(window + sincronia.VentanaDeEmision.MARGEN_DEL_MICROFONO_S)
        if references is None or recorded is None:
            return
        # The threshold follows the master volume: it asks whether *content* is playing, and
        # the references are recorded after the volume. With a fixed threshold, at -20 dB the
        # loop called music "no signal" (2026-10-01, music at -42 to -47 dBFS per speaker).
        # Whether the room hears it well enough is the loop's own filters' job. The probe is
        # off in silence by itself (`dsp/probe.py`): no probe anywhere, nothing to measure.
        if (on_probe and not any(np.any(x) for x in references.values())) or (
            not on_probe
            and not self._emission.hay_senal(references, minimo_rms=SIGNAL_RMS * 10 ** (self.motor.volumen_db / 20))
        ):
            self._record("sin señal", motivo="no hay contenido sonando: no se mide")
            return
        self._launched_at = now
        self._launch = {
            "reference": "probe" if on_probe else "music",
            "window": window,
            # The middle of the measured window, on the loop's clock: the drift is fitted on it.
            "t_mid": now - sincronia.VentanaDeEmision.MARGEN_S - window / 2,
            "delays": self._calibration_delays(),
        }
        try:
            self._measurer.lanzar(self._launch["reference"], recorded, references, o.rate)
        except Exception as exc:  # noqa: BLE001 - the loop is a side channel: it never ends the audio
            # Card best-effort-side-channels: a measurement that cannot start is reported and
            # tried again next round. On 2026-10-02 one (in another process) took the session down.
            self._record("error", motivo=f"la medición no arrancó ({exc!r}); se intenta en la próxima vuelta")

    def _note_residual(self, arrivals: dict[str, float], valid: set[str]) -> None:
        """Keep the misalignment a measurement of the loop saw: the spread of each believed
        speaker's arrival plus the calibration delay it played with. Until 2026-10-02 it was the
        spread of the arrivals alone, which leaves out the corrections in place (the references
        are taken after the delay line, `arrival_loop.py`): it showed the uncorrected offsets."""
        delays = self._launch.get("delays", {})
        heard = {n: arrivals[n] + delays.get(n, 0.0) for n in valid if n in arrivals and np.isfinite(arrivals[n])}
        if len(heard) < 2:  # noqa: PLR2004 - a misalignment needs two speakers
            return
        self.last_residual = {
            "residual_ms": round(max(heard.values()) - min(heard.values()), 3),
            "measured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "t": time.monotonic(),
            "speakers": sorted(heard),
        }

    def _record(self, kind: str, **fields: Any) -> None:
        t = round(time.monotonic() - self._started, 2)
        self.last_recalibration = {"t": t, "kind": kind, "reason": fields.get("motivo", "")}
        if "retardos_ms" in fields:
            self.recalibration_history = [*self.recalibration_history, {"t": t, "delays_ms": fields["retardos_ms"]}][
                -120:
            ]
        self.log(kind, t=t, **fields)

    # -- calibration inside the session ---------------------------------------------

    def start_calibration(self, seconds: float, amplitude: float, microphone: str | None) -> None:
        """Measure the residual misalignment, through the corrections now in the installation."""
        if self.calibration is not None and self.calibration.state in {"running", "measuring"}:
            raise SessionError("conflict", "a calibration is already running")
        if not microphone:
            raise SessionError("unavailable", "calibrating needs a microphone and none was found")
        # Only the playing speakers take part: the others never reach the microphone (spec §3).
        # Checked before pausing the loop, so a refusal leaves everything as it was.
        participants = self.outputs.playing()
        if not participants:
            raise SessionError("conflict", "no speaker is playing: nothing would reach the microphone")
        left_out = [n for n in self._sinks if n not in participants]
        # The loop is part of the protocol: a calibration pauses it (both need the microphone
        # and the loop would chase the calibration's own noise) and it comes back after.
        self._loop_paused_for_calibration = self.loop is not None
        if self._loop_paused_for_calibration:
            self._stop_loop()
        applied = {p.nombre: (p.retardo_ms, p.ganancia_db) for p in self.installation.parlantes}
        eq_on = getattr(self.motor, "ecualizacion_activa", True) and getattr(self.motor, "ecualizar", False)
        # The curve that plays (with the chain's max boost, treble cap and budget), not the stored one.
        sounding = getattr(self.motor, "curva_sonando", lambda p: p.ecualizacion_db)
        applied_eq = {p.nombre: (sounding(p) if eq_on else None) for p in self.installation.parlantes}
        cal = Calibration(participants, seconds, amplitude, self.options.rate, applied, applied_eq)
        self._close_mic_check()  # the calibration has the microphone now
        self._cal_mic = self._microphone(microphone, cal.total / self.options.rate + 3.0)
        self._cal_mic.__enter__()
        self.calibration = cal
        outside = f"; quedan fuera (no suenan): {', '.join(left_out)}" if left_out else ""
        self.log("calibración", motivo=f"{seconds:.0f} s a amplitud {amplitude}, con {microphone}{outside}")

    def cancel_calibration(self) -> None:
        if self.calibration is None or self.calibration.state not in {"running", "measuring"}:
            return
        self.calibration.state = "cancelled"
        if self._cal_mic is not None:
            self._cal_mic.cerrar()
            self._cal_mic = None
        self.log("calibración", motivo="cancelada")

    def _calibration_step(self) -> None:
        cal = self.calibration
        if cal is None or self._cal_mic is None:
            return
        self._cal_mic.bombear()
        if cal.state == "running" and cal.emitted:
            # The microphone starts a little after the stimulus (its own startup): asking for
            # the whole span would find it short. 0.4 s less still keeps 0.3 s of silence
            # before the stimulus.
            recording = self._cal_mic.ultimos(cal.total / self.options.rate - MIC_SLACK_S)
            mic, self._cal_mic = self._cal_mic, None
            mic.cerrar()
            if recording is None:
                cal.state, cal.error = "error", "the microphone did not deliver enough audio"
                return
            cal.measure(recording, lambda: self.log("calibración", motivo=f"terminada: {cal.state}"))

    # -- the microphone's level, on demand (the panel's check before calibrating) -------

    @property
    def mic_check(self) -> bool:
        """The microphone is open for the level check now."""
        return self._check_mic is not None

    def check_microphone(self, microphone: str | None, seconds: float | None = None) -> bool:
        """Open the microphone for `seconds` (default `MIC_CHECK_S`) only to show its level. With
        the loop on it already does that, and a calibration has the microphone: then nothing
        opens and the answer is False. Asked again while open, nothing changes: one check lasts
        that long from its start, so a client asking again and again never keeps it open for
        good (privacy); a new check opens only once the previous one closed."""
        if self.loop is not None or self._cal_mic is not None:
            return False
        if not microphone:
            raise SessionError("unavailable", "there is no microphone to check")
        if self._check_mic is not None:
            return True
        span = MIC_CHECK_S if seconds is None else seconds
        self._check_until = time.monotonic() + span
        mic = self._microphone(microphone, 1.0)
        mic.__enter__()
        self._check_mic = mic
        self.log("micrófono", motivo=f"abierto para medir su nivel, {span:g} s")
        return True

    def _mic_check_step(self, blocks: dict[str, np.ndarray]) -> None:
        mic = self._check_mic
        if mic is None:
            return
        if time.monotonic() >= self._check_until or self.loop is not None or self._cal_mic is not None:
            self._close_mic_check()
            return
        mic.bombear()
        heard = mic.ultimos(0.1)
        if heard is not None:
            self.meters.update("mic", heard, len(next(iter(blocks.values()), [])) / self.options.rate)
            self.telemetry.record_microphone(heard)

    def _close_mic_check(self) -> None:
        if self._check_mic is None:
            return
        self._check_mic.cerrar()
        self._check_mic = None
        if self.loop is None:
            # The reading goes with the microphone: a stale level must not look live.
            self.meters.values.pop("mic", None)

    # -- test tone ------------------------------------------------------------------

    def tone(self, speaker: str, seconds: float) -> None:
        if speaker not in self._sinks:
            raise SessionError("not_found", f"no speaker {speaker!r}")
        self._tones[speaker] = [0, int(seconds * self.options.rate)]

    def _add_tones(self, blocks: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
        if not self._tones:
            return blocks
        rate = self.options.rate
        fade = int(0.01 * rate)
        out = dict(blocks)
        for name, (pos, total) in list(self._tones.items()):
            n = len(out[name])
            i = pos + np.arange(n)
            envelope = np.clip(np.minimum(i, total - i) / fade, 0.0, 1.0)
            out[name] = out[name] + TONE_AMPLITUDE * envelope * np.sin(2 * np.pi * TONE_HZ * i / rate)
            if pos + n >= total:
                del self._tones[name]
            else:
                self._tones[name][0] = pos + n
        return out

    # -- source ---------------------------------------------------------------------

    def set_source(self, kind: str, name: str | None, done: Callable[[str | None], None]) -> None:
        """Switch the source on a worker thread; `done(error)` is called at the end."""
        if self.source is None:
            raise SessionError("unavailable", "the session is not open")
        if self.source_busy:
            raise SessionError("conflict", "the source is still switching")
        self.source_busy = True
        source = self.source

        def run() -> None:
            error = None
            try:
                # A multichannel render is read by the engine itself (multichannel.py): opened and
                # checked here, off the engine thread; nothing changes if it is not usable.
                reader = (
                    MultichannelFile(str(name), [p.nombre for p in self.installation.parlantes], self.options.rate)
                    if kind == "multichannel"
                    else None
                )
                source.set(kind, name)
                self.multichannel = reader
            except ValueError as exc:
                error = str(exc)
                source.error = error
            finally:
                self.source_busy = False
            done(error)

        threading.Thread(target=run, name="aurasync-source-switch", daemon=True).start()

    # -- what a simulated session replaces ------------------------------------------

    def _microphone(self, name: str, seconds: float) -> sonido.MicrofonoContinuo:
        return sonido.MicrofonoContinuo(name, self.options.rate, segundos=seconds)

    def _new_source(self) -> Source:
        return Source(self.options.sink_name)

    # -- for the panel --------------------------------------------------------------

    def pids(self) -> dict[str, Any]:
        players = self.outputs.pids
        return {
            "sink": self._input.pid if self._input is not None else None,
            "players": {n: players.get(sink) if sink is not None else None for n, sink in self._sinks.items()},
            "microphone": next((m.pid for m in (self._mic, self._check_mic) if m is not None), None),
            "source": self.source.pid if self.source is not None else None,
        }
