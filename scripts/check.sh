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
# Hasta la decisión de seguir (i-7c8794-0d129c), el paquete solo puede tener el
# esqueleto (d-7c8794-f619c4). Esta lista se borra cuando esa decisión se registre.
allowed="host/src/aurasync/__init__.py host/src/aurasync/__main__.py host/src/aurasync/cli.py"
found="$(find host/src/aurasync -type f ! -name '*.pyc' ! -path '*/__pycache__/*' | sort | tr '\n' ' ' | sed 's/ $//')"
if [ "$found" != "$allowed" ]; then
  echo "check: host/src/aurasync tiene archivos fuera del esqueleto (d-7c8794-f619c4):" >&2
  echo "  esperado: $allowed" >&2
  echo "  hay:      $found" >&2
  exit 1
fi
(cd host && hatch fmt --check && hatch test)
echo "check: ok"
