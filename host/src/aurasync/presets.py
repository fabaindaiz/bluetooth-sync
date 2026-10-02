"""Named presets: the artistic fields only, saved on request.

A preset holds what the listener chooses (`pan`, `ambience`, `gain_db` per speaker, and
`rear_delay_ms`, `extract_ambience`, `decorrelate`). Never `delay_ms`, which the
recalibration loop owns, nor `volume_db`, which would bias an A/B comparison (spec §5.6).

The file is written atomically: a temporary file next to it, then `os.replace`. A failure
in the middle leaves the previous file whole.

**A preset's chain** (spec 2026-10-02 §4.4) goes to a second file, `presets-chain.json`,
keyed by preset name: `presets.json` keeps exactly its shape, because its reader rejects
unknown keys and a rollback to a version without the chain must still start (card
*no-simultaneous-deploy*). A preset with no entry there loads exactly as before the chain
existed; one with an entry (even `{}`) replaces the chain's choices of every stage a preset
holds (`chain.ChainValues.with_preset`).
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

from aurasync.chain import PRESET_EXCLUDED_STAGES, clean_choices
from aurasync.control import (
    ARTISTIC_GLOBAL_FIELDS,
    ARTISTIC_SPEAKER_FIELDS,
    GLOBAL_FIELDS,
    SPEAKER_FIELDS,
    ContractError,
    check_value,
)

if TYPE_CHECKING:
    from collections.abc import Callable

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


def read_lenient(path: Path, log: Callable[[str], None]) -> Any:
    """The JSON in `path`, or None. A file that cannot be read is logged and kept aside as
    `<name>.bad`, so the next write does not destroy what a person may want to recover."""
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        log(f"{path}: unreadable ({exc}); kept as {path.name}.bad and ignored")
        with contextlib.suppress(OSError):
            shutil.copy(path, path.with_name(path.name + ".bad"))
        return None


class PresetChainStore:
    """`presets-chain.json`: the chain choices of each preset, by preset name.

    Lenient where `PresetStore` is strict: an invalid entry is dropped with a log line and
    never stops the service (the chain grows stages and knobs; a file written by a newer
    version must still be readable by this one).
    """

    def __init__(self, path: Path, log: Callable[[str], None] = lambda _: None) -> None:
        self.path = path
        self.parts: dict[str, dict] = {}
        if path.exists():
            self.parts = self._read(log)

    def _read(self, log: Callable[[str], None]) -> dict[str, dict]:
        data = read_lenient(self.path, log)
        if data is None:
            return {}
        if not isinstance(data, dict) or data.get("v") != VERSION or not isinstance(data.get("presets"), dict):
            log(f"{self.path}: expected {{'v': {VERSION}, 'presets': {{...}}}}; ignored")
            return {}
        parts = {}
        for name, part in data["presets"].items():
            clean = clean_choices(part, lambda line, name=name: log(f"preset {name!r}: {line}"))
            parts[name] = {k: v for k, v in clean.items() if k not in PRESET_EXCLUDED_STAGES}
        return parts

    def _write(self) -> None:
        text = json.dumps({"v": VERSION, "presets": self.parts}, indent=2, ensure_ascii=False) + "\n"
        write_atomic(self.path, text)

    def get(self, name: str) -> dict | None:
        """The preset's chain, or None when it has none (a preset saved before the chain)."""
        return self.parts.get(name)

    def save(self, name: str, part: dict) -> None:
        previous = self.parts.get(name)
        self.parts[name] = {k: v for k, v in part.items() if k not in PRESET_EXCLUDED_STAGES}
        try:
            self._write()
        except BaseException:
            if previous is None:
                self.parts.pop(name, None)
            else:
                self.parts[name] = previous
            raise

    def restore(self, name: str, part: dict | None) -> None:
        """Put back what `name` had before (after a failed save of the preset itself)."""
        if part is None:
            self.delete(name)
        else:
            self.save(name, part)

    def delete(self, name: str) -> None:
        if name not in self.parts:
            return
        part = self.parts.pop(name)
        try:
            self._write()
        except BaseException:
            self.parts[name] = part
            raise
