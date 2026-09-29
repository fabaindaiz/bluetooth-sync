#!/usr/bin/env bash
# The repository's gate: the bundle's integrity and privacy, and the record ids.
# bundle.py needs Python >= 3.11; this machine's default python3 is 3.9 (d-7c8794-3b6b73).
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${PY:-/opt/homebrew/bin/python3.14}"
"$PY" .agents/tools/bundle.py verify
"$PY" .agents/tools/bundle.py ids docs/decisions.md docs/roadmap.md .claude/logs/agent-changelog.md
# El paquete del host (d-7c8794-c23c20): lint y formato, y los tests. Sin hatch, el
# chequeo falla en vez de saltarse esta parte.
command -v hatch >/dev/null || { echo "check: falta hatch (ver host/README.md)" >&2; exit 1; }
# La lista cerrada de archivos de host/src/aurasync se quitó el 2026-09-28, al
# registrarse d-7c8794-9afee2: se construye el núcleo compartido con A2DP como primer
# backend emisor. El límite ahora es esa decisión, no este script.
(cd host && hatch fmt --check && hatch test)
echo "check: ok"
