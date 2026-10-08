"""Transitions of the motor (spec seamless-transitions 2026-10-08).

Switching the engine between blocks, with no cut, changes nothing audible (§2): every stage moves
its state exactly between numpy and Rust. And a change in crossfade mode (`Motor.cambiar`, §3 and
the stage-1 rows of §4) glides every ramp and crossfades every delay over `fade_ms`, with no hole,
and lands exactly where a cut lands.
"""

from __future__ import annotations

import numpy as np
import pytest

from aurasync import motor as motor_module
from aurasync.chain import ChainValues, validate_set
from aurasync.config import Instalacion, Parlante
from aurasync.dsp import backend

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


def _motor(chain: ChainValues, inst: Instalacion | None = None) -> motor_module.Motor:
    return motor_module.Motor(inst or _pair(), SR, ecualizar=False, chain=chain, semilla=1, bloque=BLOCK)


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
