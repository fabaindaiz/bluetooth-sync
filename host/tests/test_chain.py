"""The chain's descriptors, values and validation (spec 2026-10-02 §4.1, §4.3, §4.4)."""

import json

import numpy as np
import pytest

from aurasync import chain, control, motor
from aurasync.chain import ChainContext, ChainError, ChainValues
from aurasync.config import Instalacion, Parlante
from aurasync.dsp import ambience, decorrelate, eq, limiter

GO4 = ChainContext((("Red", "go4"), ("Blue", "go4")))
WITH_CHARGE = ChainContext((("Red", "go4"), ("Big", "charge6")))


def test_the_stages_of_the_specs_in_processing_order():
    assert [s.id for s in chain.CHAIN] == [
        "ambience",
        "spatial",
        "decorrelate",
        "diffuse",
        "align",
        "eq",
        "bass",
        "volume",
        "limiter",
        "transition",
    ]
    for s in chain.CHAIN:
        assert s.default_algorithm in {a.id for a in s.algorithms}
        assert s.title
        assert s.summary
        for a in s.algorithms:
            assert a.title
            assert a.summary
            for p in a.params:
                assert p.title, (s.id, a.id, p.id)
                assert p.summary, (s.id, a.id, p.id)
                if p.kind in {"float", "int"}:
                    assert p.low <= p.default <= p.high, (s.id, p.id)
                    chain.check_param(p, p.default)


def test_the_defaults_are_todays_sound():
    v = ChainValues()
    assert chain.summary(v) == {
        "ambience": "avendano_jot",
        "spatial": "classic",
        "decorrelate": "group_delay",
        "diffuse": "off",
        "align": "sinc",
        "eq": "boost_only",
        "bass": "off",
        "volume": "digital",
        "limiter": "peak",
        "transition": "crossfade",
    }
    p = ambience.Parametros()
    assert (v.param("ambience", "lam"), v.param("ambience", "threshold")) == (p.lam, p.umbral)
    assert (v.param("ambience", "sigma"), v.param("ambience", "min_energy")) == (p.sigma, p.energia_minima)
    assert v.param("decorrelate", "length") == decorrelate.LARGO_POR_DEFECTO
    assert v.param("decorrelate", "seed") == 0
    assert v.param("eq", "max_boost_db") == eq.MAX_BOOST_DB
    assert 10 ** (v.param("limiter", "ceiling_db") / 20) == limiter.CEILING
    assert v.param("limiter", "release_ms") / 1000 == limiter.RELEASE_S
    assert v.param("eq", "budget_db") == 0.0
    assert not any(chain.pending(v).values())


def test_every_declared_stage_now_runs():
    """Package E connected everything that was declared: nothing is pending any more, and the
    mechanism still reports a knob nobody runs (seen failing before the wiring, 2026-10-02)."""
    v = ChainValues()
    for stage, algorithm in (("diffuse", "noise_tail"), ("bass", "protect"), ("volume", "avrcp")):
        v = v.with_algorithm(stage, algorithm)
    v = v.with_algorithm("limiter", "true_peak")
    v = v.with_change(chain.validate_set("eq", params={"budget_db": 3, "dead_band_db": 2, "treble_cap_db": 2}))
    v = v.with_change(chain.validate_set("decorrelate", params={"mean_ms": 3, "spread_ms": 1}))
    assert not any(chain.pending(v).values())
    assert all(a.implemented for s in chain.CHAIN for a in s.algorithms)
    assert all(p.implemented for s in chain.CHAIN for a in s.algorithms for p in a.params)


def test_latency_follows_the_knobs_that_are_latency():
    v = ChainValues().with_algorithm("limiter", "true_peak")
    base = chain.latency_ms(ChainValues())
    assert chain.latency_ms(v) == pytest.approx(base + 3.0)
    v = v.with_change(chain.validate_set("limiter", params={"lookahead_ms": 5}))
    v = v.with_change(chain.validate_set("decorrelate", params={"mean_ms": 3.5}))
    assert chain.latency_ms(v) == pytest.approx(base + 5.0 + 1.0)


def test_the_alias_ranges_match_the_old_fields():
    """The old `set` and `chain_set` check the same numbers."""
    for name, (stage, param) in chain.SPEAKER_ALIASES.items():
        field, p = control.SPEAKER_FIELDS[name], chain.stage(stage).find_param(param)
        assert (field.low, field.high) == (p.low, p.high), name
        assert p.scope == "speaker"
    for name, (stage, param) in chain.GLOBAL_ALIASES.items():
        field, p = control.GLOBAL_FIELDS[name], chain.stage(stage).find_param(param)
        assert (field.low, field.high) == (p.low, p.high), name
    for name, stage in chain.ON_OFF_ALIASES.items():
        assert control.GLOBAL_FIELDS[name].kind is bool
        assert {"off", chain.on_algorithm(stage)} == {a.id for a in chain.stage(stage).algorithms}


@pytest.mark.parametrize(
    ("args", "code"),
    [
        ({"stage_id": "nope", "algorithm": "off"}, "unknown_field"),
        ({"stage_id": "limiter", "params": {"nope": 1}}, "unknown_field"),
        ({"stage_id": "limiter", "algorithm": "nope"}, "out_of_range"),
        ({"stage_id": "limiter", "params": {"ceiling_db": 1.0}}, "out_of_range"),
        ({"stage_id": "limiter", "params": {"ceiling_db": "-1"}}, "type"),
        ({"stage_id": "limiter", "params": {"ceiling_db": True}}, "type"),
        ({"stage_id": "decorrelate", "params": {"length": 300}}, "out_of_range"),
        ({"stage_id": "decorrelate", "params": {"seed": 1.5}}, "type"),
        ({"stage_id": "ambience", "params": {"pan": 0.2}}, "bad_request"),
        ({"stage_id": "ambience", "params": {"mix": 0.2}, "speaker": "Red"}, "bad_request"),
        ({"stage_id": "ambience", "params": {"mix": 0.2, "pan": 0.1}, "speaker": "Red"}, "bad_request"),
        ({"stage_id": "ambience"}, "bad_request"),
        ({"stage_id": "ambience", "params": {}}, "bad_request"),
        ({"stage_id": "bass", "algorithm": "crossover"}, "unavailable"),
        ({"stage_id": "ambience", "params": {"pan": 0.1}, "speaker": "Nobody"}, "not_found"),
        ({"stage_id": "bass", "params": {"to": "Red"}}, "out_of_range"),
    ],
)
def test_a_bad_change_is_refused_with_its_code(args, code):
    with pytest.raises(ChainError) as caught:
        chain.validate_set(context=GO4, **args)
    assert caught.value.code == code


def test_crossover_needs_a_bass_capable_speaker_and_names_it():
    assert chain.ChainContext.of(None).bass_speakers == []
    assert WITH_CHARGE.bass_speakers == ["Big"]
    change = chain.validate_set("bass", "crossover", {"to": "Big"}, context=WITH_CHARGE)
    assert change.params == {"to": "Big"}
    described = chain.describe(ChainValues(), GO4)
    bass = next(s for s in described["stages"] if s["id"] == "bass")
    crossover = next(a for a in bass["algorithms"] if a["id"] == "crossover")
    assert not crossover["available"]
    assert "graves" in crossover["unavailable_reason"]


def test_the_kind_is_guessed_from_the_name_when_unset():
    inst = Instalacion(parlantes=[Parlante("JBL Charge 6", "s0"), Parlante("Go", "s1", tipo="go4")])
    assert ChainContext.of(inst).bass_speakers == ["JBL Charge 6"]


def test_values_are_sparse_and_reset_removes_the_choice():
    v = ChainValues()
    v = v.with_change(chain.validate_set("limiter", params={"release_ms": 400}))
    v = v.with_algorithm("eq", "off")
    assert v.choices == {"limiter": {"params": {"release_ms": 400.0}}, "eq": {"algorithm": "off"}}
    assert v.with_reset("limiter", "release_ms").choices == {"eq": {"algorithm": "off"}}
    assert v.with_reset("eq").choices == {"limiter": {"params": {"release_ms": 400.0}}}
    # The installation's knobs are never kept by the chain.
    pan = chain.validate_set("ambience", params={"pan": 0.4}, speaker="Red", context=GO4)
    assert ChainValues().with_change(pan).choices == {}


def test_choosing_the_default_is_still_a_choice():
    """Card persist-inputs-derive-verdicts: what was chosen is kept as chosen."""
    v = ChainValues().with_algorithm("limiter", "peak")
    assert v.choices == {"limiter": {"algorithm": "peak"}}


def test_a_param_default_follows_the_algorithm_in_use():
    v = ChainValues().with_algorithm("bass", "crossover")
    assert v.param("bass", "cutoff_hz") == 100.0
    assert ChainValues().with_algorithm("bass", "protect").param("bass", "cutoff_hz") == 90.0


def test_stored_choices_are_cleaned_never_raised():
    lines = []
    data = {
        "limiter": {"params": {"ceiling_db": -2, "release_ms": 99999, "nope": 1}, "algorithm": "true_peak"},
        "ambience": {"params": {"pan": 0.3}, "algorithm": "bogus", "extra": 1},
        "nope": {},
        "eq": "not an object",
    }
    v = ChainValues.from_json(data, lines.append)
    assert v.choices == {"limiter": {"algorithm": "true_peak", "params": {"ceiling_db": -2.0}}}
    assert len(lines) == 7
    assert ChainValues.from_json([1, 2], lines.append).choices == {}


def test_presets_keep_everything_but_the_volume():
    v = ChainValues().with_algorithm("volume", "avrcp").with_algorithm("bass", "protect")
    part = v.preset_part()
    assert part == {"bass": {"algorithm": "protect"}}
    other = ChainValues().with_algorithm("eq", "off").with_algorithm("volume", "avrcp")
    loaded = other.with_preset(part)
    # The preset replaces eq (back to its default) and bass; the volume stays the listener's.
    assert loaded.choices == {"volume": {"algorithm": "avrcp"}, "bass": {"algorithm": "protect"}}


def test_describe_has_the_shape_of_the_contract():
    v = ChainValues().with_change(chain.validate_set("limiter", params={"release_ms": 400}))
    described = chain.describe(v, GO4, lambda stage, param, speaker: f"{stage}.{param}.{speaker}")
    json.dumps(described)
    assert described["latency_ms"] == chain.latency_ms(v)
    lim = next(s for s in described["stages"] if s["id"] == "limiter")
    assert set(lim) >= {"id", "title", "summary", "help", "algorithms", "default_algorithm", "value", "chosen"}
    assert lim["value"] == {"algorithm": "peak", "params": {"ceiling_db": -1.0, "release_ms": 400.0}, "speakers": {}}
    assert lim["chosen"] == {"params": {"release_ms": 400.0}}
    algo = lim["algorithms"][0]
    assert set(algo) >= {"id", "title", "summary", "help", "cost", "latency_ms", "available", "unavailable_reason"}
    assert set(algo["params"][0]) >= {"id", "kind", "default", "low", "high", "step", "unit", "scope", "apply"}
    amb = next(s for s in described["stages"] if s["id"] == "ambience")
    assert amb["value"]["speakers"]["Red"] == {"pan": "ambience.pan.Red", "ambience": "ambience.ambience.Red"}
    vol = next(s for s in described["stages"] if s["id"] == "volume")
    assert vol["value"]["params"]["volume_db"] == "volume.volume_db.None"


def test_latency_counts_the_stages_in_the_path():
    expected = (ambience.N_FFT + eq.LATENCY_SAMPLES + 16) / 48 + decorrelate.RETARDO_MEDIO_MS
    assert chain.latency_ms(ChainValues()) == pytest.approx(expected, abs=1e-3)
    off = ChainValues().with_algorithm("decorrelate", "off")
    assert chain.latency_ms(off) == pytest.approx(expected - decorrelate.RETARDO_MEDIO_MS, abs=1e-3)


# -- the engine reads the chain ---------------------------------------------------------------

SR = 48000
BLOCK = 1024


def _inst() -> Instalacion:
    return Instalacion(
        parlantes=[
            Parlante("L", "s0", pan=-0.7, ecualizacion_db=[6.0] * 27),
            Parlante("R", "s1", pan=0.7, ecualizacion_db=[3.0] * 27),
        ]
    )


def _loud(n: int) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(1)
    return 0.9 * rng.standard_normal(n).clip(-1, 1), 0.9 * rng.standard_normal(n).clip(-1, 1)


def _play(m: motor.Motor, blocks: int) -> dict[str, np.ndarray]:
    izq, der = _loud(blocks * BLOCK)
    return motor.procesar_completo(m, izq, der, BLOCK)


def test_the_chain_off_reaches_every_stage_of_the_engine():
    """Card kill-switch-reaches-every-path: `off` in the chain is off in the motor, from the
    first sample, for every stage that has an `off`."""
    v = ChainValues()
    for stage in ("ambience", "decorrelate", "eq"):
        v = v.with_algorithm(stage, "off")
    m = motor.Motor(_inst(), SR, ecualizar=True, chain=v)
    assert not m.extraer_ambiente_activo
    assert not m.decorrelacion_activa
    assert not m.ecualizacion_activa
    assert m._mezcla_ambiente.current == 0.0  # noqa: SLF001
    on = motor.Motor(_inst(), SR, ecualizar=True)
    assert on.extraer_ambiente_activo
    assert on.decorrelacion_activa
    assert on.ecualizacion_activa


def test_a_live_change_moves_without_a_cut_and_a_cut_change_waits_for_the_bottom():
    """With `transition=cut`; in crossfade mode the decorrelator crossfades (test_motor_transitions)."""
    cut_mode = ChainValues().with_algorithm("transition", "cut")
    m = motor.Motor(_inst(), SR, ecualizar=True, chain=cut_mode)
    _play(m, 4)
    live = cut_mode.with_change(chain.validate_set("ambience", params={"mix": 0.5}))
    assert m.aplicar_cadena(live) == "live"
    assert not m.en_corte
    assert m._mezcla_ambiente.target == 0.5  # noqa: SLF001
    cut = live.with_algorithm("decorrelate", "off")
    assert m.aplicar_cadena(cut) == "cut"
    assert m.en_corte
    assert m.decorrelacion_activa  # not yet: at the bottom of the fade
    _play(m, 16)
    assert not m.decorrelacion_activa
    assert not m.en_corte
    assert m.aplicar_cadena(cut) == "none"


def test_extractor_params_and_decorrelator_bank_change_at_the_bottom():
    """With `transition=cut`; in crossfade mode both crossfade (test_motor_transitions)."""
    cut_mode = ChainValues().with_algorithm("transition", "cut")
    m = motor.Motor(_inst(), SR, chain=cut_mode)
    before = dict(m._filtros)  # noqa: SLF001
    v = cut_mode.with_change(chain.validate_set("ambience", params={"lam": 0.8}))
    v = v.with_change(chain.validate_set("decorrelate", params={"seed": 7, "length": 512}))
    assert m.aplicar_cadena(v) == "cut"
    assert m._extractor.p.lam == 0.9  # noqa: SLF001
    _play(m, 6)
    assert m._extractor.p.lam == 0.8  # noqa: SLF001
    assert all(len(h) == 512 for h in m._filtros.values())  # noqa: SLF001
    assert not any(np.array_equal(before[n], m._filtros[n]) for n in before)  # noqa: SLF001


def test_max_boost_caps_the_stored_curve_when_it_is_read():
    m = motor.Motor(_inst(), SR, ecualizar=True)
    v = ChainValues().with_change(chain.validate_set("eq", params={"max_boost_db": 2.0}))
    assert m.aplicar_cadena(v) == "crossfade"
    _play(m, 6)
    assert m.instalacion.por_nombre("L").ecualizacion_db == [6.0] * 27  # the input is kept
    assert m.metricas_cadena()["eq"]["max_boost_db"] == {"L": 2.0, "R": 2.0}


def test_the_transition_stage_defaults():
    stage = chain.stage("transition")
    assert stage.default_algorithm == "crossfade"
    assert stage.algorithm_apply == "live"
    assert [a.id for a in stage.algorithms] == ["crossfade", "cut"]
    v = ChainValues()
    assert v.algorithm("transition") == "crossfade"
    fade = stage.find_param("fade_ms", "crossfade")
    assert (fade.default, fade.low, fade.high, fade.step) == (80, 0, 500, 10)
    assert v.param("transition", "fade_ms") == 80
    shape = stage.find_param("shape", "crossfade")
    assert shape.kind == "choice"
    assert shape.choices == ("equal_gain", "equal_power")
    assert shape.default == "equal_gain"
    assert fade.apply == shape.apply == "live"
    assert [p.id for p in stage.algorithm("cut").params] == ["start_stretch_ppm", "max_stretch_ppm"]


def test_the_stretch_knobs_are_in_both_transition_modes():
    """Stage 4 (spec 2026-10-08 §4b): the cushions' stretch, in ppm, live, the same in either mode."""
    stage = chain.stage("transition")
    for algorithm in ("crossfade", "cut"):
        start = stage.find_param("start_stretch_ppm", algorithm)
        top = stage.find_param("max_stretch_ppm", algorithm)
        assert (start.kind, start.default, start.low, start.high, start.step) == ("int", 1000, 0, 5000, 100)
        assert (top.kind, top.default, top.low, top.high, top.step) == ("int", 5000, 0, 10000, 500)
        assert start.apply == top.apply == "live"
        assert start.unit == top.unit == "ppm"
        values = ChainValues().with_algorithm("transition", algorithm)
        assert values.param("transition", "start_stretch_ppm") == 1000
        assert values.param("transition", "max_stretch_ppm") == 5000
    v = ChainValues().with_change(chain.validate_set("transition", params={"start_stretch_ppm": 0}))
    assert v.param("transition", "start_stretch_ppm") == 0
    with pytest.raises(chain.ChainError):
        chain.validate_set("transition", params={"max_stretch_ppm": 20000})


def test_limiter_knobs_are_live_and_its_activity_is_measured():
    m = motor.Motor(_inst(), SR, ecualizar=True)
    _play(m, 10)
    metrics = m.metricas_cadena()["limiter"]
    assert metrics["active_pct"]["L"] > 50
    assert metrics["reduction_db"]["L"] > 0
    v = ChainValues().with_change(chain.validate_set("limiter", params={"ceiling_db": -3.0}))
    assert m.aplicar_cadena(v) == "live"
    out = _play(m, 4)
    assert np.abs(out["L"]).max() <= 10 ** (-3 / 20) + 1e-12


def test_the_metrics_are_cheap_plain_data():
    m = motor.Motor(_inst(), SR)
    _play(m, 10)
    v = ChainValues().with_algorithm("bass", "protect")
    assert m.aplicar_cadena(v) == "crossfade"  # the default `transition`; metrics read the new stage
    _play(m, 4)
    metrics = m.metricas_cadena()
    json.dumps(metrics)
    assert [s.id for s in chain.CHAIN] == list(metrics)
    assert not metrics["bass"]["pending"]
    assert metrics["bass"]["active"]
    assert not metrics["limiter"]["pending"]
    assert 0 < metrics["ambience"]["share"] < 1


def _cut_class_cases():
    v = ChainValues
    base = v()
    return {
        "ambience.lam": base.with_change(chain.validate_set("ambience", params={"lam": 0.8})),
        "decorrelate.seed": base.with_change(chain.validate_set("decorrelate", params={"seed": 7})),
        "decorrelate.length": base.with_change(chain.validate_set("decorrelate", params={"length": 512})),
        "decorrelate.off": base.with_algorithm("decorrelate", "off"),
        "eq.off": base.with_algorithm("eq", "off"),
        "eq.max_boost_db": base.with_change(chain.validate_set("eq", params={"max_boost_db": 3.0})),
        "diffuse.on": base.with_algorithm("diffuse", "noise_tail"),
        "bass.protect": base.with_algorithm("bass", "protect"),
        "spatial.spatial": base.with_algorithm("spatial", "spatial"),
        "spatial.direct": base.with_algorithm("spatial", "direct"),
        "spatial.live_param": base.with_algorithm("spatial", "spatial").with_change(
            chain.validate_set("spatial", params={"character": 0.9})
        ),
        "limiter.lookahead": base.with_change(chain.validate_set("limiter", params={"lookahead_ms": 3.0})),
        "ambience.mix": base.with_change(chain.validate_set("ambience", params={"mix": 0.5})),
        "ambience.move_speed": base.with_change(chain.validate_set("ambience", params={"move_speed": 0.5})),
        "limiter.ceiling": base.with_change(chain.validate_set("limiter", params={"ceiling_db": -3})),
        "transition.cut": base.with_algorithm("transition", "cut"),
        "nothing": base,
    }


@pytest.mark.parametrize("name", list(_cut_class_cases()))
def test_pide_corte_agrees_with_aplicar_cadena(name):
    v = _cut_class_cases()[name]
    m = motor.Motor(_inst(), SR, ecualizar=True)
    fresh = motor.Motor(_inst(), SR, ecualizar=True)
    before = m.cadena
    assert m.pide_corte(v) == (fresh.aplicar_cadena(v) == "cut")
    assert m.cadena == before  # a query: nothing moved
