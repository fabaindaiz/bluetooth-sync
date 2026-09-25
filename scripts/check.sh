#!/usr/bin/env bash
# The repository's gate: the bundle's integrity and privacy, and the record ids.
# bundle.py needs Python >= 3.11; this machine's default python3 is 3.9 (d-7c8794-3b6b73).
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${PY:-/opt/homebrew/bin/python3.14}"
"$PY" .agents/tools/bundle.py verify
"$PY" .agents/tools/bundle.py ids docs/decisions.md docs/roadmap.md .claude/logs/agent-changelog.md
echo "check: ok"
