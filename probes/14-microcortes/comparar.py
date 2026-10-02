"""Compara sesiones de `sesion.py` con el criterio de la spec 2026-10-02 §3.3.

- **Repetible:** dentro de una condición, la tasa de descartes de un parlante se repite si
  todas sus sesiones quedan dentro de un factor 2 (la mayor ≤ 2 × la menor). Dos ceros se
  repiten; un cero contra algo no, salvo que ambas tasas estén bajo `--piso` por minuto.
- **Efecto:** una condición baja los descartes respecto de la base solo si **cada una** de
  sus repeticiones queda por debajo de **cada una** de las de la base (la mayor de la
  condición < la menor de la base). Una sola sesión que baja no cuenta.

Uso:
    python3 probes/14-microcortes/comparar.py docs/research/experimentos/datos/12/*.jsonl [--base base]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PISO = 0.1
"""Descartes por minuto bajo los cuales dos tasas se tratan como iguales (≈ 1 en 10 min)."""


def leer(ruta: Path) -> dict:
    inicio, resumen = None, None
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(linea)
        except ValueError:
            continue
        if r.get("clase") == "inicio":
            inicio = r
        elif r.get("clase") == "resumen":
            resumen = r
    if inicio is None or resumen is None:
        msg = f"{ruta}: le falta {'el inicio' if inicio is None else 'el resumen'}"
        raise ValueError(msg)
    tasas = {n: v.get("drops_por_min") for n, v in (resumen.get("radio_por_parlante") or {}).items()}
    total = sum(t for t in tasas.values() if t is not None) if tasas else None
    return {
        "ruta": ruta,
        "nota": inicio.get("nota"),
        "condicion": inicio.get("condicion") or "base",
        "completa": resumen.get("completa", True),
        "minutos": resumen.get("minutos"),
        "tasas": tasas,
        "total": total,
        "cortes_por_min": resumen.get("cortes_por_min"),
        "cortes_por_causa": resumen.get("cortes_por_causa") or {},
    }


def repetible(tasas: list[float], piso: float = PISO) -> bool:
    if len(tasas) < 2:  # noqa: PLR2004
        return False
    menor, mayor = min(tasas), max(tasas)
    if mayor <= piso:
        return True
    if menor <= 0:
        return False
    return mayor <= 2 * menor


def efecto(base: list[float], condicion: list[float]) -> str:
    if len(base) < 2 or len(condicion) < 2:  # noqa: PLR2004
        return "faltan repeticiones (hacen falta 2 de cada una)"
    if max(condicion) < min(base):
        return "BAJA en ambas repeticiones"
    if min(condicion) > max(base):
        return "SUBE en ambas repeticiones"
    return "no cambia de forma repetible"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("sesiones", nargs="+", type=Path)
    p.add_argument("--base", default="base", help="la condición contra la que se compara")
    p.add_argument("--piso", type=float, default=PISO)
    args = p.parse_args(argv)

    try:
        sesiones = [leer(r) for r in args.sesiones]
    except (OSError, ValueError) as e:
        print(f"✗ {e}", file=sys.stderr)
        return 2
    por_condicion: dict[str, list[dict]] = {}
    for s in sesiones:
        por_condicion.setdefault(s["condicion"], []).append(s)

    print("== sesiones ==")
    for s in sesiones:
        tasas = ", ".join(f"{n} {t}" for n, t in sorted(s["tasas"].items())) or "sin radio"
        incompleta = "" if s["completa"] else "  (INCOMPLETA)"
        print(f"  [{s['condicion']}] {s['nota']}: {s['minutos']} min · radio/min: {tasas}{incompleta}")
        if s["cortes_por_causa"]:
            print(f"      cortes: {s['cortes_por_causa']}")

    def series(grupo: list[dict], parlante: str | None) -> list[float]:
        if parlante is None:
            return [s["total"] for s in grupo if s["total"] is not None]
        return [s["tasas"][parlante] for s in grupo if s["tasas"].get(parlante) is not None]

    parlantes = sorted({n for s in sesiones for n in s["tasas"]})
    print("\n== repetibilidad (factor 2 dentro de cada condición) ==")
    for condicion, grupo in sorted(por_condicion.items()):
        for parlante in [None, *parlantes]:
            valores = series(grupo, parlante)
            if not valores:
                continue
            veredicto = "se repite" if repetible(valores, args.piso) else "NO se repite"
            print(f"  [{condicion}] {parlante or 'total':<20} {valores} → {veredicto}")

    base = por_condicion.get(args.base)
    if not base:
        print(f"\n(no hay sesiones de la condición '{args.base}': no hay contra qué comparar)")
        return 0
    print(f"\n== efecto contra '{args.base}' (cuenta solo si baja en ambas repeticiones) ==")
    for condicion, grupo in sorted(por_condicion.items()):
        if condicion == args.base:
            continue
        for parlante in [None, *parlantes]:
            b, c = series(base, parlante), series(grupo, parlante)
            if not b and not c:
                continue
            print(f"  [{condicion}] {parlante or 'total':<20} base {b} vs {c} → {efecto(b, c)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
