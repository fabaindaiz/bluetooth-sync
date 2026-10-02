import dataclasses

import pytest

from aurasync import control
from aurasync.config import Instalacion, Parlante
from aurasync.control import ContractError


def _code(message) -> str:
    with pytest.raises(ContractError) as info:
        control.parse(message)
    return info.value.code


def _set(changes, speaker=None):
    m = {"v": 1, "op": "set", "changes": changes}
    if speaker is not None:
        m["speaker"] = speaker
    return m


def test_a_valid_set_parses_with_every_field():
    c = control.parse(_set({"pan": -0.3, "ambience": 1, "gain_db": -3}, speaker="Go 4 Red") | {"id": 7})
    assert c.op == "set"
    assert c.id == 7
    assert c.args["changes"] == {"pan": -0.3, "ambience": 1.0, "gain_db": -3.0}


@pytest.mark.parametrize(
    ("message", "code"),
    [
        ([], "bad_request"),
        ({"v": 1}, "bad_request"),
        ({"v": 2, "op": "state"}, "version"),
        ({"op": "state"}, "version"),
        ({"v": 1, "op": "dance"}, "unknown_op"),
        ({"v": 1, "op": "state", "extra": 1}, "unknown_field"),
        ({"v": 1, "op": "start", "recalibrate": 1}, "type"),
        ({"v": 1, "op": "preset_save"}, "bad_request"),
        ({"v": 1, "op": "preset_save", "name": "  "}, "out_of_range"),
        ({"v": 1, "op": "preset_save", "name": 3}, "type"),
        (_set({}), "bad_request"),
        (_set([1]), "type"),
        (_set({"pan": 0.1}), "unknown_field"),
        (_set({"volume_db": -10}, speaker="A"), "unknown_field"),
        (_set({"delay_ms": 101.0}, speaker="A"), "out_of_range"),
        (_set({"layout": "hexa"}), "out_of_range"),
        (_set({"block_size": 3000}), "out_of_range"),
        (_set({"block_size": 4096.5}), "type"),
        (_set({"sink_description": ""}), "out_of_range"),
        (_set({"sink_description": "a\nb"}), "out_of_range"),
        ({"v": 1, "op": "connect", "address": "90:f2:60:75:4a:83"}, "out_of_range"),
        ({"v": 1, "op": "connect", "address": "x"}, "out_of_range"),
        ({"v": 1, "op": "tone", "speaker": "A", "seconds": 30}, "out_of_range"),
        ({"v": 1, "op": "tone"}, "bad_request"),
        ({"v": 1, "op": "assign", "speaker": "A", "role": "XX"}, "out_of_range"),
        ({"v": 1, "op": "source", "kind": "radio"}, "out_of_range"),
        ({"v": 1, "op": "calibrate", "amplitude": 0.5}, "out_of_range"),
        ({"v": 1, "op": "logs", "since": -1}, "out_of_range"),
        ({"v": 1, "op": "ab_play", "which": "c"}, "out_of_range"),
        (_set({"pan": "0.5"}, speaker="A"), "type"),
        (_set({"pan": True}, speaker="A"), "type"),
        (_set({"pan": 1.4}, speaker="A"), "out_of_range"),
        (_set({"gain_db": 7}, speaker="A"), "out_of_range"),
        (_set({"volume_db": 1}), "out_of_range"),
        (_set({"rear_delay_ms": 51}), "out_of_range"),
        (_set({"decorrelate": 1}), "type"),
    ],
)
def test_every_error_code_of_parse(message, code):
    assert _code(message) == code


def test_one_bad_field_rejects_the_whole_set():
    """Atomicity starts here: nothing reaches the service unless every field is valid."""
    assert _code(_set({"pan": 0.1, "ambience": 2.0}, speaker="A")) == "out_of_range"


def test_nan_and_infinity_are_rejected_when_decoding():
    with pytest.raises(ContractError) as info:
        control.decode(b'{"v": 1, "op": "set", "changes": {"pan": NaN}}')
    assert info.value.code == "type"


def test_a_body_over_64_kib_is_rejected():
    with pytest.raises(ContractError) as info:
        control.decode(b" " * (control.MAX_BYTES + 1))
    assert info.value.code == "bad_request"


def test_malformed_json_is_a_bad_request():
    with pytest.raises(ContractError) as info:
        control.decode(b"{nope")
    assert info.value.code == "bad_request"


def test_the_name_map_points_at_real_config_fields():
    speaker_fields = {f.name for f in dataclasses.fields(Parlante)}
    install_fields = {f.name for f in dataclasses.fields(Instalacion)}
    for spec in control.SPEAKER_FIELDS.values():
        assert spec.attr is None or spec.attr in speaker_fields
    assert control.GLOBAL_FIELDS["rear_delay_ms"].attr in install_fields


def test_every_error_code_has_an_http_status():
    for code in control.HTTP_STATUS:
        assert ContractError(code, "x").code == code
    with pytest.raises(ValueError, match="unknown error code"):
        ContractError("nope", "x")


class _Recorder:
    def __getattr__(self, name):
        return lambda *a, **k: {"called": name, "args": list(a), "kwargs": k}


@pytest.mark.parametrize(
    ("message", "called"),
    [
        ({"v": 1, "op": "state"}, "state"),
        ({"v": 1, "op": "start"}, "start"),
        ({"v": 1, "op": "stop"}, "stop"),
        (_set({"pan": 0.0}, speaker="A"), "set_speaker"),
        (_set({"volume_db": -6}), "set_global"),
        ({"v": 1, "op": "presets"}, "presets"),
        ({"v": 1, "op": "preset_save", "name": "a"}, "preset_save"),
        ({"v": 1, "op": "preset_load", "name": "a"}, "preset_load"),
        ({"v": 1, "op": "preset_delete", "name": "a"}, "preset_delete"),
        ({"v": 1, "op": "save"}, "save"),
        ({"v": 1, "op": "shutdown"}, "shutdown"),
    ],
)
def test_dispatch_reaches_the_right_method(message, called):
    assert control.dispatch(control.parse(message), _Recorder())["called"] == called


def test_replies_echo_the_id_only_when_given():
    assert control.ok(None, {}) == {"v": 1, "ok": True, "result": {}}
    assert control.error(3, "conflict", "m")["id"] == 3


def test_assign_needs_a_role():
    with pytest.raises(ContractError) as info:
        control.parse({"v": 1, "op": "assign", "speaker": "A", "role": None})
    assert info.value.code == "type"


def test_roles_are_recognised_from_their_values():
    assert control.role_of(-0.7, 0.55, "quad") == "RL"
    assert control.role_of(-0.7, 0.55, "lcrs") is None
    assert control.role_of(0.1, 0.2, "quad") is None


def test_delay_ms_parses_and_the_service_decides():
    """By hand it is allowed by the contract; the service refuses it while the loop runs."""
    assert control.parse(_set({"delay_ms": 3.0}, speaker="A")).args["changes"] == {"delay_ms": 3.0}
