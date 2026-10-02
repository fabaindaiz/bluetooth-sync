"""Campaña de pruebas contra el servicio vivo: solo órdenes por la API, sin reiniciar nada.

Fases (se eligen por argumento; por defecto todas menos E):
  A  calibrar, aplicar y volver a calibrar: lo que queda tiene que ser ~0 (prueba de cierre)
  B  +4 ms a propósito en un parlante: la calibración tiene que encontrarlo, y aplicar lo deshace
  C  -6 dB a propósito en otro: lo mismo con la ganancia
  D  dos calibraciones seguidas sin cambiar nada: cuánto se repite la medición
  E  con música sonando: +5 ms a propósito con el lazo apagado; se enciende el lazo y se
     anota qué propone en N ciclos. No aplica nada que el lazo no confirme dos veces.

Cada calibración emite ruido a amplitud 0,1 (nunca más de 0,2) durante `--segundos`, y corta
lo que esté sonando mientras dura. Todo queda en datos/10/campana-<hora>.jsonl.

Uso: python3 campana.py [A B C D E] [--segundos 10] [--ciclos 4]
"""

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

AQUI = Path(__file__).resolve().parent
DATOS = AQUI.parents[1] / "docs/research/experimentos/datos/10"
CONF = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "aurasync"
URL = f"http://127.0.0.1:{os.environ.get('PUERTO', '8731')}/v1"
TOKEN = json.loads((CONF / "service.json").read_text())["token"]
AMPLITUD = 0.1
sys.path.insert(0, str(AQUI))
import protocolo  # noqa: E402
REGISTRO = DATOS / f"campana-{datetime.now().strftime('%Y%m%d-%H%M%S')}.jsonl"


def anotar(clase: str, **datos) -> None:
    DATOS.mkdir(parents=True, exist_ok=True)
    with REGISTRO.open("a") as f:
        f.write(json.dumps({"t": time.time(), "clase": clase, **datos}, ensure_ascii=False) + "\n")


def orden(op: str, **args) -> dict:
    cuerpo = json.dumps({"v": 1, "op": op, **args}).encode()
    pedido = urllib.request.Request(f"{URL}/command", data=cuerpo, method="POST", headers={"Authorization": f"Bearer {TOKEN}"})
    try:
        with urllib.request.urlopen(pedido, timeout=40) as r:
            respuesta = json.loads(r.read())
    except urllib.error.HTTPError as e:
        respuesta = json.loads(e.read())
    anotar("api", op=op, args=args, respuesta=respuesta)
    if not respuesta["ok"]:
        raise RuntimeError(f"{op}: {respuesta['error']['code']}: {respuesta['error']['message']}")
    return respuesta["result"]


def estado() -> dict:
    return orden("state")


def parlantes() -> dict:
    return {p["name"]: p for p in estado()["speakers"]}


def esperar(condicion, limite: float, que: str) -> None:
    fin = time.monotonic() + limite
    while time.monotonic() < fin:
        if condicion():
            return
        time.sleep(0.3)
    raise RuntimeError(f"no pasó en {limite:.0f} s: {que}")


def calibrar(segundos: float, nota: str) -> dict:
    print(f"  · calibrando {segundos:.0f} s ({nota})…", flush=True)
    anotar("protocolo", momento="antes", nota=nota, foto=protocolo.foto())
    orden("calibrate", seconds=segundos, amplitude=AMPLITUD)
    esperar(lambda: (estado()["calibration"] or {}).get("state") in {"done", "error"}, segundos + 30, "la calibración")
    cal = estado()["calibration"]
    anotar("calibracion", nota=nota, calibracion=cal)
    anotar("protocolo", momento="despues", nota=nota, foto=protocolo.foto())
    if cal["state"] != "done":
        raise RuntimeError(f"la calibración falló: {cal['error']}")
    with_note = orden("measurement_save", note=nota)
    print(f"    guardada en {Path(with_note['path']).name}")
    filas = {r["speaker"]: r for r in cal["results"]}
    for n, r in filas.items():
        marca = " no suena" if r["silent"] else (" dudoso" if r["doubtful"] else "")
        print(f"    {n:<16} retardo {r['delay_ms']:+7.2f} ms  ganancia {r['gain_db']:+6.2f} dB  "
              f"estabilidad {r['stability_ms']:5.2f} ms  (aplicado {r['applied_delay_ms']:.2f} ms, {r['applied_gain_db']:+.2f} dB){marca}")
    print(f"    confiable: {cal['reliable']}")
    return filas


def aplicar() -> None:
    cal = {r["speaker"]: r for r in estado()["calibration"]["results"]}
    esperado = {n: r["applied_delay_ms"] + r["delay_ms"] for n, r in cal.items()}
    piso = min(esperado.values())
    esperado = {n: v - piso for n, v in esperado.items()}
    orden("calibration_apply")
    # Verificado, no supuesto: la primera campaña "aplicó" y nada cambió.
    esperar(lambda: all(abs(p["delay_ms"] - esperado[n]) < 0.01 for n, p in parlantes().items()), 5, "que aplicar cambie los retardos")
    time.sleep(0.5)  # el corte de 160 ms
    print("    aplicado: " + ", ".join(f"{n} {p['delay_ms']:.2f} ms {p['gain_db']:+.2f} dB" for n, p in parlantes().items()))


def relativo(filas: dict, a: str, b: str, campo: str) -> float:
    return filas[a][campo] - filas[b][campo]


def veredicto(nombre: str, medido: float, esperado: float, tolerancia: float, unidad: str) -> bool:
    ok = abs(medido - esperado) <= tolerancia
    print(f"  {'✓' if ok else '✗'} {nombre}: {medido:+.2f} {unidad} (esperado {esperado:+.2f} ± {tolerancia} {unidad})")
    anotar("veredicto", nombre=nombre, medido=medido, esperado=esperado, tolerancia=tolerancia, ok=ok)
    return ok


def preparar() -> list[str]:
    s = estado()
    if s["session"]["status"] != "playing":
        orden("start")
    if s["recalibration"]["active"]:
        orden("recalibrate", active=False)
    return list(parlantes())


def fase_a(seg: float) -> None:
    print("\nA · calibración y cierre")
    calibrar(seg, "A1: desde las correcciones actuales")
    aplicar()
    filas = calibrar(seg, "A2: cierre, después de aplicar A1")
    peor_r = max(abs(r["delay_ms"]) for r in filas.values())
    peor_g = max(abs(r["gain_db"]) for r in filas.values())
    veredicto("cierre, retardo residual máximo", peor_r, 0.0, 0.5, "ms")
    veredicto("cierre, ganancia residual máxima", peor_g, 0.0, 1.0, "dB")


def fase_b(seg: float, nombres: list[str]) -> None:
    print("\nB · un retraso puesto a propósito")
    victima, otro = nombres[1], nombres[0]
    base = parlantes()[victima]["delay_ms"]
    orden("set", speaker=victima, changes={"delay_ms": round(base + 4.0, 3)})
    time.sleep(1.0)
    filas = calibrar(seg, f"B: +4 ms a propósito en {victima}")
    veredicto(f"{victima} respecto de {otro}", relativo(filas, victima, otro, "delay_ms"), -4.0, 0.5, "ms")
    aplicar()
    filas = calibrar(seg, "B2: cierre después de deshacerlo")
    veredicto("cierre después de B, retardo residual máximo", max(abs(r["delay_ms"]) for r in filas.values()), 0.0, 0.5, "ms")


def fase_c(seg: float, nombres: list[str]) -> None:
    print("\nC · una ganancia puesta a propósito")
    victima, otro = nombres[0], nombres[2]
    base = parlantes()[victima]["gain_db"]
    orden("set", speaker=victima, changes={"gain_db": round(base - 6.0, 2)})
    time.sleep(1.0)
    filas = calibrar(seg, f"C: -6 dB a propósito en {victima}")
    veredicto(f"{victima} respecto de {otro} (ganancia)", relativo(filas, victima, otro, "gain_db"), 6.0, 1.0, "dB")
    aplicar()


def fase_d(seg: float) -> None:
    print("\nD · repetibilidad sin cambiar nada")
    a = calibrar(seg, "D1")
    b = calibrar(seg, "D2")
    for n in a:
        veredicto(f"{n}: D2 − D1 retardo", b[n]["delay_ms"] - a[n]["delay_ms"], 0.0, 0.3, "ms")
        veredicto(f"{n}: D2 − D1 ganancia", b[n]["gain_db"] - a[n]["gain_db"], 0.0, 0.5, "dB")


def fase_e(ciclos: int, nombres: list[str]) -> None:
    print("\nE · el lazo contra la música, con +5 ms a propósito")
    s = estado()
    if not s["health"]["input_active"]:
        print("  ✗ no entra audio: poné música en la salida «aurasync» y repetí con E")
        return
    victima = nombres[1]
    base = parlantes()[victima]["delay_ms"]
    orden("set", speaker=victima, changes={"delay_ms": round(base + 5.0, 3)})
    desde = estado()["logs_last"]
    orden("recalibrate", active=True)
    periodo = s["config"]["recalibrate_every_s"]
    print(f"  · lazo encendido; {ciclos} ciclos de {periodo:.0f} s…", flush=True)
    time.sleep(ciclos * periodo + 15)
    lineas = orden("logs", since=desde, limit=500)["records"]
    decisiones = [r for r in lineas if r["service"] == "recalibration" and "lazo:" not in r["message"]]
    for r in decisiones:
        print(f"    {r['at'][11:19]} {r['message'][:200]}")
    anotar("lazo", victima=victima, inyectado_ms=5.0, decisiones=decisiones, history=estado()["recalibration"]["history"])
    orden("recalibrate", active=False)
    ahora = parlantes()[victima]["delay_ms"]
    corregido = base + 5.0 - ahora
    print(f"  · el lazo movió {victima} {-corregido:+.2f} ms (para deshacerlo tenía que moverlo −5)")
    orden("set", speaker=victima, changes={"delay_ms": round(base, 3)})
    anotar("veredicto", nombre="lazo deshace la inyección", medido=corregido, esperado=5.0, ok=abs(corregido - 5.0) < 1.0)


def llegadas(filas: dict) -> dict:
    """Cuándo llega cada parlante respecto del primero, sin las correcciones aplicadas (ms).

    El residuo es lo que falta *agregar*; lo que el parlante tarda de verdad, más lo que ya se
    le agregó, es `aplicado + residuo` con el signo de la llegada invertido.
    """
    total = {n: r["applied_delay_ms"] + r["delay_ms"] for n, r in filas.items()}
    tope = max(total.values())
    return {n: round(tope - v, 2) for n, v in total.items()}


def latencias_pw() -> dict:
    return {n: (v or {}).get("latencia_ns") for n, v in ((d, x["pipewire"]) for d, x in protocolo.foto().items() if d != "sin_root")}


def fase_p(seg: float, n: int, pausa: float) -> None:
    print(f"\nP · {n} calibraciones seguidas: estabilidad y qué dice el protocolo")
    serie = []
    for i in range(n):
        filas = calibrar(seg, f"P{i + 1}")
        serie.append(llegadas(filas))
        foto = protocolo.foto()
        xr = {d[-5:]: foto[d]["xruns_stream"] for d in protocolo.DIRECCIONES}
        print(f"    llegadas relativas: {serie[-1]}  · xruns de cada pw-play: {xr}")
        time.sleep(pausa)
    for nombre in serie[0]:
        valores = [x[nombre] for x in serie]
        print(f"  {nombre:<16} llegada {min(valores):6.2f} a {max(valores):6.2f} ms (rango {max(valores) - min(valores):.2f})")
    anotar("resumen_p", serie=serie)


def volumen_sink(direccion: str) -> int:
    salida = subprocess.run(["pactl", "get-sink-volume", f"bluez_output.{direccion.replace(':', '_')}.1"], capture_output=True, text=True, check=False).stdout
    return int(salida.split("/")[1].strip().rstrip("%"))


def poner_volumen(direccion: str, porcentaje: int) -> None:
    subprocess.run(["pactl", "set-sink-volume", f"bluez_output.{direccion.replace(':', '_')}.1", f"{porcentaje}%"], check=True)
    time.sleep(1.0)
    real = volumen_sink(direccion)
    if abs(real - porcentaje) > 1:
        raise RuntimeError(f"pedí {porcentaje}% y el sink quedó en {real}%")


def fase_v(seg: float, nombres: list[str]) -> None:
    print("\nV · bajar el volumen por Bluetooth (AVRCP) en un parlante: ¿cambia su retardo? ¿cuántos dB ve el micrófono?")
    victima, direccion = nombres[0], protocolo.DIRECCIONES[0]
    original = volumen_sink(direccion)
    # El volumen de PipeWire es cúbico: dB = 60·log10(porcentaje). -6 dB es ×0,794.
    bajo = round(original * 10 ** (-6 / 60))
    base = calibrar(seg, f"V0: {victima} al {original}%")
    try:
        poner_volumen(direccion, bajo)
        medida = calibrar(seg, f"V1: {victima} al {bajo}% (-6 dB por AVRCP)")
    finally:
        poner_volumen(direccion, original)
    otro = nombres[2]
    cambio_g = (medida[victima]["gain_db"] - medida[otro]["gain_db"]) - (base[victima]["gain_db"] - base[otro]["gain_db"])
    cambio_r = llegadas(medida)[victima] - llegadas(base)[victima]
    veredicto(f"{victima}: el micrófono ve la bajada de volumen (corrección pedida)", cambio_g, 6.0, 1.0, "dB")
    veredicto(f"{victima}: el volumen no mueve su retardo", cambio_r, 0.0, 0.5, "ms")
    anotar("resumen_v", original=original, bajo=bajo, cambio_ganancia_db=cambio_g, cambio_retardo_ms=cambio_r)


def fase_r(seg: float) -> None:
    print("\nR · reiniciar los streams con distintos buffers de pw-play: ¿cambia el desfase entre parlantes?")
    serie = []
    try:
        for latencia in (200, 100, 400, 200):
            orden("set", changes={"player_latency_ms": latencia})
            orden("service_restart", name="session")
            esperar(lambda: estado()["session"]["status"] == "playing", 20, "la sesión")
            time.sleep(3.0)
            filas = calibrar(seg, f"R: buffer {latencia} ms, streams recién abiertos")
            serie.append({"buffer_ms": latencia, "llegadas": llegadas(filas)})
            print(f"    buffer {latencia} ms → llegadas {serie[-1]['llegadas']}")
    finally:
        orden("set", changes={"player_latency_ms": 200})
    anotar("resumen_r", serie=serie)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("fases", nargs="*", default=["A", "B", "C", "D"])
    p.add_argument("--segundos", type=float, default=10.0)
    p.add_argument("--ciclos", type=int, default=4)
    p.add_argument("--repeticiones", type=int, default=6)
    p.add_argument("--pausa", type=float, default=5.0)
    a = p.parse_args()
    nombres = preparar()
    print(f"registro: {REGISTRO}")
    try:
        for fase in a.fases:
            {"A": lambda: fase_a(a.segundos), "B": lambda: fase_b(a.segundos, nombres),
             "C": lambda: fase_c(a.segundos, nombres), "D": lambda: fase_d(a.segundos),
             "E": lambda: fase_e(a.ciclos, nombres),
             "P": lambda: fase_p(a.segundos, a.repeticiones, a.pausa), "V": lambda: fase_v(a.segundos, nombres),
             "R": lambda: fase_r(a.segundos)}[fase.upper()]()
    except RuntimeError as e:
        print(f"  ✗ {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
