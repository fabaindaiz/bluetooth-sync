"""Latencia de punta a punta de un sink, medida con el micrófono, igual en A y en B.

Toca ráfagas de ruido rosa con `pw-play --target <sink>` mientras `pw-record` graba el
micrófono, y busca cada ráfaga en la grabación con `aurasync.medicion.gcc_phat`. El número
incluye el arranque de `pw-play` y de `pw-record`, el sink que se mide (el servicio o la
PoC), el sink combinado, A2DP y el aire hasta el micrófono.

**Qué vale y qué no.** El arranque de los dos procesos es un sesgo desconocido (decenas de
ms), pero es el mismo en todas las corridas porque los comandos son los mismos: la
**diferencia** entre A y B no lo arrastra. Su variación entre corridas sí entra, y por eso se
repite (README §6). Para un número absoluto del motor se mide además la referencia R:
`--sink` directo al nodo `bluez_output.…` del parlante sin retardo agregado.

**Verificación (CLAUDE.md, experimentos/09):** `pw-play` lleva `node.dont-fallback`, y
antes de analizar se comprueba en `pw-dump` que su stream terminó enlazado al sink pedido;
si no, la medición se descarta. `pw-play --target` puede caer en el sink por defecto sin
dar error.

Uso, desde la raíz del repositorio, con el entorno de hatch:

    PY="$(cd host && hatch env find default)/bin/python"
    $PY probes/17-e-s-nativa-rust/latencia.py --sink aurasync_poc --mic <nodo del fifine> \\
        --etiqueta B --salida docs/research/experimentos/datos/<n>/latencia.jsonl
"""

from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
import tempfile
import time
import wave
from datetime import datetime
from pathlib import Path

import numpy as np

from aurasync import estimulos, medicion, sonido

SR = 48000
ANTES_S = 0.3
"""La ventana de búsqueda de cada ráfaga empieza esto antes de donde caería sin latencia:
cubre que `pw-record` arranque más tarde que `pw-play`."""


def estimulo(rafagas: int, periodo_s: float, largo_s: float, nivel_db: float) -> tuple[np.ndarray, list[int]]:
    """Ráfagas de ruido rosa, cada una con su semilla (no se confunden entre sí), con
    rampas de 5 ms. Devuelve la señal y la muestra donde empieza cada ráfaga."""
    largo = int(largo_s * SR)
    rampa = int(0.005 * SR)
    ventana = np.ones(largo)
    ventana[:rampa] = np.linspace(0, 1, rampa)
    ventana[-rampa:] = np.linspace(1, 0, rampa)
    inicio = int(0.5 * SR)
    x = np.zeros(inicio + rafagas * int(periodo_s * SR) + int(0.5 * SR))
    comienzos = []
    for k in range(rafagas):
        s = inicio + k * int(periodo_s * SR)
        x[s : s + largo] = estimulos.ruido_rosa(largo, SR, semilla=1000 + k) * ventana * 10 ** (nivel_db / 20)
        comienzos.append(s)
    return x, comienzos


def escribir_wav(ruta: Path, x: np.ndarray) -> None:
    pcm = (np.clip(x, -1, 1) * 32767).astype("<i2")
    with wave.open(str(ruta), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(np.repeat(pcm, 2).tobytes())


def destino_de(pid: int) -> str | None:
    """El `node.name` al que está enlazado el stream del proceso `pid` (vía su Client,
    como `Reproductor.destinos_reales` en sonido.py)."""
    objetos = json.loads(subprocess.run(["pw-dump"], capture_output=True, text=True, check=True).stdout)
    cliente = nodo = None
    nombres, enlaces = {}, {}
    for o in objetos:
        tipo, p = str(o.get("type", "")), (o.get("info") or {}).get("props") or {}
        if tipo.endswith("Client") and str(p.get("application.process.id")) == str(pid):
            cliente = o["id"]
        elif tipo.endswith("Node"):
            nombres[o["id"]] = p
        elif tipo.endswith("Link") and "link.output.node" in p:
            enlaces[int(p["link.output.node"])] = int(p["link.input.node"])
    for i, p in nombres.items():
        if p.get("client.id") == cliente and str(p.get("media.class", "")).startswith("Stream/Output"):
            nodo = i
    if nodo is None or nodo not in enlaces:
        return None
    return str(nombres.get(enlaces[nodo], {}).get("node.name"))


def medir(a: argparse.Namespace) -> dict:
    x, comienzos = estimulo(a.rafagas, a.periodo_s, a.largo_s, a.nivel_db)
    largo = int(a.largo_s * SR)
    with tempfile.TemporaryDirectory() as d:
        ruta_est, ruta_mic = Path(d) / "estimulo.wav", Path(d) / "mic.wav"
        escribir_wav(ruta_est, x)
        t_rec = time.monotonic()
        grabacion = sonido.grabar(ruta_mic, a.mic, SR)
        time.sleep(1.0)
        t_play = time.monotonic()
        play = subprocess.Popen(
            ["pw-play", "--target", a.sink, "-P", "{ node.dont-fallback = true node.dont-reconnect = true node.dont-move = true }", str(ruta_est)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(0.4)
        llego = destino_de(play.pid)
        play.wait()
        time.sleep(a.cola_s)
        sonido.terminar_grabacion(grabacion)
        if llego != a.sink:
            msg = f"pw-play terminó en {llego!r}, no en {a.sink!r}: la medición no vale"
            raise SystemExit(msg)
        mic = sonido.leer_wav_mono(ruta_mic)

    lanzamiento = (t_play - t_rec) * SR
    por_rafaga = []
    for k, s in enumerate(comienzos):
        desde = int(lanzamiento + s - ANTES_S * SR)
        hasta = desde + int((ANTES_S + a.maximo_ms / 1000) * SR) + largo
        if desde < 0 or hasta > len(mic):
            continue
        est = medicion.gcc_phat(mic[desde:hasta], x[s : s + largo], SR, retardo_maximo_ms=ANTES_S * 1000 + a.maximo_ms)
        por_rafaga.append(round(est.retardo_ms - ANTES_S * 1000, 2))
        print(f"ráfaga {k + 1}: {por_rafaga[-1]:.1f} ms (confianza {est.confianza:.1f})")
    if not por_rafaga:
        msg = "la grabación no cubre ninguna ráfaga"
        raise SystemExit(msg)
    return {
        "t": datetime.now().astimezone().isoformat(timespec="seconds"),
        "equipo": platform.node(),
        "etiqueta": a.etiqueta,
        "sink": a.sink,
        "mic": a.mic,
        "verificado": llego,
        "rafagas_ms": por_rafaga,
        "mediana_ms": float(np.median(por_rafaga)),
        "rango_ms": round(max(por_rafaga) - min(por_rafaga), 2),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--sink", required=True, help="aurasync (A), aurasync_poc (B) o bluez_output.… (R)")
    ap.add_argument("--mic", required=True, help="el node.name del micrófono (fifine)")
    ap.add_argument("--etiqueta", required=True, help="A, B o R, y el número de corrida")
    ap.add_argument("--salida", type=Path, required=True, help="un .jsonl donde agregar el resultado")
    ap.add_argument("--rafagas", type=int, default=5)
    ap.add_argument("--periodo-s", type=float, default=2.0)
    ap.add_argument("--largo-s", type=float, default=0.4)
    ap.add_argument("--nivel-db", type=float, default=-12.0)
    ap.add_argument("--maximo-ms", type=float, default=1500.0, help="la latencia más grande que se busca")
    ap.add_argument("--cola-s", type=float, default=2.0)
    a = ap.parse_args()
    resultado = medir(a)
    a.salida.parent.mkdir(parents=True, exist_ok=True)
    with a.salida.open("a") as f:
        f.write(json.dumps(resultado, ensure_ascii=False) + "\n")
    print(json.dumps(resultado, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
