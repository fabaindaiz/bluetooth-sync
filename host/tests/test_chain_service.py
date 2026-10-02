"""The chain through the service: `chain`, `chain_set`, `chain_reset`, the old fields as
aliases, and the files (spec 2026-10-02 §4.3, §4.4)."""

import json
import threading

import pytest

from aurasync import chain
from aurasync.config import Instalacion, Parlante
from aurasync.presets import PresetStore
from aurasync.service import Service
from tests.test_service import FakeSession, _err, _ok, _wait


def _installation(path, *, charge: bool = False):
    speakers = [Parlante("Go 4 Red", "s0", pan=-0.7), Parlante("Go 4 Blue", "s1", pan=0.7, ambiente=0.5)]
    if charge:
        speakers.append(Parlante("JBL Charge 6", "s2", ambiente=0.3))
    Instalacion(parlantes=speakers).guardar(path)


@pytest.fixture(autouse=True)
def _reset_fake():
    FakeSession.instances = []
    FakeSession.fail_open = None
    FakeSession.fail_after = None


def _service(tmp_path, lines=None):
    return Service(
        tmp_path / "inst.json",
        tmp_path / "presets.json",
        session_factory=FakeSession,
        log=(lines.append if lines is not None else lambda _: None),
    )


@pytest.fixture
def started(tmp_path):
    services = []

    def start(*, charge: bool = False, lines=None, install: bool = True) -> Service:
        if install:
            _installation(tmp_path / "inst.json", charge=charge)
        svc = _service(tmp_path, lines)
        thread = threading.Thread(target=svc.run, daemon=True)
        thread.start()
        services.append((svc, thread))
        return svc

    yield start
    for svc, thread in services:
        svc.handle({"v": 1, "op": "shutdown"})
        thread.join(timeout=5)
        svc.close()


def _stage(svc, stage):
    return next(s for s in _ok(svc, op="chain")["stages"] if s["id"] == stage)


def _chain_file(tmp_path):
    return json.loads((tmp_path / "chain.json").read_text())


def test_chain_lists_the_stages_and_state_summarises_them(started):
    svc = started()
    described = _ok(svc, op="chain")
    assert [s["id"] for s in described["stages"]] == [s.id for s in chain.CHAIN]
    state = _ok(svc, op="state")
    assert state["chain_summary"]["limiter"] == "peak"
    assert state["chain_latency_ms"] == described["latency_ms"]
    assert state["chain_pending"] == []
    amb = _stage(svc, "ambience")
    assert amb["value"]["speakers"]["Go 4 Blue"] == {"pan": 0.7, "ambience": 0.5}
    assert _stage(svc, "volume")["value"]["params"]["volume_db"] == -20.0


def test_chain_set_reaches_the_playing_motor_and_returns_the_new_value(started, tmp_path):
    svc = started()
    _ok(svc, op="start")
    motor = FakeSession.instances[0].motor
    reply = _ok(svc, op="chain_set", stage="limiter", params={"ceiling_db": -3})
    assert reply["apply"] == "live"
    assert reply["value"]["params"]["ceiling_db"] == -3.0
    assert all(abs(lim.ceiling - 10 ** (-3 / 20)) < 1e-12 for lim in motor._limitadores.values())  # noqa: SLF001
    reply = _ok(svc, op="chain_set", stage="decorrelate", params={"seed": 5})
    assert reply["apply"] == "cut"
    assert _chain_file(tmp_path) == {
        "v": 1,
        "chain": {"limiter": {"params": {"ceiling_db": -3.0}}, "decorrelate": {"params": {"seed": 5}}},
    }


@pytest.mark.parametrize(
    ("message", "code"),
    [
        ({"stage": "nope", "algorithm": "off"}, "unknown_field"),
        ({"stage": "limiter", "params": {"ceiling_db": 3}}, "out_of_range"),
        ({"stage": "limiter", "params": {"ceiling_db": -2, "release_ms": "fast"}}, "type"),
        ({"stage": "ambience", "params": {"pan": 0.1}}, "bad_request"),
        ({"stage": "ambience", "params": {"pan": 0.1}, "speaker": "Nobody"}, "not_found"),
        ({"stage": "bass", "algorithm": "crossover"}, "unavailable"),
        ({"stage": "limiter", "params": {"ceiling_db": -2}, "extra": 1}, "unknown_field"),
    ],
)
def test_a_bad_chain_set_changes_nothing(started, tmp_path, message, code):
    svc = started()
    before = _ok(svc, op="state")["sequence"]
    assert _err(svc, op="chain_set", **message) == code
    assert _ok(svc, op="state")["sequence"] == before
    assert not (tmp_path / "chain.json").exists()


def test_crossover_with_a_bass_capable_speaker(started):
    svc = started(charge=True)
    bass = _stage(svc, "bass")
    crossover = next(a for a in bass["algorithms"] if a["id"] == "crossover")
    assert crossover["available"]
    assert next(p for p in crossover["params"] if p["id"] == "to")["choices"] == ["auto", "JBL Charge 6"]
    assert _err(svc, op="chain_set", stage="bass", algorithm="crossover", params={"to": "Go 4 Red"}) == "out_of_range"
    reply = _ok(svc, op="chain_set", stage="bass", algorithm="crossover", params={"to": "JBL Charge 6"})
    assert reply["value"]["algorithm"] == "crossover"
    # It runs now (package E): nothing is pending.
    assert _ok(svc, op="state")["chain_pending"] == []


@pytest.mark.parametrize(
    ("field", "value", "stage", "algorithm"),
    [
        ("extract_ambience", False, "ambience", "off"),
        ("decorrelate", False, "decorrelate", "off"),
        ("eq_active", False, "eq", "off"),
    ],
)
def test_the_old_switches_and_the_chain_are_one(started, field, value, stage, algorithm):
    svc = started()
    _ok(svc, op="set", changes={field: value})
    assert _stage(svc, stage)["value"]["algorithm"] == algorithm
    _ok(svc, op="chain_set", stage=stage, algorithm=chain.on_algorithm(stage))
    assert _ok(svc, op="state")["global"][field] is True


def test_the_old_fields_and_the_chain_knobs_are_one(started):
    svc = started()
    _ok(svc, op="set", changes={"rear_delay_ms": 18, "volume_db": -12})
    assert _stage(svc, "align")["value"]["params"]["rear_delay_ms"] == 18
    assert _stage(svc, "volume")["value"]["params"]["volume_db"] == -12
    _ok(svc, op="chain_set", stage="align", params={"rear_delay_ms": 7})
    _ok(svc, op="chain_set", stage="volume", params={"volume_db": -30})
    glob = _ok(svc, op="state")["global"]
    assert (glob["rear_delay_ms"], glob["volume_db"]) == (7, -30)

    _ok(svc, op="set", speaker="Go 4 Red", changes={"pan": -0.2, "ambience": 0.4, "gain_db": -2, "muted": True})
    assert _stage(svc, "ambience")["value"]["speakers"]["Go 4 Red"] == {"pan": -0.2, "ambience": 0.4}
    assert _stage(svc, "volume")["value"]["speakers"]["Go 4 Red"] == {"gain_db": -2, "muted": True}
    _ok(svc, op="chain_set", stage="ambience", speaker="Go 4 Red", params={"pan": 0.1, "ambience": 0.9})
    _ok(svc, op="chain_set", stage="volume", speaker="Go 4 Red", params={"gain_db": -4, "muted": False})
    red = _ok(svc, op="state")["speakers"][0]
    assert (red["pan"], red["ambience"], red["gain_db"], red["muted"]) == (0.1, 0.9, -4, False)


def test_the_chain_off_reaches_the_motor_by_both_paths(started):
    """Card kill-switch-reaches-every-path: `off` through the chain or through the old
    field ends in the same motor state."""
    svc = started()
    _ok(svc, op="start")
    motor = FakeSession.instances[0].motor
    _ok(svc, op="chain_set", stage="ambience", algorithm="off")
    _ok(svc, op="set", changes={"decorrelate": False})
    _ok(svc, op="chain_set", stage="eq", algorithm="off")
    _wait(lambda: not motor.en_corte)
    assert not motor.extraer_ambiente_activo
    assert not motor.decorrelacion_activa
    assert not motor.ecualizacion_activa
    # And a new session starts with them off, from the first sample.
    _ok(svc, op="stop")
    _ok(svc, op="start")
    motor = FakeSession.instances[1].motor
    assert not motor.extraer_ambiente_activo
    assert not motor.decorrelacion_activa
    assert not motor.ecualizacion_activa


def test_chain_reset(started, tmp_path):
    svc = started()
    _ok(svc, op="chain_set", stage="limiter", algorithm="peak", params={"ceiling_db": -2, "release_ms": 500})
    _ok(svc, op="chain_reset", stage="limiter", param="release_ms")
    assert _stage(svc, "limiter")["chosen"] == {"algorithm": "peak", "params": {"ceiling_db": -2.0}}
    _ok(svc, op="chain_reset", stage="limiter")
    assert _stage(svc, "limiter")["chosen"] == {}
    assert _chain_file(tmp_path) == {"v": 1, "chain": {}}
    # A knob that lives in the installation goes back to its default value.
    _ok(svc, op="chain_reset", stage="ambience", param="pan", speaker="Go 4 Red")
    assert _ok(svc, op="state")["speakers"][0]["pan"] == 0.0
    assert _err(svc, op="chain_reset", stage="ambience", param="pan") == "bad_request"
    assert _err(svc, op="chain_reset", stage="limiter", param="nope") == "unknown_field"


def test_the_choices_survive_a_restart(started, tmp_path):
    svc = started()
    _ok(svc, op="chain_set", stage="ambience", params={"lam": 0.8})
    _ok(svc, op="set", changes={"decorrelate": False})
    again = started(install=False)
    assert _stage(again, "ambience")["value"]["params"]["lam"] == 0.8
    assert _ok(again, op="state")["global"]["decorrelate"] is False
    # What the chain stores is choices only, never the derived defaults.
    assert _chain_file(tmp_path)["chain"] == {"ambience": {"params": {"lam": 0.8}}, "decorrelate": {"algorithm": "off"}}


def test_sending_the_value_it_already_has_is_not_a_choice(started, tmp_path):
    svc = started()
    _ok(svc, op="set", changes={"extract_ambience": True, "decorrelate": True, "eq_active": True})
    assert not (tmp_path / "chain.json").exists()


def test_invalid_stored_choices_are_dropped_with_a_log_and_the_service_starts(started, tmp_path):
    (tmp_path / "chain.json").write_text(
        json.dumps(
            {
                "v": 1,
                "chain": {
                    "limiter": {"params": {"ceiling_db": 9, "release_ms": 300}},
                    "warp": {"algorithm": "on"},
                    "ambience": {"algorithm": "magic"},
                },
            }
        )
    )
    lines = []
    svc = started(lines=lines)
    assert _stage(svc, "limiter")["chosen"] == {"params": {"release_ms": 300.0}}
    assert _stage(svc, "ambience")["value"]["algorithm"] == "avendano_jot"
    dropped = [line for line in lines if "dropped" in line]
    assert len(dropped) == 3


def test_an_unreadable_chain_file_is_kept_aside_and_the_service_starts(started, tmp_path):
    (tmp_path / "chain.json").write_text("{not json")
    lines = []
    svc = started(lines=lines)
    assert _ok(svc, op="state")["chain_summary"]["eq"] == "boost_only"
    assert (tmp_path / "chain.json.bad").read_text() == "{not json"
    assert any("unreadable" in line for line in lines)


# -- presets: the two mixes of old and new (card no-simultaneous-deploy) -------------------------


def test_a_preset_carries_its_chain_in_a_file_of_its_own(started, tmp_path):
    svc = started()
    _ok(svc, op="chain_set", stage="bass", algorithm="protect", params={"harmonics_db": 0})
    _ok(svc, op="chain_set", stage="volume", algorithm="avrcp")
    _ok(svc, op="preset_save", name="bass")
    _ok(svc, op="chain_reset", stage="bass")
    _ok(svc, op="preset_save", name="plain")

    # presets.json keeps exactly its old shape: the reader of the version before the chain
    # (unchanged in this one: `PresetStore` rejects unknown keys) reads it.
    raw = json.loads((tmp_path / "presets.json").read_text())
    assert set(raw) == {"v", "presets"}
    assert all(set(p) == {"global", "speakers"} for p in raw["presets"].values())
    assert set(PresetStore(tmp_path / "presets.json").presets) == {"bass", "plain"}
    side = json.loads((tmp_path / "presets-chain.json").read_text())
    # The volume is the listener's, never a preset's.
    assert side == {
        "v": 1,
        "presets": {"bass": {"bass": {"algorithm": "protect", "params": {"harmonics_db": 0.0}}}, "plain": {}},
    }

    _ok(svc, op="start")
    _ok(svc, op="preset_load", name="bass")
    assert _ok(svc, op="state")["chain_summary"]["bass"] == "protect"
    _ok(svc, op="preset_load", name="plain")
    summary = _ok(svc, op="state")["chain_summary"]
    assert summary["bass"] == "off"
    assert summary["volume"] == "avrcp"
    motor = FakeSession.instances[0].motor
    _wait(lambda: not motor.en_corte)
    assert motor.cadena.algorithm("bass") == "off"

    _ok(svc, op="preset_delete", name="bass")
    side = json.loads((tmp_path / "presets-chain.json").read_text())
    assert set(side["presets"]) == {"plain"}


def test_old_files_load_as_before(started, tmp_path):
    """New reader x old files: no chain.json, no presets-chain.json, a presets.json written
    before the chain. Everything at its default, and the preset loads as it always did."""
    (tmp_path / "presets.json").write_text(
        json.dumps(
            {
                "v": 1,
                "presets": {
                    "old": {
                        "global": {"rear_delay_ms": 15.0, "extract_ambience": True, "decorrelate": False},
                        "speakers": {"Go 4 Red": {"pan": -0.3, "ambience": 0.6, "gain_db": 0.0}},
                    }
                },
            }
        )
    )
    svc = started()
    assert _ok(svc, op="state")["chain_summary"] == chain.summary(chain.ChainValues())
    _ok(svc, op="chain_set", stage="limiter", params={"release_ms": 600})
    _ok(svc, op="preset_load", name="old")
    state = _ok(svc, op="state")
    assert state["global"]["decorrelate"] is False
    assert state["global"]["rear_delay_ms"] == 15.0
    # A preset without a chain part leaves the chain's other choices alone.
    assert _stage(svc, "limiter")["chosen"] == {"params": {"release_ms": 600.0}}
    assert not (tmp_path / "presets-chain.json").exists()


def test_the_installation_file_keeps_its_shape(started, tmp_path):
    """`instalacion.json` is read with `cls(**data)`: an unknown key would stop an older
    version. The chain never writes into it."""
    svc = started()
    before = set(json.loads((tmp_path / "inst.json").read_text()))
    _ok(svc, op="chain_set", stage="align", params={"rear_delay_ms": 9, "delay_speed_ms_s": 1.0})
    _ok(svc, op="save")
    data = json.loads((tmp_path / "inst.json").read_text())
    assert set(data) == before
    assert data["retardo_traseros_ms"] == 9
    Instalacion.cargar(tmp_path / "inst.json")
