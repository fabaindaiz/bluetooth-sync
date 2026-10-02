# aurasync (host)

El paquete Python que corre en el PC (Linux o macOS): la CLI `aurasync`.

**Estado (2026-09-29):** el núcleo está construido, con **A2DP como primer backend
emisor** (d-7c8794-9afee2). Auracast sigue abierto hasta E4.

**Probado con 3 JBL Go 4 el 2026-09-29, y suena de punta a punta**
([experimentos/09](../docs/research/experimentos/09-primera-escucha-con-3-go-4.md)). El efecto
envolvente se percibe pero más débil de lo esperado, aunque la única escucha fue a volumen muy
bajo, que perjudica justamente el mecanismo que lo produce. El lazo de recalibración
(`run --recalibrar`) aplica correcciones y sus filtros evitaron escribir hasta 11 ms de error,
pero **no converge** en el parlante de `ambiente` alto. Sigue apagado por defecto.

**Dos cosas que conviene saber antes de usarlo:**

- **la calibración de `calibrate` muere con su stream.** Tres corridas seguidas dieron 15 ms de
  diferencia entre sí, así que guardarla para la sesión siguiente no sirve; queda como
  diagnóstico. La corrección útil es la que mide el lazo dentro del stream que reproduce;
- **la calibración alinea en el punto del micrófono**, no en toda la pieza: mide el retardo
  total, que incluye el vuelo por el aire (34 cm = 1 ms). Es un hueco conocido
  ([experimentos/09](../docs/research/experimentos/09-primera-escucha-con-3-go-4.md) §7).

## Qué hay en cada módulo

| Módulo | Qué hace | De dónde salen sus parámetros |
|---|---|---|
| `config.py` | la instalación: qué parlantes hay y cómo se corrige cada uno. Descritos por **coordenadas opcionales**, no por etiquetas de canal | [09](../docs/research/09-efecto-ambiental-y-diseno-de-la-experiencia.md) §4 |
| `dsp/decorrelate.py` | filtros todo-paso de fase aleatoria: lo que produce el envolvimiento | Potard y Burnett, [09](../docs/research/09-efecto-ambiental-y-diseno-de-la-experiencia.md) §11.1 |
| `dsp/ambience.py` | extracción de ambiente por coherencia entre canales, y su versión con estado para flujos | Avendaño y Jot, [09](../docs/research/09-efecto-ambiental-y-diseno-de-la-experiencia.md) §11.2 |
| `motor.py` | la cadena completa: estéreo → una señal por parlante, con retardos y ganancias que se pueden cambiar **mientras suena** | figura 9 de Avendaño y Jot |
| `dsp/retardo.py` | retardo fraccionario con rampa de velocidad limitada: cambiar el retardo sin que se oiga un clic | 0,5 ms/s = 0,05 % de cambio de tono |
| `estimulos.py` | las señales de calibración | [experimentos/06](../docs/research/experimentos/06-calibracion-rapida-y-recalibracion.md) |
| `medicion.py` | GCC-PHAT, medición simultánea, niveles y calibración autónoma | [experimentos/06](../docs/research/experimentos/06-calibracion-rapida-y-recalibracion.md) |
| `sincronia.py` | el lazo cerrado: decide si una calibración vale y la escribe sin cortar el sonido | [experimentos/08](../docs/research/experimentos/08-lazo-de-recalibracion-en-simulacion.md) |
| `sonido.py` | la capa de PipeWire: descubrir parlantes y micrófonos, el **sink virtual** del sistema, reproducir a N, grabar, el **micrófono continuo** en anillo y la **comprobación de ruteo** | P1 del roadmap, [experimentos/09](../docs/research/experimentos/09-primera-escucha-con-3-go-4.md) §2 |
| `dsp/ramps.py` | lo que se mueve mientras suena sin oírse: valores suavizados, rampa en dB y el **corte** (fundido a cero de 80 + 80 ms) | spec del servicio §6 |
| `session.py` | una sesión de audio: streams, silencio, sink virtual, comprobación de ruteo y el lazo, bloque a bloque. La usan `run` y el servicio | [experimentos/09](../docs/research/experimentos/09-primera-escucha-con-3-go-4.md) §2 |
| `control.py` | el **contrato** del servicio: valida un mensaje JSON y lo despacha. Sin E/S, igual para REST y para serie | spec del servicio §5 |
| `presets.py` | presets con nombre, solo campos artísticos, escritos de forma atómica | spec del servicio §5.6 |
| `service.py` | el programa persistente: un solo hilo escribe el motor; `service.json` con token | spec del servicio §4 |
| `rest.py` | el transporte REST, con la biblioteca estándar | spec del servicio §10, [`docs/control-api.md`](docs/control-api.md) |
| `snapshot.py` | el estado que ve el panel, armado desde el servicio | spec del servicio §15 |
| `sources.py` | la fuente del sink virtual: sistema, una aplicación, un archivo o la señal de prueba, **verificada** en `pw-dump` | spec §15, CLAUDE.md |
| `system.py` | lo que se observa del equipo (systemd, `bluetoothctl`, aplicaciones) en un hilo propio, y conectar o desconectar parlantes | spec §15 |
| `logbuffer.py` | el log del proceso en memoria, con cursor, para el panel | rama panel-demo |
| `simulated.py` | `--simular`: el motor y el lazo reales sobre una sala simulada | spec §15.5 |
| `panel/` | el panel web: `index.html`, `app.js` y `tailwind.css`, compilado de `tailwind.input.css` con `hatch run web:css` (sin Node; se versiona) | [10-panel-de-control](../docs/research/10-panel-de-control.md) |
| `dsp/eq.py` · `dsp/response.py` | ecualización por parlante desde la respuesta medida en la calibración | experimentos/10 §6 |
| `cli.py` | `doctor`, `sinks`, `init`, `calibrate`, `run`, `play`, `service` | — |

## Cómo se usa

```bash
aurasync doctor      # ¿está todo en su lugar?
aurasync init        # crea la instalación con los parlantes conectados
aurasync calibrate   # mide retardo y ganancia con el micrófono
aurasync run         # el modo de uso real (ver abajo)
aurasync play tema.wav --sin-decorrelar   # el A/B que muestra el efecto
aurasync run --recalibrar --volumen-db -12 --registro ~/lazo.jsonl   # sin validar todavía
aurasync service     # programa persistente con API REST (ver docs/control-api.md)
```

**`aurasync service` trae un panel web** (2026-10-01, d-7c8794-09d10f, sin validar con
parlantes): el link con el token que imprime al arrancar abre todo lo del panel de
`panel-demo` sobre el motor real —parlantes y roles, servicios con PID, logs, salud, niveles,
configuración, calibración dentro de la sesión, presets y A/B ciego—. `aurasync service
--simular` lo corre sin parlantes. Los tests de navegador: `hatch run browser:install` una
vez y después `hatch run browser:test` (Chromium y Firefox).

**`aurasync service` deja ajustar mientras suena** (2026-10-01, sin validar con parlantes).
Es un programa que queda vivo, con una API REST en la red local protegida por token: se
arranca y se detiene el audio, se mueven `pan`, `ambiente`, ganancias, volumen, retardo
trasero, extracción y decorrelación, y se guardan y cargan presets. La referencia, con
ejemplos de `curl`, está en [`docs/control-api.md`](docs/control-api.md) (en inglés,
d-7c8794-7b3093). Arranca con el audio detenido y el volumen en -20 dB.

**El micrófono ya no está fijo en el código:** se usa `--microfono`, si no el `microphone`
de `~/.config/aurasync/service.json`, y si no la fuente por defecto de PipeWire.

**`--recalibrar` corrige la alineación mientras suena**, midiendo contra el propio contenido:
no interrumpe ni emite ningún estímulo. Cada cambio necesita confirmarse en dos mediciones
seguidas antes de aplicarse, y el retardo se mueve con rampa para que el cambio no se oiga.
**Está sin validar acústicamente** y por eso viene apagado; antes de usarlo, leer el
protocolo en
[experimentos/08](../docs/research/experimentos/08-lazo-de-recalibracion-en-simulacion.md).
Al terminar imprime la **deriva estimada en ms/h**, que es INFERIDA.

**`run` es el modo de uso real.** Crea un dispositivo de salida que el sistema muestra como
cualquier otro; se lo elige como salida —o se le manda una aplicación sola— y todo lo que
suene ahí pasa por el procesamiento. **No deja huella**: el dispositivo vive en el proceso y
desaparece al cerrarlo.

`play` hace lo mismo desde un archivo, para probar sin depender de otra aplicación.

**Nada pide números.** `init` toma lo que hay conectado y `calibrate` mide lo demás. Lo
único que tiene sentido ajustar a mano en `instalacion.json` es `pan` y `ambiente` de cada
parlante: qué reproduce cada uno, que es una decisión artística y no algo medible.

## Requisitos

- **hatch** (probado con 1.18.1). Maneja el Python 3.12 y los entornos por su
  cuenta, fuera del repositorio, y usa uv internamente para instalar. No hay que
  instalar uv aparte.
  - macOS: `brew install hatch`.
  - Linux: con el binario oficial de hatch o `pipx install hatch`. Queda en el home
    del usuario y no requiere root.
- **macOS arm64 o Linux x86_64.** `lc3py` solo publica ruedas para esas dos
  plataformas; en cualquier otra, la instalación falla (a propósito).

## Comandos

```bash
cd host
hatch test                  # tests (pytest)
hatch fmt --check           # lint y formato (ruff, con la versión que fija hatch)
hatch run aurasync --version
```

Desde la raíz, `scripts/check.sh` corre todo esto junto con el chequeo del bundle.

## Versiones

- `bumble` y `lc3py` van con versión exacta en `pyproject.toml`, porque Bumble
  todavía no llega a 1.0 y cambia su API.
- Las dependencias transitivas no se fijan: los lockfiles de hatch 1.18.1 borran el
  propio proyecto del entorno. La explicación está en el comentario de
  `pyproject.toml` y en d-7c8794-c23c20.
