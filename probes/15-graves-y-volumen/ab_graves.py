"""A/B ciego del bajo psicoacústico, con la sonoridad igualada (experimentos/14 §C).

Dos presets que difieren **solo** en la etapa `bass`: `off` contra `protect` (corte 90 Hz, orden
4) con armónicos en `--armonicos` dB (por defecto 0 dB: el punto en que la energía de armónicos
iguala a la del grave que quita el pasa-altos, así la igualación de sonoridad tiene menos que
corregir). Si los presets no existen, se crean por la API desde el estado actual (y se dice); si
existen, se comprueba cargándolos que difieran solo en `bass`.

Usa el A/B del servicio (`ab_start` con `match_loudness: true`): antes del primer ensayo suena A
y B lo necesario para que el servicio mida y compense la sonoridad (|ΔLU| ≤ 0,5). En cada
ensayo la persona escucha A, B y X cuantas veces quiera, dice si X es A o B (ABX) y cuál
**prefiere** (comparación pareada). Qué preset es A y cuál B se sortea en cada sesión y no se
muestra hasta el final; los aciertos tampoco.

Criterio (spec §8): "se oye" con ≥ 20 de 30 en **cada una** de 2 sesiones (p ≤ 0,05 binomial
exacta, una cola); "mejor" con la preferencia por ensayo (prueba de signos), y solo si se oye.

Al terminar (también con Ctrl-C) detiene el A/B, vuelve al estado previo (un preset temporal
`_ab-graves-antes`, que se borra) y, si se usó `--wav`, devuelve la fuente.

Uso (PC-Ryzen5, con música sonando por aurasync al volumen de siempre):

    $PY probes/15-graves-y-volumen/ab_graves.py --sesion 1 --ensayos 30

Teclas en cada ensayo: `a`, `b`, `x` (escuchar), `xa` / `xb` (X es A / X es B), después `pa` /
`pb` (preferís A / B), `q` (terminar antes; se guarda lo hecho).
"""

from __future__ import annotations

import argparse
import secrets
import sys
import time
from pathlib import Path

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI))
sys.path.append(str(AQUI.parent / "16-calidad"))
import analisis as A  # noqa: E402
import servicio as V  # noqa: E402
import sistema as S  # noqa: E402

ANTES = "_ab-graves-antes"
ESPERA_MEDICION_S = 4.5
"""El servicio mide la sonoridad de A o B desde 3,3 s después de cada cambio (AB_SETTLE_S)."""


def nombres(corte: float, armonicos: float) -> dict[str, str]:
    return {"off": "ab-graves-off", "protect": f"ab-graves-protect-{corte:g}hz-{armonicos:+g}db"}


def crear_presets(srv: V.Servicio, presets: dict[str, str], corte: float, armonicos: float) -> None:
    """Los dos presets desde el estado actual, cambiando solo `bass`; `bass` queda como estaba."""
    bass_antes = srv.etapa("bass")["chosen"]
    try:
        srv.orden("chain_set", stage="bass", algorithm="off")
        srv.orden("preset_save", name=presets["off"])
        srv.orden(
            "chain_set",
            stage="bass",
            algorithm="protect",
            params={"cutoff_hz": corte, "order": 4, "harmonics_db": armonicos},
        )
        srv.orden("preset_save", name=presets["protect"])
    finally:
        srv.restaurar_etapa("bass", bass_antes)


def foto(srv: V.Servicio) -> dict:
    cadena = {s["id"]: s["value"] for s in srv.orden("chain")["stages"] if s["id"] != "volume"}
    estado = srv.estado()
    parlantes = {p["name"]: {k: p[k] for k in ("pan", "ambience", "gain_db")} for p in estado["speakers"]}
    return {
        "cadena": cadena,
        "parlantes": parlantes,
        "global": {k: estado["global"][k] for k in ("rear_delay_ms", "extract_ambience", "decorrelate")},
    }


def diferencias(a: dict, b: dict) -> list[str]:
    out = [f"cadena.{k}" for k in a["cadena"] if a["cadena"][k] != b["cadena"].get(k)]
    out += ["parlantes"] if a["parlantes"] != b["parlantes"] else []
    out += ["global"] if a["global"] != b["global"] else []
    return out


def igualar(srv: V.Servicio, salida) -> dict:
    """Suena A y B hasta que el servicio midió las dos y quedaron dentro de 0,5 LU."""
    ab = {}
    for vuelta in range(4):
        for lado in ("a", "b"):
            srv.orden("ab_play", which=lado)
            salida(f"  · igualando sonoridad: suena {lado.upper()} ({vuelta + 1})…")
            time.sleep(ESPERA_MEDICION_S)
        ab = srv.estado()["ab"] or {}
        diff = (ab.get("loudness_lu") or {}).get("diff")
        if diff is not None and abs(diff) <= 0.5:  # noqa: PLR2004
            break
    return {"loudness_lu": ab.get("loudness_lu"), "compensation_db": ab.get("compensation_db")}


def ensayos(srv: V.Servicio, n: int, leer, salida) -> list[dict]:  # noqa: C901
    """El bucle de la persona. No muestra aciertos ni qué es X."""
    hechos: list[dict] = []
    while len(hechos) < n:
        salida(f"\nensayo {len(hechos) + 1} de {n}: a / b / x para escuchar, xa / xb para responder, q para terminar")
        escuchas: list[str] = []
        respuesta = None
        while respuesta is None:
            linea = leer()
            if linea is None or linea == "q":
                return hechos
            if linea in {"a", "b", "x"}:
                srv.orden("ab_play", which=linea)
                escuchas.append(linea)
                salida(f"  suena {linea.upper()}")
            elif linea in {"xa", "xb"}:
                if "x" not in escuchas:
                    salida("  primero escuchá X")
                    continue
                respuesta = linea[1]
            elif linea:
                salida("  ? a, b, x, xa, xb o q")
        resultado = srv.orden("ab_answer", x_is=respuesta)
        preferida = None
        while preferida is None:
            salida("  ¿cuál preferís? pa / pb")
            linea = leer()
            if linea is None or linea == "q":
                hechos.append(
                    {
                        "respuesta": respuesta,
                        "escuchas": escuchas,
                        "verdad": resultado["truth"],
                        "acierto": resultado["correct"],
                        "prefiere": None,
                    }
                )
                return hechos
            if linea in {"pa", "pb"}:
                preferida = linea[1]
        hechos.append(
            {
                "respuesta": respuesta,
                "escuchas": escuchas,
                "verdad": resultado["truth"],
                "acierto": resultado["correct"],
                "prefiere": preferida,
                "t": time.time(),
            }
        )
        salida("  anotado.")
    return hechos


def main(argv: list[str] | None = None) -> int:  # noqa: C901, PLR0915
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--ensayos", type=int, default=30)
    p.add_argument("--armonicos", type=float, default=0.0, help="harmonics_db del preset protect (-24 = ninguno)")
    p.add_argument("--corte", type=float, default=90.0)
    p.add_argument("--recrear", action="store_true", help="volver a crear los presets desde el estado actual")
    p.add_argument("--wav", type=Path, help="poner esta música como fuente durante el A/B")
    p.add_argument("--puerto", type=int)
    p.add_argument("--config", type=Path)
    S.agregar_anotaciones(p)
    args = p.parse_args(argv)
    base = S.ruta_datos("14", f"ab-graves-s{args.sesion}")
    presets = nombres(args.corte, args.armonicos)

    def leer() -> str | None:
        try:
            return input("> ").strip().lower()
        except EOFError:
            return None

    def salida(texto: str) -> None:
        print(texto, flush=True)

    try:
        srv = V.Servicio.conectar(args.puerto, config=args.config, registro=base.with_suffix(".api.jsonl"))
        estado = srv.sesion_sonando()
        if estado.get("ab") and estado["ab"].get("active"):
            msg = "ya hay un A/B corriendo: terminalo en el panel"
            raise S.FaltaSistema(msg)
    except (S.FaltaSistema, V.ErrorServicio) as e:
        print(f"✗ {e}", file=sys.stderr)
        return 2

    existentes = set(srv.orden("presets")["presets"])
    registro: dict = {
        "tipo": "ab_graves",
        "entorno": S.entorno(),
        **S.anotaciones(args),
        "presets": presets,
        "armonicos_db": args.armonicos,
        "corte_hz": args.corte,
        "fuente_previa": estado.get("source"),
        "simulado": bool(estado["service"].get("simulated")),
    }
    with S.Restaurador(None) as r:
        try:
            srv.orden("preset_save", name=ANTES)
            # Se deshace al revés de como se anota: primero se carga ANTES y después se borra.
            r.cambio(
                "preset temporal",
                f"preset_delete {ANTES}",
                lambda: srv.orden("preset_delete", name=ANTES),
                sistema=False,
            )
            r.cambio(
                "estado previo", f"preset_load {ANTES}", lambda: srv.orden("preset_load", name=ANTES), sistema=False
            )
            faltan = [n for n in presets.values() if n not in existentes]
            if faltan or args.recrear:
                salida(f"  · creo los presets {list(presets.values())} desde el estado actual (cambia solo bass)")
                crear_presets(srv, presets, args.corte, args.armonicos)
                registro["presets_creados"] = True
            fotos = {}
            for clave, nombre in presets.items():
                srv.orden("preset_load", name=nombre)
                time.sleep(0.4)
                fotos[clave] = foto(srv)
            srv.orden("preset_load", name=ANTES)
            time.sleep(0.4)
            distintas = diferencias(fotos["off"], fotos["protect"])
            registro["diferencias_entre_presets"] = distintas
            if distintas != ["cadena.bass"]:
                msg = f"los presets difieren en {distintas}, no solo en bass: --recrear"
                raise S.FaltaSistema(msg)
            contra_actual = [d for d in diferencias(fotos["off"], foto(srv)) if d != "cadena.bass"]
            registro["presets_contra_estado_actual"] = contra_actual
            if contra_actual:
                salida(
                    f"  ⚠ fuera de bass, los presets no son el estado actual ({contra_actual}): se compara otra cosa; --recrear"
                )
            if args.wav:
                previa = estado.get("source") or {}
                r.cambio(
                    "fuente",
                    f"source {previa.get('kind')}",
                    lambda: srv.orden(
                        "source",
                        kind=previa.get("kind") or "system",
                        **({"name": previa["name"]} if previa.get("name") else {}),
                    ),
                    sistema=False,
                )
                srv.orden("source", kind="file", name=str(args.wav.resolve()))
            # Qué preset es A se sortea; se guarda y no se muestra.
            a, b = (
                (presets["off"], presets["protect"]) if secrets.randbelow(2) else (presets["protect"], presets["off"])
            )
            registro["a"], registro["b"] = a, b
            r.cambio(
                "A/B",
                "ab_stop",
                lambda: srv.orden("ab_stop") if (srv.estado().get("ab") or {}).get("active") else None,
                sistema=False,
            )
            srv.orden("ab_start", a=a, b=b, match_loudness=True)
            registro["igualacion"] = igualar(srv, salida)
            diff = (registro["igualacion"]["loudness_lu"] or {}).get("diff")
            salida(
                f"  sonoridad A − B igualada: Δ {diff} LU"
                if diff is not None
                else "  ⚠ el servicio no midió la sonoridad de A y B"
            )
            registro["ensayos"] = ensayos(srv, args.ensayos, leer, salida)
            registro["ab_final"] = srv.orden("ab_stop")
        except (S.FaltaSistema, V.ErrorServicio) as e:
            registro["error"] = str(e)
            print(f"✗ {e}", file=sys.stderr)
        except (KeyboardInterrupt, SystemExit) as e:
            # Ctrl-C o SIGTERM: se restaura (finally) y se guarda lo hecho, marcado.
            registro["error"] = f"interrumpido ({type(e).__name__})"
            print("\n✗ interrumpido: restauro y guardo lo hecho", file=sys.stderr)
        finally:
            registro["restauracion"] = r.restaurar()

    hechos = registro.get("ensayos") or []
    if hechos:
        aciertos = sum(1 for h in hechos if h["acierto"])
        a_es = "off" if registro["a"] == presets["off"] else "protect"
        a_preset = {"a": a_es, "b": "protect" if a_es == "off" else "off"}
        elecciones = [a_preset[h["prefiere"]] for h in hechos if h["prefiere"]]
        registro["resumen"] = {
            "aciertos": aciertos,
            "ensayos": len(hechos),
            "p_binomial": A.p_binomial(aciertos, len(hechos)),
            "aciertos_para_p05": A.aciertos_minimos(len(hechos)),
            "se_oye": len(hechos) >= 30 and A.p_binomial(aciertos, len(hechos)) <= 0.05,  # noqa: PLR2004
            "a_era": a_es,
            "preferencia": A.preferencia(elecciones, "protect"),
            "preferencia_en_aciertos": A.preferencia(
                [a_preset[h["prefiere"]] for h in hechos if h["prefiere"] and h["acierto"]], "protect"
            ),
        }
        s = registro["resumen"]
        print(
            f"\n{aciertos} de {len(hechos)} (p = {s['p_binomial']:.4f}; hacen falta {s['aciertos_para_p05']}) · A era {a_es}"
        )
        print(
            f"prefirió protect {s['preferencia']['veces']} de {s['preferencia']['de']} (p dos colas {s['preferencia']['p_dos_colas']:.3f})"
        )
    print(S.guardar_json(base.with_suffix(".json"), registro))
    return 1 if "error" in registro else 0


if __name__ == "__main__":
    sys.exit(main())
