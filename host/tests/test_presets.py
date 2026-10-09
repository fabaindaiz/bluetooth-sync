import json

import pytest

from aurasync import presets
from aurasync.control import ContractError
from aurasync.presets import PresetStore

WIDE = {
    "global": {"rear_delay_ms": 15.0, "extract_ambience": True, "decorrelate": True},
    "speakers": {"Go 4 Red": {"pan": -0.3, "ambience": 0.6, "gain_db": 0.0}},
}


def test_round_trip(tmp_path):
    path = tmp_path / "presets.json"
    PresetStore(path).save("wide", WIDE)
    assert PresetStore(path).get("wide") == WIDE
    assert json.loads(path.read_text())["v"] == 1


@pytest.mark.parametrize(
    "bad",
    [
        {"global": {"volume_db": -10}},
        {"speakers": {"A": {"delay_ms": 3.0}}},
        {"speakers": {"A": {"pan": 3.0}}},
        {"other": {}},
    ],
)
def test_a_preset_never_holds_delay_volume_or_unknown_fields(tmp_path, bad):
    with pytest.raises(ValueError, match="preset 'x'"):
        PresetStore(tmp_path / "p.json").save("x", bad)
    assert not (tmp_path / "p.json").exists()


def test_unknown_fields_in_the_file_are_rejected(tmp_path):
    path = tmp_path / "presets.json"
    path.write_text(json.dumps({"v": 1, "presets": {"x": {"global": {"volume_db": 0}}}}))
    with pytest.raises(ValueError, match="unknown global field"):
        PresetStore(path)


def test_missing_preset_is_not_found(tmp_path):
    with pytest.raises(ContractError) as info:
        PresetStore(tmp_path / "p.json").get("nope")
    assert info.value.code == "not_found"


def test_a_failed_write_leaves_the_old_file_and_memory_whole(tmp_path, monkeypatch):
    path = tmp_path / "presets.json"
    store = PresetStore(path)
    store.save("wide", WIDE)
    before = path.read_text()

    def boom(*_):
        msg = "disk full"
        raise OSError(msg)

    monkeypatch.setattr(presets.os, "replace", boom)
    with pytest.raises(OSError, match="disk full"):
        store.save("other", WIDE)
    assert path.read_text() == before
    assert "other" not in store.presets
    assert list(tmp_path.iterdir()) == [path], "the temporary file must not be left behind"


def test_delete(tmp_path):
    store = PresetStore(tmp_path / "p.json")
    store.save("wide", WIDE)
    store.delete("wide")
    assert PresetStore(tmp_path / "p.json").presets == {}


def test_renaming_a_preset_without_a_part_drops_a_stale_part_of_the_new_name(tmp_path):
    store = presets.PresetChainStore(tmp_path / "c.json")
    store.save("stale", {"bass": {"algorithm": "protect"}})
    store.rename("plain", "stale")
    assert store.get("stale") is None
    assert json.loads((tmp_path / "c.json").read_text())["presets"] == {}
