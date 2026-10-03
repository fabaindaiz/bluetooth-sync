"""Measuring each speaker against its masked probe (`probe_measure.py`), on the real engine.

SIMULATED: synthetic music, the engine with the probe on, a room with fractional delays, the
Go 4's roll-off, a reverberant tail per speaker and microphone noise (`tests/probe_room.py`).
Two seeds each (CLAUDE.md: a true delay shows up twice). Black and Blue share a pan, the case
where correlating against the music confuses them (experimentos/11).
"""

import numpy as np
import pytest

from aurasync import medicion, motor, probe_measure
from aurasync.config import Instalacion, Parlante
from aurasync.dsp import probe
from tests import probe_room

SR = 48000
BLOCK = 4096
WINDOW_S = 4.0
EXPERIMENT_11 = ((3.13, 7.61, 12.27), (1.0, 0.8, 0.6))
"""The room of `probes/13`: delays (ms) and gains of Red, Black and Blue."""
LEAD_S = 0.5
"""The microphone's slice starts this much before the references (`VentanaDeEmision`)."""


def _engine_run(n: int, seed: int, *, decorrelate: bool, margin_db: float = probe.MARGIN_DB, heard=None, room=None):
    rng = np.random.default_rng(seed)
    pans, ambience = (-0.7, 0.7, 0.7), (0.15, 0.15, 0.55)
    inst = Instalacion(
        parlantes=[Parlante(f"s{i}", f"sink{i}", pan=pans[i % 3], ambiente=ambience[i % 3]) for i in range(n)],
        retardo_traseros_ms=0.0,
    )
    m = motor.Motor(inst, SR, decorrelar=decorrelate)
    m.sonda = probe.MaskedProbe([p.nombre for p in inst.parlantes], SR, margin_db, seed=seed)
    m.sonda.enabled = True
    left, right = probe_room.music(1.0 + WINDOW_S + 1.5, rng)
    names = [p.nombre for p in inst.parlantes]
    out = {nm: [] for nm in names}
    sent = {nm: [] for nm in names}
    for i in range(0, len(left), BLOCK):
        for nm, x in m.procesar(left[i : i + BLOCK], right[i : i + BLOCK]).items():
            out[nm].append(x)
            sent[nm].append(m.sonda.last[nm])
    feeds = {nm: np.concatenate(v) for nm, v in out.items()}
    probes = {nm: np.concatenate(v) for nm, v in sent.items()}
    delays = list(rng.uniform(2, 30, n))
    gains = list(10 ** (rng.uniform(-6, 0, n) / 20))
    if room is not None:
        delays, gains = list(room[0]), list(room[1])
    if heard is not None:
        gains = [g if i in heard else 0.0 for i, g in enumerate(gains)]
    mic = probe_room.room([feeds[nm] for nm in names], delays, gains, rng)
    start = int(1.0 * SR)
    window = int(WINDOW_S * SR)
    lead = int(LEAD_S * SR)
    references = {nm: p[start : start + window] for nm, p in probes.items()}
    music_refs = {nm: f[start : start + window] for nm, f in feeds.items()}
    recording = mic[start - lead : start + window + SR]
    truth = {nm: LEAD_S * 1000 + d for nm, d in zip(names, delays, strict=True)}
    return recording, references, music_refs, truth


@pytest.mark.parametrize("seed", [0, 1])
@pytest.mark.parametrize(("n", "decorrelate"), [(3, True), (8, True), (8, False)])
def test_the_probe_finds_every_speaker(n, decorrelate, seed):
    recording, references, _, truth = _engine_run(n, seed, decorrelate=decorrelate)
    result = probe_measure.measure(recording, references, SR)
    assert result.valid == frozenset(truth), result.reasons
    errors = {nm: abs(result.arrivals_ms[nm] - truth[nm]) for nm in truth}
    assert max(errors.values()) < 0.05, errors


def test_where_the_music_confuses_the_speakers_the_probe_does_not():
    """What the loop did until now, correlating against the music each speaker got, against
    the probe, on the same recordings: experimentos/11 saw the music wrong by more than 1 ms
    in 70-85 % of the trials, with its room (Black and Blue 4.66 ms apart)."""
    music_wrong = probe_wrong = 0
    for seed in range(10, 16):
        recording, references, music_refs, truth = _engine_run(3, seed, decorrelate=True, room=EXPERIMENT_11)
        cal = medicion.calibrar(recording, music_refs, SR)
        last = max(truth.values())
        music_wrong += cal is None or any(abs(cal.retardos_ms[nm] - (last - truth[nm])) > 1.0 for nm in truth)
        result = probe_measure.measure(recording, references, SR)
        probe_wrong += any(abs(result.arrivals_ms[nm] - truth[nm]) > 1.0 for nm in truth) or len(result.valid) < 3
    assert probe_wrong == 0
    assert music_wrong >= 3, music_wrong


def test_a_speaker_the_microphone_does_not_hear_is_not_believed():
    recording, references, _, _ = _engine_run(3, 3, decorrelate=True, heard={0, 1})
    result = probe_measure.measure(recording, references, SR)
    assert "s2" not in result.valid
    assert {"s0", "s1"} <= result.valid
    assert "s2" in result.reasons


def test_a_speaker_whose_arrival_jumps_inside_the_window_is_not_believed():
    """A stream that resynchronises halfway through the window: the whole window has two
    peaks and picks one, the halves disagree. Only the repetition between the halves sees it."""
    recording, references, _, truth = _engine_run(3, 5, decorrelate=True)
    rng = np.random.default_rng(5)
    jumped = np.array(recording)
    # Speaker s1's own contribution, heard 3 ms later from the window's middle on.
    lead = int(LEAD_S * SR)
    probe_s1 = np.zeros(len(recording))
    probe_s1[lead : lead + len(references["s1"])] = references["s1"]
    middle = lead + len(references["s1"]) // 2
    before = probe_room.room([probe_s1], [truth["s1"] - LEAD_S * 1000], [1.0], rng)
    after = probe_room.room([probe_s1], [truth["s1"] - LEAD_S * 1000 + 3.0], [1.0], rng)
    jumped[middle:] += 4 * (after[middle:] - before[middle:])
    result = probe_measure.measure(jumped, references, SR)
    assert "s1" not in result.valid, result.halves_ms["s1"]
    assert "disagree" in result.reasons["s1"]
    assert {"s0", "s2"} <= result.valid


def test_a_speaker_without_probe_is_reported_as_silence():
    recording, references, _, _ = _engine_run(3, 4, decorrelate=True)
    references["s1"] = np.zeros_like(references["s1"])
    result = probe_measure.measure(recording, references, SR)
    assert "s1" not in result.valid
    assert "silence" in result.reasons["s1"]
