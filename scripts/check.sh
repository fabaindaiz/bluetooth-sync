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
# El panel compilado (d-7c8794-6da524): se comprueba sin Node que host/src/aurasync/panel/cadena.js
# es el build de host/web tal como está (un sello con el hash de las fuentes y de lo escrito). Que
# web/src/contract.gen.ts esté al día con control.py y chain.py lo comprueba hatch test
# (tests/test_contract_types.py).
"$PY" host/scripts/web_stamp.py
# La PWA (d-7c8794-37f9bc) se compila en GitHub Actions (.github/workflows/pages.yml): lo compilado
# no se versiona, y el workflow tiene que seguir existiendo.
if [ -n "$(git ls-files host/web/dist-pwa)" ]; then
  echo "check: host/web/dist-pwa/ no se versiona (lo arma .github/workflows/pages.yml)" >&2
  exit 1
fi
[ -f .github/workflows/pages.yml ] || { echo "check: falta .github/workflows/pages.yml" >&2; exit 1; }
# El motor en Rust (engine/, d-7c8794-196e0c): sin cargo el chequeo falla en vez de saltarse esta
# parte. La toolchain la fija engine/rust-toolchain.toml (rustup la baja sola la primera vez).
command -v cargo >/dev/null || { echo "check: falta cargo: instala rustup (ver engine/README.md)" >&2; exit 1; }
(cd engine && cargo fmt --check && cargo clippy --all-targets -- -D warnings && cargo test)
# `hatch test` compila la extensión en su entorno antes de cada corrida (host/pyproject.toml).
(cd host && hatch fmt --check && hatch test)
echo "check: ok"
