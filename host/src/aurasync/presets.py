"""Named presets: the artistic fields only, saved on request.

A preset holds what the listener chooses (`pan`, `ambience`, `gain_db` per speaker, and
`rear_delay_ms`, `extract_ambience`, `decorrelate`). Never `delay_ms`, which the
recalibration loop owns, nor `volume_db`, which would bias an A/B comparison (spec §5.6).

The file is written atomically: a temporary file next to it, then `os.replace`. A failure
in the middle leaves the previous file whole.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from aurasync.control import (
    ARTISTIC_GLOBAL_FIELDS,
    ARTISTIC_SPEAKER_FIELDS,
    GLOBAL_FIELDS,
    SPEAKER_FIELDS,
    ContractError,
    check_value,
)

VERSION = 1


def write_atomic(path: Path, text: str, mode: int = 0o644) -> None:
    """Write `text` to `path` so that a reader sees either the old file or the new one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def validate_preset(name: str, data: Any) -> dict:
    """Check one preset read from disk with the same rules as the contract."""
    if not isinstance(data, dict) or set(data) - {"global", "speakers"}:
        raise ValueError(f"preset {name!r}: expected only 'global' and 'speakers'")
    glob = data.get("global", {})
    speakers = data.get("speakers", {})
    if not isinstance(glob, dict) or not isinstance(speakers, dict):
        raise ValueError(f"preset {name!r}: 'global' and 'speakers' must be objects")  # noqa: TRY004
    try:
        clean_global = {}
        for key, value in glob.items():
            if key not in ARTISTIC_GLOBAL_FIELDS:
                raise ValueError(f"preset {name!r}: unknown global field {key!r}")
            clean_global[key] = check_value(key, GLOBAL_FIELDS[key], value)
        clean_speakers = {}
        for speaker, fields in speakers.items():
            if not isinstance(fields, dict):
                raise ValueError(f"preset {name!r}: speaker {speaker!r} must be an object")  # noqa: TRY004
            clean = {}
            for key, value in fields.items():
                if key not in ARTISTIC_SPEAKER_FIELDS:
                    raise ValueError(f"preset {name!r}: unknown speaker field {key!r}")
                clean[key] = check_value(key, SPEAKER_FIELDS[key], value)
            clean_speakers[speaker] = clean
    except ContractError as exc:
        raise ValueError(f"preset {name!r}: {exc.message}") from exc
    return {"global": clean_global, "speakers": clean_speakers}


class PresetStore:
    """The presets file. Read at start; written on every save and delete."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.presets: dict[str, dict] = {}
        if path.exists():
            self.presets = self._read()

    def _read(self) -> dict[str, dict]:
        data = json.loads(self.path.read_text())
        if not isinstance(data, dict) or data.get("v") != VERSION or set(data) != {"v", "presets"}:
            msg = f"{self.path}: expected {{'v': {VERSION}, 'presets': {{...}}}}"
            raise ValueError(msg)
        return {name: validate_preset(name, preset) for name, preset in data["presets"].items()}

    def _write(self) -> None:
        text = json.dumps({"v": VERSION, "presets": self.presets}, indent=2, ensure_ascii=False) + "\n"
        write_atomic(self.path, text)

    def get(self, name: str) -> dict:
        if name not in self.presets:
            raise ContractError("not_found", f"no preset {name!r}; saved: {sorted(self.presets)}")
        return self.presets[name]

    def save(self, name: str, preset: dict) -> None:
        previous = self.presets.get(name)
        self.presets[name] = validate_preset(name, preset)
        try:
            self._write()
        except BaseException:
            # Memory and disk must not disagree.
            if previous is None:
                self.presets.pop(name, None)
            else:
                self.presets[name] = previous
            raise

    def delete(self, name: str) -> None:
        preset = self.get(name)
        del self.presets[name]
        try:
            self._write()
        except BaseException:
            self.presets[name] = preset
            raise
