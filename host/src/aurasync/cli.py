"""La línea de comandos de aurasync.

El orden en que se usan los subcomandos es el orden en que se arma una sesión:

```
aurasync doctor      ¿está todo en su lugar?
aurasync init        crea la instalación con los parlantes que haya, sin escribir números
aurasync calibrate   mide retardo y ganancia de cada uno, con el micrófono
aurasync run         crea una salida del sistema y procesa en vivo lo que suene ahí
aurasync play        lo mismo, pero desde un archivo
```

`run` es el modo de uso real: crea un dispositivo de salida que el sistema muestra como
cualquier otro, y todo lo que se rutee ahí —el navegador, un reproductor, lo que sea— pasa
por el procesamiento. `play` existe para probar con un archivo sin depender de otra
aplicación.

**Nada pide números.** `init` toma lo que hay conectado y `calibrate` mide lo demás. Lo
único que tiene sentido ajustar a mano es qué reproduce cada parlante: su `pan` y cuánto
`ambiente` lleva, que son la decisión artística y no algo que se pueda medir.
"""

from __future__ import annotations

import argparse
import contextlib
import sys
import tempfile
import time
from pathlib import Path

from aurasync import __version__

SEGUNDOS_DE_CALIBRACION = 10.0
BLOQUE = 4096


def _instalacion_o_error(ruta: Path):
    from aurasync.config import Instalacion

    if not ruta.exists():
        print(f"no hay instalación en {ruta}. Creala con: aurasync init", file=sys.stderr)
        return None
    return Instalacion.cargar(ruta)


def cmd_doctor(args) -> int:
    """Revisa que el equipo pueda hacer lo que el resto de los comandos necesita."""
    from aurasync import sonido

    print("== parlantes Bluetooth ==")
    salidas = sonido.salidas_bluetooth()
    if not salidas:
        print("  ninguno conectado")
    for s in salidas:
        print(f"  {s.descripcion:<24} {s.nodo:<42} códec {s.codec}")

    problemas = []
    if not salidas:
        problemas.append("no hay parlantes conectados")
    elif sonido.codecs_mezclados(salidas):
        problemas.append(
            "hay más de un códec en uso. Con códecs distintos el desfase entre parlantes "
            "salta a 45 a 150 ms, que ya es eco audible. Se fuerza uno solo con "
            "`bluez5.codecs = [ sbc ]` en ~/.config/wireplumber/wireplumber.conf.d/"
        )

    minimo_util = 2
    if len(salidas) > 3:  # noqa: PLR2004
        problemas.append(
            f"hay {len(salidas)} parlantes: medido, este equipo sostiene 3 streams A2DP "
            "estables y con 4 el enlace se desestabiliza"
        )
    elif 0 < len(salidas) < minimo_util:
        problemas.append("con un solo parlante no hay nada que sincronizar")

    # El micrófono es el instrumento de `calibrate` y de `run --recalibrar`. Sin él no se
    # puede medir nada, y el nombre del nodo es largo y fácil de equivocar.
    print("\n== micrófono ==")
    entradas = [e for e in sonido.entradas_audio() if not e.es_monitor]
    if not entradas:
        print("  ninguno (sin micrófono no se puede calibrar)")
    elegido = resolver_microfono(args)
    for e in entradas:
        marca = "  ← el que se usa" if e.nodo == elegido else ""
        print(f"  {e.descripcion:<32} {e.nodo}{marca}")
    if entradas and elegido is None:
        print("  nota: no hay micrófono por defecto; usá --microfono o `microphone` en service.json")

    ruta = Path(args.config)
    print(f"\n== instalación ==\n  {ruta}: {'existe' if ruta.exists() else 'no existe'}")
    if ruta.exists():
        inst = _instalacion_o_error(ruta)
        if inst is not None:
            nodos = {s.nodo for s in salidas}
            for p in inst.parlantes:
                estado = "conectado" if p.sink in nodos else "NO conectado"
                print(
                    f"  {p.nombre:<16} pan {p.pan:>+5.2f}  ambiente {p.ambiente:>4.2f}  "
                    f"retardo {p.retardo_ms:>+7.2f} ms  ganancia {p.ganancia_db:>+6.2f} dB  {estado}"
                )

    print("\n== veredicto ==")
    if problemas:
        for p in problemas:
            print(f"  ✗ {p}")
        return 1
    print("  ✓ todo en su lugar")
    return 0


def cmd_sinks(_args) -> int:
    from aurasync import sonido

    for s in sonido.salidas_bluetooth():
        print(f"{s.nodo}\t{s.descripcion}\t{s.codec}\t{s.direccion}")
    return 0


def cmd_init(args) -> int:
    """Crea la instalación con los parlantes conectados, repartiendo roles por defecto."""
    from aurasync import sonido
    from aurasync.config import Instalacion, Parlante

    salidas = sonido.salidas_bluetooth()
    if not salidas:
        print("no hay parlantes Bluetooth conectados", file=sys.stderr)
        return 1

    # Reparto por defecto: el primero a la izquierda, el segundo a la derecha, y del tercero
    # en adelante, mayormente ambiente. No pretende adivinar dónde están —eso no se puede—
    # sino dar un punto de partida audible que después se ajusta de oído.
    #
    # **Ninguno queda en ambiente puro, y eso cambió el 2026-09-29 después de escucharlo.**
    # El tercer parlante arrancaba en `ambiente=1.0` y en la primera escucha real sonó "muy
    # difuso": con material corriente, el ambiente extraído por coherencia es poco y sin
    # transitorios, así que un parlante que solo reproduce eso no se percibe como parlante.
    # Con 0,55 lleva algo de directo, se ubica, y sigue aportando el envolvimiento
    # (`docs/research/experimentos/09-primera-escucha-con-3-go-4.md`).
    parlantes = []
    for i, s in enumerate(salidas):
        if i == 0:
            pan, ambiente = -0.7, 0.15
        elif i == 1:
            pan, ambiente = 0.7, 0.15
        else:
            pan, ambiente = 0.0, 0.55
        parlantes.append(Parlante(s.descripcion, s.nodo, pan=pan, ambiente=ambiente))

    inst = Instalacion(parlantes=parlantes)
    ruta = Path(args.config)
    if ruta.exists() and not args.forzar:
        print(f"{ruta} ya existe. Usá --forzar para reemplazarla.", file=sys.stderr)
        return 1
    inst.guardar(ruta)
    print(f"instalación creada en {ruta}:")
    for p in inst.parlantes:
        rol = "ambiente" if p.ambiente > 0.5 else ("izquierda" if p.pan < 0 else "derecha")  # noqa: PLR2004
        rol += f" (ambiente {p.ambiente:.2f})"
        print(f"  {p.nombre:<24} {rol}")
    print("\nAjustá `pan` y `ambiente` si querés, y después: aurasync calibrate")
    return 0


def cmd_calibrate(args) -> int:
    """Mide retardo y ganancia de cada parlante y los guarda. No pide ningún número."""
    import numpy as np

    from aurasync import estimulos, medicion, sonido

    ruta = Path(args.config)
    inst = _instalacion_o_error(ruta)
    if inst is None:
        return 1

    nodos = {s.nodo for s in sonido.salidas_bluetooth()}
    faltan = [p.nombre for p in inst.parlantes if p.sink not in nodos]
    if faltan:
        print(f"no están conectados: {faltan}", file=sys.stderr)
        return 1

    microfono = resolver_microfono(args)
    if microfono is None:
        print("no hay micrófono: pasá --microfono (aurasync doctor lista los que hay)", file=sys.stderr)
        return 1

    pistas = estimulos.calibracion(len(inst.parlantes), args.segundos, semilla=0)
    pistas = [args.amplitud * p for p in pistas]
    referencias = dict(zip((p.nombre for p in inst.parlantes), pistas, strict=True))
    por_nodo = {p.sink: referencias[p.nombre] for p in inst.parlantes}

    tmp = Path(tempfile.mkdtemp())
    grabacion = tmp / "calibracion.wav"
    print(f"midiendo {args.segundos:.0f} s con {len(inst.parlantes)} parlantes…")
    rec = sonido.grabar(grabacion, microfono)
    time.sleep(0.7)
    try:
        with sonido.Reproductor(list(por_nodo)) as rep:
            for i in range(0, len(pistas[0]), BLOQUE):
                rep.escribir({n: ref[i : i + BLOQUE] for n, ref in por_nodo.items()})
    finally:
        time.sleep(0.7)
        sonido.terminar_grabacion(rec)

    micro = sonido.leer_wav_mono(grabacion)
    resultado = medicion.calibrar(micro, referencias)
    if resultado is None:
        print("no se pudo alinear la grabación con lo reproducido.", file=sys.stderr)
        print("¿El micrófono está captando los parlantes?", file=sys.stderr)
        return 1

    # Los niveles vienen de la propia medición: recalcularlos acá, con las referencias sin
    # alinear, marcaba "no suena" a parlantes que sonaban bien.
    mudos = resultado.sin_sonar()
    print(f"\n{'parlante':<24} {'retardo':>10} {'ganancia':>10} {'estabilidad':>12}")
    for p in inst.parlantes:
        nombre = p.nombre
        marca = "  ← no suena" if nombre in mudos else ""
        print(
            f"{nombre:<24} {resultado.retardos_ms[nombre]:>+9.2f} ms "
            f"{resultado.ganancias_db[nombre]:>+8.2f} dB {resultado.estabilidad_ms[nombre]:>10.2f} ms{marca}"
        )

    if not resultado.confiable:
        print(f"\nMedición dudosa en: {resultado.dudosos()}", file=sys.stderr)
        print(
            "Se guarda igual, pero conviene repetirla con menos ruido en la sala.",
            file=sys.stderr,
        )
    for p in inst.parlantes:
        if np.isfinite(resultado.retardos_ms[p.nombre]):
            p.retardo_ms = resultado.retardos_ms[p.nombre]
            p.ganancia_db = resultado.ganancias_db[p.nombre]
    inst.guardar(ruta)
    print(f"\nguardado en {ruta}")
    return 0 if resultado.confiable else 2


def cmd_play(args) -> int:
    """Reproduce un archivo por los parlantes, con el efecto envolvente."""
    from aurasync import sonido
    from aurasync.motor import Motor

    inst = _instalacion_o_error(Path(args.config))
    if inst is None:
        return 1

    izq, der, sr = sonido.leer_wav_estereo(Path(args.archivo))
    motor = Motor(inst, sr, extraer_ambiente=not args.sin_ambiente, decorrelar=not args.sin_decorrelar)
    por_nombre = {p.nombre: p.sink for p in inst.parlantes}

    print(f"{Path(args.archivo).name}: {len(izq) / sr:.0f} s por {len(inst.parlantes)} parlantes")
    print(
        f"  ambiente: {'no' if args.sin_ambiente else 'sí'}   "
        f"decorrelación: {'no' if args.sin_decorrelar else 'sí'}   "
        f"latencia del proceso: {motor.latencia / sr * 1000:.0f} ms"
    )

    with sonido.Reproductor(list(por_nombre.values())) as rep:
        for i in range(0, len(izq), BLOQUE):
            bloques = motor.procesar(izq[i : i + BLOQUE], der[i : i + BLOQUE])
            rep.escribir({por_nombre[n]: x for n, x in bloques.items()})
    return 0


def cmd_run(args) -> int:
    """Crea una salida de audio del sistema y procesa en vivo lo que se reproduzca ahí.

    El lazo de audio vive en `session.py`, que es el mismo que usa `aurasync service`.
    """
    import json
    import signal
    import time

    from aurasync.motor import Motor
    from aurasync.session import AudioSession, SessionError, SessionOptions, missing_speakers

    inst = _instalacion_o_error(Path(args.config))
    if inst is None:
        return 1

    faltan = missing_speakers(inst)
    if faltan:
        print(f"no están conectados: {faltan}", file=sys.stderr)
        return 1

    microfono = resolver_microfono(args) if args.recalibrar else None
    if args.recalibrar and microfono is None:
        print("no hay micrófono: pasá --microfono (aurasync doctor lista los que hay)", file=sys.stderr)
        return 1

    motor = Motor(
        inst,
        args.rate,
        extraer_ambiente=not args.sin_ambiente,
        decorrelar=not args.sin_decorrelar,
        volumen_db=args.volumen_db,
        ecualizar=True,
    )

    print(f'Salida creada: "{args.descripcion}"')
    print("  Elegila como dispositivo de salida en tu sistema, o mandale una aplicación.")
    print(
        f"  {len(inst.parlantes)} parlantes · "
        f"ambiente {'no' if args.sin_ambiente else 'sí'} · "
        f"decorrelación {'no' if args.sin_decorrelar else 'sí'} · "
        f"volumen {args.volumen_db:+.0f} dB"
    )
    print(f"  latencia estimada: ~{motor.latencia / args.rate * 1000 + args.bloque / args.rate * 1000 + 200:.0f} ms")

    # -- el lazo de recalibración, si se pidió ----------------------------------------
    # Mide contra **el propio contenido**, así que no interrumpe ni emite ningún estímulo.
    # Está apagado por defecto: hasta que se valide acústicamente, el comportamiento
    # normal de `run` es el de siempre.
    if args.recalibrar:
        print(
            f"\n  Recalibración continua: cada {args.cada:.0f} s, midiendo {args.medir:.0f} s "
            f"contra el propio contenido, con micrófono {microfono.split('.')[0]}…"
        )
        print("  Cada cambio necesita confirmarse en dos mediciones seguidas antes de aplicarse.")
        if args.registro:
            print(f"  Registro: {args.registro}")

    print("\n  Ctrl-C para terminar. Al salir, la salida desaparece sola.\n")

    registro = open(args.registro, "a", buffering=1) if (args.recalibrar and args.registro) else None  # noqa: SIM115
    arranque = time.monotonic()

    # **SIGTERM se trata como Ctrl-C.** Sin esto, terminar el proceso desde afuera se lleva
    # sin pasar por el cierre: `--guardar` no escribía nada y la deriva estimada no se
    # imprimía. Pasó en la primera sesión con parlantes: el lazo había corregido 5,82 ms y
    # el archivo quedó en cero (`docs/research/experimentos/09-…`).
    def _como_ctrl_c(*_) -> None:
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _como_ctrl_c)

    def anotar(clase: str, t: float | None = None, **campos) -> None:
        t = round(time.monotonic() - arranque, 2) if t is None else t
        linea = {"t": t, "clase": clase, **campos}
        # `flush` porque `run` se deja corriendo en segundo plano y ahí Python almacena la
        # salida en buffer: sin esto no se ve nada hasta que el proceso termina.
        print(f"  [{t:>7.1f} s] {clase}: {campos.get('motivo', '')}", flush=True)
        if registro is not None and clase != "ruteo":
            registro.write(json.dumps(linea, ensure_ascii=False) + "\n")

    opciones = SessionOptions(
        rate=args.rate,
        block=args.bloque,
        sink_name=args.nombre,
        sink_description=args.descripcion,
        recalibrate=args.recalibrar,
        microphone=microfono,
        every_s=args.cada,
        measure_s=args.medir,
    )
    sesion = AudioSession(inst, motor, opciones, anotar)
    try:
        sesion.open()
        while True:
            sesion.step()
    except KeyboardInterrupt:
        print()
    except SessionError as error:
        print(error.message, file=sys.stderr)
        return 1
    finally:
        sesion.close()
        if registro is not None:
            registro.close()

    if sesion.loop is not None:
        deriva = sesion.loop.deriva_ms_h()
        if deriva:
            print("\n  Deriva estimada (INFERIDA, del propio lazo):")
            for nombre, valor in sorted(deriva.items()):
                print(f"    {nombre:<24} {valor:+8.2f} ms/h")
        if args.guardar:
            inst.guardar(Path(args.config))
            print(f"\n  Instalación guardada en {args.config}")
    return 0


def resolver_microfono(args) -> str | None:
    """`--microfono`, si no el de `service.json`, si no la fuente por defecto de PipeWire.

    Antes estaba fijo el fifine de `PC-Ryzen5`, y en cualquier otro equipo había que pasar
    `--microfono` siempre (spec del servicio, §4.3).
    """
    from aurasync import sonido
    from aurasync.service import microphone_from_config

    return getattr(args, "microfono", None) or microphone_from_config() or sonido.microfono_por_defecto()


def cmd_service(args) -> int:
    """Corre el servicio de control: un programa que queda vivo, con panel web y API REST."""
    import shutil

    from aurasync.logbuffer import LogBuffer
    from aurasync.service import ConfigError, Service, config_dir, load_config, serve
    from aurasync.session import SessionOptions
    from aurasync.system import Observer

    try:
        config = load_config(config_dir() / "service.json")
    except ConfigError as error:
        print(error, file=sys.stderr)
        return 1
    # `--config` manda sobre el archivo del servicio solo si se pasó a mano.
    instalacion = Path(args.config) if args.config_explicita else config.installation_path
    presets = config_dir() / "presets.json"
    microfono = resolver_microfono(args) if not args.simular else "simulado"
    opciones = SessionOptions(rate=args.rate, block=args.bloque, microphone=microfono)
    extra = {}
    if args.simular:
        from aurasync.config import Instalacion, Parlante
        from aurasync.simulated import SimulatedObserver, SimulatedSession

        # La simulación trabaja sobre una copia: nada de lo que se haga ahí toca la
        # instalación ni los presets reales.
        tmp = Path(tempfile.mkdtemp(prefix="aurasync-sim-"))
        copia = tmp / "instalacion.json"
        if instalacion.exists():
            shutil.copy(instalacion, copia)
        else:
            Instalacion(
                parlantes=[
                    Parlante("JBL Go 4 Red", "bluez_output.90_F2_60_75_4A_83.1", pan=-0.7, ambiente=0.15),
                    Parlante("JBL Go 4 Black", "bluez_output.90_F2_60_DA_66_6D.1", pan=0.7, ambiente=0.15),
                    Parlante("JBL Go 4 Blue", "bluez_output.90_F2_60_E3_07_39.1", pan=0.0, ambiente=0.55),
                ]
            ).guardar(copia)
        if presets.exists():
            shutil.copy(presets, tmp / "presets.json")
        instalacion, presets = copia, tmp / "presets.json"
        from aurasync.bt_volume import BluetoothVolume
        from aurasync.simulated import SimulatedMonitor, SimulatedRadio, SimulatedVolumes, simulated_log_level

        # El registro de radio y el volumen de los parlantes también se simulan: nada toca el
        # sistema, y los descartes simulados se marcan como tales (`state.radio.simulated`).
        nivel = simulated_log_level(tmp / "cambios-de-sistema.txt")
        sim_inst = Instalacion.cargar(copia)
        extra = {
            "session_factory": SimulatedSession,
            "monitor_factory": SimulatedMonitor,
            "observer": SimulatedObserver(sim_inst),
            "simulated": True,
            "log_level": nivel,
            "radio": SimulatedRadio(lambda: [p.sink for p in sim_inst.parlantes], lambda: nivel.mode is not None),
            "bt_volume": BluetoothVolume(SimulatedVolumes()),
        }
        print(f"SIMULADO: sin parlantes ni PipeWire; los cambios van a {tmp}")
    else:
        from aurasync.radio import LogLevel, RadioMonitor

        # El monitor de radio vive con el servicio; el nivel de registro se anota, con cómo
        # revertirlo, en <config>/cambios-de-sistema.txt antes de tocarlo.
        extra = {
            "observer": Observer(),
            "radio": RadioMonitor(),
            "log_level": LogLevel(config_dir() / "cambios-de-sistema.txt"),
        }
    servicio = Service(
        instalacion,
        presets,
        options=opciones,
        measurements_path=config.measurements_path,
        logs=LogBuffer(),
        config_path=None if args.simular else config_dir() / "service.json",
        monitor=None if args.simular else config.monitor,
        **extra,
    )
    print(f"instalación: {instalacion}{'' if instalacion.exists() else ' (no existe)'}")
    print(f"micrófono: {microfono or 'ninguno'} · mediciones: {config.measurements_path}")
    return serve(servicio, config, bind=args.bind, port=args.port, directory=config_dir())


def cmd_radio_log(args) -> int:
    """Sube o devuelve el nivel de registro de bluez5, para ver los paquetes que la radio descarta.

    Si el servicio está corriendo, se le pide a él (así lo revierte al cerrarse). Si no, se
    hace acá mismo, anotado en `<config>/cambios-de-sistema.txt` con cómo revertirlo; el
    servicio revierte al arrancar cualquier cambio que haya quedado sin su "revertido".
    """
    from aurasync.radio import LogLevel
    from aurasync.service import config_dir

    cambios = config_dir() / "cambios-de-sistema.txt"
    respuesta = _al_servicio(args.accion, args.modo)
    if respuesta is not None:
        estado = respuesta.get("radio_log") or {}
        if not respuesta.get("ok", True):
            print(f"el servicio no lo hizo: {respuesta['error']['message']}", file=sys.stderr)
            return 1
        print(f"servicio: registro de radio {'encendido' if estado.get('active') else 'apagado'}", end="")
        print(f" ({estado['mode']})" if estado.get("mode") else "")
        if estado.get("error"):
            print(f"  error: {estado['error']}", file=sys.stderr)
        radio = respuesta.get("radio") or {}
        print(f"  monitor: {'con datos' if radio.get('available') else radio.get('reason')}")
        for nombre, enlace in (radio.get("speakers") or {}).items():
            print(
                f"  {nombre}: bitpool {enlace.get('bitpool')}, {enlace.get('drops_total')} descartes, "
                f"{enlace.get('drops_per_min')} por minuto"
            )
        print(f"  cambios anotados en {estado.get('changes_file') or cambios}")
        return 1 if estado.get("error") else 0
    nivel = LogLevel(cambios)
    if args.accion == "on":
        estado = nivel.enable(args.modo)
        if estado["error"]:
            print(f"no se pudo: {estado['error']}", file=sys.stderr)
            return 1
        print(f"registro de radio encendido ({args.modo}), sin el servicio corriendo.")
        print(f"  anotado en {cambios}; se revierte con `aurasync radio-log off` o al arrancar el servicio")
        return 0
    if args.accion == "off":
        revertido = nivel.recover()
        print("registro de radio devuelto a como estaba" if revertido else "no había un cambio pendiente que revertir")
        return 0
    actual = nivel.read_current()
    print(f"el servicio no está corriendo; log.level de WirePlumber: {actual or 'sin tocar'}")
    return 0


def _al_servicio(accion: str, modo: str) -> dict | None:
    """Le pide `radio_log` (o el estado) al servicio local; None si no está corriendo."""
    import http.client
    import json

    from aurasync.service import ConfigError, config_dir, load_config

    ruta = config_dir() / "service.json"
    if not ruta.exists():
        return None
    try:
        config = load_config(ruta)
    except ConfigError:
        return None
    cabeceras = {"Authorization": f"Bearer {config.token}", "Content-Type": "application/json"}

    def pedir(metodo: str, ruta_api: str, cuerpo: dict | None = None) -> dict:
        conexion = http.client.HTTPConnection("127.0.0.1", config.port, timeout=5)
        try:
            datos = json.dumps(cuerpo) if cuerpo is not None else None
            conexion.request(metodo, "/v1" + ruta_api, body=datos, headers=cabeceras)
            return json.loads(conexion.getresponse().read())
        finally:
            conexion.close()

    try:
        if accion in {"on", "off"}:
            orden = {"v": 1, "op": "radio_log", "active": accion == "on"}
            if accion == "on":
                orden["mode"] = modo
            respuesta = pedir("POST", "/command", orden)
            if not respuesta.get("ok"):
                return respuesta
            # El cambio corre en un hilo del servicio: se espera a que termine.
            for _ in range(50):
                estado = pedir("GET", "/state")["result"]
                if not estado.get("radio_log", {}).get("pending"):
                    break
                time.sleep(0.1)
        estado = pedir("GET", "/state")["result"]
    except (OSError, ValueError, KeyError):
        return None
    return {"ok": True, "radio_log": estado.get("radio_log"), "radio": estado.get("radio")}


def _pedir(metodo: str, ruta_api: str, cuerpo: dict | None = None) -> tuple[int, dict] | None:
    """Una petición al servicio local con el token maestro; None si no está corriendo."""
    import http.client
    import json

    from aurasync.service import ConfigError, config_dir, load_config

    ruta = config_dir() / "service.json"
    if not ruta.exists():
        return None
    try:
        config = load_config(ruta)
    except ConfigError:
        return None
    cabeceras = {"Authorization": f"Bearer {config.token}", "Content-Type": "application/json"}
    conexion = http.client.HTTPConnection("127.0.0.1", config.port, timeout=5)
    try:
        datos = json.dumps(cuerpo) if cuerpo is not None else None
        conexion.request(metodo, "/v1" + ruta_api, body=datos, headers=cabeceras)
        respuesta = conexion.getresponse()
        return respuesta.status, json.loads(respuesta.read())
    except (OSError, ValueError):
        return None
    finally:
        conexion.close()


def cmd_clients(args) -> int:
    """Los dispositivos emparejados con el servicio y las solicitudes pendientes.

    Con el servicio corriendo, todo pasa por él (si se editara `clients.json` por debajo, el
    servicio lo pisaría al escribir). Sin el servicio, `list` y `revoke` trabajan sobre el archivo.
    """
    from aurasync.clients import ClientStore
    from aurasync.service import config_dir

    archivo = config_dir() / "clients.json"
    if args.accion == "list":
        clientes = _pedir("GET", "/clients")
        if clientes is not None and clientes[0] == 200:  # noqa: PLR2004
            lista = clientes[1]["result"]["clients"]
            pendientes = (_pedir("GET", "/pair") or (0, {}))[1].get("result", {})
        elif archivo.exists():
            lista, pendientes = ClientStore(archivo).list(), {}
            print("(el servicio no está corriendo: leído de clients.json)")
        else:
            print("no hay clientes emparejados")
            return 0
        for c in lista:
            print(
                f"  {c['id']}  {c['scope']:7}  {c['name']}  · último uso {c['last_used']} desde {c['last_ip'] or '?'}"
            )
        if not lista:
            print("  (ningún cliente; el token maestro de service.json sigue valiendo como admin)")
        for r in pendientes.get("requests", []):
            print(f"  pendiente {r['id']}  '{r['name']}' desde {r['ip']} pide {r['scope']} (control {r['check']})")
        ventana = pendientes.get("window") or {}
        if ventana.get("open"):
            print(f"  ventana de primer cliente abierta: {ventana['remaining_s']:.0f} s")
        return 0
    if args.accion == "revoke":
        if not args.id:
            print("falta el id del cliente (aurasync clients list)", file=sys.stderr)
            return 2
        respuesta = _pedir("DELETE", f"/clients/{args.id}")
        if respuesta is None:
            if not archivo.exists():
                print("no hay clients.json", file=sys.stderr)
                return 1
            from aurasync.control import ContractError

            try:
                ClientStore(archivo).revoke(args.id)
            except ContractError as error:
                print(error.message, file=sys.stderr)
                return 1
            print(f"cliente {args.id} revocado (en clients.json; el servicio no estaba corriendo)")
            return 0
        return _mostrar(respuesta, f"cliente {args.id} revocado")
    if args.accion in {"approve", "deny"}:
        if not args.id:
            print("falta el id de la solicitud (aurasync clients list)", file=sys.stderr)
            return 2
        cuerpo = {"scope": args.alcance} if args.accion == "approve" and args.alcance else None
        respuesta = _pedir("POST", f"/pair/{args.id}/{args.accion}", cuerpo)
        if respuesta is None:
            print("el servicio no está corriendo: las solicitudes viven en él", file=sys.stderr)
            return 1
        return _mostrar(respuesta, "aprobada" if args.accion == "approve" else "rechazada")
    respuesta = _pedir("POST", "/pair/code", {"seconds": args.segundos})
    if respuesta is None:
        print("el servicio no está corriendo", file=sys.stderr)
        return 1
    if respuesta[0] == 200:  # noqa: PLR2004
        r = respuesta[1]["result"]
        print(f"código de emparejamiento: {r['code']} (vale {r['expires_in_s']:.0f} s, una vez)")
        return 0
    return _mostrar(respuesta, "")


def _mostrar(respuesta: tuple[int, dict], hecho: str) -> int:
    estado, cuerpo = respuesta
    if estado == 200:  # noqa: PLR2004
        print(hecho)
        return 0
    print(f"el servicio no lo hizo: {cuerpo.get('error', {}).get('message')}", file=sys.stderr)
    return 1


def cmd_tls(args) -> int:
    """La raíz propia del servicio: dónde está, su huella y cómo instalarla en un teléfono."""
    from aurasync.service import config_dir, lan_urls, load_config
    from aurasync.tls import Certificates

    certificados = Certificates(config_dir() / "tls")
    if not certificados.root_pem.exists() or not certificados.server_pem.exists():
        print(f'no hay certificados en {certificados.dir}: arrancá `aurasync service` con "tls": true en service.json')
        return 1
    info = certificados.info()
    print(f"raíz: {info.root_pem}")
    print(f"  sha256 {info.root_sha256}")
    if args.accion == "info":
        print(f"certificado del servidor: {info.cert_pem}")
        print(f"  sha256 {info.cert_sha256}")
        print(f"  nombres: {', '.join(info.names)}")
        print(f"  vence: {info.not_after} (se renueva solo 30 días antes, o si cambia la IP)")
        print(f"  la clave de la raíz ({certificados.root_key}) no sale de este equipo")
    try:
        puerto = load_config(config_dir() / "service.json").port
    except Exception:  # noqa: BLE001 - solo para armar los links
        puerto = 8731
    lan = [u for u in lan_urls("0.0.0.0", puerto) if "127.0.0.1" not in u]  # noqa: S104 - una URL, no un bind
    if lan:
        print(f"instalar en un iPhone: abrir en Safari {lan[0]}/v1/tls/root.mobileconfig")
        print(f"instalar en Android: {lan[0]}/v1/tls/root.crt (host/README.md explica los pasos)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    from aurasync.config import ruta_por_defecto

    parser = argparse.ArgumentParser(
        prog="aurasync",
        description="Un canal distinto a cada parlante Bluetooth, para envolver una pieza.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--config", default=str(ruta_por_defecto()), help="archivo de instalación")
    subs = parser.add_subparsers(dest="comando")

    subs.add_parser("doctor", help="revisa que el equipo pueda hacer lo que hace falta")
    subs.add_parser("sinks", help="lista los parlantes Bluetooth conectados")

    p_init = subs.add_parser("init", help="crea la instalación con los parlantes conectados")
    p_init.add_argument("--forzar", action="store_true", help="reemplaza una instalación existente")

    p_cal = subs.add_parser("calibrate", help="mide retardo y ganancia con el micrófono")
    p_cal.add_argument("--segundos", type=float, default=SEGUNDOS_DE_CALIBRACION)
    p_cal.add_argument("--amplitud", type=float, default=0.4)
    p_cal.add_argument("--microfono", help="por defecto, el de service.json o la fuente por defecto del sistema")

    p_play = subs.add_parser("play", help="reproduce un WAV con el efecto envolvente")
    p_play.add_argument("archivo")
    p_play.add_argument("--sin-ambiente", action="store_true", help="apaga la extracción")
    p_play.add_argument("--sin-decorrelar", action="store_true", help="para comparar A/B")

    p_run = subs.add_parser("run", help="crea una salida de audio del sistema y procesa en vivo lo que suene ahí")
    p_run.add_argument("--nombre", default="aurasync")
    p_run.add_argument("--descripcion", default="aurasync (envolvente)")
    p_run.add_argument("--rate", type=int, default=48000)
    p_run.add_argument("--bloque", type=int, default=BLOQUE)
    p_run.add_argument("--sin-ambiente", action="store_true", help="apaga la extracción")
    p_run.add_argument("--sin-decorrelar", action="store_true", help="para comparar A/B")
    p_run.add_argument("--volumen-db", type=float, default=0.0, help="ganancia global de salida, en dB")
    p_run.add_argument(
        "--recalibrar",
        action="store_true",
        help="corrige la alineación mientras suena, midiendo contra el propio contenido",
    )
    p_run.add_argument("--microfono", help="solo con --recalibrar; por defecto, como en calibrate")
    p_run.add_argument("--cada", type=float, default=20.0, help="segundos entre intentos de medición")
    p_run.add_argument(
        "--medir",
        type=float,
        default=sincronia_segundos_de_medicion(),
        help="segundos de contenido por medición; por debajo de 10 el estimador falla en silencio",
    )
    p_run.add_argument("--registro", help="archivo JSON Lines con lo que decide el lazo")
    p_run.add_argument("--guardar", action="store_true", help="escribe la instalación al terminar")

    p_radio = subs.add_parser(
        "radio-log", help="registro de radio: ver los paquetes que el Bluetooth descarta (un cambio de sistema)"
    )
    p_radio.add_argument("accion", choices=("on", "off", "status"))
    p_radio.add_argument(
        "--modo",
        choices=("light", "heavy"),
        default="light",
        help="light: solo los temas de bluez5; heavy: todo en debug (journald puede perder líneas)",
    )

    p_srv = subs.add_parser("service", help="programa persistente con API REST para ajustar mientras suena")
    p_srv.add_argument("--bind", help="dirección de escucha; pisa la de service.json")
    p_srv.add_argument("--port", type=int, help="puerto; pisa el de service.json")
    p_srv.add_argument("--microfono", help="para start con recalibrate; por defecto, como en calibrate")
    p_srv.add_argument("--rate", type=int, default=48000)
    p_srv.add_argument("--bloque", type=int, default=BLOQUE)
    p_srv.add_argument(
        "--simular", action="store_true", help="sin parlantes ni PipeWire: el motor real sobre una sala simulada"
    )

    p_cli = subs.add_parser("clients", help="los dispositivos emparejados con el servicio, y el emparejamiento")
    p_cli.add_argument("accion", choices=("list", "revoke", "approve", "deny", "code"))
    p_cli.add_argument("id", nargs="?", help="el id del cliente (revoke) o de la solicitud (approve, deny)")
    p_cli.add_argument("--alcance", choices=("read", "control", "admin"), help="approve: el alcance que se da")
    p_cli.add_argument("--segundos", type=float, default=120.0, help="code: cuánto vale el código")

    p_tls = subs.add_parser("tls", help="la raíz propia del HTTPS del servicio: dónde está y su huella")
    p_tls.add_argument("accion", choices=("info", "root"))
    return parser


def sincronia_segundos_de_medicion() -> float:
    """El valor por defecto sale del módulo, para que no quede duplicado acá.

    Se importa dentro de la función porque `build_parser` corre en cada invocación de la
    CLI y el resto del paquete se importa recién cuando hace falta.
    """
    from aurasync.sincronia import VentanaDeEmision

    return VentanaDeEmision.SEGUNDOS_DE_MEDICION


COMANDOS = {
    "doctor": cmd_doctor,
    "sinks": cmd_sinks,
    "init": cmd_init,
    "calibrate": cmd_calibrate,
    "play": cmd_play,
    "run": cmd_run,
    "service": cmd_service,
    "radio-log": cmd_radio_log,
    "clients": cmd_clients,
    "tls": cmd_tls,
}


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    crudos = sys.argv[1:] if argv is None else argv
    args.config_explicita = any(a == "--config" or a.startswith("--config=") for a in crudos)
    if args.comando is None:
        parser.print_help()
        return 0
    with contextlib.suppress(KeyboardInterrupt):
        return COMANDOS[args.comando](args)
    return 130
