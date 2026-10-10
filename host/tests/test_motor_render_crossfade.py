"""A render switch without the cut (spec 2026-10-08-seamless-transitions §4, the render row; stage 3).

In crossfade mode the new render gets a branch of its own (`render_branch.RenderBranch`: the
upmix, the decorrelators, the diffuse tail, the bass stage, the delay lines and the EQ), warmed in
the shadow on the same input while the old one plays, then mixed per speaker before the gain over
`fade_ms`; at the end only the new branch is left. Every pair below runs with every effect on
(diffuse tail, bass protection, EQ with a curve, Haas on the rear), so a stage that `direct` steps
around is in the path one way or the other.
"""

from __future__ import annotations

import numpy as np
import pytest

from aurasync import motor as motor_module
from aurasync.chain import ChainValues
from aurasync.config import Instalacion, Parlante
from aurasync.dsp import backend
from aurasync.dsp.response import THIRDS
from aurasync.dsp.transition import Crossfaded

SR = 48000
BLOCK = 4096
WINDOW = SR // 100  # 10 ms
HOP = SR // 1000
CHANGE_AT = 10
BLOCKS = 48  # 4.1 s
SETTLED = (CHANGE_AT + 24) * BLOCK  # 2 s after the request: warm (<= 1 s), fade, and every memory
SPEAKERS = (
    # name, pan, ambience, delay (ms), gain (dB); the angles the spatial ring takes from them
    ("left", -0.8, 0.2, 3.25, -1.0),
    ("right", 0.6, 0.3, 0.0, 0.0),
    ("rear", 0.0, 0.9, 11.7, -3.0),
)
CURVE = [round(3.0 + 3.0 * np.sin(0.7 * k), 3) for k in range(len(THIRDS))]
PAIRS = [
    ("classic", "front"),
    ("front", "classic"),
    ("front", "spatial"),
    ("spatial", "front"),
    ("classic", "direct"),
    ("direct", "classic"),
    ("direct", "spatial"),
    ("spatial", "direct"),
]
IDS = [f"{a}-{b}" for a, b in PAIRS]


@pytest.fixture(autouse=True)
def _numpy():
    backend.reset()
    backend.use("numpy")
    yield
    backend.reset()


def _installation() -> Instalacion:
    return Instalacion(
        parlantes=[
            Parlante(name, None, pan=pan, ambiente=amb, retardo_ms=delay, ganancia_db=gain, ecualizacion_db=CURVE)
            for name, pan, amb, delay, gain in SPEAKERS
        ],
        retardo_traseros_ms=15.0,
    )


def _chain(render: str, transition: str = "crossfade") -> ChainValues:
    return ChainValues.from_json(
        {
            "spatial": {"algorithm": render},
            "diffuse": {"algorithm": "noise_tail"},
            "bass": {"algorithm": "protect"},
            "transition": {"algorithm": transition},
        }
    )


def _motor(render: str, transition: str = "crossfade") -> motor_module.Motor:
    return motor_module.Motor(
        _installation(), SR, ecualizar=True, chain=_chain(render, transition), semilla=1, bloque=BLOCK, volumen_db=-6.0
    )


def _music(count: int = BLOCKS, seed: int = 5) -> list[tuple[np.ndarray, np.ndarray]]:
    """Partly correlated L and R (a common part and each side's own), well under the limiter."""
    rng = np.random.default_rng(seed)
    blocks = []
    for _ in range(count):
        common = rng.standard_normal(BLOCK)
        blocks.append(
            (0.05 * (common + 0.6 * rng.standard_normal(BLOCK)), 0.05 * (common + 0.6 * rng.standard_normal(BLOCK)))
        )
    return blocks


def _play(m: motor_module.Motor, blocks, events=None) -> dict[str, np.ndarray]:
    out: dict[str, list] = {}
    for k, (left, right) in enumerate(blocks):
        if events and k in events:
            events[k](m)
        for name, block in m.procesar(left, right).items():
            out.setdefault(name, []).append(block)
    return {name: np.concatenate(parts) for name, parts in out.items()}


def _to(render: str, transition: str = "crossfade"):
    def ask(m: motor_module.Motor) -> None:
        m.aplicar_cadena(_chain(render, transition))

    return ask


def _windows_db(x: np.ndarray) -> np.ndarray:
    return np.array(
        [10 * np.log10(max(float(np.mean(x[i : i + WINDOW] ** 2)), 1e-30)) for i in range(0, len(x) - WINDOW + 1, HOP)]
    )


def worst_dip_db(before: str, after: str) -> dict[str, float]:
    """Per speaker, how far the crossfade's quietest 10 ms window falls under the quieter of the
    same window played steady in the old render and steady in the new one (positive: a dip)."""
    blocks = _music()
    m = _motor(before)
    out = _play(m, blocks, {CHANGE_AT: _to(after)})
    assert not m.en_corte
    steady_before = _play(_motor(before), blocks)
    steady_after = _play(_motor(after), blocks)
    start = CHANGE_AT * BLOCK
    dips = {}
    for name, x in out.items():
        floor = np.minimum(_windows_db(steady_before[name][start:]), _windows_db(steady_after[name][start:]))
        dips[name] = float(np.max(floor - _windows_db(x[start:])))
    return dips


def worst_bulge_db(before: str, after: str) -> dict[str, float]:
    """Per speaker, how far the crossfade's loudest 10 ms window rises over the louder of the same
    window played steady in each render (positive: a bulge)."""
    blocks = _music()
    out = _play(_motor(before), blocks, {CHANGE_AT: _to(after)})
    steady_before = _play(_motor(before), blocks)
    steady_after = _play(_motor(after), blocks)
    start = CHANGE_AT * BLOCK
    bulges = {}
    for name, x in out.items():
        ceiling = np.maximum(_windows_db(steady_before[name][start:]), _windows_db(steady_after[name][start:]))
        bulges[name] = float(np.max(_windows_db(x[start:]) - ceiling))
    return bulges


def _assert_resolved(m: motor_module.Motor) -> None:
    assert m._rama_vieja is None  # noqa: SLF001
    assert m._render_pendiente is None  # noqa: SLF001
    stages = [m._difusion, m._graves, *m._ecualizador.values(), *m._decorreladores.values()]  # noqa: SLF001
    assert not any(isinstance(s, Crossfaded) for s in stages)


@pytest.mark.parametrize(("before", "after"), PAIRS, ids=IDS)
def test_a_render_switch_has_no_hole(before, after):
    """The spec's criterion (§7): no 10 ms window more than 1 dB under the quieter steady level."""
    dips = worst_dip_db(before, after)
    assert max(dips.values()) <= 1.0, dips


@pytest.mark.parametrize(("before", "after"), PAIRS, ids=IDS)
def test_a_render_switch_has_no_bulge(before, after):
    """The mirror of the hole: two renders that sound alike (`spatial`, `front`) mixed at equal
    power rose 2 dB mid-fade; no window may rise more than 1 dB over the louder steady level."""
    bulges = worst_bulge_db(before, after)
    assert max(bulges.values()) <= 1.0, bulges


@pytest.mark.parametrize(
    ("before", "after", "shape"),
    [("spatial", "front", "equal_gain"), ("classic", "spatial", "equal_power"), ("direct", "front", "equal_power")],
)
def test_the_render_fade_shape_follows_the_pair(before, after, shape):
    assert motor_module.forma_render(before, after) == shape


@pytest.mark.parametrize(("before", "after"), PAIRS, ids=IDS)
def test_a_render_switch_lands_like_a_cut(before, after):
    blocks = _music()
    faded = _motor(before)
    crossfade = _play(faded, blocks, {CHANGE_AT: _to(after)})
    cut = _play(_motor(before, "cut"), blocks, {CHANGE_AT: _to(after, "cut")})
    assert faded.render == after
    assert not faded.en_corte
    _assert_resolved(faded)
    for name in crossfade:
        assert np.max(np.abs(crossfade[name][SETTLED:] - cut[name][SETTLED:])) <= 1e-9, name


@pytest.mark.parametrize(
    ("before", "after"), [("classic", "direct"), ("front", "spatial")], ids=["to-direct", "spatial"]
)
def test_in_cut_mode_a_render_switch_still_cuts(before, after):
    m = _motor(before, "cut")
    assert m.aplicar_cadena(_chain(after, "cut")) == "cut"
    out = _play(m, _music(CHANGE_AT))
    for name, x in out.items():
        assert _windows_db(x[BLOCK:]).min() < -60, name


def test_the_old_render_plays_alone_until_the_new_one_has_warmed():
    """WARM: the new branch runs in the shadow; the output is the old render's, sample for sample."""
    blocks = _music()
    steady = _play(_motor("classic"), blocks)
    m = _motor("classic")
    out = _play(m, blocks[: CHANGE_AT + 1], {CHANGE_AT: _to("spatial")})
    assert m._transicion.state == "warm"  # noqa: SLF001
    assert m.render == "spatial", "metrics and the knobs already read the new render"
    for name in out:
        assert np.max(np.abs(out[name] - steady[name][: (CHANGE_AT + 1) * BLOCK])) <= 1e-12, name


def test_metrics_and_latency_during_the_fade_report_the_new_render():
    m = _motor("classic")
    before = m.latencia, m.latencia_ecualizador
    _play(m, _music(CHANGE_AT + 2), {CHANGE_AT: _to("direct")})
    assert m.en_corte
    assert m.metricas_cadena()["spatial"]["render"] == "direct"
    assert m.retardos_actuales_ms()["rear"] == pytest.approx(11.7), "no Haas in direct"
    assert (m.latencia, m.latencia_ecualizador) == before


def test_a_cut_during_the_render_fade_lands_on_the_new_render():
    m = _motor("classic")
    _play(m, _music(CHANGE_AT + 2), {CHANGE_AT: _to("spatial")})
    assert m._rama_vieja is not None  # noqa: SLF001
    m.cortar()
    _play(m, _music(6, seed=9))
    assert m.render == "spatial"
    assert not m.en_corte
    _assert_resolved(m)


def test_dragging_the_render_knob_never_nests_branches():
    """classic → spatial → front → direct while the first fade runs: one pending transition, the
    last render wins, and there is never more than one branch leaving."""
    m = _motor("classic")
    olds = []

    def drag(render):
        def ask(mm):
            _to(render)(mm)
            olds.append(mm._rama_vieja)  # noqa: SLF001

        return ask

    events = {CHANGE_AT: drag("spatial"), CHANGE_AT + 1: drag("front"), CHANGE_AT + 2: drag("direct")}
    blocks = _music()
    _play(m, blocks, events)
    assert m.render == "direct"
    _assert_resolved(m)
    assert all(old is None or not isinstance(old.diffuse, Crossfaded) for old in olds)


def test_a_render_dragged_back_within_one_start_changes_nothing():
    """Asked and undone before the transition starts: the batch leaves the render where it was."""
    blocks = _music(CHANGE_AT + 30)
    steady = _play(_motor("classic"), blocks)

    def there_and_back(m):
        _to("spatial")(m)
        _to("classic")(m)

    m = _motor("classic")
    out = _play(m, blocks, {CHANGE_AT: there_and_back})
    assert m.render == "classic"
    for name in out:
        assert np.max(np.abs(out[name] - steady[name])) <= 1e-12, name


def test_leaving_direct_by_crossfade_replays_nothing_of_before_it():
    """As with the cut (review 2026-10-06): the music stops while `direct` plays; back in classic,
    once the fade is over, nothing of the music from before `direct` may sound."""
    blocks = _music(12) + [(np.zeros(BLOCK), np.zeros(BLOCK))] * 40
    m = _motor("classic")
    out = _play(m, blocks, {4: _to("direct"), 20: _to("classic")})
    assert m.render == "classic"
    tail = (20 + 30) * BLOCK
    for name, x in out.items():
        assert np.max(np.abs(x[tail:])) <= 1e-12, name


def test_an_engine_switch_during_the_render_fade_changes_nothing():
    pytest.importorskip("aurasync_engine")
    blocks = _music()

    def run(switches: dict[int, str]) -> dict[str, np.ndarray]:
        backend.reset()
        backend.use("numpy")
        m = _motor("classic")
        out: dict[str, list] = {}
        for k, (left, right) in enumerate(blocks):
            if k == CHANGE_AT:
                _to("spatial")(m)
            if k in switches:
                backend.use(switches[k])
            for name, block in m.procesar(left, right).items():
                out.setdefault(name, []).append(block)
        return {name: np.concatenate(parts) for name, parts in out.items()}

    base = run({})
    hot = run({CHANGE_AT + 2: "rust", CHANGE_AT + 12: "numpy"})
    for name in base:
        assert np.max(np.abs(hot[name] - base[name])) <= 1e-9, name


def test_the_makeup_is_asked_at_the_request_and_glides():
    asked = []

    def makeup_for(render: str) -> float:
        asked.append(render)
        return {"direct": 3.0, "classic": 0.0}[render]

    m = _motor("classic")
    m.on_render_switch = makeup_for
    m.aplicar_cadena(_chain("direct"))
    assert asked == []
    _play(m, _music(1))
    assert asked == ["direct"]
    assert m.render_makeup_db == 3.0
    blocks_db = []
    while m.en_corte:
        _play(m, _music(1))
        blocks_db.append(np.atleast_1d(m.render_makeup_block_db))
    steps = np.diff(np.concatenate(blocks_db))
    assert np.max(np.abs(steps)) < 0.01, "a glide, not a jump"
    assert float(np.concatenate(blocks_db)[-1]) == pytest.approx(3.0)


def test_render_changes_do_not_ask_for_a_cut_in_crossfade_mode():
    m = _motor("classic")
    for render in ("spatial", "front", "direct"):
        assert not m.pide_corte(_chain(render))
    assert m.pide_corte(_chain("direct", "cut"))
