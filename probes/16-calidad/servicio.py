"""Hablar con `aurasync service` por su API REST, y dejar la cadena como estaba.

Lo usan `probes/16-calidad/` y `probes/15-graves-y-volumen/`. Cada llamada queda en un
registro JSON Lines (si se pasa uno), con la hora, para cruzarla con la grabación.

Errores claros antes de cambiar nada: sin `service.json`, sin servicio en el puerto, o sin
una sesión sonando, `ErrorServicio` dice qué falta.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any


class ErrorServicio(RuntimeError):
    def __init__(self, codigo: str, mensaje: str) -> None:
        super().__init__(f"{codigo}: {mensaje}")
        self.codigo = codigo


def carpeta_config(config: Path | None = None) -> Path:
    return config or Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "aurasync"


class Servicio:
    def __init__(self, url: str, token: str, registro: Path | None = None) -> None:
        self.url, self.token, self.registro = url.rstrip("/"), token, registro

    @classmethod
    def conectar(
        cls, puerto: int | None = None, url: str | None = None, config: Path | None = None, registro: Path | None = None
    ) -> Servicio:
        archivo = carpeta_config(config) / "service.json"
        try:
            datos = json.loads(archivo.read_text())
            token = datos["token"]
        except (OSError, ValueError, KeyError) as e:
            msg = f"no pude leer el token de {archivo} ({e}). ¿Se corrió `aurasync service` con ese XDG_CONFIG_HOME?"
            raise ErrorServicio("sin_servicio", msg) from e
        puerto = puerto or int(os.environ.get("PUERTO") or datos.get("port") or 8731)
        servicio = cls(url or f"http://127.0.0.1:{puerto}/v1", token, registro)
        servicio.estado()  # que responda ahora, no a mitad de la prueba
        return servicio

    def _anotar(self, **datos: Any) -> None:
        if self.registro is None:
            return
        self.registro.parent.mkdir(parents=True, exist_ok=True)
        with self.registro.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"t": time.time(), **datos}, ensure_ascii=False) + "\n")

    def orden(self, op: str, timeout: float = 40.0, **args: Any) -> dict:
        cuerpo = json.dumps({"v": 1, "op": op, **args}).encode()
        pedido = urllib.request.Request(
            f"{self.url}/command", data=cuerpo, method="POST", headers={"Authorization": f"Bearer {self.token}"}
        )
        try:
            with urllib.request.urlopen(pedido, timeout=timeout) as r:
                respuesta = json.loads(r.read())
        except urllib.error.HTTPError as e:
            try:
                respuesta = json.loads(e.read())
            except ValueError:
                raise ErrorServicio("http", f"{op}: HTTP {e.code}") from e
        except (urllib.error.URLError, OSError, ValueError) as e:
            msg = f"el servicio no responde en {self.url} ({e}). ¿Está corriendo `aurasync service` en ese puerto?"
            raise ErrorServicio("sin_servicio", msg) from e
        if op != "state":
            self._anotar(op=op, args=args, respuesta=respuesta)
        if not respuesta.get("ok"):
            error = respuesta.get("error") or {}
            raise ErrorServicio(str(error.get("code")), f"{op}: {error.get('message')}")
        return respuesta["result"]

    # -- lecturas -------------------------------------------------------------------------

    def estado(self) -> dict:
        return self.orden("state")

    def sesion_sonando(self) -> dict:
        estado = self.estado()
        if estado["session"]["status"] != "playing":
            msg = (
                f"la sesión está '{estado['session']['status']}': hace falta una sonando "
                "(panel → Iniciar, o POST /v1/session/start)"
            )
            raise ErrorServicio("sin_sesion", msg)
        return estado

    def parlantes(self) -> dict[str, dict]:
        return {p["name"]: p for p in self.estado()["speakers"]}

    def etapa(self, stage: str) -> dict:
        for s in self.orden("chain")["stages"]:
            if s["id"] == stage:
                return s
        msg = f"la cadena no tiene la etapa {stage!r}"
        raise ErrorServicio("unknown_field", msg)

    def esperar(self, condicion: Callable[[dict], bool], limite_s: float, que: str, cada_s: float = 0.3) -> dict:
        fin = time.monotonic() + limite_s
        while True:
            estado = self.estado()
            if condicion(estado):
                return estado
            if time.monotonic() > fin:
                msg = f"no pasó en {limite_s:.0f} s: {que}"
                raise ErrorServicio("tiempo", msg)
            time.sleep(cada_s)

    # -- la cadena, puesta y devuelta ---------------------------------------------------------

    def restaurar_etapa(self, stage: str, elegido_antes: dict) -> bool:
        """Dejar las elecciones (`chosen`) de una etapa como estaban, y comprobarlo.

        Si todas las perillas de la etapa viven en la cadena (`store == "chain"`), se borra la
        etapa con `chain_reset` y se vuelven a poner las elecciones previas: queda exactamente
        igual, incluso "sin elección". Si alguna vive en la instalación o la sesión (pan,
        volumen…), `chain_reset` de la etapa entera las devolvería a su valor por defecto: ahí
        solo se vuelve a poner el algoritmo (y las perillas de la cadena que había elegidas).
        """
        actual = self.etapa(stage)
        if actual["chosen"] == elegido_antes:
            return True
        solo_cadena = all(p["store"] == "chain" for a in actual["algorithms"] for p in a["params"])
        algoritmo = elegido_antes.get("algorithm")
        params = elegido_antes.get("params") or {}
        if solo_cadena:
            self.orden("chain_reset", stage=stage)
        elif algoritmo is None:
            algoritmo = actual["default_algorithm"]
        if algoritmo is not None or params:
            self.orden(
                "chain_set",
                stage=stage,
                **({"algorithm": algoritmo} if algoritmo is not None else {}),
                **({"params": params} if params else {}),
            )
        for parlante, valores in (elegido_antes.get("speakers") or {}).items():
            self.orden("chain_set", stage=stage, speaker=parlante, params=valores)
        return self.etapa(stage)["chosen"] == elegido_antes


def cadena_por_defecto(cadena: dict) -> list[str]:
    """Las etapas cuyo algoritmo no es el de por defecto, o que tienen perillas de la cadena
    elegidas (la instalación —pan, ambiente, ganancia, retardo— no cuenta: es la calibración)."""
    distintas = []
    for s in cadena["stages"]:
        elegido = s.get("chosen") or {}
        if s["value"]["algorithm"] != s["default_algorithm"] or elegido.get("params"):
            distintas.append(s["id"])
    return distintas
