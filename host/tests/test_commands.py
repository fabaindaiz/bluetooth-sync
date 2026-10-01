"""Contrato de órdenes (docs/research/09 §5): cada regla tiene un test que la rompe."""

import json

import pytest

from aurasync.engine.base import Command, CommandError, check_against_state, parse_command
from aurasync.state import MODES, Snapshot

from .factories import snapshot_with


def test_modes_name_their_channels():
    assert MODES["quad"] == ("FL", "FR", "RL", "RR")
    assert MODES["lcrs"] == ("FL", "FC", "FR", "RC")


def test_snapshot_serializes_to_plain_json():
    snap = snapshot_with()
    data = json.loads(snap.to_json())
    assert data["engine"]["mode"] == "quad"
    assert isinstance(data["speakers"], list)
    assert set(data["meters"]) == set(MODES["quad"])


def test_unobserved_iso_capability_stays_null_not_false():
    data = json.loads(snapshot_with(iso_broadcaster=None).to_json())
    assert data["controller"]["iso_broadcaster"] is None


def test_valid_command_parses():
    cmd = parse_command({"cmd": "assign", "args": {"address": "02:00:00:00:00:01", "channel": "FL"}})
    assert cmd == Command("assign", {"address": "02:00:00:00:00:01", "channel": "FL"})


@pytest.mark.parametrize(
    "raw",
    [
        {},
        {"cmd": 3},
        {"cmd": "explode"},
        {"cmd": "set_mode", "args": {"mode": "5.1"}},
        {"cmd": "set_mode", "args": {}},
        {"cmd": "set_source", "args": {"kind": "app"}},
        {"cmd": "set_source", "args": {"kind": "file", "name": ""}},
        {"cmd": "set_source", "args": {"kind": "radio"}},
        {"cmd": "set_master", "args": {"db": 3}},
        {"cmd": "set_master", "args": {"db": -61}},
        {"cmd": "set_master", "args": {"db": "alto"}},
        {"cmd": "set_master", "args": {"db": True}},
        {"cmd": "set_speaker_volume", "args": {"db": -10}},
        {"cmd": "set_mute", "args": {"address": "02:00:00:00:00:01"}},
        {"cmd": "assign", "args": {"address": "02:00:00:00:00:01"}},
        {"cmd": "tone", "args": {"seconds": 2}},
        {"cmd": "tone", "args": {"channel": "FL", "seconds": 0.1}},
        {"cmd": "tone", "args": {"channel": "FL", "seconds": 30}},
        {"cmd": "start", "args": {"extra": 1}},
    ],
)
def test_malformed_commands_are_refused_not_defaulted(raw):
    with pytest.raises(CommandError):
        parse_command(raw)


def test_speaker_volume_has_no_implicit_all_target():
    with pytest.raises(CommandError, match="address"):
        parse_command({"cmd": "set_speaker_volume", "args": {"address": "", "db": -10}})


def test_unknown_speaker_is_refused():
    snap = snapshot_with()
    cmd = parse_command({"cmd": "set_mute", "args": {"address": "02:00:00:00:00:99", "muted": True}})
    with pytest.raises(CommandError, match="parlante"):
        check_against_state(cmd, snap)


def test_channel_outside_current_mode_is_refused():
    snap = snapshot_with(mode="quad")
    cmd = parse_command({"cmd": "assign", "args": {"address": "02:00:00:00:00:01", "channel": "FC"}})
    with pytest.raises(CommandError, match="FC"):
        check_against_state(cmd, snap)


def test_occupied_channel_is_refused_until_freed():
    snap = snapshot_with(assignments={"02:00:00:00:00:01": "FL"})
    cmd = parse_command({"cmd": "assign", "args": {"address": "02:00:00:00:00:02", "channel": "FL"}})
    with pytest.raises(CommandError, match="ocupado"):
        check_against_state(cmd, snap)


def test_reassigning_the_same_speaker_to_its_channel_is_allowed():
    snap = snapshot_with(assignments={"02:00:00:00:00:01": "FL"})
    cmd = parse_command({"cmd": "assign", "args": {"address": "02:00:00:00:00:01", "channel": "FL"}})
    check_against_state(cmd, snap)


def test_tone_needs_a_running_transmission():
    snap = snapshot_with(running=False)
    cmd = parse_command({"cmd": "tone", "args": {"channel": "FL", "seconds": 1}})
    with pytest.raises(CommandError, match="transmitir"):
        check_against_state(cmd, snap)


def test_tone_on_channel_outside_mode_is_refused():
    snap = snapshot_with(mode="lcrs", running=True)
    cmd = parse_command({"cmd": "tone", "args": {"channel": "RL", "seconds": 1}})
    with pytest.raises(CommandError, match="RL"):
        check_against_state(cmd, snap)


def test_calibration_needs_every_channel_assigned():
    snap = snapshot_with(assignments={"02:00:00:00:00:01": "FL"}, running=True)
    cmd = parse_command({"cmd": "calibrate"})
    with pytest.raises(CommandError, match="sin parlante"):
        check_against_state(cmd, snap)


def test_snapshot_factory_returns_snapshot():
    assert isinstance(snapshot_with(), Snapshot)


def test_calibration_needs_a_running_transmission():
    every = dict(
        zip(
            ("02:00:00:00:00:01", "02:00:00:00:00:02", "02:00:00:00:00:03", "02:00:00:00:00:04"),
            MODES["quad"],
            strict=True,
        )
    )
    cmd = parse_command({"cmd": "calibrate"})
    with pytest.raises(CommandError, match="transmitir"):
        check_against_state(cmd, snapshot_with(assignments=every, running=False))
    check_against_state(cmd, snapshot_with(assignments=every, running=True))


# -- configuración del motor -----------------------------------------------------


@pytest.mark.parametrize(
    "args",
    [
        {"key": "upmix", "value": "simple"},
        {"key": "rear_delay_ms", "value": 20},
        {"key": "presentation_delay_us", "value": 80000},
        {"key": "bitrate_kbps", "value": 96},
        {"key": "transport", "value": "low_latency"},
        {"key": "broadcast_name", "value": "sala"},
    ],
)
def test_valid_config_parses(args):
    assert parse_command({"cmd": "set_config", "args": args}).args == args


@pytest.mark.parametrize(
    "args",
    [
        {"key": "upmix", "value": "rainbow"},
        {"key": "rear_delay_ms", "value": 31},
        {"key": "rear_delay_ms", "value": "12"},
        {"key": "presentation_delay_us", "value": 50000},
        {"key": "bitrate_kbps", "value": "80"},
        {"key": "transport", "value": "fast"},
        {"key": "broadcast_name", "value": ""},
        {"key": "broadcast_name", "value": "x" * 33},
        {"key": "broadcast_name", "value": "con\nsalto"},
        {"key": "manufacturer_data", "value": "otro"},
        {"key": "volume", "value": 1},
        {"key": "upmix"},
    ],
)
def test_invalid_config_is_refused(args):
    with pytest.raises(CommandError):
        parse_command({"cmd": "set_config", "args": args})


def test_manufacturer_data_is_fixed():
    with pytest.raises(CommandError, match="fij"):
        parse_command({"cmd": "set_config", "args": {"key": "manufacturer_data", "value": "harman"}})


# -- parlantes: ajustes y búsqueda ------------------------------------------------


def test_trim_needs_at_least_one_value_in_range():
    ok = parse_command({"cmd": "set_speaker_trim", "args": {"address": "a", "delay_ms": 5}})
    assert ok.args == {"address": "a", "delay_ms": 5.0}
    for bad in ({"address": "a"}, {"address": "a", "delay_ms": 101}, {"address": "a", "gain_db": 7}):
        with pytest.raises(CommandError):
            parse_command({"cmd": "set_speaker_trim", "args": bad})


def test_calibration_apply_needs_a_finished_calibration():
    cmd = parse_command({"cmd": "calibration_apply"})
    with pytest.raises(CommandError, match="calibración"):
        check_against_state(cmd, snapshot_with(calibration="idle"))
    check_against_state(cmd, snapshot_with(calibration="done"))


# -- servicios --------------------------------------------------------------------


def test_unknown_service_is_refused():
    cmd = parse_command({"cmd": "service_start", "args": {"name": "nope"}})
    with pytest.raises(CommandError, match="servicio"):
        check_against_state(cmd, snapshot_with())


def test_system_and_unmanaged_services_cannot_be_driven():
    for name in ("coreaudiod", "panel", "bass"):
        cmd = parse_command({"cmd": "service_stop", "args": {"name": name}})
        with pytest.raises(CommandError, match="no se puede"):
            check_against_state(cmd, snapshot_with())


def test_starting_a_service_needs_its_dependencies_and_names_them():
    cmd = parse_command({"cmd": "service_start", "args": {"name": "emitter"}})
    with pytest.raises(CommandError, match=r"controller.*dsp|dsp.*controller"):
        check_against_state(cmd, snapshot_with())
    check_against_state(
        cmd, snapshot_with(service_states={"controller": "running", "dsp": "starting", "capture": "running"})
    )


def test_fault_injection_exists_only_in_the_demo():
    cmd = parse_command({"cmd": "service_fail", "args": {"name": "emitter"}})
    check_against_state(cmd, snapshot_with(kind="simulated"))
    with pytest.raises(CommandError, match="demo"):
        check_against_state(cmd, snapshot_with(kind="real"))
