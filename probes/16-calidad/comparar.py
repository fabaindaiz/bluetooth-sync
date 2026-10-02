"""Los criterios de experimentos/15 sobre varias corridas: repetición entre sesiones y colocaciones.

- `respuesta`: por parlante, las colocaciones (≥ 2; el protocolo pide 3) dentro de ±1,5 dB de
  100 Hz a 8 kHz donde γ² ≥ 0,9 en todas, y cada una estable entre segmentos (≤ 0,5 dB).
- `directo_vs_motor`: cada canción en cada sesión cumple (|ΔLUFS| ≤ 0,5 LU, ≤ 1 dB por sexto)
  y es medible; hacen falta 3 canciones × 2 sesiones.
- `suma_go4`: el veredicto de cada corrida, y si coinciden.

Uso:  $PY probes/16-calidad/comparar.py docs/research/experimentos/datos/15/*.json
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI))
import analisis as A  # noqa: E402


def _arr(v: list) -> np.ndarray:
    return np.array([np.nan if x is None else x for x in v], dtype=float)


def respuesta(corridas: list[dict]) -> dict:
    por_parlante: dict[str, list] = defaultdict(list)
    for c in corridas:
        for nombre, r in c["resultado"]["parlantes"].items():
            por_parlante[nombre].append((c["colocacion"], c["sesion"], r))
    out = {}
    for nombre, filas in por_parlante.items():
        curvas = [_arr(r["normalizada_db"]) for _, _, r in filas]
        coherencias = [_arr(r["coherencia"]) for _, _, r in filas]
        entre = A.comparar_colocaciones(curvas, coherencias)
        estables = all(r["estabilidad"]["ok"] for _, _, r in filas)
        colocaciones = sorted({str(c) for c, _, _ in filas})
        out[nombre] = {
            **entre,
            "colocaciones": colocaciones,
            "todas_estables": estables,
            "cumple": bool(entre["ok"] and estables and len(colocaciones) >= 3),  # noqa: PLR2004
        }
    return out


def directo_vs_motor(corridas: list[dict]) -> dict:
    tabla: dict[str, dict[str, dict]] = defaultdict(dict)
    for c in corridas:
        for cancion in c.get("canciones", []):
            if "criterio" in cancion:
                r = cancion["resultado"]
                tabla[cancion["wav"]][str(c["sesion"])] = {
                    **cancion["criterio"],
                    "delta_lufs": r["delta_lufs"],
                    "peor_delta_banda_db": r["peor_delta_banda_db"],
                }
    sesiones = {s for filas in tabla.values() for s in filas}
    completo = len(tabla) >= 3 and len(sesiones) >= 2 and all(len(f) >= 2 for f in tabla.values())  # noqa: PLR2004
    todas = all(v["cumple"] for f in tabla.values() for v in f.values())
    return {"canciones": tabla, "completo": completo, "cumple": bool(completo and todas)}


def suma_go4(corridas: list[dict]) -> dict:
    vistos = [
        {"parlante": c["parlante"], "sesion": c["sesion"], "veredicto": c["resultado"]["decision"]["veredicto"]}
        for c in corridas
        if "resultado" in c
    ]
    por_parlante = defaultdict(set)
    for v in vistos:
        por_parlante[v["parlante"]].add(v["veredicto"])
    return {"corridas": vistos, "coinciden": all(len(s) == 1 for s in por_parlante.values())}


def main(rutas: list[str]) -> int:
    por_tipo: dict[str, list] = defaultdict(list)
    for ruta in rutas:
        datos = json.loads(Path(ruta).read_text())
        if "tipo" in datos and "error" not in datos and not datos.get("ensayo"):
            por_tipo[datos["tipo"]].append(datos)
    salida = {}
    if por_tipo.get("respuesta"):
        salida["respuesta"] = respuesta(por_tipo["respuesta"])
    if por_tipo.get("directo_vs_motor"):
        salida["directo_vs_motor"] = directo_vs_motor(por_tipo["directo_vs_motor"])
    if por_tipo.get("suma_go4"):
        salida["suma_go4"] = suma_go4(por_tipo["suma_go4"])
    print(
        json.dumps(
            salida, indent=2, ensure_ascii=False, default=lambda x: x.tolist() if hasattr(x, "tolist") else str(x)
        )
    )
    veredictos = [v.get("cumple") for t in ("directo_vs_motor",) if t in salida for v in [salida[t]]]
    veredictos += [v["cumple"] for v in salida.get("respuesta", {}).values()]
    return 0 if veredictos and all(veredictos) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
