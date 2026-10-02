"""Los criterios de experimentos/14 sobre varias sesiones: un resultado cuenta si se repite.

- `curva_avrcp`: por parlante, monótona en cada sesión, con la ida y la vuelta dentro de 0,5 dB, y
  repetible a ±0,5 dB entre dos sesiones independientes (otra colocación del micrófono).
- `proteccion`: por parlante, con `protect` la caída de 125 Hz se retrasa o desaparece en **las
  dos** sesiones (y en ninguna se adelanta); "inconcluso" si sin protección no hubo caída.
- `ab_graves`: ≥ 20 de 30 en **cada una** de 2 sesiones para "se oye"; la preferencia de todas
  las sesiones juntas (prueba de signos) para "mejor", solo si se oye.

Uso:  $PY probes/15-graves-y-volumen/comparar.py docs/research/experimentos/datos/14/*.json
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI))
import analisis as A  # noqa: E402


def curva(corridas: list[dict]) -> dict:
    por: dict[str, list] = defaultdict(list)
    for c in corridas:
        for nombre, r in c["parlantes"].items():
            por[nombre].append(
                (
                    str(c["sesion"]),
                    c.get("microfono_en"),
                    {float(k): v for k, v in r["curva_db"].items()},
                    r["monotona"]["ok"],
                    r["histeresis_db"],
                )
            )
    out = {}
    for nombre, filas in por.items():
        if len(filas) < 2:  # noqa: PLR2004
            out[nombre] = {"cumple": False, "motivo": "hace falta una segunda sesión"}
            continue
        a, b = filas[0], filas[1]
        rep = A.repetibilidad(a[2], b[2])
        otra_colocacion = a[1] != b[1]
        sin_histeresis = max(a[4], b[4]) <= A.REPETIBLE_DB
        out[nombre] = {
            "sesiones": [a[0], b[0]],
            "monotona_en_ambas": a[3] and b[3],
            "histeresis_db": [a[4], b[4]],
            "repetibilidad": rep,
            "otra_colocacion": otra_colocacion,
            "cumple": bool(a[3] and b[3] and sin_histeresis and rep["ok"] and otra_colocacion),
        }
    return out


def proteccion(corridas: list[dict]) -> dict:
    por: dict[str, list] = defaultdict(list)
    for c in corridas:
        if "resultado" in c:
            por[c["parlante"]].append((str(c["sesion"]), c["resultado"]["veredicto"]["125"]))
    out = {}
    for nombre, filas in por.items():
        veredictos = [v for _, v in filas]
        mejora = {"se_retrasa", "desaparece"}
        out[nombre] = {
            "sesiones": filas,
            "cumple": len(filas) >= 2 and all(v in mejora for v in veredictos),  # noqa: PLR2004
            "inconcluso": any(v == "inconcluso" for v in veredictos),
        }
    return out


def ab(corridas: list[dict]) -> dict:
    sesiones = [c for c in corridas if c.get("resumen") and not c.get("simulado")]
    filas = [
        {
            "sesion": str(c["sesion"]),
            "aciertos": c["resumen"]["aciertos"],
            "ensayos": c["resumen"]["ensayos"],
            "p": c["resumen"]["p_binomial"],
        }
        for c in sesiones
    ]
    se_oye = len(filas) >= 2 and all(
        f["ensayos"] >= 30 and f["aciertos"] >= (A.aciertos_minimos(f["ensayos"]) or f["ensayos"] + 1) for f in filas
    )  # noqa: PLR2004
    elecciones = []
    for c in sesiones:
        a_es = c["resumen"]["a_era"]
        mapa = {"a": a_es, "b": "protect" if a_es == "off" else "off"}
        elecciones += [mapa[e["prefiere"]] for e in c["ensayos"] if e.get("prefiere")]
    pref = A.preferencia(elecciones, "protect")
    mejor = None
    if se_oye and pref["p_dos_colas"] <= 0.05:  # noqa: PLR2004
        mejor = "protect" if pref["veces"] > pref["de"] / 2 else "off"
    return {"sesiones": filas, "se_oye": se_oye, "preferencia": pref, "mejor": mejor}


def main(rutas: list[str]) -> int:
    por_tipo: dict[str, list] = defaultdict(list)
    for ruta in rutas:
        datos = json.loads(Path(ruta).read_text())
        if "tipo" in datos and "error" not in datos and not datos.get("ensayo"):
            por_tipo[datos["tipo"]].append(datos)
    salida = {}
    if por_tipo.get("curva_avrcp"):
        salida["curva_avrcp"] = curva(por_tipo["curva_avrcp"])
    if por_tipo.get("proteccion"):
        salida["proteccion"] = proteccion(por_tipo["proteccion"])
    if por_tipo.get("ab_graves"):
        salida["ab_graves"] = ab(por_tipo["ab_graves"])
    print(json.dumps(salida, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
