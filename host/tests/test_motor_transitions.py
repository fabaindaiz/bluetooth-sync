"""Transitions of the motor (spec seamless-transitions 2026-10-08).

Switching the engine between blocks, with no cut, changes nothing audible (§2): every stage moves
its state exactly between numpy and Rust. And a change in crossfade mode (`Motor.cambiar`, §3 and
the stage-1 rows of §4) glides every ramp and crossfades every delay over `fade_ms`, with no hole,
and lands exactly where a cut lands.
"""

from __future__ import annotations

import itertools
import json

import numpy as np
import pytest

from aurasync import motor as motor_module
from aurasync.chain import ChainValues, validate_set
from aurasync.config import Instalacion, Parlante
from aurasync.dsp import ambience, backend, eq
from aurasync.dsp.transition import Crossfaded, Transition

BLOCK = 4096
# 27 third-octave bands, each between 0 and 6 dB, not flat.
CURVE = [round(3.0 + 3.0 * np.sin(0.7 * k), 3) for k in range(27)]


def _run(inst, chain, blocks, switches):
    backend.reset()
    backend.use("numpy")
    motor = motor_module.Motor(inst, 48000, ecualizar=True, chain=chain, semilla=1, bloque=BLOCK)
    out: dict[str, list] = {}
    for k, (left, right) in enumerate(blocks):
        if k in switches:
            backend.use(switches[k])  # no cortar: straight between blocks
        for name, block in motor.procesar(left, right).items():
            out.setdefault(name, []).append(block)
    return {name: np.concatenate(parts) for name, parts in out.items()}


def test_a_hot_switch_on_the_full_chain_changes_nothing():
    pytest.importorskip("aurasync_engine")
    speakers = [
        Parlante(f"V{i}", None, pan=pan, retardo_ms=delay, ecualizacion_db=CURVE)
        for i, (pan, delay) in enumerate([(-0.8, 3.2), (0.8, 7.5), (-0.6, 11.0), (0.6, 0.0)])
    ]
    inst = Instalacion(parlantes=speakers)
    chain = ChainValues()
    for stage, algorithm in {
        "spatial": "front",
        "diffuse": "noise_tail",
        "bass": "protect",
        "limiter": "true_peak",
    }.items():
        chain = chain.with_algorithm(stage, algorithm)
    rng = np.random.default_rng(3)
    blocks = [(rng.standard_normal(BLOCK) * 0.2, rng.standard_normal(BLOCK) * 0.2) for _ in range(16)]
    try:
        base = _run(inst, chain, blocks, {})
        hot = _run(inst, chain, blocks, {5: "rust", 10: "numpy"})
    finally:
        backend.reset()
    assert base.keys() == hot.keys()
    for name in base:
        assert np.max(np.abs(hot[name] - base[name])) <= 1e-9, name


# -- the crossfade mode (Motor.cambiar) --------------------------------------------------------

SR = 48000
WINDOW = SR // 100  # 10 ms
HOP = SR // 1000  # windows every 1 ms
CHANGE_AT = 10  # the block before which the change is asked


def _chain(algorithm: str = "crossfade", **params) -> ChainValues:
    values = ChainValues().with_algorithm("transition", algorithm)
    if params:
        values = values.with_change(validate_set("transition", params=params))
    return values


def _pair() -> Instalacion:
    return Instalacion(
        parlantes=[
            Parlante("A", None, pan=-0.7, ganancia_db=-3.0, retardo_ms=3.0),
            Parlante("B", None, pan=0.7, ganancia_db=-3.0, retardo_ms=3.0),
        ]
    )


def _change(inst: Instalacion) -> None:
    """Pan -0.7 -> 0.4 (mirrored on B), gain -3 -> 0 dB, delay 3 -> 25 ms."""
    a, b = inst.parlantes
    a.pan, b.pan = 0.4, -0.4
    for p in inst.parlantes:
        p.ganancia_db, p.retardo_ms = 0.0, 25.0


def _noise(count: int, seed: int = 5) -> list[tuple[np.ndarray, np.ndarray]]:
    """Independent noise on L and R, x0.05: far below the limiter's ceiling."""
    rng = np.random.default_rng(seed)
    return [(rng.standard_normal(BLOCK) * 0.05, rng.standard_normal(BLOCK) * 0.05) for _ in range(count)]


def _motor(chain: ChainValues, inst: Instalacion | None = None, *, equalize: bool = False) -> motor_module.Motor:
    return motor_module.Motor(inst or _pair(), SR, ecualizar=equalize, chain=chain, semilla=1, bloque=BLOCK)


def _play(m: motor_module.Motor, blocks, events=None) -> dict[str, np.ndarray]:
    """`events[k](m)` runs before block k."""
    out: dict[str, list] = {}
    for k, (left, right) in enumerate(blocks):
        if events and k in events:
            events[k](m)
        for name, block in m.procesar(left, right).items():
            out.setdefault(name, []).append(block)
    return {name: np.concatenate(parts) for name, parts in out.items()}


def _rms_db(x: np.ndarray) -> float:
    return 10 * np.log10(max(float(np.mean(x**2)), 1e-30))


def _windows_db(x: np.ndarray) -> np.ndarray:
    """The 10 ms RMS (dBFS) of `x`, every 1 ms."""
    return np.array([_rms_db(x[i : i + WINDOW]) for i in range(0, len(x) - WINDOW + 1, HOP)])


def _ask(how: str):
    return lambda m: getattr(m, how)(lambda: _change(m.instalacion))


def test_a_change_in_crossfade_mode_has_no_hole():
    """With the default chain (`shape=equal_gain`). The lowest window, 0.7-0.8 dB under the steady
    level (measured 2026-10-08), is the noise's own spread, the same as in the steady part."""
    blocks = _noise(30)
    out = _play(_motor(_chain()), blocks, {CHANGE_AT: _ask("cambiar")})
    for name, x in out.items():
        before = _rms_db(x[3 * BLOCK : CHANGE_AT * BLOCK])
        after = _rms_db(x[(CHANGE_AT + 4) * BLOCK :])
        windows = _windows_db(x[3 * BLOCK :])
        assert windows.min() >= min(before, after) - 1.0, (name, windows.min(), before, after)


def test_the_same_change_in_cut_mode_drops_to_silence():
    out = _play(_motor(_chain("cut")), _noise(30), {CHANGE_AT: _ask("cambiar")})
    for name, x in out.items():
        assert _windows_db(x[3 * BLOCK :]).min() < -60, name


def test_a_crossfade_lands_where_a_cut_lands():
    blocks = _noise(30)
    faded = _motor(_chain())
    crossfade = _play(faded, blocks, {CHANGE_AT: _ask("cambiar")})
    cut = _play(_motor(_chain()), blocks, {CHANGE_AT: _ask("cortar")})
    start = CHANGE_AT * BLOCK + round(0.080 * SR) + SR
    assert start < 30 * BLOCK - SR // 2
    for name in crossfade:
        assert np.max(np.abs(crossfade[name][start:] - cut[name][start:])) <= 1e-9, name
    assert not faded.en_corte
    assert faded.retardos_actuales_ms() == {"A": 25.0, "B": 25.0}


def test_requests_in_a_row_make_one_pending_transition():
    m = _motor(_chain(fade_ms=500.0))  # 24000 samples: almost six blocks
    started = []
    begin = m._empezar_transicion  # noqa: SLF001
    m._empezar_transicion = lambda: (started.append(1), begin())  # noqa: SLF001
    a = m.instalacion.parlantes[0]
    pans = list(np.linspace(-0.6, 0.6, 50))

    def ask(values):
        return lambda m: [m.cambiar(lambda v=v: setattr(a, "pan", float(v))) for v in values]

    events = {2: ask(pans[:1]), 3: ask(pans[1:20]), 4: ask(pans[20:40]), 5: ask(pans[40:])}
    _play(m, _noise(20), events)
    assert len(started) == 2
    assert not m.en_corte
    assert a.pan == pans[-1]
    assert m._pan["A"].target == m._pan["A"].current == pans[-1]  # noqa: SLF001


def test_a_cut_during_a_transition_lands_everything_at_the_bottom():
    m = _motor(_chain(fade_ms=500.0))
    later = []
    events = {
        2: _ask("cambiar"),
        3: lambda m: (m.cambiar(lambda: later.append(1)), m.cortar()),
    }
    _play(m, _noise(8), events)  # the cut is done after 2 blocks; the fade would need 6
    assert later == [1]  # the pending action ran at the bottom
    assert not m._transicion.busy  # noqa: SLF001
    assert not m.en_corte
    assert not any(linea.fundiendo for linea in m._lineas.values())  # noqa: SLF001
    for p in m.instalacion.parlantes:
        assert m._pan[p.nombre].current == m._pan[p.nombre].target == p.pan  # noqa: SLF001
        assert m._ganancia[p.nombre] == 10 ** (p.ganancia_db / 20)  # noqa: SLF001
    assert m.retardos_actuales_ms() == m.retardos_efectivos_ms()


def test_hot_switch_during_a_crossfade_changes_nothing():
    pytest.importorskip("aurasync_engine")
    blocks = _noise(16)

    def run(switches: dict[int, str]) -> dict[str, np.ndarray]:
        backend.reset()
        backend.use("numpy")
        events = {CHANGE_AT - 4: _ask("cambiar")}
        events.update({k: (lambda _m, e=e: backend.use(e)) for k, e in switches.items()})
        return _play(_motor(_chain(fade_ms=500.0)), blocks, events)

    try:
        base = run({})
        hot = run({CHANGE_AT - 2: "rust"})  # two blocks into a six-block fade
    finally:
        backend.reset()
    for name in base:
        assert np.max(np.abs(hot[name] - base[name])) <= 1e-9, name


def test_slow_control_delay_goes_through_the_transition():
    inst = _pair()
    inst.parlantes[1].retardo_ms = 160.0  # the lines are built for up to 320 ms

    def jump(m):
        inst.parlantes[0].retardo_ms = 303.0  # 300 ms: ten minutes of ramp
        m.actualizar_desde_control()
        assert m.en_corte
        assert m.metricas_cadena()["transition"]["busy"]

    m = _motor(_chain(), inst)
    out = _play(m, _noise(24), {CHANGE_AT: jump})
    for name, x in out.items():
        assert _windows_db(x[5 * BLOCK :]).min() > -60, name
    assert m.retardos_actuales_ms()["A"] == pytest.approx(303.0, abs=1e-9)


def test_a_control_that_undoes_a_fading_delay_fades_back():
    """During a fade a line still reports its old delay (`actual_ms`), but it is going to the new
    one: a control that asks for the old value again is a slow change from where the line lands,
    so it fades back in the next transition instead of crawling 22 ms at 0.5 ms/s afterwards."""
    m = _motor(_chain(fade_ms=500.0))

    def undo(m):
        for p in m.instalacion.parlantes:
            p.retardo_ms = 3.0
        m.actualizar_desde_control()

    _play(m, _noise(20), {2: lambda m: m.cambiar(lambda: _change(m.instalacion)), 4: undo})
    assert not m.en_corte
    assert m.retardos_actuales_ms() == {"A": 3.0, "B": 3.0}


def test_a_request_re_entered_mid_block_starts_at_the_next_block():
    """The motor has one writer, but `cambiar` can be re-entered from inside `procesar` (an action
    or a callback asking for a change): a `begin` that lands after the block's start check must not
    be counted by that block, or a fade with nothing to do would run first and the change would
    wait for a second one (here 500 ms: six blocks each)."""
    m = _motor(_chain(fade_ms=500.0))
    blocks = _noise(8)
    asked = []

    def mid_block(*_args):
        if not asked:
            asked.append(1)
            m.cambiar(lambda: _change(m.instalacion))
        return 0.0

    m._volumen.block_db = mid_block  # noqa: SLF001  (called once per block, after the start check)
    _play(m, blocks[:1])
    assert m.en_corte
    assert m._transicion.busy  # noqa: SLF001
    assert not m._transicion.started  # noqa: SLF001
    _play(m, blocks[1:])  # seven blocks: one fade fits, two do not
    assert not m.en_corte
    assert m.retardos_actuales_ms() == {"A": 25.0, "B": 25.0}
    assert m._pan["A"].current == 0.4  # noqa: SLF001


def test_a_delay_crossfade_is_equal_power_whatever_the_shape():
    """Two reads of one line 22 ms apart are uncorrelated on noise (and on broadband music):
    `equal_gain` weights dipped 3.2 dB at mid-fade (measured 2026-10-08), so the lines always fade at
    equal power; `shape` is for the stateful stages' crossfades (spec stage 2)."""

    def only_delay(m):
        for p in m.instalacion.parlantes:
            p.retardo_ms = 25.0

    m = _motor(_chain(shape="equal_gain", fade_ms=500.0))  # 24000 samples
    out = _play(m, _noise(20), {CHANGE_AT: lambda m: m.cambiar(lambda: only_delay(m))})
    middle = CHANGE_AT * BLOCK + 12000
    for name, x in out.items():
        steady = _rms_db(x[3 * BLOCK : CHANGE_AT * BLOCK])
        assert abs(_rms_db(x[middle - WINDOW // 2 : middle + WINDOW // 2]) - steady) <= 1.0, name


def test_a_pending_batch_cuts_if_the_mode_turned_to_cut():
    """`transition` switched to `cut` during a fade: what waits for the next transition goes
    through one cut, not another crossfade."""
    m = _motor(_chain(fade_ms=500.0))
    ran = []
    events = {
        2: _ask("cambiar"),
        3: lambda m: (
            m.cambiar(lambda: ran.append(1)),
            m.aplicar_cadena(m.cadena.with_algorithm("transition", "cut")),
        ),
    }
    out = _play(m, _noise(14), events)
    assert ran == [1]
    assert not m.en_corte
    for name, x in out.items():
        assert _windows_db(x[8 * BLOCK :]).min() < -60, name  # the cut's bottom, after the fade


def test_a_change_asked_after_a_pending_cut_lands_after_it():
    """A `cambiar` asked while a cut fades out joins that cut's bottom, after what the cut carries:
    the later request wins. Before, the transition started at the next block and the cut's actions
    ran at its bottom 1-2 blocks later, so the earlier request won (final review, 2026-10-08)."""
    m = _motor(_chain())
    a = m.instalacion.parlantes[0]
    _play(m, _noise(1))
    m.cortar(lambda: setattr(a, "retardo_ms", 10.0))
    assert m.corte_pendiente
    m.cambiar(lambda: setattr(a, "retardo_ms", 20.0))
    _play(m, _noise(10))
    assert not m.en_corte
    assert a.retardo_ms == 20.0
    assert m.retardos_actuales_ms()["A"] == 20.0
    assert not m._transicion.busy  # noqa: SLF001  (one cut, no transition after it)


def test_a_change_during_the_fade_in_of_a_cut_crossfades_without_a_second_dip():
    """Once a cut is past its bottom it is no longer pending: what is asked during its fade-in
    crossfades instead of turning the gate around for a second dip."""
    m = _motor(_chain())
    blocks = _noise(16)
    _play(m, blocks[:2])
    m.cortar()
    _play(m, blocks[2:3])  # the whole fade-out (80 ms) fits in this block: the bottom
    assert m._corte.state == "in"  # noqa: SLF001
    assert not m.corte_pendiente
    m.cambiar(lambda: _change(m.instalacion))
    assert m._corte.state == "in"  # noqa: SLF001
    assert m._transicion.busy  # noqa: SLF001
    rest = _play(m, blocks[3:])
    assert not m.en_corte
    assert m.retardos_actuales_ms() == {"A": 25.0, "B": 25.0}
    for name, x in rest.items():
        # The fade-in, then the crossfade: nothing goes back to silence after the bottom.
        assert _windows_db(x[BLOCK:]).min() > -40, name


def test_a_cut_bottom_in_the_same_block_as_a_fade_end_runs_the_batch_once():
    """The fade ends in the same block as a cut's bottom, with a pending batch and `transition` turned
    to `cut`: the batch lands at that bottom and the fade-in starts at the next block. Before, the
    batch asked for a second cut at fade-in position 0: one more silent block and an empty bottom."""
    m = _motor(_chain(fade_ms=100.0))  # 4800 samples: the fade ends after the second block
    ran, bottoms = [], []
    jump = m._saltar  # noqa: SLF001
    m._saltar = lambda: (bottoms.append(1), jump())  # noqa: SLF001
    events = {
        0: _ask("cambiar"),
        1: lambda m: (
            m.cambiar(lambda: ran.append(1)),
            m.aplicar_cadena(m.cadena.with_algorithm("transition", "cut")),
            m.cortar(),
        ),
    }
    out = _play(m, _noise(8), events)
    assert ran == [1]
    assert bottoms == [1]
    assert not m.en_corte
    for name, x in out.items():
        # The block after the bottom is the fade-in, not one more block of silence.
        assert _rms_db(x[2 * BLOCK : 3 * BLOCK]) > -40, name


# -- stage 2: stateful stages crossfade whole objects (diffuse, bass, limiter) -----------------

FADE = round(0.080 * SR)


def _with(values: ChainValues, stage: str, algorithm: str | None = None, **params) -> ChainValues:
    if algorithm is not None:
        values = values.with_algorithm(stage, algorithm)
    if params:
        values = values.with_change(validate_set(stage, params=params))
    return values


def _bass_pair() -> Instalacion:
    """A is small (no profile); B is a Charge 6, able to take the bass."""
    inst = _pair()
    inst.parlantes[1].tipo = "charge6"
    return inst


def _blocks_of(samples: int) -> int:
    return -(-samples // BLOCK) * BLOCK


class _Case:
    """One stateful change: the chain before, how to ask it, the input's scale, and the memory of
    the new stage (samples) after which a crossfade and a cut must give the same output."""

    def __init__(self, before, change, *, inst=_pair, scale=1.0, memory=0, warm=None, equalize=False, count=30):
        self.before, self.change, self.inst, self.scale = before, change, inst, scale
        self.memory = memory
        self.warm = memory if warm is None else warm
        self.equalize, self.count = equalize, count

    def motor(self, transition: str = "crossfade") -> motor_module.Motor:
        return _motor(self.before.with_algorithm("transition", transition), self.inst(), equalize=self.equalize)

    def blocks(self, count: int | None = None):
        return [(left * self.scale, right * self.scale) for left, right in _noise(count or self.count)]

    def settled(self) -> int:
        """Where both the crossfade (warm, fade) and a cut (its bottom, its fade-in) are done and
        the new stage has run for its whole memory in both."""
        return CHANGE_AT * BLOCK + BLOCK + _blocks_of(min(self.warm, SR)) + 2 * FADE + self.memory + SR // 10


def _apply(stage: str, **params):
    def ask(m: motor_module.Motor) -> None:
        m.aplicar_cadena(_with(m.cadena, stage, **params))

    return ask


def _kind_change(m: motor_module.Motor) -> None:
    m.instalacion.parlantes[1].tipo = "charge6"
    m.actualizar_tipos()


def _diffuse_case() -> _Case:
    # rt60 0.6 -> 0.3 s; the new tail's memory is its predelay + rt60: 15 + 300 ms.
    return _Case(_with(ChainValues(), "diffuse", "noise_tail"), _apply("diffuse", rt60_s=0.3), memory=15120)


def _bass_case() -> _Case:
    # The crossover's three call sites (feed, before_delay, process) all change: cutoff 90 -> 120 Hz.
    return _Case(
        _with(ChainValues(), "bass", "crossover"), _apply("bass", cutoff_hz=120.0), inst=_bass_pair, memory=2048 + 240
    )


def _bass_kind_case() -> _Case:
    # `protect`: B turns from a small speaker (high-passed, harmonics) into a Charge 6 (left alone).
    return _Case(_with(ChainValues(), "bass", "protect"), _kind_change, memory=10239 + 4607)


def _limiter_case() -> _Case:
    # A cut-class knob of the same latency (3.0 -> 3.01 ms: 144 samples both); loud noise so it limits.
    return _Case(
        _with(ChainValues(), "limiter", "true_peak"), _apply("limiter", lookahead_ms=3.01), scale=10.0, memory=SR // 4
    )


def _assert_no_hole(case: _Case) -> None:
    """The spec's criterion (§7) window by window, so the noise's own quiet patches (1.1-1.4 dB
    under its mean here, with no change at all) are in both sides: each 10 ms window of the
    crossfade is at most 1 dB under the quieter of the same window played steady before the change
    (a motor that never changes) and steady after it (one that changed by a cut long before)."""
    blocks = case.blocks()
    m = case.motor()
    out = _play(m, blocks, {CHANGE_AT: case.change})
    assert not m.en_corte
    steady_before = _play(case.motor(), blocks)
    steady_after = _play(case.motor("cut"), blocks, {1: case.change})
    for name, x in out.items():
        start = CHANGE_AT * BLOCK
        windows = _windows_db(x[start:])
        floor = np.minimum(_windows_db(steady_before[name][start:]), _windows_db(steady_after[name][start:]))
        worst = int(np.argmin(windows - floor))
        assert windows[worst] >= floor[worst] - 1.0, (name, worst, windows[worst], floor[worst])


def _assert_lands_like_a_cut(case: _Case) -> None:
    blocks = case.blocks()
    faded = case.motor()
    crossfade = _play(faded, blocks, {CHANGE_AT: case.change})
    cut = _play(case.motor("cut"), blocks, {CHANGE_AT: case.change})
    start = case.settled()
    assert start < len(blocks) * BLOCK - SR // 4
    for name in crossfade:
        assert np.max(np.abs(crossfade[name][start:] - cut[name][start:])) <= 1e-9, name
    assert not faded.en_corte
    assert not faded._transicion.busy  # noqa: SLF001
    _assert_resolved(faded)


def _assert_cuts(case: _Case) -> None:
    out = _play(case.motor("cut"), case.blocks(), {CHANGE_AT: case.change})
    for name, x in out.items():
        assert _windows_db(x[3 * BLOCK :]).min() < -60, name


def _assert_resolved(m: motor_module.Motor) -> None:
    stages = [
        m._difusion,  # noqa: SLF001
        m._graves,  # noqa: SLF001
        m._extractor,  # noqa: SLF001
        *m._limitadores.values(),  # noqa: SLF001
        *m._ecualizador.values(),  # noqa: SLF001
        *m._decorreladores.values(),  # noqa: SLF001
    ]
    assert not any(isinstance(s, Crossfaded) for s in stages)
    assert m._decorrelacion_destino is None  # noqa: SLF001


def test_diffuse_change_has_no_hole():
    _assert_no_hole(_diffuse_case())


def test_diffuse_change_lands_like_a_cut():
    _assert_lands_like_a_cut(_diffuse_case())


def test_diffuse_change_in_cut_mode_still_cuts():
    _assert_cuts(_diffuse_case())


def test_bass_change_has_no_hole():
    _assert_no_hole(_bass_case())


def test_bass_change_lands_like_a_cut():
    _assert_lands_like_a_cut(_bass_case())


def test_bass_change_in_cut_mode_still_cuts():
    _assert_cuts(_bass_case())


def test_bass_kind_change_has_no_hole():
    _assert_no_hole(_bass_kind_case())


def test_bass_kind_change_lands_like_a_cut():
    _assert_lands_like_a_cut(_bass_kind_case())


def test_bass_kind_change_in_cut_mode_still_cuts():
    _assert_cuts(_bass_kind_case())


def test_limiter_change_has_no_hole():
    _assert_no_hole(_limiter_case())


def test_limiter_change_lands_like_a_cut():
    _assert_lands_like_a_cut(_limiter_case())


def test_limiter_change_in_cut_mode_still_cuts():
    _assert_cuts(_limiter_case())


def test_stage_changes_do_not_ask_for_a_cut_in_crossfade_mode():
    for before, after, inst in (
        (_with(ChainValues(), "diffuse", "noise_tail"), {"stage": "diffuse", "rt60_s": 0.3}, _pair),
        (_with(ChainValues(), "bass", "crossover"), {"stage": "bass", "cutoff_hz": 120.0}, _bass_pair),
        (_with(ChainValues(), "limiter", "true_peak"), {"stage": "limiter", "lookahead_ms": 3.01}, _pair),
    ):
        for transition, cuts in (("crossfade", False), ("cut", True)):
            start = before.with_algorithm("transition", transition)
            assert _motor(start, inst()).pide_corte(_with(start, **after)) is cuts, (after, transition)


def test_dragging_a_stage_knob_never_nests_crossfades():
    m = _diffuse_case().motor()
    started = []
    begin = m._empezar_transicion  # noqa: SLF001
    m._empezar_transicion = lambda: (started.append(1), begin())  # noqa: SLF001
    values = [round(0.2 + 0.05 * k, 2) for k in range(20)]
    seen = []

    def drag(chunk):
        def ask(m):
            seen.append(m._difusion)  # noqa: SLF001
            for v in chunk:
                m.aplicar_cadena(_with(m.cadena, "diffuse", rt60_s=v))

        return ask

    events = {CHANGE_AT: drag(values[:1]), CHANGE_AT + 1: drag(values[1:10]), CHANGE_AT + 3: drag(values[10:])}
    blocks = _noise(40)
    out = _play(m, blocks, events)
    for stage in seen:
        if isinstance(stage, Crossfaded):
            assert not isinstance(stage.old, Crossfaded)
            assert not isinstance(stage.new, Crossfaded)
    assert len(started) == 2  # the first change, then one pending batch with the other 19
    assert not m.en_corte
    _assert_resolved(m)
    assert m.cadena.param("diffuse", "rt60_s") == values[-1]
    assert m._difusion.memory_samples() == round((0.015 + values[-1]) * SR)  # noqa: SLF001
    for name, x in out.items():
        assert _windows_db(x[3 * BLOCK :]).min() > -60, name


def test_a_cut_during_a_stage_warm_resolves_to_new():
    m = _diffuse_case().motor()
    blocks = _noise(16)
    _play(m, blocks[:CHANGE_AT])
    _diffuse_case().change(m)
    _play(m, blocks[CHANGE_AT : CHANGE_AT + 1])
    assert m._transicion.state == Transition.WARM  # noqa: SLF001
    assert isinstance(m._difusion, Crossfaded)  # noqa: SLF001
    new = m._difusion.new  # noqa: SLF001
    m.cortar()
    _play(m, blocks[CHANGE_AT + 1 :])
    assert not m.en_corte
    assert m._difusion is new  # noqa: SLF001
    _assert_resolved(m)


def test_engine_switch_during_a_stage_crossfade_changes_nothing():
    pytest.importorskip("aurasync_engine")
    blocks = [(left * 10.0, right * 10.0) for left, right in _noise(24)]
    before = _with(_with(_with(ChainValues(), "diffuse", "noise_tail"), "bass", "protect"), "limiter", "true_peak")

    def change(m):
        m.aplicar_cadena(
            _with(_with(_with(m.cadena, "diffuse", rt60_s=0.3), "bass", cutoff_hz=120.0), "limiter", lookahead_ms=3.01)
        )

    def run(switches: dict[int, str]) -> dict[str, np.ndarray]:
        backend.reset()
        backend.use("numpy")
        events = {CHANGE_AT: change}
        events.update({k: (lambda _m, e=e: backend.use(e)) for k, e in switches.items()})
        return _play(_motor(before), blocks, events)

    try:
        base = run({})
        hot = run({CHANGE_AT + 1: "rust", CHANGE_AT + 4: "numpy"})  # in the warm, then in the fade
    finally:
        backend.reset()
    for name in base:
        assert np.max(np.abs(hot[name] - base[name])) <= 1e-9, name


def test_a_limiter_change_of_latency_still_cuts():
    for before, after in (
        (ChainValues(), _with(ChainValues(), "limiter", "true_peak")),
        (_with(ChainValues(), "limiter", "true_peak"), _with(ChainValues(), "limiter", "true_peak", lookahead_ms=4.0)),
    ):
        m = _motor(before.with_algorithm("transition", "crossfade"))
        target = after.with_algorithm("transition", "crossfade")
        assert m.pide_corte(target)
        out = _play(m, _noise(16), {CHANGE_AT: lambda m, a=target: m.aplicar_cadena(a)})
        for name, x in out.items():
            assert _windows_db(x[3 * BLOCK :]).min() < -60, name


def test_ramps_start_with_the_fade_not_the_warm():
    """A preset that changes a stateful stage and the speakers: the pan, the gain and the delay hold
    while the new stage warms, then all move together over the fade."""

    m = _motor(_with(_diffuse_case().before, "transition", "crossfade", fade_ms=500.0))  # 24000 samples
    blocks = _noise(30)
    _play(m, blocks[:CHANGE_AT])
    m.aplicar_cadena(_with(m.cadena, "diffuse", rt60_s=0.3))
    m.cambiar(lambda: _change(m.instalacion))
    _play(m, blocks[CHANGE_AT : CHANGE_AT + 2])
    assert m._transicion.state == Transition.WARM  # noqa: SLF001
    assert m._pan["A"].current == -0.7  # noqa: SLF001
    assert m._ganancia["A"] == 10 ** (-3 / 20)  # noqa: SLF001
    assert not any(linea.fundiendo for linea in m._lineas.values())  # noqa: SLF001
    k = CHANGE_AT + 2
    while m._transicion.state == Transition.WARM:  # noqa: SLF001
        _play(m, blocks[k : k + 1])
        k += 1
    assert m._pan["A"].current == -0.7  # noqa: SLF001  (the fade starts at the next block)
    _play(m, blocks[k : k + 1])
    assert -0.7 < m._pan["A"].current < 0.4  # noqa: SLF001
    assert all(linea.fundiendo for linea in m._lineas.values())  # noqa: SLF001
    _play(m, blocks[k + 1 :])
    assert not m.en_corte
    assert m._pan["A"].current == 0.4  # noqa: SLF001
    assert m.retardos_actuales_ms() == {"A": 25.0, "B": 25.0}


def _diffuse_off_case() -> _Case:
    # The old stage has the tail and the new one has none: the tail fades out instead of stopping.
    return _Case(
        _with(ChainValues(), "diffuse", "noise_tail"),
        lambda m: m.aplicar_cadena(m.cadena.with_algorithm("diffuse", "off")),
    )


def test_turning_the_diffuse_tail_off_fades_it_out():
    _assert_no_hole(_diffuse_off_case())
    _assert_lands_like_a_cut(_diffuse_off_case())


def test_metrics_during_a_stage_crossfade_read_the_new_stage():
    m = _motor(_chain())  # diffuse off, bass off, the peak limiter
    blocks = [(left * 10.0, right * 10.0) for left, right in _noise(8)]
    _play(m, blocks[:2])
    after = _with(_with(m.cadena, "diffuse", "noise_tail"), "bass", "protect")
    assert m.aplicar_cadena(after) == "crossfade"
    _play(m, blocks[2:3])
    assert isinstance(m._difusion, Crossfaded)  # noqa: SLF001
    metrics = m.metricas_cadena()
    json.dumps(metrics)
    assert metrics["diffuse"]["active"]
    assert metrics["bass"]["active"]
    assert metrics["transition"]["busy"]
    assert set(m.uso_limitador_pct()) == {"A", "B"}


def test_the_peak_limiter_accounting_works_through_a_crossfade():
    """Crossfaded, the peak limiter's output is always a new array: `limitado is not x` would count
    every block as limited. On quiet noise nothing is limited, during the crossfade too."""

    m = _motor(_chain(fade_ms=500.0))
    _play(m, _noise(2))
    # `lookahead_ms` is kept while the limiter is `peak`: a cut-class change with no latency (0 -> 0).
    assert m.aplicar_cadena(_with(m.cadena, "limiter", lookahead_ms=4.0)) == "crossfade"
    _play(m, _noise(2, seed=6))
    assert all(isinstance(lim, Crossfaded) for lim in m._limitadores.values())  # noqa: SLF001
    assert m.uso_limitador_pct() == {"A": 0.0, "B": 0.0}
    loud = [(left * 40.0, right * 40.0) for left, right in _noise(1, seed=7)]
    _play(m, loud)
    assert all(v > 0 for v in m.uso_limitador_pct().values())


# -- fix round 1 (review of Task 2) ----------------------------------------------------------------


def test_a_limiter_crossfade_is_equal_gain_whatever_the_shape():
    """Two limiters fed the same input give nearly the same output: mixed at equal power (cos + sin,
    up to 1.414 at mid-fade) they broke the ceiling by +3 dB (measured in the review). The limiters
    always cross at equal gain, as the delay lines always cross at equal power."""
    before = _with(_with(ChainValues(), "limiter", "true_peak"), "transition", "crossfade", shape="equal_power")
    m = _motor(_with(before, "transition", fade_ms=500.0))
    ceiling = 10 ** (m.cadena.param("limiter", "ceiling_db") / 20)
    blocks = [(left * 10.0, right * 10.0) for left, right in _noise(24)]
    out = _play(m, blocks, {CHANGE_AT: _apply("limiter", lookahead_ms=3.01)})
    assert not m.en_corte
    for name, x in out.items():
        assert float(np.max(np.abs(x))) <= ceiling, name


def _drag_then_live(stage: str, cut_knob: str, values: tuple, live: dict):
    """A cut-class change at block 10, another while the first crossfades (it waits in the pending
    batch, built with the live values of then), then a live knob at block 12."""
    return {
        CHANGE_AT: _apply(stage, **{cut_knob: values[0]}),
        CHANGE_AT + 1: _apply(stage, **{cut_knob: values[1]}),
        CHANGE_AT + 2: _apply(stage, **live),
    }


def test_a_live_knob_reaches_a_stage_waiting_in_the_pending_batch():
    # Diffuse: the measured case of the review (crossfade mode ended at -12 dB, the cut at -24).
    m = _diffuse_case().motor()
    _play(m, _noise(40), _drag_then_live("diffuse", "rt60_s", (0.3, 0.4), {"level_db": -24.0}))
    assert not m.en_corte
    assert {tail.level_db for tail in m._difusion._tails.values()} == {-24.0}  # noqa: SLF001
    # Bass `protect`: the harmonics.
    m = _motor(_with(ChainValues(), "bass", "protect"))
    _play(m, _noise(40), _drag_then_live("bass", "cutoff_hz", (100.0, 110.0), {"harmonics_db": -6.0}))
    assert not m.en_corte
    assert {vb.harmonics_db for vb in m._graves._harmonics.values()} == {-6.0}  # noqa: SLF001
    # The limiter: the ceiling and the release.
    m = _limiter_case().motor()
    loud = [(left * 10.0, right * 10.0) for left, right in _noise(40)]
    _play(m, loud, _drag_then_live("limiter", "lookahead_ms", (3.01, 3.02), {"ceiling_db": -3.0, "release_ms": 400.0}))
    assert not m.en_corte
    for lim in m._limitadores.values():  # noqa: SLF001
        assert lim.ceiling == 10 ** (-3.0 / 20)
        assert lim._rate == 1.0 / (400.0 / 1000 * SR)  # noqa: SLF001


def test_dragging_warms_for_the_stage_that_ends_up_installed():
    """A drag 2 s -> 0.3 s: the pending batch builds every intermediate tail, but only the last one is
    installed, so the warm is its 0.315 s (4 blocks), not the 1 s cap of the 2 s tail (12 blocks)."""
    m = _diffuse_case().motor()
    drag = [2.0, 1.5, 1.0, 0.3]
    events = {CHANGE_AT: _apply("diffuse", rt60_s=drag[0])}
    events[CHANGE_AT + 1] = lambda m: [m.aplicar_cadena(_with(m.cadena, "diffuse", rt60_s=v)) for v in drag[1:]]
    states = []
    for k, (left, right) in enumerate(_noise(45)):
        if k in events:
            events[k](m)
        m.procesar(left, right)
        states.append(m._transicion.state)  # noqa: SLF001
    warms = [len(list(run)) for state, run in itertools.groupby(states) if state == Transition.WARM]
    # The first transition warms the 2 s tail for the 1 s cap; the pending batch, only the last tail.
    # (The state is read after each block: the last block of a warm already reads FADE.)
    assert warms == [_blocks_of(SR) // BLOCK - 1, _blocks_of(15120) // BLOCK - 1]
    assert m._difusion.memory_samples() == 15120  # noqa: SLF001


# -- stage 2, Tasks 3-5: the EQ, the decorrelator and the ambience extractor ---------------------

CURVE_B = [round(2.0 + 2.0 * np.cos(0.5 * k), 3) for k in range(27)]


def _eq_pair() -> Instalacion:
    inst = _pair()
    inst.parlantes[0].ecualizacion_db = list(CURVE)
    inst.parlantes[1].ecualizacion_db = list(CURVE_B)
    return inst


def _eq_case(change, before: ChainValues | None = None) -> _Case:
    # The new FIRs' memory: TAPS - 1 (2047 samples).
    return _Case(before or ChainValues(), change, inst=_eq_pair, equalize=True, memory=eq.TAPS - 1)


def _algorithm(stage: str, algorithm: str):
    return lambda m: m.aplicar_cadena(m.cadena.with_algorithm(stage, algorithm))


def _new_curves(m: motor_module.Motor) -> None:
    """What applying a calibration does: new curves in the installation, then the motor rereads them."""
    a, b = m.instalacion.parlantes
    a.ecualizacion_db, b.ecualizacion_db = list(CURVE_B), list(CURVE)
    m.actualizar_ecualizacion()


def _eq_cases() -> dict[str, _Case]:
    return {
        "off": _eq_case(_algorithm("eq", "off")),
        "on": _eq_case(_algorithm("eq", "boost_only"), ChainValues().with_algorithm("eq", "off")),
        "max_boost": _eq_case(_apply("eq", max_boost_db=3.0)),
        "calibration": _eq_case(_new_curves),
    }


def test_eq_on_off_has_no_hole():
    cases = _eq_cases()
    _assert_no_hole(cases["off"])
    _assert_no_hole(cases["on"])


def test_eq_curve_change_lands_like_a_cut():
    for name in ("max_boost", "off", "on"):
        _assert_lands_like_a_cut(_eq_cases()[name])


def test_eq_change_in_cut_mode_still_cuts():
    for case in _eq_cases().values():
        _assert_cuts(case)


def test_calibration_eq_update_crossfades():
    case = _eq_cases()["calibration"]
    m = case.motor()
    blocks = case.blocks()
    _play(m, blocks[:CHANGE_AT])
    old = dict(m._ecualizador)  # noqa: SLF001
    _new_curves(m)
    assert not m.cortando
    _play(m, blocks[CHANGE_AT : CHANGE_AT + 1])
    assert m._transicion.busy  # noqa: SLF001  (the warm, 2047 samples, fits in that block)
    for name, fir in m._ecualizador.items():  # noqa: SLF001
        assert isinstance(fir, Crossfaded)
        assert fir.old is old[name]  # the old FIR keeps its taps until resolved
        assert np.array_equal(fir.new.taps, m._taps_de(m.instalacion.por_nombre(name)))  # noqa: SLF001
        assert not np.array_equal(fir.old.taps, fir.new.taps)
    _assert_no_hole(case)
    _assert_lands_like_a_cut(case)


def test_eq_in_direct_stays_flat():
    """`direct` plays a flat EQ: turning the EQ off (or moving its knobs) crossfades flat into flat, so
    nothing moves, and the flag lands on the new value."""
    before = ChainValues().with_algorithm("spatial", "direct")
    for change in (_algorithm("eq", "off"), _apply("eq", max_boost_db=3.0), _new_curves):
        case = _eq_case(change, before)
        m = case.motor()
        blocks = case.blocks(20)
        flat = eq.fir(None)
        seen = []

        def look(m, seen=seen, flat=flat):
            for fir in m._ecualizador.values():  # noqa: SLF001
                seen.append(fir)
                for instance in (fir.old, fir.new) if isinstance(fir, Crossfaded) else (fir,):
                    assert np.array_equal(instance.taps, flat)

        out = _play(m, blocks, {CHANGE_AT: change, CHANGE_AT + 1: look, CHANGE_AT + 2: look})
        assert any(isinstance(fir, Crossfaded) for fir in seen)
        steady = _play(case.motor(), blocks)
        for name in out:
            assert np.max(np.abs(out[name] - steady[name])) <= 1e-9, name
        assert not m.en_corte
        _assert_resolved(m)


def test_the_eq_flag_takes_its_new_value_with_the_new_taps():
    m = _eq_cases()["off"].motor()
    blocks = _noise(4)
    _play(m, blocks[:1])
    assert m.aplicar_cadena(m.cadena.with_algorithm("eq", "off")) == "crossfade"
    assert m.ecualizacion_activa  # the new taps are built when the transition starts
    _play(m, blocks[1:2])
    assert not m.ecualizacion_activa
    assert not m.metricas_cadena()["eq"]["active"]
    fir = m._ecualizador["A"]  # noqa: SLF001
    assert np.array_equal(fir.new.taps, eq.fir(None))
    assert not np.array_equal(fir.old.taps, eq.fir(None))


def _decorrelator_bank_case() -> _Case:
    # spread 1.5 -> 1.0 ms: another bank (the seed is fixed by `semilla`); the filters are 256 long.
    return _Case(ChainValues(), _apply("decorrelate", spread_ms=1.0), memory=255)


def _decorrelator_off_case() -> _Case:
    return _Case(ChainValues(), _algorithm("decorrelate", "off"))


def _decorrelator_on_case() -> _Case:
    return _Case(ChainValues().with_algorithm("decorrelate", "off"), _algorithm("decorrelate", "group_delay"))


def test_decorrelator_bank_change_has_no_hole():
    """With the chain's default `shape=equal_gain`: the decorrelator crosses at equal power anyway. Two
    banks (or the dry and the decorrelated signal) are nearly uncorrelated on noise: at equal gain the
    mix dipped 2.4-3.0 dB at mid-fade, at equal power 0.5 dB at most (measured 2026-10-09)."""
    _assert_no_hole(_decorrelator_bank_case())


def test_decorrelator_on_off_has_no_hole():
    _assert_no_hole(_decorrelator_off_case())
    _assert_no_hole(_decorrelator_on_case())


def test_decorrelator_change_lands_like_a_cut():
    for case in (_decorrelator_bank_case(), _decorrelator_off_case(), _decorrelator_on_case()):
        _assert_lands_like_a_cut(case)


def test_decorrelator_change_in_cut_mode_still_cuts():
    for case in (_decorrelator_bank_case(), _decorrelator_off_case(), _decorrelator_on_case()):
        _assert_cuts(case)


def test_the_decorrelator_flag_flips_when_the_fade_ends():
    m = _motor(_chain(fade_ms=500.0))
    blocks = _noise(8)
    _play(m, blocks[:1])
    assert m.aplicar_cadena(m.cadena.with_algorithm("decorrelate", "off")) == "crossfade"
    _play(m, blocks[1:2])
    assert m._transicion.busy  # noqa: SLF001
    assert m.decorrelacion_activa  # still: the motor mixes both signals while it fades
    assert m._atraso_graves() > 0  # noqa: SLF001  (the bass feed follows the flag at the end)
    _play(m, blocks[2:])
    assert not m._transicion.busy  # noqa: SLF001
    assert not m.decorrelacion_activa
    assert m._atraso_graves() == 0  # noqa: SLF001


def test_metrics_show_the_new_bank_during_the_fade():
    m = _motor(_chain(fade_ms=500.0))
    blocks = _noise(12)
    _play(m, blocks[:1])
    before = m.metricas_cadena()["decorrelate"]
    m.aplicar_cadena(_with(m.cadena, "decorrelate", length=512, spread_ms=1.0))
    _play(m, blocks[1:2])
    assert m._transicion.busy  # noqa: SLF001
    assert all(isinstance(f, Crossfaded) for f in m._decorreladores.values())  # noqa: SLF001
    during = m.metricas_cadena()["decorrelate"]
    json.dumps(during)
    assert before["length"] == 256
    assert during["length"] == 512
    assert during["worst_above_500"] == m._banco_actual.worst_above_500  # noqa: SLF001
    assert during["worst_above_500"] != before["worst_above_500"]
    # Turning it off: the metrics say off from the start of the fade, the flag flips at its end.
    _play(m, blocks[2:])
    assert not m._transicion.busy  # noqa: SLF001
    m.aplicar_cadena(m.cadena.with_algorithm("decorrelate", "off"))
    _play(m, _noise(1, seed=6))
    assert m.decorrelacion_activa
    assert m.metricas_cadena()["decorrelate"]["active"] is False


def _ambience_pair() -> Instalacion:
    inst = _pair()
    for p in inst.parlantes:
        p.ambiente = 0.6
    return inst


def _extractor_case() -> _Case:
    # threshold 0.5 -> 0.3. The new extractor starts its smoothing (lam 0.9 per 512-sample hop) from
    # zero, while a cut keeps the old one's: they agree once that is forgotten (`memory`).
    return _Case(
        ChainValues(),
        _apply("ambience", threshold=0.3),
        inst=_ambience_pair,
        memory=2 * SR,
        warm=ambience.N_FFT + 10 * ambience.SALTO,
        count=60,
    )


def test_extractor_knob_change_has_no_hole():
    _assert_no_hole(_extractor_case())


def test_extractor_change_lands_like_a_cut():
    _assert_lands_like_a_cut(_extractor_case())


def test_extractor_change_in_cut_mode_still_cuts():
    _assert_cuts(_extractor_case())


def test_latency_is_unchanged_through_the_crossfade():
    case = _extractor_case()
    m = case.motor()
    latency = m.latencia
    assert latency == ambience.N_FFT
    blocks = case.blocks()
    states = set()

    def look(m):
        states.add(m._transicion.state)  # noqa: SLF001
        extractor = m._extractor  # noqa: SLF001
        assert m.tiene_extractor
        assert m.latencia == latency
        assert extractor.latencia == latency
        if isinstance(extractor, Crossfaded):
            assert extractor.old.latencia == extractor.new.latencia == latency
        assert len(m._cola_directo_izq) == len(m._cola_directo_der) == latency  # noqa: SLF001

    events = {CHANGE_AT: case.change}
    events.update(dict.fromkeys(range(CHANGE_AT + 1, CHANGE_AT + 6), look))
    _play(m, blocks, events)
    assert {Transition.WARM, Transition.FADE} <= states
    assert m.latencia == latency
    _assert_resolved(m)


def test_reiniciar_during_an_extractor_crossfade_lands_on_the_new_one():
    case = _extractor_case()
    m = case.motor()
    blocks = case.blocks(14)
    _play(m, blocks[:CHANGE_AT])
    case.change(m)
    _play(m, blocks[CHANGE_AT : CHANGE_AT + 1])
    assert isinstance(m._extractor, Crossfaded)  # noqa: SLF001
    new = m._extractor.new  # noqa: SLF001
    m.reiniciar()
    assert m._extractor is new  # noqa: SLF001
    assert m._extractor.p.umbral == 0.3  # noqa: SLF001
    _play(m, blocks[CHANGE_AT + 1 :])
    assert not m.en_corte


def test_eq_decorrelator_and_extractor_changes_do_not_ask_for_a_cut_in_crossfade_mode():
    for case, after in (
        (_eq_cases()["off"], ChainValues().with_algorithm("eq", "off")),
        (_eq_cases()["max_boost"], _with(ChainValues(), "eq", max_boost_db=3.0)),
        (_decorrelator_bank_case(), _with(ChainValues(), "decorrelate", spread_ms=1.0)),
        (_decorrelator_off_case(), ChainValues().with_algorithm("decorrelate", "off")),
        (_extractor_case(), _with(ChainValues(), "ambience", threshold=0.3)),
    ):
        for transition, cuts in (("crossfade", False), ("cut", True)):
            m = case.motor(transition)
            assert m.pide_corte(after.with_algorithm("transition", transition)) is cuts, (after, transition)


def test_a_cut_during_an_eq_crossfade_resolves_to_new():
    case = _eq_cases()["calibration"]
    m = case.motor()
    blocks = case.blocks(16)
    _play(m, blocks[:CHANGE_AT])
    case.change(m)
    _play(m, blocks[CHANGE_AT : CHANGE_AT + 1])
    new = {name: fir.new for name, fir in m._ecualizador.items()}  # noqa: SLF001
    m.cortar()
    _play(m, blocks[CHANGE_AT + 1 :])
    assert not m.en_corte
    assert m._ecualizador == new  # noqa: SLF001
    _assert_resolved(m)


def test_engine_switch_during_an_eq_decorrelator_and_extractor_crossfade_changes_nothing():
    pytest.importorskip("aurasync_engine")
    blocks = _noise(24)

    def change(m):
        a, b = m.instalacion.parlantes
        a.ecualizacion_db, b.ecualizacion_db = list(CURVE_B), list(CURVE)
        m.aplicar_cadena(_with(_with(m.cadena, "decorrelate", spread_ms=1.0), "ambience", threshold=0.3))
        m.actualizar_ecualizacion()

    def run(switches: dict[int, str]) -> dict[str, np.ndarray]:
        backend.reset()
        backend.use("numpy")
        events = {CHANGE_AT: change}
        events.update({k: (lambda _m, e=e: backend.use(e)) for k, e in switches.items()})
        inst = _eq_pair()
        for p in inst.parlantes:
            p.ambiente = 0.6
        return _play(_motor(_chain(), inst, equalize=True), blocks, events)

    try:
        base = run({})
        hot = run({CHANGE_AT + 1: "rust", CHANGE_AT + 3: "numpy"})  # in the warm, then in the fade
    finally:
        backend.reset()
    for name in base:
        assert np.max(np.abs(hot[name] - base[name])) <= 1e-9, name


def _feeds_by_instance(case: _Case) -> dict[int, list[np.ndarray]]:
    """The bass feed (`_fed`) each `BassStage` instance made, block by block, while it played."""
    m = case.motor()
    feeds: dict[int, list[np.ndarray]] = {}
    for k, (left, right) in enumerate(case.blocks(20)):
        if k == CHANGE_AT:
            case.change(m)
        m.procesar(left, right)
        for stage in motor_module._instancias(m._graves):  # noqa: SLF001
            feeds.setdefault(id(stage), []).append(stage._fed.copy())  # noqa: SLF001
    return feeds


def _bass_and_decorrelator_cases() -> list[_Case]:
    bass = _with(ChainValues(), "bass", "crossover")
    off = _Case(bass, _algorithm("decorrelate", "off"), inst=_bass_pair, memory=2048 + 240)
    on = _Case(
        bass.with_algorithm("decorrelate", "off"),
        _algorithm("decorrelate", "group_delay"),
        inst=_bass_pair,
        memory=2048 + 240,
    )
    return [off, on]


def test_the_bass_feed_never_changes_its_delay_while_it_plays():
    """The bass feed is held by the decorrelator's group delay while it is on, and a feed whose delay
    changes starts from an empty history (`BassStage._delayed`): flipped at the fade's end, the feed of
    the bass speaker jumped 19-44 times its largest step (measured 2026-10-09). The flip crosses a new
    bass stage, fed with the new delay from the start, so no feed ever jumps."""
    for case in _bass_and_decorrelator_cases():
        feeds = _feeds_by_instance(case)
        steady = max(float(np.max(np.abs(np.diff(f)))) for f in next(iter(feeds.values()))[:CHANGE_AT])
        for blocks in feeds.values():
            joined = np.concatenate(blocks[1:] if len(blocks) > 1 else blocks)
            assert float(np.max(np.abs(np.diff(joined)))) <= 1.5 * steady
        assert len(feeds) == 2  # the old stage, then the new one
        _assert_no_hole(case)
        _assert_lands_like_a_cut(case)


def test_the_eq_crosses_once_per_start_with_what_the_starting_actions_left():
    """The EQ is crossed once, after every starting action ran: a dragged knob (ten changes in one
    batch) builds each speaker's filter once, and a curve another action of the batch writes (a
    preset's fields travel in their own action, after the chain's) is the one that plays."""
    m = _eq_cases()["max_boost"].motor()
    built = []
    taps_of = m._taps_de  # noqa: SLF001
    m._taps_de = lambda p: (built.append(p.nombre), taps_of(p))[1]  # noqa: SLF001
    blocks = _noise(4)
    _play(m, blocks[:1])
    for value in np.linspace(5.5, 3.0, 10):
        m.aplicar_cadena(_with(m.cadena, "eq", max_boost_db=float(value)))
    a = m.instalacion.parlantes[0]
    m.cambiar(lambda: setattr(a, "ecualizacion_db", list(CURVE_B)))
    _play(m, blocks[1:2])
    assert sorted(built) == ["A", "B"]
    new = m._ecualizador["A"].new  # noqa: SLF001
    assert np.array_equal(new.taps, eq.fir(np.minimum(CURVE_B, 3.0)))


def test_a_bank_crossfade_assigns_the_filters_from_what_the_starting_actions_left():
    """`assignment=mix`: the filters go by each speaker's mix. In crossfade mode a preset writes its pans
    and ambiences in an action after the chain's, in the same batch: the assignment is made after both,
    as on the cut path, where the preset's fields are written first and then the chain is applied at
    the bottom (`service.preset_load`)."""
    before = _with(ChainValues(), "decorrelate", assignment="mix")

    def fields(m):
        a, b, c = m.instalacion.parlantes
        a.ambiente, b.ambiente, c.pan = 0.9, 0.9, -0.9

    def crossfade(m):
        m.aplicar_cadena(_with(m.cadena, "decorrelate", spread_ms=1.0))
        m.cambiar(lambda: fields(m))

    def cut(m):
        after = _with(m.cadena, "decorrelate", spread_ms=1.0)
        m.cortar(lambda: (fields(m), m.aplicar_cadena(after, en_corte=True)))

    orders = {}
    for mode, preset in (("crossfade", crossfade), ("cut", cut)):
        inst = _pair()
        inst.parlantes.append(Parlante("C", None, pan=0.0, ambiente=0.0))
        m = _motor(before.with_algorithm("transition", mode), inst)
        start = list(m._orden)  # noqa: SLF001
        _play(m, _noise(6), {1: preset})
        assert not m.en_corte
        orders[mode] = list(m._orden)  # noqa: SLF001
        assert orders[mode] != start, mode
    assert orders["crossfade"] == orders["cut"]
