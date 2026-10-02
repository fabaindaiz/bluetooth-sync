"""Check, without Node, that the compiled panel matches its sources (d-7c8794-6da524).

`npm run build` (in `host/web/`) writes `src/aurasync/panel/cadena.js` and, next to it, the
stamp `cadena.build.json`: the hash of the sources that produced it and the hash of what it
wrote (`host/web/stamp.ts`). This recomputes both with the standard library, so
`scripts/check.sh` fails when someone changed `host/web/` and did not rebuild, or edited the
compiled file by hand. The definition must stay identical to `stamp.ts`:

    sources = ROOT_FILES + every file under src/ (no dotfiles), as POSIX paths, sorted
    digest  = sha256( for each: "<path>\\n" + sha256(bytes).hexdigest() + "\\n" )

Usage: `python3 host/scripts/web_stamp.py` (any Python >= 3.9; exit 1 with the reason).
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

HOST = Path(__file__).resolve().parents[1]
WEB = HOST / "web"
PANEL = HOST / "src" / "aurasync" / "panel"
ROOT_FILES = ("package.json", "package-lock.json", "tsconfig.json", "tsconfig.node.json", "vite.config.ts", "stamp.ts")
STAMP = "cadena.build.json"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sources_digest(web: Path = WEB) -> str:
    paths = set(ROOT_FILES)
    for path in (web / "src").rglob("*"):
        rel = path.relative_to(web)
        if path.is_file() and not any(part.startswith(".") for part in rel.parts):
            paths.add(rel.as_posix())
    lines = "".join(f"{p}\n{_sha256((web / p).read_bytes())}\n" for p in sorted(paths))
    return _sha256(lines.encode())


def problems(web: Path = WEB, panel: Path = PANEL) -> list[str]:
    """What does not match, in words (empty: the build is the one of these sources)."""
    stamp_path = panel / STAMP
    if not stamp_path.exists():
        return [f"falta {stamp_path}: corré `npm run build` en host/web"]
    stamp = json.loads(stamp_path.read_text(encoding="utf-8"))
    out = []
    if stamp.get("sources_sha256") != sources_digest(web):
        out.append(
            "host/web cambió desde el último build: corré `npm ci && npm run build` en host/web y versioná lo compilado"
        )
    for name, digest in (stamp.get("outputs") or {}).items():
        path = panel / name
        if not path.exists():
            out.append(f"falta {path}, que el build escribió")
        elif _sha256(path.read_bytes()) != digest:
            out.append(f"{path} no es lo que escribió el build (¿editado a mano?): corré `npm run build` en host/web")
    if not stamp.get("outputs"):
        out.append(f"{stamp_path} no lista lo que escribió el build")
    return out


def main() -> int:
    found = problems()
    for line in found:
        print(f"web_stamp: {line}", file=sys.stderr)
    if not found:
        print("web_stamp: el panel compilado corresponde a host/web")
    return 1 if found else 0


if __name__ == "__main__":
    raise SystemExit(main())
