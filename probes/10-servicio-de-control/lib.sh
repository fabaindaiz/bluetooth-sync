# Funciones comunes de los pasos del experimento 10. Se carga con `source`.
#
# Todo lo que se le pide al servicio queda en $REGISTRO (JSON Lines, una línea por
# llamada, con la hora), para poder cruzar después cada cambio con la grabación del
# micrófono (`clics.py`).
set -uo pipefail

PROBE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RAIZ="$(cd "$PROBE/../.." && pwd)"
HOST="$RAIZ/host"
DATOS="$RAIZ/docs/research/experimentos/datos/10"
TMPD="${TMPDIR:-/tmp}/aurasync-pruebas"
mkdir -p "$DATOS" "$TMPD"

CONF="${XDG_CONFIG_HOME:-$HOME/.config}/aurasync"
PUERTO="${PUERTO:-8731}"
URL="http://127.0.0.1:$PUERTO/v1"
REGISTRO="${REGISTRO:-$DATOS/sesion-$(date +%F).jsonl}"

# El tope de volumen de las pruebas: amplitud 0,2 = -14 dB, y nunca más
# (memoria del proyecto: "0,1 por defecto, nunca más de 0,2").
VOLUMEN_MAXIMO_DB=-14

aurasync() { (cd "$HOST" && hatch run aurasync "$@"); }

# Un proceso lanzado con `&` desde un script de bash nace con SIGINT **ignorada**, y entonces
# `kill -INT` (lo que manda Ctrl-C) no le hace nada: pasó en el paso 1 del 2026-10-01, y
# `run` siguió vivo. Esto restaura SIGINT antes de lanzar, para que el cierre con Ctrl-C se
# pruebe de verdad.
con_ctrl_c() {
  python3 -c 'import os, signal, sys; signal.signal(signal.SIGINT, signal.SIG_DFL); os.execvp(sys.argv[1], sys.argv[1:])' "$@"
}

# El pid del proceso de aurasync (no el de `hatch run`, que lo envuelve).
pid_de() { pgrep -f "bin/aurasync $1" | head -1; }
py() { (cd "$HOST" && hatch run python "$@"); }

token() {
  python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["token"])' "$CONF/service.json"
}

anotar() {  # anotar <clase> <texto>
  python3 - "$REGISTRO" "$1" "$2" <<'EOF'
import json, sys, time
with open(sys.argv[1], "a") as f:
    f.write(json.dumps({"t": time.time(), "clase": sys.argv[2], "texto": sys.argv[3]}, ensure_ascii=False) + "\n")
EOF
  echo "  · $2"
}

# api <MÉTODO> <ruta> [json] — imprime la respuesta y la anota. Devuelve 1 si no fue ok.
api() {
  local metodo="$1" ruta="$2" cuerpo="${3:-}"
  if [[ "$cuerpo" =~ \"volume_db\"[[:space:]]*:[[:space:]]*(-?[0-9.]+) ]]; then
    if python3 -c "import sys; sys.exit(0 if float(sys.argv[1]) > float(sys.argv[2]) else 1)" \
        "${BASH_REMATCH[1]}" "$VOLUMEN_MAXIMO_DB"; then
      echo "  ✗ volume_db ${BASH_REMATCH[1]} supera el tope de $VOLUMEN_MAXIMO_DB dB de las pruebas" >&2
      return 1
    fi
  fi
  local args=(-s -X "$metodo" -H "Authorization: Bearer $(token)")
  [[ -n "$cuerpo" ]] && args+=(-d "$cuerpo")
  local t0 respuesta
  t0=$(date +%s.%N)
  respuesta=$(curl "${args[@]}" "$URL$ruta")
  python3 - "$REGISTRO" "$t0" "$metodo" "$ruta" "$cuerpo" "$respuesta" <<'EOF'
import json, sys
_, reg, t, metodo, ruta, cuerpo, respuesta = sys.argv
try:
    r = json.loads(respuesta)
except ValueError:
    r = {"crudo": respuesta}
with open(reg, "a") as f:
    f.write(json.dumps({"t": float(t), "clase": "api", "metodo": metodo, "ruta": ruta,
                        "cuerpo": json.loads(cuerpo) if cuerpo else None, "respuesta": r},
                       ensure_ascii=False) + "\n")
ok = isinstance(r, dict) and r.get("ok")
detalle = "" if ok else f"  ← {r.get('error', r) if isinstance(r, dict) else r}"
print(f"  {'✓' if ok else '✗'} {metodo} {ruta} {cuerpo}{detalle}")
sys.exit(0 if ok else 1)
EOF
}

# estado — una línea por parlante, la sesión y los avisos.
estado() {
  curl -s -H "Authorization: Bearer $(token)" "$URL/state" | python3 "$PROBE/estado.py"
}

# esperar_sesion <estado> [segundos]
esperar_sesion() {
  local objetivo="$1" limite="${2:-30}" fin=$((SECONDS + ${2:-30}))
  while ((SECONDS < fin)); do
    local actual
    actual=$(curl -s -H "Authorization: Bearer $(token)" "$URL/state" |
      python3 -c 'import json,sys; print(json.load(sys.stdin)["result"]["session"]["status"])' 2>/dev/null)
    [[ "$actual" == "$objetivo" ]] && return 0
    sleep 1
  done
  echo "  ✗ la sesión no llegó a '$objetivo' en $limite s" >&2
  return 1
}

servicio_vivo() { curl -s -o /dev/null -m 2 "$URL/state"; }

nodo_aurasync() { pactl list short sinks | awk '$2 == "aurasync"' | grep -q .; }

pausa() {  # pausa <segundos> <qué escuchar>
  echo "  ⏳ ${1}s — ESCUCHÁ: $2"
  sleep "$1"
}
