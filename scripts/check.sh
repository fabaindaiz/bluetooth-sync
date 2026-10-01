#!/usr/bin/env bash
# The repository's gate: the bundle's integrity and privacy, and the record ids.
# bundle.py needs Python >= 3.11; this machine's default python3 is 3.9 (d-7c8794-3b6b73).
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${PY:-/opt/homebrew/bin/python3.14}"
"$PY" .agents/tools/bundle.py verify
"$PY" .agents/tools/bundle.py ids docs/decisions.md docs/roadmap.md .claude/logs/agent-changelog.md
# El paquete del host (d-7c8794-c23c20): lint, formato y tipos, y los tests. Sin hatch, el
# chequeo falla en vez de saltarse esta parte.
command -v hatch >/dev/null || { echo "check: falta hatch (ver host/README.md)" >&2; exit 1; }
# Hasta la decisión de seguir (i-7c8794-0d129c), el paquete solo puede tener el
# esqueleto (d-7c8794-f619c4) y el panel con su motor simulado (d-7c8794-b1eaac).
# Ningún motor real. Esta lista se borra cuando esa decisión se registre.
allowed="$(printf '%s ' \
  host/src/aurasync/__init__.py \
  host/src/aurasync/__main__.py \
  host/src/aurasync/cli.py \
  host/src/aurasync/engine/__init__.py \
  host/src/aurasync/engine/base.py \
  host/src/aurasync/engine/simulated.py \
  host/src/aurasync/logbuffer.py \
  host/src/aurasync/panel/__init__.py \
  host/src/aurasync/panel/auth.py \
  host/src/aurasync/panel/pairing.py \
  host/src/aurasync/panel/server.py \
  host/src/aurasync/panel/static/app.js \
  host/src/aurasync/panel/static/index.html \
  host/src/aurasync/panel/static/styles.css \
  host/src/aurasync/state.py \
  | tr ' ' '\n' | sed '/^$/d' | LC_ALL=C sort | tr '\n' ' ' | sed 's/ $//')"
# Las dos listas se ordenan con LC_ALL=C: con otro locale, el orden de "__init__"
# cambia y el chequeo fallaría sin motivo en otro equipo.
found="$(find host/src/aurasync -type f ! -name '*.pyc' ! -path '*/__pycache__/*' | LC_ALL=C sort | tr '\n' ' ' | sed 's/ $//')"
if [ "$found" != "$allowed" ]; then
  echo "check: host/src/aurasync tiene archivos fuera de lo permitido (d-7c8794-f619c4, d-7c8794-b1eaac):" >&2
  echo "  esperado: $allowed" >&2
  echo "  hay:      $found" >&2
  exit 1
fi
(cd host && hatch check && hatch test)
echo "check: ok"
