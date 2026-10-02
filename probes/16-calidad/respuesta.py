"""La respuesta de cada parlante en el micrófono, con su coherencia (experimentos/15 §C).

Usa la calibración del servicio: `calibrate` emite un ruido rosa **independiente** por parlante,
todos a la vez, a amplitud 0,1, y `calibration_dump` deja la grabación y las referencias en un
`.npz`. El análisis (`analisis.respuesta_multiple`) resuelve las tres respuestas juntas y da a
cada una la coherencia que tendría sonando sola con el ruido que de verdad quedó; corrige la
deriva de reloj trozo a trozo. Se repite con segmentos de 8192, 16384 y 32768: la curva no
debería moverse más de 0,5 dB (un parámetro que no debería importar, CLAUDE.md).

La ecualización en uso entra en lo que se mide (la calibración pasa por ella): por defecto se
apaga mientras dura (`eq` = `off`) y se vuelve a dejar como estaba, también con Ctrl-C.

Se corre **tres veces, con el micrófono en tres lugares** (`--colocacion 1|2|3`), y
`comparar.py` aplica el criterio: ±1,5 dB entre colocaciones de 100 Hz a 8 kHz donde γ² ≥ 0,9.

Uso (PC-Ryzen5; en el Mac funciona entero contra `aurasync service --simular`):

    $PY probes/16-calidad/respuesta.py --colocacion 1 --microfono-en "punto de escucha" --segundos 20
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

import numpy as np

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI))
import analisis as A  # noqa: E402
import servicio as V  # noqa: E402
import sistema as S  # noqa: E402

SEGMENTOS = (8192, 16384, 32768)


def analizar(npz: Path) -> dict:
    datos = np.load(npz)
    grabacion = np.asarray(datos["recording"], dtype=float).ravel()
    nombres = [str(n) for n in datos["names"]]
    referencias = [np.asarray(r, dtype=float) for r in datos["references"]]
    sr = int(datos["rate"])
    if sr != S.SR:
        msg = f"la calibración está a {sr} Hz; el análisis espera {S.SR}"
        raise ValueError(msg)
    ubicacion = [A.desfase_y_deriva(grabacion, r, sr) for r in referencias]
    desfases = [u["desfase"] for u in ubicacion]
    derivas = [u["deriva"] for u in ubicacion]
    por_segmento = {
        s: A.respuesta_por_tercio(A.respuesta_multiple(grabacion, referencias, desfases, derivas, s, sr))
        for s in SEGMENTOS
    }
    parlantes = {}
    for i, nombre in enumerate(nombres):
        principal = por_segmento[16384]["parlantes"][i]
        parlantes[nombre] = {
            "desfase_s": desfases[i] / sr,
            "deriva_ppm": derivas[i] * 1e6,
            "trozos": ubicacion[i]["trozos"],
            "trozos_descartados": ubicacion[i]["descartados"],
            "db": principal["db"],
            "normalizada_db": principal["normalizada_db"],
            "coherencia": principal["coherencia"],
            "por_segmento": {str(s): por_segmento[s]["parlantes"][i]["normalizada_db"] for s in SEGMENTOS},
            "estabilidad": A.estabilidad_entre_segmentos(por_segmento, i),
        }
    return {"centros_hz": A.TERCIOS, "parlantes": parlantes, "aplicado": datos["applied"].tolist()}


def main(argv: list[str] | None = None) -> int:  # noqa: PLR0915
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument(
        "--colocacion", required=True, help="1, 2, 3: dónde está el micrófono (y describilo en --microfono-en)"
    )
    p.add_argument("--segundos", type=float, default=20.0, help="duración de la calibración (5 a 20 s)")
    p.add_argument("--amplitud", type=float, default=0.1)
    p.add_argument("--con-eq", action="store_true", help="medir con la ecualización en uso (por defecto se apaga)")
    p.add_argument("--analizar", type=Path, help="solo analizar un .npz ya guardado")
    p.add_argument("--puerto", type=int)
    p.add_argument("--config", type=Path)
    S.agregar_anotaciones(p)
    args = p.parse_args(argv)
    amplitud = S.limitar_amplitud(args.amplitud)
    base = S.ruta_datos("15", f"respuesta-c{args.colocacion}-s{args.sesion}")
    registro: dict = {"tipo": "respuesta", "entorno": S.entorno(), **S.anotaciones(args), "colocacion": args.colocacion}

    if args.analizar:
        registro["npz"] = str(args.analizar)
        registro["resultado"] = analizar(args.analizar)
        imprimir(registro["resultado"])
        print(S.guardar_json(base.with_suffix(".json"), registro))
        return 0

    try:
        srv = V.Servicio.conectar(args.puerto, config=args.config, registro=base.with_suffix(".api.jsonl"))
        estado = srv.sesion_sonando()
    except V.ErrorServicio as e:
        print(f"✗ {e}", file=sys.stderr)
        return 2
    registro["estado"] = {k: estado.get(k) for k in ("global", "speakers", "chain_summary", "service", "config")}
    registro["microfono_del_servicio"] = estado.get("microphones")
    if shutil.which("pactl"):
        vol = S.VolumenParlante()
        registro["volumen_avrcp_pct"] = {sp["name"]: vol.leer(sp["sink"]) for sp in estado["speakers"]}
    else:
        registro["volumen_avrcp_pct"] = "sin pactl (simulación)"

    with S.Restaurador(None) as restaurador:
        try:
            eq_antes = srv.etapa("eq")
            registro["eq_antes"] = {"value": eq_antes["value"], "chosen": eq_antes["chosen"]}
            if not args.con_eq and eq_antes["value"]["algorithm"] != "off":
                restaurador.cambio(
                    "eq apagada",
                    "chain_set eq como estaba",
                    lambda: srv.restaurar_etapa("eq", eq_antes["chosen"]),
                    sistema=False,
                )
                srv.orden("chain_set", stage="eq", algorithm="off")
            print(
                f"  · calibrando {args.segundos:.0f} s a amplitud {amplitud} (ruido independiente por parlante)…",
                flush=True,
            )
            srv.orden("calibrate", seconds=args.segundos, amplitude=amplitud)
            estado = srv.esperar(
                lambda e: (e.get("calibration") or {}).get("state") in {"done", "error"},
                args.segundos + 40,
                "la calibración",
            )
            registro["calibracion"] = estado["calibration"]
            if estado["calibration"]["state"] != "done":
                print(
                    f"  ⚠ la calibración terminó en error: {estado['calibration'].get('error')}; se analiza igual",
                    file=sys.stderr,
                )
            npz = Path(srv.orden("calibration_dump")["path"])
            copia = base.with_suffix(".npz")
            shutil.copyfile(npz, copia)
            registro["npz"] = copia.name
            registro["npz_original"] = str(npz)
        except V.ErrorServicio as e:
            registro["error"] = str(e)
            print(f"✗ {e}", file=sys.stderr)
        except (KeyboardInterrupt, SystemExit) as e:
            # Ctrl-C o SIGTERM: se restaura (finally) y se guarda lo hecho, marcado.
            registro["error"] = f"interrumpido ({type(e).__name__})"
            print("\n✗ interrumpido: restauro y guardo lo hecho", file=sys.stderr)
        finally:
            registro["restauracion"] = restaurador.restaurar()
    if "error" in registro:
        S.guardar_json(base.with_suffix(".json"), registro)
        return 1
    registro["resultado"] = analizar(copia)
    servicio_db = {r["speaker"]: r.get("response_db") for r in registro["calibracion"].get("results", [])}
    registro["respuesta_del_servicio_db"] = servicio_db
    imprimir(registro["resultado"])
    print(f"\n{S.guardar_json(base.with_suffix('.json'), registro)}")
    return 0


def imprimir(resultado: dict) -> None:
    cs = resultado["centros_hz"]
    marcas = [k for k, c in enumerate(cs) if c in {cs[i] for i in range(0, len(cs), 3)}]
    print("\n                   " + " ".join(f"{int(cs[k]):>6}" for k in marcas))
    for nombre, r in resultado["parlantes"].items():
        print(f"{nombre[:16]:16} dB " + " ".join(f"{r['normalizada_db'][k]:6.1f}" for k in marcas))
        print(f"{'':16} γ² " + " ".join(f"{r['coherencia'][k]:6.2f}" for k in marcas))
        e = r["estabilidad"]
        print(
            f"{'':16} deriva {r['deriva_ppm']:+.1f} ppm · entre segmentos {e['max_db']:.2f} dB en "
            f"{e['tercios_validos']} tercios {'✓' if e['ok'] else '✗'}"
        )


if __name__ == "__main__":
    sys.exit(main())
