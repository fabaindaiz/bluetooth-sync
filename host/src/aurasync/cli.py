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
    # en adelante, ambiente. No pretende adivinar dónde están —eso no se puede— sino dar un
    # punto de partida audible que después se ajusta de oído.
    parlantes = []
    for i, s in enumerate(salidas):
        if i == 0:
            pan, ambiente = -0.7, 0.0
        elif i == 1:
            pan, ambiente = 0.7, 0.0
        else:
            pan, ambiente = 0.0, 1.0
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

    pistas = estimulos.calibracion(len(inst.parlantes), args.segundos, semilla=0)
    pistas = [args.amplitud * p for p in pistas]
    referencias = dict(zip((p.nombre for p in inst.parlantes), pistas, strict=True))
    por_nodo = {p.sink: referencias[p.nombre] for p in inst.parlantes}

    tmp = Path(tempfile.mkdtemp())
    grabacion = tmp / "calibracion.wav"
    print(f"midiendo {args.segundos:.0f} s con {len(inst.parlantes)} parlantes…")
    rec = sonido.grabar(grabacion, args.microfono)
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

    mudos = medicion.parlantes_sin_sonar(medicion.niveles(micro, referencias, resultado.retardos_ms))
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
    """Crea una salida de audio del sistema y procesa en vivo lo que se reproduzca ahí."""
    import numpy as np

    from aurasync import sonido
    from aurasync.motor import Motor

    inst = _instalacion_o_error(Path(args.config))
    if inst is None:
        return 1

    nodos = {s.nodo for s in sonido.salidas_bluetooth()}
    faltan = [p.nombre for p in inst.parlantes if p.sink not in nodos]
    if faltan:
        print(f"no están conectados: {faltan}", file=sys.stderr)
        return 1

    motor = Motor(inst, args.rate, extraer_ambiente=not args.sin_ambiente, decorrelar=not args.sin_decorrelar)
    por_nombre = {p.nombre: p.sink for p in inst.parlantes}
    silencio = np.zeros(args.bloque)

    print(f'Salida creada: "{args.descripcion}"')
    print("  Elegila como dispositivo de salida en tu sistema, o mandale una aplicación.")
    print(
        f"  {len(inst.parlantes)} parlantes · "
        f"ambiente {'no' if args.sin_ambiente else 'sí'} · "
        f"decorrelación {'no' if args.sin_decorrelar else 'sí'}"
    )
    print(f"  latencia estimada: ~{motor.latencia / args.rate * 1000 + args.bloque / args.rate * 1000 + 200:.0f} ms")
    print("\n  Ctrl-C para terminar. Al salir, la salida desaparece sola.\n")

    with (
        sonido.SinkVirtual(args.nombre, args.descripcion, args.rate) as entrada,
        sonido.Reproductor(list(por_nombre.values()), args.rate) as rep,
    ):
        while True:
            par = entrada.leer(args.bloque)
            if par is None:
                # Nada reproduciéndose. Se manda silencio igual, para que los streams A2DP
                # no se suspendan: al despertar traerían un desfase distinto del que acaba
                # de medir la calibración.
                rep.escribir(dict.fromkeys(por_nombre.values(), silencio))
                continue
            izq, der = par
            bloques = motor.procesar(izq, der)
            rep.escribir({por_nombre[n]: x for n, x in bloques.items()})
            if not rep.vivos:
                print("se desconectaron todos los parlantes", file=sys.stderr)
                return 1


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
    p_cal.add_argument("--microfono", default="alsa_input.usb-3142_fifine_Microphone-00.analog-stereo")

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
    return parser


COMANDOS = {
    "doctor": cmd_doctor,
    "sinks": cmd_sinks,
    "init": cmd_init,
    "calibrate": cmd_calibrate,
    "play": cmd_play,
    "run": cmd_run,
}


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.comando is None:
        parser.print_help()
        return 0
    with contextlib.suppress(KeyboardInterrupt):
        return COMANDOS[args.comando](args)
    return 130
