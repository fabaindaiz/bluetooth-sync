"""The PWA's demo state (web/src/demo/fixture.json, written by scripts/demo_fixture.py): public, so it
must carry nothing of a real device or machine, and it must still look like what the service answers."""

import json
import re
from pathlib import Path

HOST = Path(__file__).resolve().parents[1]
FIXTURE = HOST / "web" / "src" / "demo" / "fixture.json"
CONTRACT = HOST / "web" / "src" / "contract.gen.ts"

# The same as web/pwa/privacy.ts, plus what the build cannot know (a path, this user's name).
FORBIDDEN = {
    "a MAC address": r"\b[0-9A-F]{2}([:_-])[0-9A-F]{2}(?:\1[0-9A-F]{2}){4}\b",
    "a private IPv4 address": r"\b(?:10\.\d{1,3}|192\.168|172\.(?:1[6-9]|2\d|3[01]))\.\d{1,3}(?:\.\d{1,3})?\b",
    "a client token": r"\basc_[0-9a-z]",
    "a Bluetooth sink name": r"bluez_(?:output|input|card)\.[0-9A-F]{2}",
    "a machine of this project": r"\b(?:pc-ryzen5|hp-o16)\b",
    "a home path": r"/(?:home|Users)/[^\s\"/]+",
}


def test_the_demo_carries_nothing_of_a_real_device():
    text = FIXTURE.read_text()
    for what, pattern in FORBIDDEN.items():
        found = re.findall(pattern, text, flags=re.IGNORECASE)
        assert not found, f"{what} in the demo fixture: {found[:3]}"


def test_the_demo_still_has_what_the_contract_says_a_state_has():
    """If the snapshot grows a field the contract types name, run scripts/demo_fixture.py again."""
    state = json.loads(FIXTURE.read_text())["state"]
    contract = CONTRACT.read_text()
    view = contract[contract.index("export interface StateView {") :]
    view = view[: view.index("\n}")]
    fields = re.findall(r"^  ([a-z_]+)\??:", view, flags=re.MULTILINE)
    assert fields
    missing = [f for f in fields if f not in state]
    assert not missing, f"the demo's state lacks {missing}: run scripts/demo_fixture.py"


def test_the_demo_has_a_whole_installation():
    data = json.loads(FIXTURE.read_text())
    assert len(data["state"]["speakers"]) == 3
    assert data["chain"]["stages"]
    assert len(data["presets"]["presets"]) >= 2
    assert not data["spatial_explain"].get("pending")
