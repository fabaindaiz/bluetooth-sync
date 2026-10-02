"""Todo lo que la capa Bluetooth y PipeWire dicen de cada parlante, en una foto.

Sin root: lo que no se puede leer así (RSSI, potencia de transmisión: `btmgmt conn-info`
responde *Permission Denied*) queda fuera y se dice.

Por parlante:
- BlueZ, transporte A2DP: `Delay` (el retardo que declara el parlante, si soporta Delay
  Reporting), códec, configuración, estado y volumen;
- BlueZ, endpoints remotos: qué códecs y capacidades declara el parlante;
- PipeWire, nodo: la latencia que reporta (`Latency`), `latencyOffsetNsec`, volúmenes
  (`channelVolumes` es el volumen absoluto AVRCP), cuantum y tasa;
- `pw-top`: ERR (xruns) por nodo.

Uso: python3 protocolo.py  (imprime JSON)   ·   import protocolo; protocolo.foto()
"""

import json
import re
import subprocess

DIRECCIONES = ("90:F2:60:75:4A:83", "90:F2:60:DA:66:6D", "90:F2:60:E3:07:39")


def _run(*args: str, timeout: float = 5) -> str:
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


def _prop(path: str, interface: str, name: str):
    salida = _run("busctl", "get-property", "org.bluez", path, interface, name).strip()
    if not salida:
        return None
    tipo, _, valor = salida.partition(" ")
    if tipo == "ay":
        return [int(x) for x in valor.split()[1:]]
    if tipo in {"q", "u", "y", "n", "i", "t", "x"}:
        return int(valor)
    return valor.strip('"')


def _bluez(direccion: str) -> dict:
    base = f"/org/bluez/hci0/dev_{direccion.replace(':', '_')}"
    arbol = _run("busctl", "tree", "org.bluez")
    transportes = sorted(set(re.findall(rf"{base}/sep\d+/fd\d+", arbol)))
    endpoints = sorted(set(re.findall(rf"{base}/sep\d+(?=\s|$)", arbol)))
    return {
        "transportes": [
            {
                "ruta": t,
                **{k: _prop(t, "org.bluez.MediaTransport1", k) for k in ("Delay", "Codec", "Configuration", "State", "Volume")},
            }
            for t in transportes
        ],
        "endpoints_remotos": [
            {"ruta": e, "codec": _prop(e, "org.bluez.MediaEndpoint1", "Codec"), "capacidades": _prop(e, "org.bluez.MediaEndpoint1", "Capabilities")}
            for e in endpoints
        ],
    }


def _pipewire() -> dict:
    nodos = {}
    for o in json.loads(_run("pw-dump") or "[]"):
        info = o.get("info") or {}
        props = info.get("props") or {}
        nombre = str(props.get("node.name", ""))
        if not nombre.startswith("bluez_output."):
            continue
        params = info.get("params") or {}
        latencia = next(iter(params.get("Latency") or []), {})
        props_param = {k: v for p in params.get("Props") or [] for k, v in p.items() if k in {"channelVolumes", "softVolumes", "volume", "latencyOffsetNsec", "mute"}}
        nodos[nombre] = {
            "id": o["id"],
            "estado": info.get("state"),
            "codec": props.get("api.bluez5.codec"),
            "latencia_ns": [latencia.get("minNs"), latencia.get("maxNs")],
            "latencia_quantum": [latencia.get("minQuantum"), latencia.get("maxQuantum")],
            **props_param,
        }
    return nodos


def _xruns() -> tuple[dict, dict]:
    """ERR de pw-top: por sink Bluetooth, y por stream de pw-play según el parlante al que va.

    Los streams de pw-play son los que pueden quedarse sin datos; los sinks, no.
    """
    destino = {}
    for o in json.loads(_run("pw-dump") or "[]"):
        props = (o.get("info") or {}).get("props") or {}
        if props.get("application.name") == "pw-play" and props.get("target.object"):
            destino[str(o["id"])] = props["target.object"]
    salida = _run("pw-top", "-b", "-n", "2", timeout=8).splitlines()
    # La última vuelta de pw-top es la que vale: empieza en el último encabezado.
    inicio = max((i for i, l in enumerate(salida) if l.lstrip().startswith("S   ID")), default=0)
    sinks, streams = {}, {}
    for linea in salida[inicio:]:
        partes = linea.split()
        if len(partes) < 9 or not partes[1].isdigit():  # noqa: PLR2004
            continue
        try:
            err = int(partes[8])
        except ValueError:
            continue
        if partes[-1].startswith("bluez_output."):
            sinks[partes[-1]] = max(err, sinks.get(partes[-1], 0))
        elif partes[1] in destino:
            streams[destino[partes[1]]] = max(err, streams.get(destino[partes[1]], 0))
    return sinks, streams


def foto() -> dict:
    pw = _pipewire()
    xr_sinks, xr_streams = _xruns()
    salida = {}
    for direccion in DIRECCIONES:
        nodo = f"bluez_output.{direccion.replace(':', '_')}.1"
        salida[direccion] = {
            "pipewire": pw.get(nodo),
            "xruns": xr_sinks.get(nodo),
            "xruns_stream": xr_streams.get(nodo),
            "bluez": _bluez(direccion),
        }
    salida["sin_root"] = "RSSI y potencia: btmgmt conn-info responde Permission Denied"
    return salida


if __name__ == "__main__":
    print(json.dumps(foto(), indent=2, ensure_ascii=False))
