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
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

import numpy as np

from aurasync import arrival_loop, estimulos, group_calibration, medicion, probe_measure, sincronia, sonido
from aurasync.cuts import LATE_MS, LOW_MS, CutLog
from aurasync.dsp import eq, response
from aurasync.dsp import probe as masked_probe
from aurasync.dsp.input_analysis import InputAnalyzer
from aurasync.dsp.retardo import LineaDeRetardo
from aurasync.multichannel import MultichannelFile
from aurasync.probe_ring import ProbeRing
from aurasync.quality import QualityMeter
from aurasync.sources import Source
from aurasync.telemetry import Telemetry

if TYPE_CHECKING:
    from collections.abc import Callable

    from aurasync.config import Instalacion
    from aurasync.motor import Motor


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
    nodes = {s.nodo for s in sonido.salidas_bluetooth()}
    return [p.nombre for p in installation.parlantes if p.sink not in nodes]


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


class AudioSession:
    """Opens the streams and the virtual sink, processes one block per `step`, closes."""

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
        self.last_recalibration: dict[str, Any] | None = None
        self.recalibration_history: list[dict] = []
        """The delay the loop applied to each speaker, after each decision (for the chart)."""
        self.last_residual: dict[str, Any] | None = None
        """The residual misalignment the loop measured last, through the corrections in place
        (the spread of the measured delays, the speakers heard), with its clock: the panel's
        "Sincronía" (spec 2026-10-02 §7.3.4). Only a reliable measurement without a cut."""
        self.lost: list[str] = []
        """Speakers whose stream died. The rest keep playing, as `run` always did."""
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
        self._last_step_at: float | None = None
        self._input_gap_since: float | None = None
        self._was_fading = False
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
        self._player: sonido.Reproductor | None = None
        self._input: sonido.SinkVirtual | None = None
        self._mic: sonido.MicrofonoContinuo | None = None
        self._cal_mic: sonido.MicrofonoContinuo | None = None
        self.monitor = None
        """The headphone monitor (monitor.MonitorOutput), not synchronised with the speakers."""
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
        missing = missing_speakers(self.installation)
        if missing:
            raise SessionError("unavailable", f"not connected: {', '.join(missing)}")
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

    def _open_streams(self) -> None:
        o = self.options
        nodes = list(self._sinks.values())
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
        pipe_ms = pipe_size_ms(o.block, o.rate, o.player_latency_ms)
        if o.output == "combinado":
            player = sonido.ReproductorCombinado(
                nodes, o.rate, o.player_latency_ms, tuberia_ms=pipe_ms, nombre=f"{o.sink_name}_salida"
            )
        else:
            player = sonido.Reproductor(nodes, o.rate, o.player_latency_ms, tuberia_ms=pipe_ms)
        self._player = self._stack.enter_context(player)
        for _ in range(max(1, int(0.5 * o.rate / o.block))):
            self._player.escribir(dict.fromkeys(nodes, self._silence))
        self._input = self._stack.enter_context(sonido.SinkVirtual(o.sink_name, o.sink_description, o.rate))

        # **La comprobación va después de crear la entrada, y no antes.** Al aparecer,
        # WirePlumber toma el sink virtual como salida por defecto y mueve el stream que
        # apuntaba al default anterior. Comprobar antes no veía nada.
        time.sleep(1.0)
        for asked, real in self._player.reparar_ruteo().items():
            self.log("ruteo", motivo=f"se desvió a {real or 'ningún destino'}: {asked} — devuelto")
        time.sleep(0.5)
        lost = self._player.mal_ruteados()
        if lost:
            detail = "; ".join(f"{asked} → {real or 'ningún destino'}" for asked, real in lost.items())
            raise SessionError("unavailable", f"the streams did not reach their speakers: {detail}")

        self._stack.callback(self._recal_stack.close)
        if o.recalibrate:
            self.enable_recalibration(o.microphone)
        self.source = self._new_source()
        self._stack.callback(self.source.close)
        self._next_routing_check = time.monotonic() + ROUTING_CHECK_S

    def close(self) -> None:
        """Closes everything in reverse order. Safe to call twice."""
        self.attach_monitor(None)
        self._stack.close()
        self.quality.close()
        self.cuts.close()
        self._routing_pool.shutdown(wait=False, cancel_futures=True)
        self._routing_future = None
        if self._cal_mic is not None:
            self._cal_mic.cerrar()
        self._player = self._input = self._mic = self._cal_mic = None
        self.loop = None

    # -- one block ------------------------------------------------------------------

    def step(self) -> None:
        if self._player is None or self._input is None:
            msg = "the session is not open"
            raise RuntimeError(msg)
        o = self.options
        seconds = o.block / o.rate
        started = time.monotonic()
        late_ms = None
        if self._last_step_at is not None:
            late_ms = (started - self._last_step_at - seconds) * 1000
        self._last_step_at = started
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
            blocks = self.calibration.next_blocks(o.block)
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
        self._player.escribir({self._sinks[n]: x for n, x in blocks.items()})
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
        alive = set(self._player.vivos)
        if not alive:
            raise SessionError("unavailable", "every speaker disconnected")
        lost = [n for n, sink in self._sinks.items() if sink not in alive]
        if lost != self.lost:
            for name in set(lost) - set(self.lost):
                self.cuts.add("lost", name, "el stream hacia el parlante murió")
            self.log("parlante perdido", motivo=f"sin stream: {', '.join(lost)}; siguen los demás")
            self.lost = lost
        self._check_routing()
        if self.motor.en_corte:
            self._last_fade = time.monotonic()
            if not self._was_fading:
                self.cuts.add("fade", None, self.cuts.context.get("last_order") or "")
        self._was_fading = self.motor.en_corte
        self._calibration_step()
        if (
            getattr(self, "_loop_paused_for_calibration", False)
            and self.calibration is not None
            and self.calibration.state not in {"running", "measuring"}
        ):
            self._loop_paused_for_calibration = False
            self.enable_recalibration(self.microphone)
        if self.loop is not None and (self.calibration is None or self.calibration.state != "running"):
            self._recalibration_step(blocks)

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
        level = getattr(self._player, "nivel_ms", lambda: None)()
        self.pipe_ms = level
        if late_ms is not None and late_ms > LATE_MS:
            self.cuts.add("late", "motor", f"{late_ms:.0f} ms tarde", late_ms=round(late_ms))
        if level is None or self.blocks < 3:  # noqa: PLR2004 - the pipe fills during the first blocks
            return
        if level < 1.0:
            self.cuts.add("underrun", "salida", "la tubería estaba vacía", level_ms=round(level, 1))
        elif level < LOW_MS:
            self.cuts.add("low", "salida", f"quedaban {level:.0f} ms", level_ms=round(level, 1))

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
            self._routing_future = None
            try:
                wrong, existing = future.result()
            except Exception as exc:  # noqa: BLE001 - a failed check must not stop the audio
                self.log("ruteo", motivo=f"no se pudo comprobar: {exc!r}")
                wrong, existing = {}, set()
            alive = set(self._player.vivos)
            wrong = {asked: real for asked, real in wrong.items() if asked in alive}
            # A stream whose speaker no longer exists cannot be moved back: the speaker was
            # turned off. Its stream is closed so that it cannot play anywhere else.
            gone = [asked for asked in wrong if asked not in existing]
            for asked in gone:
                self._player.soltar(asked)
                self.log(
                    "parlante perdido",
                    motivo=f"{asked} ya no existe; su stream iba a {wrong[asked] or 'ningún destino'}",
                )
            movable = {asked: real for asked, real in wrong.items() if asked not in gone}
            if movable:
                self._routing_pool.submit(self._player.reparar_ruteo)
                self.routing_repairs += 1
                for asked, real in movable.items():
                    self.cuts.add("routing", asked, f"había ido a {real or 'ningún destino'}")
                    self.log("ruteo", motivo=f"se desvió a {real or 'ningún destino'}: {asked} — devuelto")
            if not self._player.vivos:
                raise SessionError("unavailable", "every speaker disconnected")
        now = time.monotonic()
        if now < self._next_routing_check or self._routing_future is not None:
            return
        self._next_routing_check = now + ROUTING_CHECK_S
        self._routing_future = self._routing_pool.submit(self._read_routing, self._player)

    @staticmethod
    def _read_routing(player) -> tuple[dict[str, str | None], set[str]]:
        """On the worker: the streams that are not where they were asked, and the nodes that exist."""
        wrong = player.mal_ruteados()
        existing = sonido.leer_nombres_de_nodo(sonido._pw_dump()) if wrong else set()  # noqa: SLF001
        return wrong, existing

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
        if probe is None or not probe.active or set(probe.last) != set(blocks):
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
        self._emission = sincronia.VentanaDeEmision(list(self._sinks), o.rate, segundos=o.measure_s + 2.0)
        self._probe_emission = sincronia.VentanaDeEmision(list(self._sinks), o.rate, segundos=o.measure_s + 2.0)
        self._measurer = sincronia.MedicionEnSegundoPlano(self._measure)
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
        # sonda sumó (ceros si está apagada), que es la referencia con la sonda.
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
        # The loop is part of the protocol: a calibration pauses it (both need the microphone
        # and the loop would chase the calibration's own noise) and it comes back after.
        self._loop_paused_for_calibration = self.loop is not None
        if self._loop_paused_for_calibration:
            self.disable_recalibration()
        applied = {p.nombre: (p.retardo_ms, p.ganancia_db) for p in self.installation.parlantes}
        eq_on = getattr(self.motor, "ecualizacion_activa", True) and getattr(self.motor, "ecualizar", False)
        # The curve that plays (with the chain's max boost, treble cap and budget), not the stored one.
        sounding = getattr(self.motor, "curva_sonando", lambda p: p.ecualizacion_db)
        applied_eq = {p.nombre: (sounding(p) if eq_on else None) for p in self.installation.parlantes}
        cal = Calibration(list(self._sinks), seconds, amplitude, self.options.rate, applied, applied_eq)
        self._cal_mic = self._microphone(microphone, cal.total / self.options.rate + 3.0)
        self._cal_mic.__enter__()
        self.calibration = cal
        self.log("calibración", motivo=f"{seconds:.0f} s a amplitud {amplitude}, con {microphone}")

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
        players = self._player.pids if self._player is not None else {}
        return {
            "sink": self._input.pid if self._input is not None else None,
            "players": {n: players.get(sink) for n, sink in self._sinks.items()},
            "microphone": self._mic.pid if self._mic is not None else None,
            "source": self.source.pid if self.source is not None else None,
        }
